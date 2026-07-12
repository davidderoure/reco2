"""HTML report generator for synthetic user journeys.

Produces a self-contained HTML file with per-user charts and summary
statistics — designed to be shared with colleagues who don't have Python.

Run (clean):  python -m simulation.report
Run (noisy):  python -m simulation.report --noise
"""

from __future__ import annotations

import json
import random
import sys
import time

from .noise import InterruptionType, NO_NOISE, NoiseConfig, high_progress, low_progress, sample_interruption
from .personas import PERSONAS, ROBUSTNESS_PERSONAS, simulated_connectedness
from .synthetic_catalogue import FORMAT_TAGS, THEME_TAGS, generate_catalogue
from recommender.engine import RecommenderEngine

USERS_PER_PERSONA = 2
N_ROUNDS = 15


def collect_journey(
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
            engine.record_engagement_stop(user_id, opened_story_id, 100.0, timestamp=timestamp)
        elif interruption == InterruptionType.STOP_EARLY:
            score = None
            pct = low_progress(rng)
            engine.record_engagement_progress(user_id, opened_story_id, pct, timestamp=timestamp)
            engine.record_engagement_stop(user_id, opened_story_id, pct, timestamp=timestamp)
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

        rec_type_names = {1: "content", 2: "collab", 3: "topical", 4: "wildcard"}
        rounds.append({
            "round": round_idx + 1,
            "opened": opened_story_id,
            "tags": story.tags if story else [],
            "rec_type": rec_type_names.get(opened_type, "?"),
            "score": score,
            "interruption": interruption.value,
        })

    user = engine.population.get(user_id)
    final_affinity = dict(user.tag_affinity) if user else {}
    return rounds, final_affinity


REC_TYPE_COLORS = {
    "content":  "#2a78d6",
    "collab":   "#1baf7a",
    "topical":  "#eda100",
    "wildcard": "#4a3aa7",
}

INTERRUPTION_LABELS = {
    "none":       "",
    "stop_early": "stop-early",
    "abort_low":  "abort (low)",
    "abort_high": "abort (high)",
    "no_event":   "no event",
}


def _html(all_users: list[dict], with_noise: bool, n_stories: int) -> str:
    noise_note = (
        "15% interruption rate — stop-early, abort-low, abort-high, no-event. "
        "Interrupted rounds shown as gaps; hover for details."
        if with_noise else
        "Clean baseline — no interruption noise."
    )

    users_json = json.dumps(all_users)
    rec_colors_json = json.dumps(REC_TYPE_COLORS)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>ORIGIN recommender — synthetic journeys</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: system-ui, sans-serif; font-size: 14px; color: #1a1a18;
         background: #f7f6f2; padding: 2rem; }}
  h1 {{ font-size: 20px; font-weight: 500; margin-bottom: 0.25rem; }}
  .meta {{ color: #52514e; font-size: 13px; margin-bottom: 2rem; }}
  .persona-section {{ margin-bottom: 3rem; }}
  .persona-heading {{ font-size: 16px; font-weight: 500; margin-bottom: 0.25rem; border-top: 1px solid #ddd; padding-top: 1.5rem; }}
  .persona-desc {{ color: #52514e; font-size: 13px; margin-bottom: 1rem; }}
  .user-block {{ background: #fff; border: 0.5px solid #ddd; border-radius: 10px;
                 padding: 1.25rem; margin-bottom: 1rem; }}
  .user-label {{ font-size: 13px; font-weight: 500; color: #52514e; margin-bottom: 0.75rem; }}
  .stats-row {{ display: flex; gap: 12px; margin-bottom: 1rem; flex-wrap: wrap; }}
  .stat {{ background: #f7f6f2; border-radius: 8px; padding: 0.6rem 0.9rem; flex: 1; min-width: 80px; }}
  .stat-label {{ font-size: 11px; color: #898781; text-transform: uppercase; letter-spacing: 0.04em; }}
  .stat-value {{ font-size: 20px; font-weight: 500; margin-top: 2px; }}
  .charts-row {{ display: grid; grid-template-columns: 2fr 1fr; gap: 1rem; }}
  .chart-wrap {{ position: relative; }}
  .chart-label {{ font-size: 11px; color: #898781; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.04em; }}
  .legend {{ display: flex; gap: 12px; flex-wrap: wrap; margin-top: 8px; }}
  .legend-item {{ display: flex; align-items: center; gap: 4px; font-size: 11px; color: #52514e; }}
  .legend-dot {{ width: 8px; height: 8px; border-radius: 50%; }}
  @media (max-width: 600px) {{ .charts-row {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<h1>ORIGIN recommender — synthetic user journeys</h1>
<p class="meta">
  Catalogue: {n_stories} stories &nbsp;·&nbsp; {N_ROUNDS} rounds per user &nbsp;·&nbsp;
  {noise_note}
</p>

<div id="root"></div>

<script>
const USERS = {users_json};
const REC_COLORS = {rec_colors_json};

const INTERRUPTION_LABELS = {{
  "none": "", "stop_early": "stop-early",
  "abort_low": "abort (low%)", "abort_high": "abort (high%)", "no_event": "no event"
}};

function statCard(label, value, color) {{
  return `<div class="stat">
    <div class="stat-label">${{label}}</div>
    <div class="stat-value" style="${{color ? 'color:'+color : ''}}">${{value}}</div>
  </div>`;
}}

function buildUser(u, container) {{
  const scored = u.rounds.filter(r => r.score !== null);
  const interrupted = u.rounds.filter(r => r.interruption !== "none");
  const meanScore = scored.length ? (scored.reduce((s,r) => s+r.score, 0) / scored.length).toFixed(1) : "—";
  const recTypeCounts = {{}};
  u.rounds.forEach(r => {{ recTypeCounts[r.rec_type] = (recTypeCounts[r.rec_type]||0)+1; }});

  const block = document.createElement("div");
  block.className = "user-block";
  block.innerHTML = `
    <div class="user-label">User ${{u.user_id}}</div>
    <div class="stats-row">
      ${{statCard("scored", scored.length)}}
      ${{statCard("interrupted", interrupted.length, interrupted.length > 0 ? "#e34948" : null)}}
      ${{statCard("mean score", meanScore, "#2a78d6")}}
      ${{statCard("content", recTypeCounts.content||0, "#2a78d6")}}
      ${{statCard("collab", recTypeCounts.collab||0, "#1baf7a")}}
      ${{statCard("topical", recTypeCounts.topical||0, "#eda100")}}
      ${{statCard("wildcard", recTypeCounts.wildcard||0, "#4a3aa7")}}
    </div>
    <div class="charts-row">
      <div>
        <div class="chart-label">Connectedness per round (1–9)</div>
        <div class="chart-wrap" style="height:180px"><canvas id="line-${{u.user_id}}" role="img" aria-label="Connectedness scores over ${{N_ROUNDS}} rounds for user ${{u.user_id}}">Scores: ${{scored.map(r=>`round ${{r.round}}: ${{r.score}}`).join(', ')}}</canvas></div>
      </div>
      <div>
        <div class="chart-label">Top tag affinities</div>
        <div class="chart-wrap" style="height:180px"><canvas id="aff-${{u.user_id}}" role="img" aria-label="Top tag affinities for user ${{u.user_id}}"></canvas></div>
      </div>
    </div>
    <div class="legend">
      ${{Object.entries(REC_COLORS).map(([k,c]) =>
        `<span class="legend-item"><span class="legend-dot" style="background:${{c}}"></span>${{k}}</span>`
      ).join("")}}
      <span class="legend-item"><span style="font-size:13px">⚡</span> interrupted (gap = no score)</span>
    </div>
  `;
  container.appendChild(block);

  const rounds = u.rounds;
  const labels = rounds.map(r => `R${{r.round}}`);
  const scores = rounds.map(r => r.score);
  const pointColors = rounds.map(r => REC_COLORS[r.rec_type] || "#888");
  const pointRadii = rounds.map(r => r.score !== null ? 5 : 0);

  new Chart(document.getElementById(`line-${{u.user_id}}`), {{
    type: "line",
    data: {{
      labels,
      datasets: [{{
        data: scores,
        borderColor: "#2a78d6",
        borderWidth: 2,
        pointBackgroundColor: pointColors,
        pointBorderColor: "#fff",
        pointBorderWidth: 2,
        pointRadius: pointRadii,
        spanGaps: false,
        tension: 0.3,
      }}]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }}, tooltip: {{
        callbacks: {{
          label: (ctx) => {{
            const r = rounds[ctx.dataIndex];
            const parts = [`score: ${{r.score ?? "—"}}`, `type: ${{r.rec_type}}`];
            if (r.interruption !== "none") parts.push(`⚡ ${{INTERRUPTION_LABELS[r.interruption]}}`);
            parts.push(`tags: ${{r.tags.join(", ") || "none"}}`);
            return parts;
          }}
        }}
      }} }},
      scales: {{
        y: {{ min: 1, max: 9, ticks: {{ stepSize: 2, color: "#898781" }}, grid: {{ color: "#e8e7e1" }} }},
        x: {{ ticks: {{ color: "#898781", maxRotation: 0 }}, grid: {{ display: false }} }}
      }}
    }}
  }});

  const topAff = Object.entries(u.final_affinity)
    .sort((a,b) => b[1]-a[1]).slice(0, 8);
  new Chart(document.getElementById(`aff-${{u.user_id}}`), {{
    type: "bar",
    data: {{
      labels: topAff.map(([t]) => t.length > 14 ? t.slice(0,13)+"…" : t),
      datasets: [{{
        data: topAff.map(([,v]) => Math.round(v * 100)),
        backgroundColor: "#2a78d6",
        borderRadius: 3,
        barThickness: 12,
      }}]
    }},
    options: {{
      indexAxis: "y",
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }}, tooltip: {{
        callbacks: {{ label: ctx => ` ${{ctx.raw}}%` }}
      }} }},
      scales: {{
        x: {{ min: 0, max: 100, ticks: {{ callback: v => v+"%", color: "#898781" }}, grid: {{ color: "#e8e7e1" }} }},
        y: {{ ticks: {{ color: "#898781", font: {{ size: 11 }} }}, grid: {{ display: false }} }}
      }}
    }}
  }});
}}

const root = document.getElementById("root");
const byPersona = {{}};
USERS.forEach(u => {{
  if (!byPersona[u.persona]) byPersona[u.persona] = {{ desc: u.persona_desc, users: [] }};
  byPersona[u.persona].users.push(u);
}});

Object.entries(byPersona).forEach(([persona, data]) => {{
  const sec = document.createElement("div");
  sec.className = "persona-section";
  sec.innerHTML = `<div class="persona-heading">${{persona.replace(/_/g," ")}}</div>
    <div class="persona-desc">${{data.desc}}</div>`;
  data.users.forEach(u => buildUser(u, sec));
  root.appendChild(sec);
}});
</script>
</body>
</html>"""


def main(with_noise: bool = False, robustness: bool = False) -> None:
    noise = NoiseConfig() if with_noise else NO_NOISE
    now = time.time()
    catalogue = generate_catalogue(n_stories=120, seed=1, now=now)
    engine = RecommenderEngine(catalogue)
    rng = random.Random(42)
    themes = set(THEME_TAGS)

    if robustness:
        personas_to_run = [(p, 1) for p in ROBUSTNESS_PERSONAS]
    else:
        personas_to_run = [(p, USERS_PER_PERSONA) for p in PERSONAS]

    all_users = []
    for persona, n_users in personas_to_run:
        for i in range(n_users):
            user_id = f"{persona.name}-{i}"
            rounds, final_affinity = collect_journey(
                engine, user_id, persona, rng, now, N_ROUNDS, noise=noise
            )
            all_users.append({
                "user_id": user_id,
                "persona": persona.name,
                "persona_desc": persona.description,
                "rounds": rounds,
                "final_affinity": {k: round(v, 3) for k, v in final_affinity.items()},
                "true_theme_prefs": {
                    tag: persona.theme_weights.get(tag, 0.3)
                    for tag in themes
                    if persona.theme_weights.get(tag, 0.3) != 0.3
                },
            })

    if robustness:
        suffix = "_robustness"
    elif with_noise:
        suffix = "_noise"
    else:
        suffix = ""
    path = f"simulation/journeys_report{suffix}.html"
    with open(path, "w") as f:
        f.write(_html(all_users, with_noise, len(catalogue)))
    print(f"Wrote {path}")


if __name__ == "__main__":
    with_noise = "--noise" in sys.argv
    robustness = "--robustness" in sys.argv
    main(with_noise=with_noise, robustness=robustness)
