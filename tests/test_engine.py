import time

from recommender.catalogue import Catalogue
from recommender.engine import (
    CONTENT_BASED,
    MIN_FRESH_PER_BATCH,
    REENGAGEMENT_RAMP_THRESHOLD,
    SLOT_COUNTS,
    RecommenderEngine,
)
from recommender.models import Story


def make_catalogue(n=20):
    tags_cycle = [["a", "b"], ["b", "c"], ["c", "d"], ["a", "d"]]
    stories = [
        Story(story_id=f"s{i}", title=f"Story {i}", tags=tags_cycle[i % len(tags_cycle)], created_at=i)
        for i in range(n)
    ]
    catalogue = Catalogue()
    catalogue.load(stories)
    return catalogue


def test_cold_start_returns_six_unique():
    engine = RecommenderEngine(make_catalogue())
    recs = engine.get_recommendations("new-user")
    assert len(recs) == 6
    ids = [sid for sid, _ in recs]
    assert len(set(ids)) == 6


def test_steady_state_returns_six_unique_after_history():
    # Catalogue large enough that the unread pool stays above the
    # re-engagement ramp threshold, so this exercises the "plenty of fresh
    # content left" path only.
    n = REENGAGEMENT_RAMP_THRESHOLD + 10
    engine = RecommenderEngine(make_catalogue(n=n))
    user_id = "u1"
    now = time.time()
    for i in range(5):
        engine.record_answered_question(user_id, f"s{i}", [8, 5, 5, 5], timestamp=now)

    recs = engine.get_recommendations(user_id)
    assert len(recs) == 6
    ids = [sid for sid, _ in recs]
    assert len(set(ids)) == 6
    # none of the already-rated stories should reappear while plenty of
    # unread content remains
    assert not set(ids) & {f"s{i}" for i in range(5)}


def test_reengagement_ramps_in_as_unread_pool_shrinks():
    # Open question #4 (decided 2026-06-22): re-engagement should blend in
    # before the unread pool is fully exhausted, not only once it hits zero.
    n = REENGAGEMENT_RAMP_THRESHOLD  # unread pool starts right at the ramp threshold
    engine = RecommenderEngine(make_catalogue(n=n))
    user_id = "u1"
    now = time.time()
    rated = [f"s{i}" for i in range(n - 2)]  # leave only 2 unread
    for story_id in rated:
        engine.record_answered_question(user_id, story_id, [9, 5, 5, 5], timestamp=now)

    recs = engine.get_recommendations(user_id)
    assert len(recs) == 6
    ids = [sid for sid, _ in recs]
    assert len(set(ids)) == 6
    # with only 2 unread stories left, most of the batch must be re-engagement
    assert len(set(ids) & set(rated)) >= 3


def test_exhaustion_falls_back_to_reengagement():
    catalogue = make_catalogue(n=6)
    engine = RecommenderEngine(catalogue)
    user_id = "u1"
    now = time.time()
    for i in range(6):
        engine.record_answered_question(user_id, f"s{i}", [7, 5, 5, 5], timestamp=now)

    recs = engine.get_recommendations(user_id)
    assert len(recs) == 6
    ids = [sid for sid, _ in recs]
    assert len(set(ids)) == 6
    assert set(ids) <= {f"s{i}" for i in range(6)}


def test_minimum_freshness_policy_allows_repeats_but_requires_some_fresh():
    # Open question #6 (decided 2026-06-22): repeats across requests are
    # allowed, but at least MIN_FRESH_PER_BATCH per batch must be stories
    # never recommended to this user before.
    engine = RecommenderEngine(make_catalogue(n=30))
    user_id = "u1"
    first_ids = {sid for sid, _ in engine.get_recommendations(user_id)}
    second_ids = {sid for sid, _ in engine.get_recommendations(user_id)}

    fresh_in_second = second_ids - first_ids
    assert len(fresh_in_second) >= MIN_FRESH_PER_BATCH


def test_slot_counts_sum_to_six():
    assert sum(SLOT_COUNTS.values()) == 6


def make_untagged_catalogue(n=20):
    stories = [Story(story_id=f"s{i}", title=f"Story {i}", tags=[], created_at=i) for i in range(n)]
    catalogue = Catalogue()
    catalogue.load(stories)
    return catalogue


def test_user_with_history_is_not_stuck_in_cold_start_when_catalogue_has_no_tags():
    # If stories have no tags (e.g. early testing with mock stories before
    # the CMS is populated), tag_affinity can never be built. Cold-start
    # detection must not depend on tag_affinity, or every user would look
    # cold-start forever regardless of how much history they have.
    n = REENGAGEMENT_RAMP_THRESHOLD + 10
    engine = RecommenderEngine(make_untagged_catalogue(n=n))
    user_id = "u1"
    now = time.time()
    for i in range(5):
        engine.record_answered_question(user_id, f"s{i}", [8, 5, 5, 5], timestamp=now)

    user = engine.population[user_id]
    assert user.tag_affinity == {}  # confirms the no-tags premise
    assert engine._seen_story_ids(user)  # but the user does have real history

    recs = engine.get_recommendations(user_id)
    assert len(recs) == 6
    ids = [sid for sid, _ in recs]
    assert len(set(ids)) == 6
    # already-rated stories shouldn't reappear while plenty unread remains
    assert not set(ids) & {f"s{i}" for i in range(5)}


def test_bookmark_alone_does_not_affect_tag_affinity():
    # Open question #7 (decided 2026-06-22): bookmarking alone does NOT
    # contribute to tag_affinity (capability retained for future use, but
    # off by default).
    engine = RecommenderEngine(make_catalogue())
    user_id = "u1"
    engine.record_bookmark(user_id, "s0", timestamp=time.time())  # tags ["a", "b"]

    user = engine.population[user_id]
    assert user.tag_affinity == {}
    # bookmark is still tracked on the model, just not used for affinity
    assert "s0" in user.bookmarked_story_ids
    # bookmarking alone shouldn't mark the story as "read" either
    assert "s0" not in user.story_history


def test_unbookmark_removes_tracked_bookmark():
    engine = RecommenderEngine(make_catalogue())
    user_id = "u1"
    engine.record_bookmark(user_id, "s0", timestamp=time.time())
    engine.record_unbookmark(user_id, "s0", timestamp=time.time())
    assert engine.population[user_id].bookmarked_story_ids == {}
