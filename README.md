# ORIGIN Recommender

Python recommender back end for ORIGIN, a research project developing a
co-designed online intervention (story-based app) to reduce depression and
anxiety in young people aged 16-24, evaluated via a clinical trial.

Communicates with the C# app server over gRPC: this service is the gRPC
*server* for `RecommenderService` (event notifications + `GetRecommendations`)
and a gRPC *client* of `StoryService` (catalogue, user-model persistence).

Built directly against [`proto/recommender.proto`](proto/recommender.proto),
which is kept in sync with the
[mock recommender](https://bitbucket.org/imagineear1/mockrecommender) as the
shared source of truth.

## Setup

```bash
pip install -r requirements.txt

# Regenerate gRPC stubs from the proto (output goes to generated/, gitignored)
python3 -m grpc_tools.protoc -Iproto --python_out=generated \
    --grpc_python_out=generated --pyi_out=generated proto/recommender.proto
```

## Running the server

Via Docker (recommended for integration):

```bash
docker compose up --build
```

Against the real C# `StoryService` directly:

```bash
STORY_SERVICE_ADDR=story-service:50052 GRPC_SERVER_PORT=50051 python3 server.py
```

The catalogue (stories and their tags) is re-fetched periodically in the
background — tags can change during the trial (including user-suggested
free-text tags), so a long-running process needs to pick that up without a
restart. The refresh interval is tuned via the default in each code drop
(`CATALOGUE_REFRESH_SECONDS` in `server.py`, currently 3600s / 1 hour) rather
than by env var on the deployed side. A failed refresh (e.g. transient
`StoryService` outage) is logged and retried next interval — it doesn't crash
the server or wipe the existing catalogue.

**Offline / standalone mode** (no C# backend available) — uses an in-memory
`FakeStoryClient` seeded with 12 mock stories (no tags):

```bash
RECOMMENDER_OFFLINE=1 python3 server.py
```

In offline mode, user models are kept in memory only and lost on restart —
there's no real persistence, just enough to exercise the RPCs.

## Testing

```bash
pytest                              # unit + integration + concurrency + restart tests (38 tests)
python3 -m simulation.simulate      # synthetic population, response-time percentiles, recommender-vs-random effectiveness
python3 -m simulation.journeys      # per-user round-by-round transcripts → simulation/journeys_output.md
python3 -m simulation.journeys --noise       # same with 15% engagement interruption noise
python3 -m simulation.journeys --robustness  # extreme-behaviour personas → simulation/journeys_robustness.md
python3 -m simulation.report                 # HTML report for sharing → simulation/journeys_report.html
python3 -m simulation.report --noise
python3 -m simulation.report --robustness    # → simulation/journeys_report_robustness.html
```

The simulation uses the definitive ORIGIN tag vocabulary (4 format + 47 theme
tags) and 6 named personas. Connectedness trends upward across rounds for 8/12
synthetic users in the clean baseline — confirmed after each code drop.

The `--robustness` flag runs 5 extreme-behaviour personas (always-first,
always-random, fixed scores 1/5/9) to verify the recommender behaves sensibly
under degenerate input. It is a sanity check against synthetic ground truth —
not a substitute for testing against real trial data.

## Design decisions

The spec left a number of points open; all were resolved at design
meetings and are implemented with the rationale in code comments
(search `Open question #` in `recommender/engine.py`):

1. **New-user cold start**: random draw from the most popular stories so
   far, not strictly the single most popular.
2. **Tag avoidance ("selecting away")**: de-prioritize, don't promote.
   Direction confirmed; dedicated avoidance-detection mechanism pending
   count/recency parameter decisions.
3. **Time decay**: exponential decay on event weight (14-day half-life).
4. **Catalogue exhaustion**: re-engagement on prior high-connectedness
   stories ramps in as the unread pool shrinks, not only once exhausted.
5. **Multi-question signal**: only `scores[0]` (compulsory) drives logic;
   `scores[1:4]` are stored but unused, ready for future use.
6. **Repeats**: allowed, but every batch of 6 must include at least 2
   stories never recommended to that user before. Stories from the last
   `RECENT_BATCHES_TO_EXCLUDE` (N=2) batches are excluded from fresh
   recommendations; the window relaxes automatically when the catalogue
   is too small to honour it.
7. **Bookmarks**: do not affect tag-affinity (capability retained but
   disabled — see `BOOKMARKS_AFFECT_AFFINITY` in `engine.py`).
8. **Abandoned engagement**: a story opened but closed without answering
   the question doesn't count as "seen" and doesn't affect affinity.
   `UserEngagementStoryProgress` updates `viewed_pct` in case a stop/abort
   event is lost. Early exit and interruptions (stop with no score) preserve
   the previous batch on the next visit — the app holds the user's place.
   "Get me out of here" (abort) is treated differently: it bypasses
   preservation and generates a fresh set, as it is a deliberate signal that
   the user wants something different. Tag-based avoidance logic (longer-term
   consequence of abort) is pending count/recency parameter decisions.
9. **"New" stories during the trial**: the `topical` slot prioritizes
   stories added since *this user's own* last `GetRecommendations` call
   (`UserModel.last_recommendation_request_at`), not just globally
   newest — a story can be new for one user and old news for another.
10. **Content-based diversity**: candidates are scored by tag-affinity match
    and the top `POOL_SIZE` (N=6) are shuffled uniformly before the engine
    picks from them. This prevents a tight positive-feedback loop (high score
    → tag reinforced → same story always first) while keeping recommendations
    preference-led. Pool size is the tuning knob: smaller = faster
    personalisation, larger = more variety.

## Pending / coming in a future release

- **Tag avoidance / "get me out of here"**: `UserEngagementStoryAbort`
  is wired and the `aborted` flag is captured on the user model, but the
  avoidance logic is pending count/recency parameter decisions.
- **Pull-to-refresh**: a `force_refresh` flag on `GetRecommendationsRequest`
  would bypass batch preservation; requires a proto change coordinated with
  the back-end dev.

## Known limitations / things to check during review

- **Collaborative filtering is weak early in the trial** — it needs
  multiple users to have rated overlapping stories, which won't be true
  on day one. This is expected, not a bug.
- **Tag staleness on catalogue refresh**: `tag_affinity` is recomputed
  on a user's next triggering event, not proactively when the catalogue
  refreshes. There is a short window where a user's affinity reflects a
  story's old tags — self-correcting, accepted as a deliberate tradeoff.
- **`StoryMessage.subtitle`** — the proto comment says "display name of
  the story's author." Not used by any logic here.
- **`LoadUserModel(user_ids=[])` ("load all") has no pagination** — at
  trial scale this risks exceeding gRPC's 4MB default message size limit.
  Measured at ~11 KB/user after 60 rounds of engagement; projected 1,500
  users = ~16 MB raw, ~200–800 KB gzipped. Mitigated with gzip compression
  on the channel (`StoryClient`/`server.py` both set
  `compression=grpc.Compression.Gzip`). If the trial grows further, the
  proper fix is a proto change (server-streaming `LoadUserModel`) coordinated
  with the back-end dev. A budget regression test is in
  `tests/test_concurrent.py::test_user_model_json_size_within_grpc_budget`.
- **Persistence is synchronous and per-event** (`server.py`'s `_persist`)
  — simple and correct, but chatty; worth revisiting once real traffic
  volume is known.
