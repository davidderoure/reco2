"""Tests that the recommender correctly restores state after a restart.

The restart path is:
  1. Engine runs, handling events and recommendations.
  2. On each event, server.py calls story_client.save_user_model(user),
     which serialises via user.to_json() and sends to C#.
  3. On startup, server.py calls story_client.load_all_user_models(),
     which returns the stored JSON blobs.
  4. Engine calls load_population([UserModel.from_json(uid, blob) ...]).

These tests verify that the full to_json → from_json roundtrip preserves
enough state for the engine to continue correctly — no regression to cold
start, N-batch exclusion intact, batch preservation intact, tag affinity
intact — without requiring the C# backend.
"""

from __future__ import annotations

import time

from recommender.catalogue import Catalogue
from recommender.engine import RecommenderEngine, RECENT_BATCHES_TO_EXCLUDE
from recommender.models import Story, UserModel


def make_catalogue(n: int = 60) -> Catalogue:
    tags_cycle = [["a", "b"], ["b", "c"], ["c", "d"], ["a", "d"]]
    stories = [
        Story(story_id=f"s{i}", title=f"Story {i}",
              tags=tags_cycle[i % len(tags_cycle)], created_at=float(i))
        for i in range(n)
    ]
    c = Catalogue()
    c.load(stories)
    return c


def simulate_and_serialise(engine: RecommenderEngine, user_id: str, n_rounds: int, now: float) -> str:
    """Run n_rounds of scored engagement and return the serialised user model."""
    for r in range(n_rounds):
        ts = now + r * 86400
        recs = engine.get_recommendations(user_id, timestamp=ts)
        story_id = recs[0][0]
        engine.record_answered_question(user_id, story_id, [7, 5, 5, 5], timestamp=ts)
    return engine.population[user_id].to_json()


def restart_with_blob(catalogue: Catalogue, user_id: str, blob: str) -> RecommenderEngine:
    """Simulate a server restart: fresh engine, population loaded from JSON."""
    fresh_engine = RecommenderEngine(catalogue)
    user = UserModel.from_json(user_id, blob)
    fresh_engine.load_population([user])
    return fresh_engine


def test_restart_does_not_regress_to_cold_start():
    """After reload, a user with history must not be treated as a new user.
    Cold-start gives topical+collaborative; warm-start gives content-based.
    """
    catalogue = make_catalogue(n=60)
    engine = RecommenderEngine(catalogue)
    now = time.time()
    user_id = "u1"

    blob = simulate_and_serialise(engine, user_id, n_rounds=5, now=now)

    fresh_engine = restart_with_blob(catalogue, user_id, blob)
    recs = fresh_engine.get_recommendations(user_id, timestamp=now + 10 * 86400)
    rec_types = [rt for _, rt in recs]

    from recommender.models import CONTENT_BASED
    assert CONTENT_BASED in rec_types, (
        "After reload, user with history should get content-based recommendations"
    )


def test_restart_preserves_seen_stories():
    """Seen (scored) stories must not reappear after a restart while the
    catalogue has plenty of unseen content."""
    catalogue = make_catalogue(n=60)
    engine = RecommenderEngine(catalogue)
    now = time.time()
    user_id = "u1"

    blob = simulate_and_serialise(engine, user_id, n_rounds=5, now=now)
    seen_ids = set(engine.population[user_id].story_history.keys())

    fresh_engine = restart_with_blob(catalogue, user_id, blob)
    recs = fresh_engine.get_recommendations(user_id, timestamp=now + 10 * 86400)
    rec_ids = {sid for sid, _ in recs}

    assert not rec_ids & seen_ids, (
        f"Stories seen before restart reappeared after reload: {rec_ids & seen_ids}"
    )


def test_restart_preserves_tag_affinity():
    """Tag affinity built before a restart must be identical after reload."""
    catalogue = make_catalogue(n=60)
    engine = RecommenderEngine(catalogue)
    now = time.time()
    user_id = "u1"

    blob = simulate_and_serialise(engine, user_id, n_rounds=5, now=now)
    affinity_before = dict(engine.population[user_id].tag_affinity)

    fresh_engine = restart_with_blob(catalogue, user_id, blob)
    affinity_after = dict(fresh_engine.population[user_id].tag_affinity)

    assert affinity_before == affinity_after, (
        f"Tag affinity changed across restart:\n  before={affinity_before}\n  after={affinity_after}"
    )


def test_restart_preserves_recent_batches_exclusion():
    """Stories from recent batches must still be excluded after a restart,
    so the N-batch window isn't reset to zero by a process restart."""
    catalogue = make_catalogue(n=60)
    engine = RecommenderEngine(catalogue)
    now = time.time()
    user_id = "u1"

    blob = simulate_and_serialise(engine, user_id, n_rounds=2, now=now)
    recent_before = [
        sid
        for batch in engine.population[user_id].recent_batches[:RECENT_BATCHES_TO_EXCLUDE]
        for sid in batch
    ]

    fresh_engine = restart_with_blob(catalogue, user_id, blob)
    # Fresh batch must score a connectedness answer first (has_new_score=True from reload? No —
    # has_new_score resets to False on restart, so we need to answer first)
    fresh_engine.record_answered_question(user_id, recent_before[0], [7, 5, 5, 5], timestamp=now + 3 * 86400)
    recs = fresh_engine.get_recommendations(user_id, timestamp=now + 4 * 86400)
    rec_ids = {sid for sid, _ in recs}

    assert not rec_ids & set(recent_before), (
        f"Recent-batch stories leaked after restart: {rec_ids & set(recent_before)}"
    )


def test_restart_preserves_batch_preservation_state():
    """If the user's last action before restart was a quick exit (no score),
    the next call after reload should return the preserved batch."""
    catalogue = make_catalogue(n=60)
    engine = RecommenderEngine(catalogue)
    now = time.time()
    user_id = "u1"

    # Round 1: score a story to build history
    recs1 = engine.get_recommendations(user_id, timestamp=now)
    engine.record_answered_question(user_id, recs1[0][0], [7, 5, 5, 5], timestamp=now)

    # Round 2: get a batch but don't score — quick exit
    recs2 = engine.get_recommendations(user_id, timestamp=now + 86400)
    batch2_ids = {sid for sid, _ in recs2}

    # Serialise immediately after quick exit (no score given)
    blob = engine.population[user_id].to_json()

    fresh_engine = restart_with_blob(catalogue, user_id, blob)
    recs3 = fresh_engine.get_recommendations(user_id, timestamp=now + 2 * 86400)
    batch3_ids = {sid for sid, _ in recs3}

    assert batch3_ids == batch2_ids, (
        "After restart following a quick exit, should return the preserved batch\n"
        f"  expected: {batch2_ids}\n  got:      {batch3_ids}"
    )


def test_json_roundtrip_is_lossless():
    """to_json → from_json must reproduce every field exactly."""
    catalogue = make_catalogue(n=60)
    engine = RecommenderEngine(catalogue)
    now = time.time()
    user_id = "u1"

    blob = simulate_and_serialise(engine, user_id, n_rounds=5, now=now)
    original = engine.population[user_id]
    restored = UserModel.from_json(user_id, blob)

    assert original.tag_affinity == restored.tag_affinity
    assert original.recommended_story_ids == restored.recommended_story_ids
    assert original.recent_batches == restored.recent_batches
    assert original.last_recommendation_request_at == restored.last_recommendation_request_at
    assert original.has_new_score_since_last_request == restored.has_new_score_since_last_request
    assert set(original.story_history.keys()) == set(restored.story_history.keys())
    for sid in original.story_history:
        o, r = original.story_history[sid], restored.story_history[sid]
        assert o.connectedness == r.connectedness
        assert o.viewed_pct == r.viewed_pct
        assert o.timestamp == r.timestamp
        assert o.secondary_scores == r.secondary_scores
        assert o.aborted == r.aborted
