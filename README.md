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
pytest                              # unit + integration + concurrency + restart tests (75 tests)
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

The `--robustness` flag runs 6 extreme-behaviour personas (always-first,
always-random, fixed scores 1/5/9, consistent-aborter) to verify the recommender
behaves sensibly under degenerate input. It is a sanity check against synthetic
ground truth — not a substitute for testing against real trial data.

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
11. **Exploration widening for sustained high engagement**: once a user has
    scored at least `HIGH_ENGAGEMENT_MIN_ROUNDS` (N=5) rounds with a mean Q1
    score at or above `HIGH_ENGAGEMENT_SCORE_THRESHOLD` (7/9), one
    content-based slot is shifted to wildcard. This widens exploration as the
    user's affinity profile matures, preventing it from narrowing into an
    ever-tighter feedback loop. Both thresholds are named constants, tunable
    in response to early trial feedback.

## Trial API client

A separate client in `trial/` fetches engagement data from the Trial API for
ad hoc analysis and daily monitoring by the research team.

Credentials can be set via environment variables or a `.env` file in the repo
root (gitignored — obtain values from the back-end dev):

```
TRIAL_CLIENT_ID=trial-api-m2m
TRIAL_CLIENT_SECRET=...
```

Authentication uses OAuth2 client credentials (M2M); tokens are refreshed
automatically before expiry.

```bash
# Fetch raw data — last 7 days to stdout
python -m trial.fetch --days 7

# Date range — CSV to file
python -m trial.fetch --from 2025-01-01 --to 2025-01-31 --format csv --output jan.csv

# Population-level analysis report
python -m trial.analyse --days 30
python -m trial.analyse --days 30 --output report.txt

# Per-participant timeline (ASCII, TRE-compatible)
python -m trial.timeline --days 30
python -m trial.timeline --days 30 --participants AB12-CD34
python -m trial.timeline --days 30 --html timeline.html   # richer local view
```

### OriginId lists

The Trial API requires an explicit list of OriginIds — it no longer accepts
requests without one (data protection). The tools load these from local files
in `ids/` (gitignored — obtain from the study team):

| File          | user_type | Group             |
|---------------|-----------|-------------------|
| `ids/ids_0.txt` | 0       | Trial participants |
| `ids/ids_1.txt` | 1       | Test accounts      |
| `ids/ids_2.txt` | 2       | Usability trial    |

Each file contains one OriginId per line (`XXXX-XXXX`); blank lines and
`#` comments are ignored. The tools default to `ids/ids_2.txt` during the
current usability trial phase (`DEFAULT_IDS_FILE` in `trial/client.py`).

To use a different file:
```bash
python -m trial.analyse --days 30 --ids-file ids/ids_1.txt
```

To look up a single participant without a file:
```bash
python -m trial.timeline --days 30 --participants AB12-CD34
```

The analysis report covers: recommender type distribution, story popularity,
high-abort stories, per-participant Q1 trend, personalisation pattern, and
potential state-loss detection (reversion to cold-start distinguished from
natural lapse after >30 days away). The timeline tool is designed for terminal
use in a TRE (Trusted Research Environment) with an optional `--html` output
for local review before deployment there.

## User groups (WOB-433)

Every gRPC request carries a `user_type` field: `0 = Trial participant`,
`1 = Test account`. The recommender stores this on each `UserModel` and uses
it to scope collaborative filtering and cold-start cohort ranking to the same
group, so test-account ratings cannot influence trial recommendations.

## Population reload

The recommender's in-memory population is loaded from C# at startup and
updated incrementally as events arrive. Accounts deleted on the C# side are
not automatically evicted — a reload is needed to drop them from memory.

Two mechanisms are provided:

- **Periodic reload** (default every 24 h): re-fetches all user models from C#
  and atomically replaces the population. Controlled by
  `POPULATION_RELOAD_SECONDS` (set to `0` to disable; the SIGUSR1 trigger
  still works).
- **SIGUSR1**: triggers an immediate reload without waiting for the next
  scheduled interval. In a container:
  ```bash
  docker kill --signal=USR1 <container_name>
  ```

A failed reload (e.g. transient StoryService outage) is logged and retried at
the next interval — the existing population is left untouched.

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
- **`GetRecommendations` persist is async; Stop and Abort do not persist**
  (`server.py`): `SaveUserModel` for `GetRecommendations` fires on a bounded
  background pool (`ThreadPoolExecutor(max_workers=10)`) after the response is
  returned. `UserAnsweredQuestion` and bookmarks remain synchronous.
  `UserEngagementStoryStop` and `UserEngagementStoryAbort` do not persist
  (state is saved on the next `GetRecommendations`). Switching all event
  handlers to async (via `grpc.aio`) would reduce thread-pool pressure further
  under burst load — worth revisiting if the trial grows.
- **History trimming is on** (`MAX_HISTORY_SIZE = 70` in `engine.py`):
  calibrated for the 200-story catalogue expected at go-live (confirmed by load
  testing). Oldest entries are evicted after each scored answer; tag affinity is
  recomputed from survivors so no signal is lost. Adjust the constant if the
  catalogue or engagement patterns change significantly.
