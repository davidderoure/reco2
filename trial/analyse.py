"""Analyse Trial API engagement data to monitor recommender behaviour.

Fetches data for a period and produces a report covering:
  - Recommender type distribution (are all strategies contributing?)
  - Story popularity across the population (any dominant stories?)
  - High-abort stories (cross-user escape signal for the story team)
  - Per-participant Q1 score trend (improving / flat / declining)
  - Per-participant recommender diversity (personalisation vs cold-start pattern)
  - Potential state-loss detection (reversion to cold-start pattern mid-trial)

Run:
  python -m trial.analyse --days 30
  python -m trial.analyse --from 2025-01-01 --to 2025-01-31
  python -m trial.analyse --days 30 --participants AB12-CD34 EF56-GH78
  python -m trial.analyse --days 30 --output report.txt
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from .client import TrialAPIError, TrialClient
from .models import EngagementRecord, ParticipantEngagement, RECOMMENDER_TYPE_NAMES

# Recommender types that indicate the personalisation model is active
PERSONALISED_TYPES = {"ContentBased", "Collaborative"}
# Types expected during cold start or non-personalised fills
COLD_START_TYPES = {"Topical", "Wildcard", "Unspecified"}
# Gap between records (seconds) that suggests a new session
SESSION_GAP_SECONDS = 3600

# Abort threshold: flag a story if this fraction of openers across the
# population did not complete it (percent_complete below ABORT_PCT_THRESHOLD)
# and at least ABORT_MIN_OPENERS users opened it.
ABORT_PCT_THRESHOLD = 20.0
ABORT_MIN_OPENERS = 2


def _parse_date(s: str) -> datetime:
    try:
        dt = datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid date {s!r} — expected YYYY-MM-DD")
    return dt.replace(tzinfo=timezone.utc)


def _mean(values: list[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _trend(values: list[float]) -> str:
    """Simple linear trend over a list of values."""
    if len(values) < 3:
        return "insufficient data"
    n = len(values)
    mid = n // 2
    first_half = _mean(values[:mid])
    second_half = _mean(values[mid:])
    if first_half is None or second_half is None:
        return "insufficient data"
    diff = second_half - first_half
    if diff > 0.5:
        return f"improving (+{diff:.1f})"
    elif diff < -0.5:
        return f"declining ({diff:.1f})"
    else:
        return f"stable ({diff:+.1f})"


def _sessions(records: list[EngagementRecord]) -> list[list[EngagementRecord]]:
    """Group records into sessions by time gap."""
    if not records:
        return []
    sorted_records = sorted(records, key=lambda r: r.time_start)
    sessions = [[sorted_records[0]]]
    for r in sorted_records[1:]:
        gap = (r.time_start - sessions[-1][-1].time_start).total_seconds()
        if gap > SESSION_GAP_SECONDS:
            sessions.append([])
        sessions[-1].append(r)
    return sessions


def analyse(
    participants: list[ParticipantEngagement],
    period_start: datetime,
    period_end: datetime,
) -> str:
    lines: list[str] = []

    def section(title: str) -> None:
        lines.append("")
        lines.append(title)
        lines.append("=" * len(title))

    def subsection(title: str) -> None:
        lines.append("")
        lines.append(title)
        lines.append("-" * len(title))

    lines.append("ORIGIN Trial — Recommender Behaviour Analysis")
    lines.append(f"Period : {period_start.date()} to {period_end.date()}")
    lines.append(f"Participants : {len(participants)}")

    all_records = [r for p in participants for r in p.records]
    scored = [r for r in all_records if r.question1_rating is not None]
    lines.append(f"Total records : {len(all_records)}  |  Q1 scored : {len(scored)}")

    # ------------------------------------------------------------------ #
    # 1. Recommender type distribution
    # ------------------------------------------------------------------ #
    section("1. Recommender type distribution")

    type_counts: dict[str, int] = defaultdict(int)
    for r in all_records:
        type_counts[r.recommender_type_name] += 1

    total = len(all_records) or 1
    lines.append(f"  {'Type':<20} {'Count':>6}  {'%':>6}")
    lines.append(f"  {'-'*20} {'-'*6}  {'-'*6}")
    for name, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        lines.append(f"  {name:<20} {count:>6}  {count/total*100:>5.1f}%")

    personalised = sum(type_counts.get(t, 0) for t in PERSONALISED_TYPES)
    lines.append(f"\n  Personalised (ContentBased + Collaborative): "
                 f"{personalised} / {total} ({personalised/total*100:.1f}%)")
    if personalised / total < 0.2:
        lines.append("  ⚠  Low personalised fraction — expected early in trial "
                     "when affinity data is sparse.")

    # ------------------------------------------------------------------ #
    # 2. Story popularity across population
    # ------------------------------------------------------------------ #
    section("2. Story popularity across population")
    lines.append("  (Top 15 stories by engagement count)")

    story_counts: dict[str, int] = defaultdict(int)
    story_scorers: dict[str, list[float]] = defaultdict(list)
    for r in all_records:
        story_counts[r.story_id] += 1
        if r.question1_rating is not None:
            story_scorers[r.story_id].append(r.question1_rating)

    top_stories = sorted(story_counts.items(), key=lambda x: -x[1])[:15]
    lines.append(f"  {'Story ID':<20} {'Engagements':>12} {'Mean Q1':>8}")
    lines.append(f"  {'-'*20} {'-'*12} {'-'*8}")
    for story_id, count in top_stories:
        scores = story_scorers.get(story_id, [])
        mean_q1 = f"{_mean(scores):.1f}" if scores else "—"
        lines.append(f"  {story_id:<20} {count:>12} {mean_q1:>8}")

    top_count = top_stories[0][1] if top_stories else 0
    if top_count > total * 0.15:
        lines.append(f"\n  ⚠  '{top_stories[0][0]}' accounts for "
                     f"{top_count/total*100:.1f}% of all engagements — "
                     f"may indicate over-promotion.")

    # ------------------------------------------------------------------ #
    # 3. High-abort stories (cross-user escape signal)
    # ------------------------------------------------------------------ #
    section("3. High-abort stories (cross-user escape signal)")
    lines.append(f"  Stories opened by ≥{ABORT_MIN_OPENERS} participants "
                 f"where >{100-ABORT_PCT_THRESHOLD:.0f}% did not reach "
                 f"{ABORT_PCT_THRESHOLD:.0f}% completion.")

    story_openers: dict[str, set[str]] = defaultdict(set)
    story_low_pct: dict[str, set[str]] = defaultdict(set)
    for p in participants:
        for r in p.records:
            story_openers[r.story_id].add(p.origin_id)
            if r.percent_complete is not None and r.percent_complete < ABORT_PCT_THRESHOLD:
                story_low_pct[r.story_id].add(p.origin_id)

    flagged = []
    for story_id, openers in story_openers.items():
        if len(openers) < ABORT_MIN_OPENERS:
            continue
        low = story_low_pct.get(story_id, set())
        abort_rate = len(low) / len(openers)
        if abort_rate > (1 - ABORT_PCT_THRESHOLD / 100):
            flagged.append((abort_rate, len(openers), story_id))

    if flagged:
        flagged.sort(reverse=True)
        lines.append(f"  {'Story ID':<20} {'Openers':>8} {'Low-completion %':>18}")
        lines.append(f"  {'-'*20} {'-'*8} {'-'*18}")
        for rate, n_openers, story_id in flagged:
            lines.append(f"  {story_id:<20} {n_openers:>8} {rate*100:>17.0f}%")
    else:
        lines.append("  No stories flagged.")

    # ------------------------------------------------------------------ #
    # 4. Per-participant Q1 score trend
    # ------------------------------------------------------------------ #
    section("4. Per-participant Q1 score trend")

    lines.append(f"  {'Origin ID':<14} {'Scored':>7} {'Mean Q1':>8}  Trend")
    lines.append(f"  {'-'*14} {'-'*7} {'-'*8}  {'-'*20}")
    for p in sorted(participants, key=lambda x: x.origin_id):
        q1s = [r.question1_rating for r in p.records if r.question1_rating is not None]
        mean = f"{_mean(q1s):.1f}" if q1s else "—"
        trend = _trend([float(s) for s in q1s])
        lines.append(f"  {p.origin_id:<14} {len(q1s):>7} {mean:>8}  {trend}")

    # ------------------------------------------------------------------ #
    # 5. Per-participant recommender diversity
    # ------------------------------------------------------------------ #
    section("5. Per-participant recommender diversity")
    lines.append("  Fraction of engagements that are personalised "
                 "(ContentBased or Collaborative).")
    lines.append("  A rising fraction over time suggests the model is learning.")
    lines.append("")
    lines.append(f"  {'Origin ID':<14} {'Records':>8} {'Personalised':>13}  Session pattern")
    lines.append(f"  {'-'*14} {'-'*8} {'-'*13}  {'-'*30}")

    for p in sorted(participants, key=lambda x: x.origin_id):
        if not p.records:
            continue
        n = len(p.records)
        pers = sum(1 for r in p.records if r.recommender_type_name in PERSONALISED_TYPES)
        sessions = _sessions(p.records)
        session_types = []
        for sess in sessions:
            pers_in_sess = sum(1 for r in sess if r.recommender_type_name in PERSONALISED_TYPES)
            frac = pers_in_sess / len(sess)
            session_types.append("P" if frac >= 0.5 else "C")
        pattern = " ".join(session_types) if session_types else "—"
        lines.append(
            f"  {p.origin_id:<14} {n:>8} {pers:>6}/{n:<5}  {pattern}"
        )
    lines.append("")
    lines.append("  Session pattern key: P = mostly personalised, C = mostly cold-start/wildcard")
    lines.append("  A sequence like C C P P P suggests the model is warming up normally.")
    lines.append("  A sequence like P P C C suggests a possible state loss.")

    # ------------------------------------------------------------------ #
    # 6. State-loss detection
    # ------------------------------------------------------------------ #
    section("6. Potential state-loss detection")
    lines.append("  Flags participants whose session pattern reverts to cold-start")
    lines.append("  after at least one personalised session — a possible sign of")
    lines.append("  recommender state not persisting across restarts.")

    flagged_state = []
    for p in participants:
        sessions = _sessions(p.records)
        if len(sessions) < 3:
            continue
        patterns = []
        for sess in sessions:
            pers_in_sess = sum(1 for r in sess if r.recommender_type_name in PERSONALISED_TYPES)
            patterns.append(pers_in_sess / len(sess) >= 0.5)
        # Look for P followed later by C
        had_personalised = False
        reverted = False
        for is_pers in patterns:
            if is_pers:
                had_personalised = True
            elif had_personalised:
                reverted = True
                break
        if reverted:
            flagged_state.append(p.origin_id)

    if flagged_state:
        lines.append(f"\n  ⚠  Possible state loss: {', '.join(flagged_state)}")
        lines.append("  Recommend manual review of session timeline for these participants.")
    else:
        lines.append("\n  No reversion patterns detected.")

    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyse ORIGIN Trial recommender behaviour",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    period_group = parser.add_mutually_exclusive_group(required=True)
    period_group.add_argument("--days", type=int, metavar="N",
                              help="Analyse the last N days")
    period_group.add_argument("--from", dest="date_from", type=_parse_date,
                              metavar="YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", type=_parse_date,
                        metavar="YYYY-MM-DD")
    parser.add_argument("--participants", nargs="+", metavar="XXXX-XXXX")
    parser.add_argument("--output", metavar="FILE")

    args = parser.parse_args(argv)

    if args.days is not None:
        period_end = datetime.now(timezone.utc)
        period_start = period_end - timedelta(days=args.days)
    else:
        if args.date_to is None:
            parser.error("--from requires --to")
        period_start = args.date_from
        period_end = args.date_to + timedelta(days=1) - timedelta(seconds=1)

    try:
        client = TrialClient.from_env()
        participants = client.fetch(period_start, period_end,
                                    origin_ids=args.participants)
    except EnvironmentError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except TrialAPIError as e:
        print(f"API error: {e}", file=sys.stderr)
        return 1

    report = analyse(participants, period_start, period_end)

    if args.output:
        with open(args.output, "w") as f:
            f.write(report)
        print(f"Wrote {args.output}")
    else:
        print(report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
