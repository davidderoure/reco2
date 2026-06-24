"""Per-user journey logging: round-by-round trace of what each synthetic
persona was recommended, what they "opened", and what connectedness score
that produced — for colleagues to eyeball, not just aggregate stats.

Uses the preliminary ORIGIN tag vocabulary (synthetic_catalogue.py) and
named personas (personas.py). Synthetic ground truth only — useful for
catching logic bugs and producing something concrete to react to, not a
substitute for clinical validation.

Run: python -m simulation.journeys
"""

from __future__ import annotations

import random
import time

from recommender.engine import RecommenderEngine
from .personas import PERSONAS, simulated_connectedness
from .synthetic_catalogue import generate_catalogue

USERS_PER_PERSONA = 2
N_ROUNDS = 15


def run_journey(engine: RecommenderEngine, user_id: str, persona, rng: random.Random, now: float, n_rounds: int) -> list[dict]:
    rounds = []
    for round_idx in range(n_rounds):
        timestamp = now + round_idx * 86400
        recs = engine.get_recommendations(user_id, timestamp=timestamp)
        opened_story_id, opened_type = recs[0]
        story = engine.catalogue.get(opened_story_id)
        score = simulated_connectedness(story, persona, rng)

        engine.record_answered_question(user_id, opened_story_id, [score, 5, 5, 5], timestamp=timestamp)
        engine.record_engagement_stop(user_id, opened_story_id, progress_percentage=100.0, timestamp=timestamp)

        rounds.append({
            "round": round_idx + 1,
            "recommendations": recs,
            "opened": opened_story_id,
            "opened_type": opened_type,
            "score": score,
        })
    return rounds


REC_TYPE_NAMES = {1: "content-based", 2: "collaborative", 3: "topical", 4: "wildcard"}


def render_transcript(user_id: str, persona, rounds: list[dict], engine: RecommenderEngine) -> str:
    lines = [
        f"## {user_id} — persona: {persona.name}",
        f"_{persona.description}_",
        "",
        "| Round | Recommended (story: tags [method]) | Opened | Score |",
        "|---|---|---|---|",
    ]
    for r in rounds:
        rec_cells = []
        for story_id, rec_type in r["recommendations"]:
            story = engine.catalogue.get(story_id)
            marker = "**" if story_id == r["opened"] else ""
            tags = ", ".join(story.tags) if story else "?"
            rec_cells.append(f"{marker}{story_id} [{REC_TYPE_NAMES[rec_type]}]: {tags}{marker}")
        lines.append(
            f"| {r['round']} | {'<br>'.join(rec_cells)} | {r['opened']} ({REC_TYPE_NAMES[r['opened_type']]}) | {r['score']}/9 |"
        )
    lines.append("")
    return "\n".join(lines)


def main(output_path: str = "simulation/journeys_output.md") -> None:
    now = time.time()
    catalogue = generate_catalogue(n_stories=120, seed=1, now=now)
    engine = RecommenderEngine(catalogue)
    rng = random.Random(42)

    sections = [
        "# Synthetic user journeys",
        "",
        "Generated against the preliminary ORIGIN tag vocabulary "
        "(4 format tags, 6 theme tags) for internal review — synthetic "
        "ground truth, not a substitute for clinical validation.",
        "",
        f"Catalogue: {len(catalogue)} stories. Rounds per user: {N_ROUNDS}. "
        f"**Bold** marks the story the synthetic user opened each round.",
        "",
    ]

    for persona in PERSONAS:
        for i in range(USERS_PER_PERSONA):
            user_id = f"{persona.name}-{i}"
            rounds = run_journey(engine, user_id, persona, rng, now, N_ROUNDS)
            sections.append(render_transcript(user_id, persona, rounds, engine))

    report = "\n".join(sections)
    with open(output_path, "w") as f:
        f.write(report)

    print(f"Wrote {len(PERSONAS) * USERS_PER_PERSONA} user journeys ({N_ROUNDS} rounds each) to {output_path}")


if __name__ == "__main__":
    main()
