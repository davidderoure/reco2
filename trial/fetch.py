"""CLI for fetching Trial API engagement data.

Usage examples:
  # Last 7 days, print summary to stdout
  python -m trial.fetch --days 7

  # Specific date range, JSON output
  python -m trial.fetch --from 2025-01-01 --to 2025-01-31 --format json

  # Filter to two participants, save as CSV
  python -m trial.fetch --days 30 --participants AB12-CD34 EF56-GH78 --format csv --output data.csv

  # Full JSON to file
  python -m trial.fetch --days 14 --format json --output results.json

Credentials must be set in the environment:
  export TRIAL_CLIENT_ID=...
  export TRIAL_CLIENT_SECRET=...
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

from .client import TrialClient, TrialAPIError
from .models import ParticipantEngagement, RECOMMENDER_TYPE_NAMES


def _parse_date(s: str) -> datetime:
    """Parse YYYY-MM-DD date string to midnight UTC datetime."""
    try:
        dt = datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid date {s!r} — expected YYYY-MM-DD")
    return dt.replace(tzinfo=timezone.utc)


def _to_json(participants: list[ParticipantEngagement]) -> str:
    out = []
    for p in participants:
        records = []
        for r in p.records:
            records.append({
                "story_id": r.story_id,
                "time_start": r.time_start.isoformat(),
                "time_end": r.time_end.isoformat() if r.time_end else None,
                "percent_complete": r.percent_complete,
                "recommender_type": r.recommender_type,
                "recommender_type_name": r.recommender_type_name,
                "viewpoint_text": r.viewpoint_text,
                "mood": r.mood,
                "mood_time": r.mood_time.isoformat() if r.mood_time else None,
                "question1_rating": r.question1_rating,
                "question2_rating": r.question2_rating,
                "question3_rating": r.question3_rating,
                "question4_rating": r.question4_rating,
            })
        out.append({"origin_id": p.origin_id, "records": records})
    return json.dumps(out, indent=2)


def _to_csv(participants: list[ParticipantEngagement]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "origin_id", "story_id", "time_start", "time_end", "percent_complete",
        "recommender_type", "recommender_type_name", "viewpoint_text",
        "mood", "mood_time",
        "question1_rating", "question2_rating", "question3_rating", "question4_rating",
    ])
    for p in participants:
        for r in p.records:
            writer.writerow([
                p.origin_id,
                r.story_id,
                r.time_start.isoformat(),
                r.time_end.isoformat() if r.time_end else "",
                r.percent_complete if r.percent_complete is not None else "",
                r.recommender_type if r.recommender_type is not None else "",
                r.recommender_type_name,
                r.viewpoint_text or "",
                r.mood if r.mood is not None else "",
                r.mood_time.isoformat() if r.mood_time else "",
                r.question1_rating if r.question1_rating is not None else "",
                r.question2_rating if r.question2_rating is not None else "",
                r.question3_rating if r.question3_rating is not None else "",
                r.question4_rating if r.question4_rating is not None else "",
            ])
    return buf.getvalue()


def _to_summary(participants: list[ParticipantEngagement], period_start: datetime, period_end: datetime) -> str:
    lines = [
        f"ORIGIN Trial — Engagement Summary",
        f"Period: {period_start.date()} to {period_end.date()}",
        f"Participants: {len(participants)}",
        "",
    ]

    total_records = sum(len(p.records) for p in participants)
    total_scored = sum(
        1 for p in participants for r in p.records
        if r.question1_rating is not None
    )
    lines.append(f"Total engagement records : {total_records}")
    lines.append(f"Records with Q1 score    : {total_scored}")
    lines.append("")

    # Recommender type breakdown
    type_counts: dict[str, int] = {}
    for p in participants:
        for r in p.records:
            name = r.recommender_type_name
            type_counts[name] = type_counts.get(name, 0) + 1
    if type_counts:
        lines.append("Recommendations by type:")
        for name, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  {name:<20} {count:>5}")
        lines.append("")

    # Per-participant summary
    lines.append("Per-participant:")
    lines.append(f"  {'Origin ID':<14} {'Records':>8} {'Q1 scored':>10} {'Mean Q1':>10}")
    lines.append(f"  {'-'*14} {'-'*8} {'-'*10} {'-'*10}")
    for p in sorted(participants, key=lambda x: x.origin_id):
        scored = [r.question1_rating for r in p.records if r.question1_rating is not None]
        mean_q1 = f"{sum(scored)/len(scored):.1f}" if scored else "—"
        lines.append(f"  {p.origin_id:<14} {len(p.records):>8} {len(scored):>10} {mean_q1:>10}")

    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch ORIGIN Trial API engagement data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    period_group = parser.add_mutually_exclusive_group(required=True)
    period_group.add_argument(
        "--days", type=int, metavar="N",
        help="Fetch the last N days (ending now)"
    )
    period_group.add_argument(
        "--from", dest="date_from", type=_parse_date, metavar="YYYY-MM-DD",
        help="Start of period (requires --to)"
    )

    parser.add_argument(
        "--to", dest="date_to", type=_parse_date, metavar="YYYY-MM-DD",
        help="End of period (required when using --from)"
    )
    parser.add_argument(
        "--participants", nargs="+", metavar="XXXX-XXXX",
        help="Filter to specific participant IDs (space-separated, format XXXX-XXXX)"
    )
    parser.add_argument(
        "--format", choices=["summary", "json", "csv"], default="summary",
        help="Output format (default: summary)"
    )
    parser.add_argument(
        "--output", metavar="FILE",
        help="Write output to FILE instead of stdout"
    )

    args = parser.parse_args(argv)

    # Resolve period
    if args.days is not None:
        period_end = datetime.now(timezone.utc)
        period_start = period_end - timedelta(days=args.days)
    else:
        if args.date_to is None:
            parser.error("--from requires --to")
        period_start = args.date_from
        # End of the 'to' day
        period_end = args.date_to + timedelta(days=1) - timedelta(seconds=1)

    try:
        client = TrialClient.from_env()
        participants = client.fetch(period_start, period_end, origin_ids=args.participants)
    except EnvironmentError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except TrialAPIError as e:
        print(f"API error: {e}", file=sys.stderr)
        return 1

    # Format output
    if args.format == "json":
        output = _to_json(participants)
    elif args.format == "csv":
        output = _to_csv(participants)
    else:
        output = _to_summary(participants, period_start, period_end)

    if args.output:
        with open(args.output, "w", newline="" if args.format == "csv" else "\n") as f:
            f.write(output)
        print(f"Wrote {args.output}")
    else:
        print(output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
