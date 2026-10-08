"""Per-participant timeline visualisation.

Shows each participant's engagement history as an ASCII timeline — one row
per session, with recommender types, Q1 scores, completion, and flags for
state-loss reversion and high-abort stories.

Designed for terminal use (TRE-compatible). Optional --html flag saves a
richer version as a self-contained HTML file for local review.

Usage:
    python -m trial.timeline --days 30
    python -m trial.timeline --days 30 --participants TEST-LEE1
    python -m trial.timeline --days 30 --html timeline.html
    python -m trial.timeline --from 2026-07-01 --to 2026-08-21
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

from .analyse import LAPSE_GAP_DAYS, _sessions
from .client import TrialAPIError, TrialClient, load_ids, DEFAULT_IDS_FILE
from .fetch import load_participants_json
from .models import EngagementRecord, ParticipantEngagement

# Short one-letter codes for recommender types in the timeline
TYPE_CODES = {
    "ContentBased":  "B",  # content-Based
    "Collaborative": "L",  # coLlaborative
    "Topical":       "T",
    "Wildcard":      "W",
    "Search":        "S",
    "Recents":       "R",
    "Saved":         "V",  # saVed
    "Unspecified":   "?",
    "Unknown":       "?",
}

PERSONALISED_CODES = {"B", "L"}

# Sparkline chars for scores 1-5
SPARK_CHARS = " ▁▂▃▄▅▆▇█"

# How many chars wide the timeline type column is
TYPE_COL_WIDTH = 14


def _type_code(rec: EngagementRecord) -> str:
    return TYPE_CODES.get(rec.recommender_type_name, "?")


def _sparkline(scores: list[Optional[int]], width: int) -> str:
    """Render a row of Q1 scores as a sparkline string of fixed width."""
    if not scores:
        return " " * width
    chars = []
    for s in scores:
        if s is None:
            chars.append("·")
        else:
            # Map 1-5 to spark chars index 1-8
            idx = max(1, min(8, round((s - 1) / 4 * 7) + 1))
            chars.append(SPARK_CHARS[idx])
    return "".join(chars)[:width].ljust(width)


def _q1_summary(records: list[EngagementRecord]) -> str:
    scores = [r.question1_rating for r in records if r.question1_rating is not None]
    if not scores:
        return "—"
    mean = sum(scores) / len(scores)
    return f"{mean:.1f}"


def _pct_summary(records: list[EngagementRecord]) -> str:
    pcts = [r.percent_complete for r in records if r.percent_complete is not None]
    if not pcts:
        return "—"
    mean = sum(pcts) / len(pcts)
    return f"{mean:.0f}%"


# ---------------------------------------------------------------------------
# ASCII timeline
# ---------------------------------------------------------------------------

def render_ascii(participant: ParticipantEngagement) -> str:
    p = participant
    sessions = _sessions(p.records)
    if not sessions:
        return f"{p.origin_id} — no records\n"

    all_q1 = [r.question1_rating for r in sorted(p.records, key=lambda r: r.time_start)]
    n_personalised = sum(1 for r in p.records if _type_code(r) in PERSONALISED_CODES)
    n_total = len(p.records)

    lines = []
    lines.append(f"{'─' * 72}")
    lines.append(
        f"{p.origin_id}  —  {n_total} records  |  "
        f"{len(sessions)} sessions  |  "
        f"personalised: {n_personalised}/{n_total}"
    )
    lines.append(f"{'─' * 72}")
    lines.append(
        f"  {'Date':<12} {'Types':<{TYPE_COL_WIDTH}} {'Q1':>4}  {'Comp':>5}  "
        f"{'Stories':>7}  Notes"
    )
    lines.append(
        f"  {'────────────':<12} {'──────────────':<{TYPE_COL_WIDTH}} {'──':>4}  "
        f"{'─────':>5}  {'───────':>7}  ─────"
    )

    # Track reversion for flagging
    had_personalised = False
    last_pers_date: Optional[datetime] = None

    for i, sess in enumerate(sessions):
        date_str = sess[0].time_start.strftime("%Y-%m-%d")
        type_str = " ".join(_type_code(r) for r in sess)[:TYPE_COL_WIDTH].ljust(TYPE_COL_WIDTH)
        q1 = _q1_summary(sess)
        pct = _pct_summary(sess)
        n_stories = len(set(r.story_id for r in sess))

        is_pers = sum(1 for r in sess if _type_code(r) in PERSONALISED_CODES) / len(sess) >= 0.5

        # Detect reversion
        notes = []
        if is_pers:
            had_personalised = True
            last_pers_date = max(r.time_start for r in sess)
        elif had_personalised:
            gap_days = (sess[0].time_start - last_pers_date).total_seconds() / 86400 if last_pers_date else 0
            if gap_days > LAPSE_GAP_DAYS:
                notes.append(f"↩ lapse ({gap_days:.0f}d gap)")
            else:
                notes.append(f"⚠ reversion ({gap_days:.0f}d gap)")

        # Flag aborts
        aborts = sum(1 for r in sess if r.percent_complete is not None and r.percent_complete < 20)
        if aborts:
            notes.append(f"{aborts}✕")

        note_str = "  ".join(notes)
        lines.append(
            f"  {date_str:<12} {type_str:<{TYPE_COL_WIDTH}} {q1:>4}  {pct:>5}  "
            f"{n_stories:>7}  {note_str}"
        )

    # Q1 sparkline
    lines.append("")
    q1_scores = [r.question1_rating for r in sorted(p.records, key=lambda r: r.time_start)]
    spark = _sparkline(q1_scores, 60)
    lines.append(f"  Q1 scores ({len(q1_scores)} records, · = no score):")
    lines.append(f"  [{spark}]")

    # Session pattern
    patterns = []
    for sess in sessions:
        pers = sum(1 for r in sess if _type_code(r) in PERSONALISED_CODES)
        patterns.append("P" if pers / len(sess) >= 0.5 else "C")
    lines.append(f"  Session pattern: {' '.join(patterns)}")
    lines.append(f"  Key: B=content-Based  L=coLlaborative  T=Topical  W=Wildcard  S=Search  ·=no score")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML timeline
# ---------------------------------------------------------------------------

def render_html(participants: list[ParticipantEngagement], period_start: datetime, period_end: datetime) -> str:
    body_parts = []

    for p in participants:
        sessions = _sessions(p.records)
        if not sessions:
            body_parts.append(f'<div class="participant"><h2>{p.origin_id}</h2><p>No records.</p></div>')
            continue

        n_total = len(p.records)
        n_pers = sum(1 for r in p.records if _type_code(r) in PERSONALISED_CODES)

        rows = []
        had_personalised = False
        last_pers_date = None

        for sess in sessions:
            date_str = sess[0].time_start.strftime("%Y-%m-%d %H:%M")
            type_str = " ".join(_type_code(r) for r in sess)
            q1 = _q1_summary(sess)
            pct = _pct_summary(sess)
            n_stories = len(set(r.story_id for r in sess))
            is_pers = sum(1 for r in sess if _type_code(r) in PERSONALISED_CODES) / len(sess) >= 0.5
            aborts = sum(1 for r in sess if r.percent_complete is not None and r.percent_complete < 20)

            row_class = "pers" if is_pers else "cold"
            notes = []
            if is_pers:
                had_personalised = True
                last_pers_date = max(r.time_start for r in sess)
            elif had_personalised:
                gap_days = (sess[0].time_start - last_pers_date).total_seconds() / 86400 if last_pers_date else 0
                if gap_days > LAPSE_GAP_DAYS:
                    notes.append(f'<span class="lapse">↩ lapse ({gap_days:.0f}d)</span>')
                    row_class = "lapse"
                else:
                    notes.append(f'<span class="reversion">⚠ reversion ({gap_days:.0f}d)</span>')
                    row_class = "reversion"
            if aborts:
                notes.append(f'<span class="abort">{aborts}✕ abort</span>')

            rows.append(
                f'<tr class="{row_class}">'
                f'<td>{date_str}</td>'
                f'<td class="mono">{type_str}</td>'
                f'<td>{q1}</td><td>{pct}</td><td>{n_stories}</td>'
                f'<td>{"  ".join(notes)}</td></tr>'
            )

        # Score sparkline as coloured dots
        sorted_records = sorted(p.records, key=lambda r: r.time_start)
        dots = []
        for r in sorted_records:
            s = r.question1_rating
            if s is None:
                dots.append('<span class="dot dot-none" title="no score">·</span>')
            else:
                dots.append(f'<span class="dot dot-{s}" title="Q1={s}">{s}</span>')
        dot_html = "".join(dots)

        q1_scores_all = [r.question1_rating for r in sorted_records if r.question1_rating is not None]
        mean_q1 = f"{sum(q1_scores_all)/len(q1_scores_all):.1f}" if q1_scores_all else "—"

        patterns = []
        for sess in sessions:
            pers = sum(1 for r in sess if _type_code(r) in PERSONALISED_CODES)
            patterns.append("P" if pers / len(sess) >= 0.5 else "C")
        pattern_str = " ".join(patterns)

        body_parts.append(f"""
<div class="participant">
  <h2>{p.origin_id}</h2>
  <div class="summary">{n_total} records &nbsp;|&nbsp; {len(sessions)} sessions &nbsp;|&nbsp;
    personalised: {n_pers}/{n_total} &nbsp;|&nbsp; mean Q1: {mean_q1}</div>
  <table>
    <thead><tr><th>Session start</th><th>Types</th><th>Q1</th><th>Comp</th><th>Stories</th><th>Notes</th></tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table>
  <div class="sparkrow"><strong>Q1 scores:</strong> {dot_html}</div>
  <div class="pattern"><strong>Session pattern:</strong> <span class="mono">{pattern_str}</span></div>
</div>""")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>ORIGIN Participant Timelines</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 1100px; margin: 2rem auto;
         padding: 0 1rem; color: #1a1a1a; background: #fafafa; }}
  h1 {{ font-size: 1.4rem; border-bottom: 2px solid #333; padding-bottom: .4rem; }}
  h2 {{ font-size: 1.1rem; margin: 2rem 0 .3rem; color: #222; }}
  .participant {{ background: white; border: 1px solid #ddd; border-radius: 6px;
                  padding: 1rem 1.2rem; margin: 1.2rem 0; }}
  .summary {{ font-size: .85rem; color: #555; margin-bottom: .75rem; }}
  table {{ width: 100%; border-collapse: collapse; font-size: .82rem; margin-bottom: .75rem; }}
  th {{ background: #333; color: white; text-align: left; padding: .3rem .6rem; }}
  td {{ padding: .3rem .6rem; border-bottom: 1px solid #eee; }}
  tr.pers td {{ background: #efffef; }}
  tr.reversion td {{ background: #fff3e0; }}
  tr.lapse td {{ background: #f0f4ff; }}
  tr.cold td {{ background: white; }}
  .reversion {{ color: #c05000; font-weight: bold; }}
  .lapse {{ color: #3355aa; }}
  .abort {{ color: #aa2222; }}
  .mono {{ font-family: monospace; letter-spacing: .05em; }}
  .sparkrow {{ font-size: .9rem; margin: .3rem 0; }}
  .pattern {{ font-size: .85rem; color: #444; }}
  .dot {{ display: inline-block; width: 1.1em; text-align: center;
          font-size: .85rem; font-weight: bold; border-radius: 3px; margin: 1px; }}
  .dot-none {{ color: #aaa; }}
  .dot-1 {{ background: #ffd0d0; color: #800; }}
  .dot-2 {{ background: #ffe8c0; color: #840; }}
  .dot-3 {{ background: #ffffc0; color: #660; }}
  .dot-4 {{ background: #d0ffd0; color: #040; }}
  .dot-5 {{ background: #a0e0a0; color: #020; }}
  .legend {{ font-size: .78rem; color: #666; margin-top: 1.5rem; border-top: 1px solid #ddd;
             padding-top: .5rem; }}
</style>
</head>
<body>
<h1>ORIGIN Participant Timelines</h1>
<p>Period: {period_start.date()} to {period_end.date()} &nbsp;|&nbsp; {len(participants)} participant(s)</p>
{"".join(body_parts)}
<div class="legend">
  Types: B=content-Based &nbsp; L=coLlaborative &nbsp; T=Topical &nbsp;
  W=Wildcard &nbsp; S=Search &nbsp; R=Recents &nbsp; V=saVed<br>
  Rows: <span style="background:#efffef;padding:0 4px">green</span> = personalised session &nbsp;
  <span style="background:#fff3e0;padding:0 4px">orange</span> = ⚠ reversion (possible state loss) &nbsp;
  <span style="background:#f0f4ff;padding:0 4px">blue</span> = ↩ natural lapse
</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _parse_date(s: str) -> datetime:
    try:
        dt = datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid date {s!r} — expected YYYY-MM-DD")
    return dt.replace(tzinfo=timezone.utc)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Per-participant engagement timeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--input", metavar="FILE",
                        help="Read from a JSON file saved by 'trial.fetch --format json' "
                             "instead of calling the API. Credentials are not required.")
    period_group = parser.add_mutually_exclusive_group()
    period_group.add_argument("--days", type=int, metavar="N")
    period_group.add_argument("--from", dest="date_from", type=_parse_date, metavar="YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", type=_parse_date, metavar="YYYY-MM-DD")
    parser.add_argument("--participants", nargs="+", metavar="XXXX-XXXX",
                        help="Filter to specific IDs (space-separated). Overrides --ids-file.")
    parser.add_argument("--ids-file", metavar="FILE", default=DEFAULT_IDS_FILE,
                        help=f"File of OriginIds to fetch, one per line (default: {DEFAULT_IDS_FILE})")
    parser.add_argument("--html", metavar="FILE", help="Also save an HTML version")

    args = parser.parse_args(argv)

    if args.input:
        if args.days or args.date_from:
            parser.error("--input cannot be combined with --days / --from / --to")
        try:
            participants = load_participants_json(args.input)
        except (FileNotFoundError, ValueError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        all_times = [r.time_start for p in participants for r in p.records]
        if all_times:
            period_start = min(all_times)
            period_end = max(all_times)
        else:
            period_start = period_end = datetime.now(timezone.utc)
    else:
        if args.days is None and args.date_from is None:
            parser.error("one of --input, --days, or --from is required")
        if args.days is not None:
            period_end = datetime.now(timezone.utc)
            period_start = period_end - timedelta(days=args.days)
        else:
            if args.date_to is None:
                parser.error("--from requires --to")
            period_start = args.date_from
            period_end = args.date_to + timedelta(days=1) - timedelta(seconds=1)
        try:
            origin_ids = args.participants or load_ids(args.ids_file)
            client = TrialClient.from_env()
            participants = client.fetch(period_start, period_end, origin_ids=origin_ids)
        except (FileNotFoundError, ValueError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        except EnvironmentError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        except TrialAPIError as e:
            print(f"API error: {e}", file=sys.stderr)
            return 1

    for p in sorted(participants, key=lambda x: x.origin_id):
        print(render_ascii(p))

    if args.html:
        html = render_html(participants, period_start, period_end)
        with open(args.html, "w") as f:
            f.write(html)
        print(f"Wrote {args.html}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
