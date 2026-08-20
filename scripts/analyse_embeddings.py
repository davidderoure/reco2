"""Experiment: do embeddings usefully augment tag-based recommendations?

Three analyses:
  1. Nearest-neighbour comparison — for each story, top-5 by embedding vs
     top-5 by tag overlap. Flags stories where the two methods strongly disagree.
  2. Untagged stories — embedding-based neighbours for the 6 stories with no tags,
     which the tag system cannot place at all.
  3. Simulation comparison — run real-catalogue journeys with and without
     embeddings; compare mean Q1 score distributions per persona.

Usage:
    python scripts/analyse_embeddings.py
    python scripts/analyse_embeddings.py --output scripts/embedding_analysis.html
    python scripts/analyse_embeddings.py --embeddings data/embeddings.json
"""

from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

# Allow running from repo root without installing the package.
sys.path.insert(0, str(Path(__file__).parent.parent))

from recommender.catalogue import Catalogue
from recommender.embeddings import EmbeddingIndex
from recommender.engine import RecommenderEngine
from recommender.models import Story
from simulation.journeys import run_journey, N_ROUNDS
from simulation.noise import NO_NOISE
from simulation.real_catalogue import FORMAT_TAGS, load_real_catalogue
from simulation.real_personas import REAL_PERSONAS

DEFAULT_EMBEDDINGS = Path(__file__).parent.parent / "data" / "embeddings.json"
DEFAULT_CATALOGUE = Path(__file__).parent.parent / "stories.json"
DEFAULT_OUTPUT = Path(__file__).parent / "embedding_analysis.html"

TOP_N = 5          # neighbours to compare
N_SIM_ROUNDS = 20  # rounds per user in simulation comparison
USERS_PER_PERSONA = 3


# ---------------------------------------------------------------------------
# Tag-overlap similarity (Jaccard on tag sets)
# ---------------------------------------------------------------------------

def tag_similarity(a: Story, b: Story) -> float:
    if not a.tags or not b.tags:
        return 0.0
    sa, sb = set(a.tags), set(b.tags)
    return len(sa & sb) / len(sa | sb)


def top_by_tags(story: Story, catalogue: Catalogue, n: int) -> list[tuple[float, Story]]:
    scored = [
        (tag_similarity(story, other), other)
        for other in catalogue.all_stories()
        if other.story_id != story.story_id
    ]
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:n]


def top_by_embeddings(story: Story, index: EmbeddingIndex, catalogue: Catalogue, n: int) -> list[tuple[float, Story]]:
    scored = [
        (index.similarity(story.story_id, other.story_id), other)
        for other in catalogue.all_stories()
        if other.story_id != story.story_id
    ]
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:n]


# ---------------------------------------------------------------------------
# Analysis 1 — nearest-neighbour comparison
# ---------------------------------------------------------------------------

def analyse_neighbours(catalogue: Catalogue, index: EmbeddingIndex) -> list[dict]:
    results = []
    for story in catalogue.all_stories():
        tag_neighbours = top_by_tags(story, catalogue, TOP_N)
        emb_neighbours = top_by_embeddings(story, index, catalogue, TOP_N)

        tag_ids = {s.story_id for _, s in tag_neighbours}
        emb_ids = {s.story_id for _, s in emb_neighbours}
        overlap = len(tag_ids & emb_ids)
        disagreement = TOP_N - overlap  # 0 = perfect agreement, 5 = total disagreement

        results.append({
            "story": story,
            "tag_neighbours": tag_neighbours,
            "emb_neighbours": emb_neighbours,
            "overlap": overlap,
            "disagreement": disagreement,
            "untagged": len(story.tags) == 0,
        })

    results.sort(key=lambda r: r["disagreement"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Analysis 2 — untagged stories
# ---------------------------------------------------------------------------

def analyse_untagged(catalogue: Catalogue, index: EmbeddingIndex) -> list[dict]:
    return [
        {
            "story": story,
            "emb_neighbours": top_by_embeddings(story, index, catalogue, TOP_N),
        }
        for story in catalogue.all_stories()
        if not story.tags
    ]


# ---------------------------------------------------------------------------
# Analysis 3 — simulation comparison
# ---------------------------------------------------------------------------

def run_simulation(catalogue: Catalogue, index: EmbeddingIndex | None, seed: int) -> list[dict]:
    engine = RecommenderEngine(catalogue, rng=random.Random(seed))
    if index is not None:
        engine.load_embeddings(index)

    rng = random.Random(seed + 1)
    now = time.time()
    results = []

    for persona in REAL_PERSONAS:
        scores_this_persona = []
        for i in range(USERS_PER_PERSONA):
            user_id = f"{persona.name}-{i}"
            rounds = run_journey(
                engine, user_id, persona, rng, now, N_SIM_ROUNDS,
                noise=NO_NOISE, format_tags=FORMAT_TAGS,
            )
            user_scores = [r["score"] for r in rounds if r["score"] is not None]
            scores_this_persona.extend(user_scores)

        if scores_this_persona:
            mean = sum(scores_this_persona) / len(scores_this_persona)
            results.append({
                "persona": persona.name,
                "n_scores": len(scores_this_persona),
                "mean": mean,
            })

    return results


def compare_simulations(catalogue: Catalogue, index: EmbeddingIndex) -> list[dict]:
    print("  running without embeddings...")
    baseline = run_simulation(catalogue, None, seed=42)
    print("  running with embeddings...")
    with_emb = run_simulation(catalogue, index, seed=42)

    baseline_by_persona = {r["persona"]: r for r in baseline}
    comparison = []
    for r in with_emb:
        persona = r["persona"]
        base = baseline_by_persona.get(persona)
        if base:
            diff = r["mean"] - base["mean"]
            comparison.append({
                "persona": persona,
                "baseline_mean": base["mean"],
                "emb_mean": r["mean"],
                "diff": diff,
                "n_scores": r["n_scores"],
            })
    comparison.sort(key=lambda r: r["diff"], reverse=True)
    return comparison


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

def _fmt_tags(tags: list[str]) -> str:
    theme = [t for t in tags if t not in FORMAT_TAGS]
    fmt = [t for t in tags if t in FORMAT_TAGS]
    parts = []
    if fmt:
        parts.append(f'<span class="fmt-tag">{", ".join(fmt)}</span>')
    if theme:
        parts.append(f'<span class="theme-tag">{", ".join(theme)}</span>')
    return " ".join(parts) if parts else '<span class="no-tag">no tags</span>'


def _neighbour_rows(neighbours: list[tuple[float, Story]], highlight_ids: set[str] = None) -> str:
    rows = []
    for score, s in neighbours:
        cls = "shared" if highlight_ids and s.story_id in highlight_ids else ""
        rows.append(
            f'<tr class="{cls}"><td>{score:.3f}</td>'
            f'<td>{s.title}</td>'
            f'<td>{_fmt_tags(s.tags)}</td></tr>'
        )
    return "".join(rows)


def build_html(
    neighbour_results: list[dict],
    untagged_results: list[dict],
    sim_comparison: list[dict],
    catalogue_size: int,
) -> str:

    # --- Section 1: show top 15 most-disagreeing stories ---
    top_disagreements = neighbour_results[:15]
    all_same = [r for r in neighbour_results if r["disagreement"] == 0]

    disagreement_html = ""
    for r in top_disagreements:
        story = r["story"]
        tag_ids = {s.story_id for _, s in r["tag_neighbours"]}
        emb_ids = {s.story_id for _, s in r["emb_neighbours"]}
        disagreement_html += f"""
<div class="story-block">
  <div class="story-title">{story.title}</div>
  <div class="story-subtitle">{_fmt_tags(story.tags)}</div>
  <div class="story-meta">Agreement: {r['overlap']}/{TOP_N} neighbours in common</div>
  <div class="two-col">
    <div>
      <div class="col-heading">Tag-based neighbours</div>
      <table><thead><tr><th>Jaccard</th><th>Title</th><th>Tags</th></tr></thead>
      <tbody>{_neighbour_rows(r['tag_neighbours'], emb_ids)}</tbody></table>
    </div>
    <div>
      <div class="col-heading">Embedding neighbours</div>
      <table><thead><tr><th>Cosine</th><th>Title</th><th>Tags</th></tr></thead>
      <tbody>{_neighbour_rows(r['emb_neighbours'], tag_ids)}</tbody></table>
    </div>
  </div>
</div>"""

    # --- Section 2: untagged stories ---
    untagged_html = ""
    for r in untagged_results:
        story = r["story"]
        untagged_html += f"""
<div class="story-block">
  <div class="story-title">{story.title} <span class="no-tag">(no tags)</span></div>
  <table><thead><tr><th>Cosine</th><th>Title</th><th>Tags</th></tr></thead>
  <tbody>{_neighbour_rows(r['emb_neighbours'])}</tbody></table>
</div>"""

    # --- Section 3: simulation comparison ---
    sim_rows = ""
    for r in sim_comparison:
        arrow = "▲" if r["diff"] > 0.05 else ("▼" if r["diff"] < -0.05 else "→")
        cls = "positive" if r["diff"] > 0.05 else ("negative" if r["diff"] < -0.05 else "neutral")
        sim_rows += (
            f'<tr class="{cls}"><td>{r["persona"].replace("_", " ")}</td>'
            f'<td>{r["baseline_mean"]:.2f}</td>'
            f'<td>{r["emb_mean"]:.2f}</td>'
            f'<td class="diff">{arrow} {r["diff"]:+.2f}</td>'
            f'<td>{r["n_scores"]}</td></tr>'
        )

    mean_diff = sum(r["diff"] for r in sim_comparison) / len(sim_comparison) if sim_comparison else 0

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Embedding Analysis</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 1200px; margin: 2rem auto; padding: 0 1rem;
         color: #1a1a1a; background: #fafafa; }}
  h1 {{ font-size: 1.5rem; border-bottom: 2px solid #333; padding-bottom: .5rem; }}
  h2 {{ font-size: 1.2rem; margin-top: 2.5rem; border-bottom: 1px solid #ccc; padding-bottom: .3rem; }}
  h3 {{ font-size: 1rem; color: #555; }}
  .summary {{ background: #f0f4ff; border-left: 4px solid #4466cc; padding: .75rem 1rem;
              margin: 1rem 0; border-radius: 0 4px 4px 0; }}
  .story-block {{ background: white; border: 1px solid #ddd; border-radius: 6px;
                  padding: 1rem; margin: 1rem 0; }}
  .story-title {{ font-weight: bold; font-size: 1rem; }}
  .story-subtitle {{ color: #555; font-size: .85rem; margin: .2rem 0 .5rem; }}
  .story-meta {{ font-size: .8rem; color: #777; margin-bottom: .75rem; }}
  .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }}
  .col-heading {{ font-weight: bold; font-size: .85rem; margin-bottom: .4rem; color: #333; }}
  table {{ width: 100%; border-collapse: collapse; font-size: .82rem; }}
  th {{ background: #f0f0f0; text-align: left; padding: .3rem .5rem; }}
  td {{ padding: .3rem .5rem; border-bottom: 1px solid #eee; vertical-align: top; }}
  tr.shared td {{ background: #efffef; }}
  .fmt-tag {{ color: #2255aa; font-size: .8rem; }}
  .theme-tag {{ color: #333; font-size: .8rem; }}
  .no-tag {{ color: #cc4444; font-size: .8rem; font-style: italic; }}
  .sim-table {{ width: 100%; border-collapse: collapse; font-size: .9rem; }}
  .sim-table th {{ background: #333; color: white; padding: .4rem .75rem; text-align: left; }}
  .sim-table td {{ padding: .4rem .75rem; border-bottom: 1px solid #eee; }}
  .sim-table tr.positive td {{ background: #efffef; }}
  .sim-table tr.negative td {{ background: #fff0f0; }}
  .diff {{ font-weight: bold; }}
  .positive .diff {{ color: #2a7a2a; }}
  .negative .diff {{ color: #aa2222; }}
</style>
</head>
<body>
<h1>Embedding Experiment — ORIGIN Story Recommender</h1>
<p>Catalogue: {catalogue_size} stories &nbsp;|&nbsp; Top-N: {TOP_N} &nbsp;|&nbsp;
   Simulation: {N_SIM_ROUNDS} rounds × {USERS_PER_PERSONA} users × {len(sim_comparison)} personas</p>

<div class="summary">
  <strong>Summary:</strong> {len(all_same)} of {catalogue_size} stories have identical top-{TOP_N}
  neighbours by tag and by embedding (perfect agreement).
  {len(top_disagreements)} stories shown below with the most disagreement.
  Simulation mean Q1 shift across all personas: <strong>{mean_diff:+.2f}</strong> on the 1–5 scale
  (positive = embeddings help, negative = embeddings hurt).
  <br><br>
  <em>Shared rows (green) appear in both tag and embedding neighbour lists.</em>
</div>

<h2>1. Nearest-neighbour comparison — top {len(top_disagreements)} most-disagreeing stories</h2>
<p>Stories where embedding similarity and tag overlap surface different neighbours.
   Green rows are stories that appear in <em>both</em> lists.</p>
{disagreement_html}

<h2>2. Untagged stories — what embeddings find</h2>
<p>These {len(untagged_results)} stories have no tags; the tag-based recommender
   can only surface them as wildcards. Embeddings give them a content-based identity.</p>
{untagged_html}

<h2>3. Simulation comparison — mean Q1 score with vs without embeddings</h2>
<p>Same random seed, same personas, same rounds. Only the content-based scoring changes.
   ▲ = embeddings improved mean score &nbsp; ▼ = embeddings hurt &nbsp; → = negligible difference (±0.05).</p>
<table class="sim-table">
  <thead><tr><th>Persona</th><th>Baseline mean Q1</th><th>With embeddings</th><th>Diff</th><th>N scores</th></tr></thead>
  <tbody>{sim_rows}</tbody>
</table>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(
    embeddings_path: Path = DEFAULT_EMBEDDINGS,
    catalogue_path: Path = DEFAULT_CATALOGUE,
    output_path: Path = DEFAULT_OUTPUT,
) -> None:
    print(f"Loading catalogue from {catalogue_path}...")
    catalogue = load_real_catalogue(catalogue_path)
    print(f"  {len(catalogue)} stories")

    print(f"Loading embeddings from {embeddings_path}...")
    index = EmbeddingIndex.load(embeddings_path)
    print(f"  {len(index.story_ids())} story embeddings")

    print("Analysis 1: nearest-neighbour comparison...")
    neighbour_results = analyse_neighbours(catalogue, index)

    print("Analysis 2: untagged stories...")
    untagged_results = analyse_untagged(catalogue, index)

    print("Analysis 3: simulation comparison...")
    sim_comparison = compare_simulations(catalogue, index)

    print(f"Writing report to {output_path}...")
    html = build_html(neighbour_results, untagged_results, sim_comparison, len(catalogue))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        f.write(html)
    print(f"Done — open {output_path}")

    # Print simulation summary to console too.
    print("\nSimulation summary (mean Q1, 1–5 scale):")
    print(f"  {'Persona':<35} {'Baseline':>9} {'Embedding':>9} {'Diff':>7}")
    print(f"  {'-'*35} {'-'*9} {'-'*9} {'-'*7}")
    for r in sim_comparison:
        print(f"  {r['persona']:<35} {r['baseline_mean']:>9.2f} {r['emb_mean']:>9.2f} {r['diff']:>+7.2f}")
    mean_diff = sum(r["diff"] for r in sim_comparison) / len(sim_comparison)
    print(f"\n  Overall mean shift: {mean_diff:+.3f}")


if __name__ == "__main__":
    emb_arg = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--embeddings" and i + 1 < len(sys.argv)), None)
    cat_arg = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--catalogue" and i + 1 < len(sys.argv)), None)
    out_arg = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--output" and i + 1 < len(sys.argv)), None)

    main(
        embeddings_path=Path(emb_arg) if emb_arg else DEFAULT_EMBEDDINGS,
        catalogue_path=Path(cat_arg) if cat_arg else DEFAULT_CATALOGUE,
        output_path=Path(out_arg) if out_arg else DEFAULT_OUTPUT,
    )
