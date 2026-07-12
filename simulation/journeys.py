"""Per-user journey logging: round-by-round trace of what each synthetic
persona was recommended, what they "opened", and what connectedness score
that produced — for colleagues to eyeball, not just aggregate stats.

Uses the definitive ORIGIN tag vocabulary (synthetic_catalogue.py) and
named personas (personas.py). Synthetic ground truth only — useful for
catching logic bugs and producing something concrete to react to, not a
substitute for clinical validation.

Run: python -m simulation.journeys
Run with noise: python -m simulation.journeys --noise
"""

from __future__ import annotations

import random
import sys
import time

from recommender.engine import RecommenderEngine
from .noise import InterruptionType, NO_NOISE, NoiseConfig, high_progress, low_progress, sample_interruption
from .personas import PERSONAS, ROBUSTNESS_PERSONAS, simulated_connectedness
from .synthetic_catalogue import generate_catalogue

USERS_PER_PERSONA = 2
N_ROUNDS = 15


def run_journey(
    engine: RecommenderEngine,
    user_id: str,
    persona,
    rng: random.Random,
    now: float,
    n_rounds: int,
    noise: NoiseConfig = NO_NOISE,
) -> list[dict]:
    rounds = []
    for round_idx in range(n_rounds):
        timestamp = now + round_idx * 86400
        recs = engine.get_recommendations(user_id, timestamp=timestamp)
        if persona.selection == "random":
            opened_story_id, opened_type = rng.choice(recs)
        else:
            opened_story_id, opened_type = recs[0]
        story = engine.catalogue.get(opened_story_id)

        interruption = sample_interruption(rng, noise)

        if interruption == InterruptionType.NONE:
            score = (
                persona.fixed_score
                if persona.fixed_score is not None
                else simulated_connectedness(story, persona, rng)
            )
            engine.record_answered_question(user_id, opened_story_id, [score, 5, 5, 5], timestamp=timestamp)
            engine.record_engagement_stop(user_id, opened_story_id, progress_percentage=100.0, timestamp=timestamp)
        elif interruption == InterruptionType.STOP_EARLY:
            score = None
            pct = low_progress(rng)
            engine.record_engagement_progress(user_id, opened_story_id, pct, timestamp=timestamp)
            engine.record_engagement_stop(user_id, opened_story_id, progress_percentage=pct, timestamp=timestamp)
        elif interruption == InterruptionType.ABORT_LOW:
            score = None
            pct = low_progress(rng)
            engine.record_engagement_progress(user_id, opened_story_id, pct, timestamp=timestamp)
            engine.record_abort(user_id, opened_story_id, timestamp=timestamp)
        elif interruption == InterruptionType.ABORT_HIGH:
            score = None
            pct = high_progress(rng)
            engine.record_engagement_progress(user_id, opened_story_id, pct, timestamp=timestamp)
            engine.record_abort(user_id, opened_story_id, timestamp=timestamp)
        elif interruption == InterruptionType.NO_EVENT:
            score = None
            # No events fired — app closed before anything was recorded.

        rounds.append({
            "round": round_idx + 1,
            "recommendations": recs,
            "opened": opened_story_id,
            "opened_type": opened_type,
            "score": score,
            "interruption": interruption,
        })
    return rounds


REC_TYPE_NAMES = {1: "content-based", 2: "collaborative", 3: "topical", 4: "wildcard"}

INTERRUPTION_LABELS = {
    InterruptionType.NONE:       "",
    InterruptionType.STOP_EARLY: " ⚡stop-early",
    InterruptionType.ABORT_LOW:  " ⚡abort-low",
    InterruptionType.ABORT_HIGH: " ⚡abort-high",
    InterruptionType.NO_EVENT:   " ⚡no-event",
}


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
        score_cell = f"{r['score']}/9" if r["score"] is not None else "—"
        interruption_label = INTERRUPTION_LABELS[r["interruption"]]
        lines.append(
            f"| {r['round']} | {'<br>'.join(rec_cells)} | "
            f"{r['opened']} ({REC_TYPE_NAMES[r['opened_type']]}){interruption_label} | {score_cell} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(
    output_path: str = "simulation/journeys_output.md",
    with_noise: bool = False,
    robustness: bool = False,
) -> None:
    noise = NoiseConfig() if with_noise else NO_NOISE
    now = time.time()
    catalogue = generate_catalogue(n_stories=120, seed=1, now=now)
    engine = RecommenderEngine(catalogue)
    rng = random.Random(42)

    noise_note = (
        f"Noise enabled: {int(noise.interruption_probability * 100)}% interruption rate "
        f"(stop-early {int(noise.weights[InterruptionType.STOP_EARLY]*100)}%, "
        f"abort-low {int(noise.weights[InterruptionType.ABORT_LOW]*100)}%, "
        f"abort-high {int(noise.weights[InterruptionType.ABORT_HIGH]*100)}%, "
        f"no-event {int(noise.weights[InterruptionType.NO_EVENT]*100)}%). "
        f"⚡ marks interrupted rounds."
        if with_noise else "No noise (clean baseline)."
    )

    if robustness:
        personas_to_run = [(p, 1) for p in ROBUSTNESS_PERSONAS]
        title = "# Robustness journeys — extreme user behaviours"
        subtitle = (
            "Each persona exercises an extreme edge case "
            "(fixed score, non-standard selection) to verify the "
            "recommender behaves reasonably under adversarial or degenerate input."
        )
    else:
        personas_to_run = [(p, USERS_PER_PERSONA) for p in PERSONAS]
        title = "# Synthetic user journeys"
        subtitle = (
            "Generated against the definitive ORIGIN tag vocabulary "
            "(4 format tags, 47 theme tags) for internal review — synthetic "
            "ground truth, not a substitute for clinical validation."
        )

    sections = [
        title,
        "",
        subtitle,
        "",
        f"Catalogue: {len(catalogue)} stories. Rounds per user: {N_ROUNDS}. "
        f"**Bold** marks the story the synthetic user opened each round.",
        "",
        f"_{noise_note}_",
        "",
    ]

    total = 0
    for persona, n_users in personas_to_run:
        for i in range(n_users):
            user_id = f"{persona.name}-{i}"
            rounds = run_journey(engine, user_id, persona, rng, now, N_ROUNDS, noise=noise)
            sections.append(render_transcript(user_id, persona, rounds, engine))
            total += 1

    report = "\n".join(sections)
    with open(output_path, "w") as f:
        f.write(report)

    print(f"Wrote {total} user journeys ({N_ROUNDS} rounds each) to {output_path}")


if __name__ == "__main__":
    with_noise = "--noise" in sys.argv
    robustness = "--robustness" in sys.argv
    if robustness:
        output = "simulation/journeys_robustness.md"
    elif with_noise:
        output = "simulation/journeys_output_noise.md"
    else:
        output = "simulation/journeys_output.md"
    main(output_path=output, with_noise=with_noise, robustness=robustness)
