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
pytest                          # unit + concurrency tests
python3 -m simulation.simulate  # synthetic population, response-time percentiles, recommender-vs-random effectiveness
python3 -m simulation.journeys  # per-user round-by-round transcripts → simulation/journeys_output.md
python3 -m simulation.journeys --noise  # same with 15% engagement interruption noise
python3 -m simulation.report    # HTML report for sharing → simulation/journeys_report.html
python3 -m simulation.report --noise
```

The simulation uses the definitive ORIGIN tag vocabulary (4 format + 47 theme
tags) and 6 named personas. It is a sanity check against synthetic ground
truth — useful for catching logic bugs and checking response-time budgets, not
a substitute for testing against real trial data.

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
   stories never recommended to that user before.
7. **Bookmarks**: do not affect tag-affinity (capability retained but
   disabled — see `BOOKMARKS_AFFECT_AFFINITY` in `engine.py`).
8. **Abandoned engagement**: a story opened but closed without answering
   the question doesn't count for anything (doesn't mark the story as
   "seen", doesn't affect affinity). `UserEngagementStoryProgress` events
   update the last-known position (`viewed_pct`) in case a stop/abort
   event is lost.
9. **"New" stories during the trial**: the `topical` slot prioritizes
   stories added since *this user's own* last `GetRecommendations` call
   (`UserModel.last_recommendation_request_at`), not just globally
   newest — a story can be new for one user and old news for another.

## Pending / coming in next release

- **Repeat recommendation window**: last-N-batches exclusion to prevent
  stories cycling back too quickly across successive calls.
- **Batch preservation on early exit**: if the user's last engagement
  produced no connectedness answer, return the previous batch rather than
  reshuffling.
- **Tag avoidance / "get me out of here"**: `UserEngagementStoryAbort`
  is wired and the `aborted` flag is captured on the user model, but the
  avoidance logic is pending count/recency parameter decisions.

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
  Mitigated with gzip compression on the channel (`StoryClient`/`server.py`
  both set `compression=grpc.Compression.Gzip` — 5-20x reduction on
  realistic user-model JSON). If the trial or catalogue grows further,
  the proper fix is a proto change (server-streaming `LoadUserModel`)
  coordinated with the back-end dev.
- **Persistence is synchronous and per-event** (`server.py`'s `_persist`)
  — simple and correct, but chatty; worth revisiting once real traffic
  volume is known.
