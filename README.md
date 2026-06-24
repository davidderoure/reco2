# ORIGIN Recommender

Python recommender back end for ORIGIN, a research project developing a
co-designed online intervention (story-based app) to reduce depression and
anxiety in young people aged 16-24, evaluated via a clinical trial.

Communicates with the C# app server over gRPC: this service is the gRPC
*server* for `RecommenderService` (event notifications + `GetRecommendations`)
and a gRPC *client* of `StoryService` (catalogue, user-model persistence).

This is a first-pass implementation built directly against
[`proto/recommender.proto`](proto/recommender.proto) — a fork of the
[mock recommender](https://bitbucket.org/imagineear1/mockrecommender)'s
gRPC plumbing, with real recommendation logic in place of the mock's
hardcoded responses. The older `~/code/recommender`/`mock_server.py`
prototype (a separate, earlier experiment) was deliberately not reused.

## Setup

```bash
pip install -r requirements.txt

# Regenerate gRPC stubs from the proto (output goes to generated/, gitignored)
python3 -m grpc_tools.protoc -Iproto --python_out=generated \
    --grpc_python_out=generated --pyi_out=generated proto/recommender.proto
```

## Running the server

Against the real C# `StoryService`:

```bash
STORY_SERVICE_ADDR=story-service:50052 GRPC_SERVER_PORT=50051 python3 server.py
```

The catalogue (stories and their tags) is re-fetched periodically in the
background, not just at startup — tags can change during the trial
(including user-suggested free-text tags), so a long-running process
needs to pick that up without a restart. Interval defaults to 5 minutes
(`CATALOGUE_REFRESH_SECONDS`), which is deliberately frequent for
**testing** (fast feedback when stories/tags change). Expect to turn
this down once the trial is live and the catalogue is more stable —
e.g. hourly (`CATALOGUE_REFRESH_SECONDS=3600`) — to reduce load on
`StoryService`; no code change needed, just the env var. A failed
refresh (e.g. transient `StoryService` outage) is logged and retried
next interval — it doesn't crash the server or wipe the existing
catalogue.

**Offline / standalone mode** (no C# backend available yet) — uses an
in-memory `FakeStoryClient` seeded with 12 mock stories (no tags, matching
the early-testing setup):

```bash
RECOMMENDER_OFFLINE=1 python3 server.py
```

In offline mode, user models are kept in memory only and lost on restart —
there's no real persistence, just enough to exercise the RPCs.

## Testing

```bash
pytest                          # unit tests (engine logic) + integration tests (real gRPC server, faked StoryService)
python3 -m simulation.simulate  # synthetic population/catalogue, response-time percentiles, recommender-vs-random effectiveness
```

The simulation is a sanity check against synthetic ground truth, not a
substitute for testing against real trial data — it confirms the latency
budget is met and that the logic isn't actively broken, not that the
recommendations are clinically useful.

## Design decisions

The spec left a number of points open; all were resolved at design
meetings and are implemented with the rationale in code comments
(search `Open question #` in `recommender/engine.py`):

1. **New-user cold start**: random draw from the most popular stories so
   far, not strictly the single most popular.
2. **Tag avoidance ("selecting away")**: de-prioritize, don't promote.
   Direction confirmed; no dedicated avoidance-detector built yet.
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
   "seen", doesn't affect affinity).
9. **"New" stories during the trial**: the `topical` slot prioritizes
   stories added since *this user's own* last `GetRecommendations` call
   (`UserModel.last_recommendation_request_at`), not just globally
   newest — a story can be new for one user and old news for another.
   Falls back to global newest-first once nothing is new since their
   last visit.

## Known limitations / things to check during review

- **No tags yet in test data**: with an untagged catalogue, content-based
  scoring and wildcard's serendipity weighting both degenerate (there's
  nothing for them to score on) — `topical`/`wildcard`/re-engagement still
  work and the recommendation mix is still valid, just less personalised
  until tags exist. See `test_user_with_history_is_not_stuck_in_cold_start_when_catalogue_has_no_tags`
  in `tests/test_engine.py`.
- **Persistence is synchronous and per-event** (`server.py`'s `_persist`)
  — simple and correct, but chatty; worth revisiting once real traffic
  volume is known. The 500ms budget applies to `GetRecommendations`
  specifically, not to event handlers.
- **Collaborative filtering is weak early in the trial** — it needs
  multiple users to have rated overlapping stories, which won't be true
  on day one. This is expected, not a bug, but worth knowing when
  interpreting early test results.
- **`StoryMessage.subtitle` field meaning is unconfirmed** — the proto
  comment says "display name of the story's author," which conflicts with
  the field name. Not used by any logic here, but don't build on it
  without checking with the back-end dev first.
- **`LoadUserModel(user_ids=[])` ("load all") has no pagination** — at
  trial scale this risks exceeding gRPC's 4MB default message size limit
  (measured: ~18 stories read per user, on average, across 1500 users is
  enough to cross it). Mitigated for now with gzip compression on the
  channel (`StoryClient`/`server.py` both set `compression=grpc.Compression.Gzip`
  — measured 5-20x reduction on real user-model JSON), which gives a lot
  of headroom, but doesn't remove the ceiling. If the trial or catalogue
  grows further, the proper fix is a proto change — e.g. a server-streaming
  `LoadUserModel` response instead of one bulk message — which needs
  coordinating with the back-end dev since it changes the `StoryService`
  contract they implement.
