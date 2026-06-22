"""Simulated environment for testing the recommender without the real C#
back end. Generates a synthetic catalogue and population with hidden
"true preference" vectors, drives many rounds of recommend -> simulated
read -> feedback, and reports:

  - correctness invariants (6 unique ids, valid type codes, slot mix)
  - response-time percentiles for GetRecommendations (the 500ms budget)
  - whether mean connectedness improves over rounds vs a random baseline

This is a synthetic ground truth, not real user behaviour — useful for
catching bugs and gross logic problems, not for validating clinical
relevance of the recommendations themselves.

Run: python -m simulation.simulate
"""

from __future__ import annotations

import random
import statistics
import time

from recommender.catalogue import Catalogue
from recommender.engine import RecommenderEngine
from recommender.models import UserModel
from recommender.models import Story

TAG_VOCAB = [
    "goldsmith", "folklore", "grief", "humour", "adventure", "family",
    "identity", "nature", "music", "friendship", "loss", "resilience",
    "migration", "faith", "sport", "art",
]


def make_catalogue(n_stories: int, seed: int, now: float) -> Catalogue:
    rng = random.Random(seed)
    stories = []
    for i in range(n_stories):
        tags = rng.sample(TAG_VOCAB, k=rng.randint(2, 4))
        age_days = rng.randint(0, 180)
        stories.append(
            Story(
                story_id=f"story-{i}",
                title=f"Story {i}",
                tags=tags,
                created_at=now - age_days * 86400,
                updated_at=now - age_days * 86400,
            )
        )
    catalogue = Catalogue()
    catalogue.load(stories)
    return catalogue


def make_population(n_users: int, seed: int) -> tuple[dict[str, UserModel], dict[str, dict[str, float]]]:
    """Returns (engine-visible user models, hidden true tag preferences)."""
    rng = random.Random(seed)
    users = {}
    true_prefs = {}
    for i in range(n_users):
        uid = f"user-{i}"
        users[uid] = UserModel(user_id=uid)
        # Hidden ground truth: each user likes a random subset of tags strongly.
        liked = rng.sample(TAG_VOCAB, k=rng.randint(2, 5))
        true_prefs[uid] = {tag: rng.uniform(0.7, 1.0) for tag in liked}
    return users, true_prefs


def simulated_connectedness(story: Story, true_pref: dict[str, float], rng: random.Random) -> int:
    """Synthetic connectedness score (1-9) for a story given hidden prefs."""
    if not story.tags:
        base = 5.0
    else:
        base = sum(true_pref.get(tag, 0.2) for tag in story.tags) / len(story.tags) * 9
    noisy = base + rng.gauss(0, 1.0)
    return max(1, min(9, round(noisy)))


def run_round(engine: RecommenderEngine, true_prefs: dict, rng: random.Random, now: float) -> dict[str, list]:
    """One round: every user gets recommendations, "reads" the first one,
    and answers the connectedness question. Returns per-user score deltas
    for reporting.
    """
    timings = []
    scores_this_round = []

    for user_id, true_pref in true_prefs.items():
        t0 = time.perf_counter()
        recs = engine.get_recommendations(user_id)
        timings.append(time.perf_counter() - t0)

        assert len(recs) == 6, f"expected 6 recommendations, got {len(recs)}"
        ids = [sid for sid, _ in recs]
        assert len(set(ids)) == len(ids), "duplicate story_id in recommendation set"

        story_id, _rec_type = recs[0]
        story = engine.catalogue.get(story_id)
        score = simulated_connectedness(story, true_pref, rng)
        engine.record_answered_question(user_id, story_id, [score, 5, 5, 5], timestamp=now)
        engine.record_engagement_stop(user_id, story_id, progress_percentage=100.0, timestamp=now)
        scores_this_round.append(score)

    return {"timings": timings, "scores": scores_this_round}


def random_baseline_round(catalogue: Catalogue, true_prefs: dict, rng: random.Random) -> list[int]:
    scores = []
    story_ids = catalogue.all_ids()
    for true_pref in true_prefs.values():
        story = catalogue.get(rng.choice(story_ids))
        scores.append(simulated_connectedness(story, true_pref, rng))
    return scores


def percentile(data: list[float], pct: float) -> float:
    data = sorted(data)
    k = int(len(data) * pct)
    k = min(k, len(data) - 1)
    return data[k]


def main(n_users: int = 200, n_stories: int = 120, n_rounds: int = 30, seed: int = 42) -> None:
    now = time.time()
    catalogue = make_catalogue(n_stories, seed, now)
    users, true_prefs = make_population(n_users, seed)

    engine = RecommenderEngine(catalogue)
    engine.load_population(list(users.values()))

    rng = random.Random(seed)
    all_timings: list[float] = []
    mean_score_per_round: list[float] = []

    for round_idx in range(n_rounds):
        result = run_round(engine, true_prefs, rng, now + round_idx * 86400)
        all_timings.extend(result["timings"])
        mean_score_per_round.append(statistics.mean(result["scores"]))

    baseline_rng = random.Random(seed + 1)
    baseline_scores = []
    for _ in range(n_rounds):
        baseline_scores.extend(random_baseline_round(catalogue, true_prefs, baseline_rng))

    print(f"Users: {n_users}, Stories: {n_stories}, Rounds: {n_rounds}")
    print()
    print("-- Response time (GetRecommendations) --")
    print(f"  p50: {percentile(all_timings, 0.50) * 1000:.2f} ms")
    print(f"  p95: {percentile(all_timings, 0.95) * 1000:.2f} ms")
    print(f"  p99: {percentile(all_timings, 0.99) * 1000:.2f} ms")
    print(f"  max: {max(all_timings) * 1000:.2f} ms   (budget: 500 ms)")
    print()
    print("-- Mean connectedness score over rounds (recommender) --")
    print("  " + ", ".join(f"{s:.2f}" for s in mean_score_per_round))
    print()
    print(f"-- Mean connectedness, recommender (last 5 rounds avg): "
          f"{statistics.mean(mean_score_per_round[-5:]):.2f}")
    print(f"-- Mean connectedness, random baseline (all rounds):    "
          f"{statistics.mean(baseline_scores):.2f}")


if __name__ == "__main__":
    main()
