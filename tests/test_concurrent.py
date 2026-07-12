"""Concurrent-access stress tests for the recommender engine.

The gRPC server runs one Python process with a thread-pool executor —
multiple request threads share a single RecommenderEngine instance.
These tests verify that concurrent calls produce correct results and
don't corrupt shared state (population dict, catalogue, rng instances).

Run standalone for a longer soak: pytest tests/test_concurrent.py -v -s
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from recommender.catalogue import Catalogue
from recommender.engine import RecommenderEngine
from recommender.models import Story


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


def test_concurrent_get_recommendations_returns_valid_results():
    """10 users firing GetRecommendations simultaneously — each must get
    6 unique story IDs with no exceptions."""
    engine = RecommenderEngine(make_catalogue())
    n_users = 10
    errors = []
    results = {}

    def get_recs(user_id: str) -> list:
        return engine.get_recommendations(user_id, timestamp=time.time())

    with ThreadPoolExecutor(max_workers=n_users) as ex:
        futures = {ex.submit(get_recs, f"user-{i}"): i for i in range(n_users)}
        for future in as_completed(futures):
            i = futures[future]
            try:
                recs = future.result()
                results[i] = recs
            except Exception as e:
                errors.append(e)

    assert not errors, f"Exceptions during concurrent recommendations: {errors}"
    for i, recs in results.items():
        ids = [sid for sid, _ in recs]
        assert len(recs) == 6, f"user-{i} got {len(recs)} recommendations, expected 6"
        assert len(set(ids)) == 6, f"user-{i} got duplicate story IDs: {ids}"


def test_concurrent_mixed_events_and_recommendations():
    """Simulate realistic concurrent load: events (answered question, bookmark,
    abort) and GetRecommendations firing simultaneously across 10 users."""
    engine = RecommenderEngine(make_catalogue())
    n_users = 10
    n_rounds = 5
    errors = []
    now = time.time()

    def user_session(user_id: str) -> None:
        for round_idx in range(n_rounds):
            ts = now + round_idx * 86400
            recs = engine.get_recommendations(user_id, timestamp=ts)
            assert len(recs) == 6
            story_id = recs[0][0]
            engine.record_answered_question(user_id, story_id, [7, 5, 5, 5], timestamp=ts)
            engine.record_engagement_stop(user_id, story_id, 100.0, timestamp=ts)
            if round_idx == 2:
                engine.record_bookmark(user_id, recs[1][0], timestamp=ts)
            if round_idx == 3:
                engine.record_abort(user_id, recs[2][0], timestamp=ts)

    with ThreadPoolExecutor(max_workers=n_users) as ex:
        futures = [ex.submit(user_session, f"user-{i}") for i in range(n_users)]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                errors.append(e)

    assert not errors, f"Exceptions during concurrent mixed events: {errors}"


def test_concurrent_catalogue_refresh_during_recommendations():
    """Background catalogue refresh (load) running while request threads are
    calling GetRecommendations — must not raise or produce corrupt results."""
    engine = RecommenderEngine(make_catalogue(n=40))
    stop_event = threading.Event()
    errors = []

    def refresh_loop():
        i = 0
        while not stop_event.is_set():
            new_stories = [
                Story(story_id=f"refresh-{i}-{j}", title=f"Story {j}",
                      tags=["a", "b"], created_at=float(j))
                for j in range(40)
            ]
            engine.catalogue.load(new_stories)
            i += 1
            time.sleep(0.005)

    def get_recs(user_id: str) -> None:
        for _ in range(10):
            recs = engine.get_recommendations(user_id, timestamp=time.time())
            assert len(recs) == 6
            ids = [sid for sid, _ in recs]
            assert len(set(ids)) == 6

    refresh_thread = threading.Thread(target=refresh_loop, daemon=True)
    refresh_thread.start()

    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = [ex.submit(get_recs, f"user-{i}") for i in range(10)]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                errors.append(e)

    stop_event.set()
    assert not errors, f"Exceptions during concurrent catalogue refresh: {errors}"


def test_same_user_concurrent_events_do_not_corrupt_model():
    """Two threads firing events for the same user simultaneously must not
    leave the user model in an inconsistent state."""
    engine = RecommenderEngine(make_catalogue())
    user_id = "shared-user"
    errors = []
    now = time.time()

    def fire_events(story_id: str, score: int) -> None:
        engine.record_answered_question(user_id, story_id, [score, 5, 5, 5], timestamp=now)

    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = [
            ex.submit(fire_events, f"s{i}", (i % 9) + 1)
            for i in range(20)
        ]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                errors.append(e)

    assert not errors, f"Exceptions during concurrent same-user events: {errors}"

    user = engine.population[user_id]
    assert len(user.story_history) == 20
    assert all(e.connectedness is not None for e in user.story_history.values())


def test_concurrent_new_user_creation():
    """Many threads creating different new users simultaneously — no user
    should be lost or initialised with another user's ID."""
    engine = RecommenderEngine(make_catalogue())
    n_users = 50

    with ThreadPoolExecutor(max_workers=20) as ex:
        futures = [
            ex.submit(engine.get_or_create_user, f"new-user-{i}")
            for i in range(n_users)
        ]
        users = [f.result() for f in as_completed(futures)]

    assert len(engine.population) == n_users
    for user in users:
        assert user.user_id in engine.population
        assert engine.population[user.user_id].user_id == user.user_id


def test_concurrent_realistic_app_sessions():
    """Mirrors concurrent real-app usage: 20 users, each running 8 rounds
    of realistic events including batch preservation (quick exit) and scored
    engagement, interleaved across threads.

    Verifies:
    - No exceptions or corrupted state under genuine concurrent load
    - Each user ends with a non-empty story history and valid recommendation set
    - Batch preservation path (no score between calls) doesn't collide with
      concurrent fresh-recommendation generation for other users
    """
    engine = RecommenderEngine(make_catalogue(n=80))
    n_users = 20
    n_rounds = 8
    errors = []
    now = time.time()

    def app_session(user_id: str, seed: int) -> None:
        rng = __import__('random').Random(seed)
        for round_idx in range(n_rounds):
            ts = now + round_idx * 3600  # hourly rather than daily — more concurrent overlap
            recs = engine.get_recommendations(user_id, timestamp=ts)
            assert len(recs) == 6
            ids = [sid for sid, _ in recs]
            assert len(set(ids)) == 6, f"{user_id} round {round_idx}: duplicate IDs {ids}"

            # Simulate 3 patterns in rotation:
            # 0: full engagement (score + stop)
            # 1: quick exit — progress then stop, no score (triggers batch preservation next round)
            # 2: abort (escape button)
            pattern = round_idx % 3
            opened = recs[0][0]

            if pattern == 0:
                score = rng.randint(4, 9)
                engine.record_answered_question(user_id, opened, [score, 5, 5, 5], timestamp=ts)
                engine.record_engagement_stop(user_id, opened, 100.0, timestamp=ts)
            elif pattern == 1:
                pct = rng.uniform(5.0, 30.0)
                engine.record_engagement_progress(user_id, opened, pct, timestamp=ts)
                engine.record_engagement_stop(user_id, opened, pct, timestamp=ts)
            else:
                engine.record_abort(user_id, opened, timestamp=ts)

    with ThreadPoolExecutor(max_workers=n_users) as ex:
        futures = {ex.submit(app_session, f"app-user-{i}", i): i for i in range(n_users)}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                errors.append((futures[future], e))

    assert not errors, f"Exceptions in app sessions: {errors}"

    # Every user should have scored at least some stories (rounds 0, 3, 6)
    for i in range(n_users):
        user = engine.population.get(f"app-user-{i}")
        assert user is not None
        scored = [e for e in user.story_history.values() if e.connectedness is not None]
        assert len(scored) >= 2, f"app-user-{i} has too few scored stories: {len(scored)}"


def test_user_model_json_size_within_grpc_budget():
    """User model JSON must stay within reasonable bounds at trial scale.
    Raw (uncompressed) size at 1500 users must not exceed 25 MB — the
    gzip compression already on the channel (20-80x) puts the wire size
    well under the 4 MB gRPC default.
    """
    engine = RecommenderEngine(make_catalogue(n=120))
    now = time.time()
    rng = __import__('random').Random(99)

    # Simulate a heavy user: 60 scored rounds
    user_id = "size-test-user"
    for r in range(60):
        ts = now + r * 86400
        recs = engine.get_recommendations(user_id, timestamp=ts)
        story_id = recs[0][0]
        score = rng.randint(4, 9)
        engine.record_answered_question(user_id, story_id, [score, 5, 5, 5], timestamp=ts)

    user = engine.population[user_id]
    blob = user.to_json()
    per_user_bytes = len(blob.encode("utf-8"))

    # At 1500 users, raw total must be under 25 MB
    projected_bytes = per_user_bytes * 1500
    assert projected_bytes < 25 * 1024 * 1024, (
        f"User model too large: {per_user_bytes} bytes/user → "
        f"{projected_bytes / 1024 / 1024:.1f} MB projected for 1500 users"
    )
