"""Explicit checks that the engine handles a catalogue that changes shape
during the trial: new stories appearing, new tag values appearing, and
existing stories' tags being edited. Catalogue refresh itself (the
background polling loop) is covered separately in
test_catalogue_refresh.py — these tests assume the refreshed catalogue
is already in place and check the engine's *reaction* to it.
"""

import time

from recommender.catalogue import Catalogue
from recommender.engine import RecommenderEngine
from recommender.models import Story


def make_catalogue(n=20):
    stories = [
        Story(story_id=f"s{i}", title=f"Story {i}", tags=["a", "b"], created_at=i)
        for i in range(n)
    ]
    catalogue = Catalogue()
    catalogue.load(stories)
    return catalogue


def test_new_story_can_be_recommended_after_appearing_mid_trial():
    engine = RecommenderEngine(make_catalogue())
    user_id = "u1"
    now = time.time()
    for i in range(5):
        engine.record_answered_question(user_id, f"s{i}", [4, 3, 3, 3], timestamp=now)

    # A story that didn't exist when the user started — simulates the
    # catalogue refresh loop picking up something added mid-trial.
    engine.catalogue.upsert(Story(story_id="late-arrival", title="Late arrival", tags=["a", "b"], created_at=now + 1000))

    recs = engine.get_recommendations(user_id, timestamp=now + 2000)
    ids = [sid for sid, _ in recs]
    assert "late-arrival" in ids


def test_brand_new_tag_value_does_not_break_anything():
    # A tag string never seen before in the catalogue or any user's
    # tag_affinity. Tags are plain strings (no fixed enum), so this should
    # just work — verifying it explicitly since the tag vocabulary is
    # expected to grow during the trial (including user-suggested
    # free-text tags).
    catalogue = make_catalogue(n=10)
    catalogue.upsert(Story(story_id="novel", title="Novel tag story", tags=["never-seen-before-tag"]))
    engine = RecommenderEngine(catalogue)
    user_id = "u1"
    now = time.time()

    engine.record_answered_question(user_id, "novel", [5, 3, 3, 3], timestamp=now)

    user = engine.population[user_id]
    assert user.tag_affinity["never-seen-before-tag"] == 1.0  # score 9/9 normalized

    # Recommendations still work normally afterward.
    recs = engine.get_recommendations(user_id, timestamp=now)
    assert len(recs) == 6
    assert len(set(sid for sid, _ in recs)) == 6


def test_tag_affinity_is_stale_until_the_next_triggering_event():
    # tag_affinity is NOT proactively recomputed just because the
    # catalogue changed — it's recomputed from story_history + current
    # catalogue tags on the user's *next* triggering event (answered
    # question / bookmark / unbookmark). This is a deliberate tradeoff
    # (avoids recomputing for the whole population on every catalogue
    # refresh) but worth being explicit about: there's a window where a
    # user's affinity reflects a story's old tags.
    catalogue = Catalogue()
    catalogue.load([Story(story_id="s0", title="Story 0", tags=["unique-tag"])])
    engine = RecommenderEngine(catalogue)
    user_id = "u1"
    now = time.time()

    engine.record_answered_question(user_id, "s0", [5, 3, 3, 3], timestamp=now)
    user = engine.population[user_id]
    assert user.tag_affinity["unique-tag"] == 1.0

    # Edit s0's tags in place (same story_id, new tags) — simulates a CMS
    # edit picked up by the catalogue refresh loop.
    engine.catalogue.upsert(Story(story_id="s0", title="Story 0 retagged", tags=["different-tag"]))

    # No recompute has happened yet — still reflects the old tag.
    assert user.tag_affinity["unique-tag"] == 1.0
    assert "different-tag" not in user.tag_affinity


def test_tag_removed_from_a_story_no_longer_contributes_once_recomputed():
    catalogue = Catalogue()
    catalogue.load([
        Story(story_id="only-source", title="Only source of this tag", tags=["unique-tag"]),
        Story(story_id="other", title="Other", tags=["other-tag"]),
    ])
    engine = RecommenderEngine(catalogue)
    user_id = "u1"
    now = time.time()

    engine.record_answered_question(user_id, "only-source", [5, 3, 3, 3], timestamp=now)
    user = engine.population[user_id]
    assert "unique-tag" in user.tag_affinity

    # Retag "only-source" so it no longer carries "unique-tag" at all.
    engine.catalogue.upsert(Story(story_id="only-source", title="Only source", tags=["different-tag"]))

    # Trigger a recompute via another event for this user.
    engine.record_answered_question(user_id, "other", [3, 3, 3, 3], timestamp=now + 1)

    assert "unique-tag" not in user.tag_affinity
    assert "different-tag" in user.tag_affinity
