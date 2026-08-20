"""Load test for RecommenderEngine.

Simulates realistic concurrent usage at trial scale:
  - 1500 total users, varying amounts of history
  - 200 concurrent get_recommendations() calls
  - Mixed read/write: interleaved record_* events alongside recommendations

Reports latency percentiles and checks the 500ms budget.

Usage:
    python scripts/load_test.py
    python scripts/load_test.py --users 1500 --concurrent 200 --rounds 5
    python scripts/load_test.py --quick   # 200 users, 50 concurrent, 2 rounds
"""

from __future__ import annotations

import random
import sys
import time
import threading
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from recommender.catalogue import Catalogue
from recommender.engine import RecommenderEngine
from recommender.models import Story, StoryHistoryEntry, UserModel
from simulation.real_catalogue import load_real_catalogue

DEFAULT_CATALOGUE = Path(__file__).parent.parent / "stories.json"

BUDGET_MS = 500  # target latency budget per get_recommendations() call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def build_catalogue(catalogue_path: Path | None) -> Catalogue:
    if catalogue_path and catalogue_path.exists():
        print(f"Using real catalogue: {catalogue_path}")
        return load_real_catalogue(catalogue_path)
    # Synthetic fallback: 120 stories across 4 tag clusters.
    print("stories.json not found — using synthetic catalogue (120 stories)")
    tags_pool = [
        ["Being Judged", "Hiding Self", "Written"],
        ["LGBTQ+ Experience", "Gender", "Written"],
        ["WWII", "Heritage", "Audio"],
        ["Activism", "Community", "Written"],
        ["Migration", "Refugee Experience", "Video"],
        ["Mental Health", "Feeling Lonely", "Written"],
        ["Sports", "Adventure", "Visual"],
        ["Science", "Academia", "Written"],
    ]
    stories = [
        Story(story_id=f"s{i:03d}", title=f"Story {i}",
              tags=tags_pool[i % len(tags_pool)], created_at=float(i))
        for i in range(120)
    ]
    cat = Catalogue()
    cat.load(stories)
    return cat


def build_population(n_users: int, catalogue: Catalogue, rng: random.Random) -> list[UserModel]:
    """Create users with realistic varying amounts of history."""
    all_stories = catalogue.all_stories()
    users = []
    for i in range(n_users):
        user = UserModel(user_id=f"user-{i:04d}")
        # Distribute history: ~30% cold-start, ~40% light, ~30% established
        if i % 10 < 3:
            n_history = 0  # cold-start
        elif i % 10 < 7:
            n_history = rng.randint(1, 5)  # light
        else:
            n_history = rng.randint(6, 20)  # established

        seen = rng.sample(all_stories, min(n_history, len(all_stories)))
        for story in seen:
            entry = StoryHistoryEntry()
            entry.connectedness = rng.random()
            entry.timestamp = time.time() - rng.randint(0, 30) * 86400
            # Build tag affinity from history
            for tag in story.tags:
                user.tag_affinity[tag] = min(
                    1.0, user.tag_affinity.get(tag, 0.0) + entry.connectedness * 0.3
                )
            user.story_history[story.story_id] = entry
        users.append(user)
    return users


# ---------------------------------------------------------------------------
# Load test scenarios
# ---------------------------------------------------------------------------

def scenario_get_recommendations(
    engine: RecommenderEngine,
    user_ids: list[str],
    n_calls: int,
    rng: random.Random,
) -> list[float]:
    """Fire n_calls get_recommendations() spread across user_ids, return latencies."""
    latencies = []
    for _ in range(n_calls):
        uid = rng.choice(user_ids)
        t0 = time.perf_counter()
        engine.get_recommendations(uid)
        latencies.append((time.perf_counter() - t0) * 1000)
    return latencies


def scenario_mixed_readwrite(
    engine: RecommenderEngine,
    user_ids: list[str],
    all_story_ids: list[str],
    n_calls: int,
    rng: random.Random,
) -> list[float]:
    """Interleave recommendations with record_* events, return get_recommendations latencies."""
    latencies = []
    ts = time.time()
    for k in range(n_calls):
        uid = rng.choice(user_ids)
        sid = rng.choice(all_story_ids)
        # Every third call: write event first, then get recommendations.
        if k % 3 == 0:
            engine.record_answered_question(uid, sid, [rng.randint(1, 5), None, None, None], timestamp=ts)
            engine.record_engagement_stop(uid, sid, progress_percentage=100.0, timestamp=ts)
        t0 = time.perf_counter()
        engine.get_recommendations(uid)
        latencies.append((time.perf_counter() - t0) * 1000)
        ts += 1.0
    return latencies


def run_concurrent(
    fn,
    n_threads: int,
    calls_per_thread: int,
) -> tuple[list[float], float]:
    """Run fn in n_threads threads, each making calls_per_thread calls.

    Returns (all_latencies, wall_clock_seconds).
    """
    results: dict[int, list[float]] = {}
    threads = []

    def worker(thread_id: int) -> None:
        results[thread_id] = fn(calls_per_thread)

    t_start = time.perf_counter()
    for i in range(n_threads):
        t = threading.Thread(target=worker, args=(i,))
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - t_start

    all_latencies = []
    for lats in results.values():
        all_latencies.extend(lats)
    return all_latencies, wall


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def percentile(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    idx = int(len(s) * p / 100)
    return s[min(idx, len(s) - 1)]


def print_stats(label: str, latencies: list[float], wall: float, n_threads: int) -> bool:
    n = len(latencies)
    p50 = percentile(latencies, 50)
    p95 = percentile(latencies, 95)
    p99 = percentile(latencies, 99)
    p_max = max(latencies)
    throughput = n / wall

    budget_ok = p95 < BUDGET_MS
    status = "PASS ✓" if budget_ok else "FAIL ✗"

    print(f"\n  {label}")
    print(f"    calls={n}  threads={n_threads}  wall={wall:.1f}s  throughput={throughput:.0f} req/s")
    print(f"    p50={p50:.1f}ms  p95={p95:.1f}ms  p99={p99:.1f}ms  max={p_max:.1f}ms")
    print(f"    budget ({BUDGET_MS}ms at p95): {status}")
    return budget_ok


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(
    n_users: int = 1500,
    n_concurrent: int = 200,
    n_rounds: int = 5,
    catalogue_path: Path = DEFAULT_CATALOGUE,
    quick: bool = False,
) -> None:
    if quick:
        n_users, n_concurrent, n_rounds = 200, 50, 2

    rng = random.Random(42)

    print(f"\nLoad test — {n_users} users, {n_concurrent} concurrent threads, {n_rounds} rounds")
    print("=" * 65)

    print("\nBuilding catalogue...")
    catalogue = build_catalogue(catalogue_path)
    print(f"  {len(catalogue)} stories")

    print(f"Building population ({n_users} users)...")
    users = build_population(n_users, catalogue, rng)
    cold = sum(1 for u in users if not u.story_history)
    light = sum(1 for u in users if 1 <= len(u.story_history) <= 5)
    estab = sum(1 for u in users if len(u.story_history) > 5)
    print(f"  cold-start={cold}  light={light}  established={estab}")

    print("Initialising engine...")
    engine = RecommenderEngine(catalogue, rng=random.Random(1))
    engine.load_population(users)
    user_ids = [u.user_id for u in users]
    all_story_ids = [s.story_id for s in catalogue.all_stories()]

    calls_per_thread = max(1, (n_users // n_concurrent) * n_rounds)
    all_pass = True

    # --- Scenario 1: pure get_recommendations() ---
    print(f"\nScenario 1: get_recommendations() only")
    print(f"  {n_concurrent} threads × {calls_per_thread} calls = {n_concurrent * calls_per_thread} total")

    def s1(n: int) -> list[float]:
        return scenario_get_recommendations(engine, user_ids, n, random.Random(rng.randint(0, 2**32)))

    lats, wall = run_concurrent(s1, n_concurrent, calls_per_thread)
    ok = print_stats("Results", lats, wall, n_concurrent)
    all_pass = all_pass and ok

    # --- Scenario 2: mixed reads + writes ---
    print(f"\nScenario 2: mixed get_recommendations() + record_* events")
    print(f"  {n_concurrent} threads × {calls_per_thread} calls (~1/3 with preceding write)")

    def s2(n: int) -> list[float]:
        return scenario_mixed_readwrite(engine, user_ids, all_story_ids, n, random.Random(rng.randint(0, 2**32)))

    lats2, wall2 = run_concurrent(s2, n_concurrent, calls_per_thread)
    ok2 = print_stats("Results", lats2, wall2, n_concurrent)
    all_pass = all_pass and ok2

    # --- Scenario 3: cold-start burst (new users arriving simultaneously) ---
    print(f"\nScenario 3: cold-start burst — {n_concurrent} brand-new users first recommendation")
    new_ids = [f"newuser-{i}" for i in range(n_concurrent)]

    def s3(_: int) -> list[float]:
        uid = new_ids[threading.current_thread().ident % n_concurrent]
        t0 = time.perf_counter()
        engine.get_recommendations(uid)
        return [(time.perf_counter() - t0) * 1000]

    lats3, wall3 = run_concurrent(s3, n_concurrent, 1)
    ok3 = print_stats("Results", lats3, wall3, n_concurrent)
    all_pass = all_pass and ok3

    # --- Correctness spot-check ---
    print(f"\nCorrectness spot-check (no duplicate recommendations)...")
    failures = 0
    for uid in rng.sample(user_ids, min(20, len(user_ids))):
        recs = engine.get_recommendations(uid)
        if len(recs) != len(set(s for s, _ in recs)):
            failures += 1
    print(f"  checked 20 users — {'no duplicates found ✓' if failures == 0 else f'{failures} users had duplicates ✗'}")

    print("\n" + "=" * 65)
    print(f"Overall: {'PASS ✓' if all_pass else 'FAIL ✗'} (p95 < {BUDGET_MS}ms budget)")


if __name__ == "__main__":
    quick = "--quick" in sys.argv

    def _arg(flag: str, default: int) -> int:
        try:
            return int(sys.argv[sys.argv.index(flag) + 1])
        except (ValueError, IndexError):
            return default

    cat_arg = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--catalogue" and i + 1 < len(sys.argv)), None)
    main(
        n_users=_arg("--users", 1500),
        n_concurrent=_arg("--concurrent", 200),
        n_rounds=_arg("--rounds", 5),
        catalogue_path=Path(cat_arg) if cat_arg else DEFAULT_CATALOGUE,
        quick=quick,
    )
