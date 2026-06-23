import random

from recommender.catalogue import Catalogue
from recommender.models import Story, UserModel
from recommender.strategies.wildcard import WildcardStrategy


def make_catalogue():
    stories = [
        Story(story_id="disliked", title="Disliked tag", tags=["judged"]),
        Story(story_id="unexplored", title="Unexplored tag", tags=["new_theme"]),
        Story(story_id="liked", title="Liked tag", tags=["lonely"]),
    ]
    catalogue = Catalogue()
    catalogue.load(stories)
    return catalogue


def test_wildcard_does_not_chase_explicitly_disliked_tags():
    # Regression test: a tag the user has rated low (real signal, e.g.
    # avoided/triggering content per open question #2) must not be
    # confused with a tag they've simply never encountered. Conflating
    # the two made wildcard actively chase disliked tags, surfaced via
    # synthetic-user journey logging on 2026-06-22.
    catalogue = make_catalogue()
    user = UserModel(user_id="u1")
    user.tag_affinity = {"judged": 0.1, "lonely": 0.8}  # "new_theme" never encountered

    rankings = {sid: 0 for sid in ("disliked", "unexplored", "liked")}
    strategy = WildcardStrategy(rng=random.Random(0))
    for trial in range(200):
        strategy.rng = random.Random(trial)
        candidates = strategy.candidates(user, catalogue, {}, excluded=set())
        for rank, story_id in enumerate(candidates):
            rankings[story_id] += rank

    avg_rank = {sid: total / 200 for sid, total in rankings.items()}
    # Lower average rank = picked first more often. The disliked tag
    # should rank no better than the unexplored one.
    assert avg_rank["disliked"] >= avg_rank["unexplored"]
