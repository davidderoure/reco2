"""gRPC server for the ORIGIN recommender.

Forked from the mock recommender's server.py structure (same RPC surface,
same logging style) but every handler is wired into the real
RecommenderEngine instead of being a no-op / hardcoded response.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from concurrent import futures

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "generated"))

import grpc
import recommender_pb2
import recommender_pb2_grpc
from google.protobuf import empty_pb2

from recommender.catalogue import Catalogue
from recommender.engine import RecommenderEngine
from story_client import FakeStoryClient, StoryClient

GRPC_SERVER_PORT = os.getenv("GRPC_SERVER_PORT", "50051")

# Tags (and the catalogue generally) can change during the trial — new
# stories, new tags on existing stories, and now also user-suggested
# free-text tags. The catalogue was previously only fetched once at
# startup, so a long-running service would silently go stale. Refresh on
# this interval instead. 5 minutes is a starting guess, not a measured
# value — tags/stories don't need to propagate in real time, just
# regularly enough that "stale for the life of the process" isn't a risk.
CATALOGUE_REFRESH_SECONDS = int(os.getenv("CATALOGUE_REFRESH_SECONDS", "300"))


class RecommenderServicer(recommender_pb2_grpc.RecommenderServiceServicer):
    def __init__(self, engine: RecommenderEngine, story_client: StoryClient) -> None:
        self.engine = engine
        self.story_client = story_client

    def UserAnsweredQuestion(self, request, context):
        print(f"[EVENT] User {request.user_id} answered questions for story {request.story_id} "
              f"with scores {list(request.scores)}")
        self.engine.record_answered_question(
            request.user_id,
            request.story_id,
            list(request.scores),
            timestamp=_to_epoch(request.timestamp),
        )
        self._persist(request.user_id)
        return empty_pb2.Empty()

    def UserProvidedMood(self, request, context):
        print(f"[EVENT] User {request.user_id} provided mood score {request.mood_score} "
              f"(Story: {request.story_id})")
        self.engine.record_mood(
            request.user_id,
            request.mood_score,
            story_id=request.story_id,
            timestamp=_to_epoch(request.timestamp),
        )
        # No persistence: mood doesn't affect the user model (see engine.record_mood).
        return empty_pb2.Empty()

    def UserBookmarkedStory(self, request, context):
        print(f"[EVENT] User {request.user_id} bookmarked story {request.story_id}")
        self.engine.record_bookmark(
            request.user_id, request.story_id, timestamp=_to_epoch(request.timestamp)
        )
        self._persist(request.user_id)
        return empty_pb2.Empty()

    def UserUnbookmarkedStory(self, request, context):
        print(f"[EVENT] User {request.user_id} unbookmarked story {request.story_id}")
        self.engine.record_unbookmark(
            request.user_id, request.story_id, timestamp=_to_epoch(request.timestamp)
        )
        self._persist(request.user_id)
        return empty_pb2.Empty()

    def UserEngagementStoryStart(self, request, context):
        engagement_source = _engagement_type_name(request.engagement_type)
        print(f"[EVENT] User {request.user_id} started engagement with story {request.story_id} "
              f"(source: {engagement_source})")
        # Not used by any logic yet: starting an engagement carries no
        # signal until it's confirmed/stopped/scored.
        self.engine.get_or_create_user(request.user_id)
        return empty_pb2.Empty()

    def UserEngagementStoryConfirm(self, request, context):
        engagement_source = _engagement_type_name(request.engagement_type)
        print(f"[EVENT] User {request.user_id} confirmed engagement with story {request.story_id} "
              f"(source: {engagement_source})")
        self.engine.get_or_create_user(request.user_id)
        return empty_pb2.Empty()

    def UserEngagementStoryProgress(self, request, context):
        engagement_source = _engagement_type_name(getattr(request, "engagement_type", 0))
        print(f"[EVENT] User {request.user_id} progressed story {request.story_id} "
              f"to {request.progress_percentage} (source: {engagement_source})")
        self.engine.record_engagement_progress(
            request.user_id,
            request.story_id,
            request.progress_percentage,
            timestamp=_to_epoch(request.timestamp),
        )
        return empty_pb2.Empty()

    def UserEngagementStoryStop(self, request, context):
        engagement_source = _engagement_type_name(request.engagement_type)
        print(f"[EVENT] User {request.user_id} stopped engagement with story {request.story_id} "
              f"at {request.progress_percentage} (source: {engagement_source})")
        self.engine.record_engagement_stop(
            request.user_id,
            request.story_id,
            request.progress_percentage,
            timestamp=_to_epoch(request.timestamp),
        )
        self._persist(request.user_id)
        return empty_pb2.Empty()

    def GetRecommendations(self, request, context):
        recs = self.engine.get_recommendations(request.user_id, timestamp=_to_epoch(request.timestamp))
        recommendations = [
            recommender_pb2.RecommendationResult(story_id=story_id, recommender_type=rec_type)
            for story_id, rec_type in recs
        ]
        self._persist(request.user_id)
        return recommender_pb2.GetRecommendationsResponse(recommendations=recommendations)

    def _persist(self, user_id: str) -> None:
        """Push this user's updated model back to the C# StoryService.

        Persisting synchronously and per-event is simple and correct, but
        chatty — revisit (e.g. batch/async) once we see real trial traffic
        volume; the 500ms budget is on GetRecommendations specifically, and
        this call sits outside that one.
        """
        user = self.engine.get_or_create_user(user_id)
        self.story_client.save_user_model(user)


def _to_epoch(timestamp) -> float | None:
    if timestamp.seconds == 0 and timestamp.nanos == 0:
        return None
    return timestamp.seconds + timestamp.nanos / 1e9


def _engagement_type_name(engagement_type) -> str:
    names = {
        recommender_pb2.ENGAGEMENT_TYPE_UNSPECIFIED: "unspecified",
        recommender_pb2.ENGAGEMENT_TYPE_RECOMMENDATION_CONTENT_BASED: "content-based recommendation",
        recommender_pb2.ENGAGEMENT_TYPE_RECOMMENDATION_COLLABORATIVE: "collaborative recommendation",
        recommender_pb2.ENGAGEMENT_TYPE_RECOMMENDATION_TOPICAL: "topical recommendation",
        recommender_pb2.ENGAGEMENT_TYPE_RECOMMENDATION_WILDCARD: "wildcard recommendation",
        recommender_pb2.ENGAGEMENT_TYPE_RECENTS: "recents",
        recommender_pb2.ENGAGEMENT_TYPE_SAVED: "saved",
        recommender_pb2.ENGAGEMENT_TYPE_SEARCH: "search",
    }
    return names.get(engagement_type, f"unknown({engagement_type})")


def build_engine():
    if os.getenv("RECOMMENDER_OFFLINE", "").lower() in ("1", "true", "yes"):
        # No C# StoryService available — use an in-memory stand-in seeded
        # with mock stories, for local/standalone testing of this service.
        print("RECOMMENDER_OFFLINE=1: using FakeStoryClient with seeded mock stories (no tags)")
        story_client = FakeStoryClient()
    else:
        story_client = StoryClient()

    catalogue = story_client.fetch_catalogue()
    print(f"Loaded catalogue: {len(catalogue)} stories")

    engine = RecommenderEngine(catalogue)
    users = story_client.load_all_user_models()
    engine.load_population(users)
    print(f"Loaded {len(users)} persisted user models")

    return engine, story_client


def refresh_catalogue_loop(engine: RecommenderEngine, story_client, interval_seconds: int) -> None:
    while True:
        time.sleep(interval_seconds)
        try:
            fresh = story_client.fetch_catalogue()
            engine.catalogue.load(fresh.all_stories())
            print(f"Refreshed catalogue: {len(engine.catalogue)} stories")
        except Exception as exc:  # noqa: BLE001 - a transient StoryService
            # outage must not crash the whole recommender; just try again
            # next interval and keep serving with the catalogue we have.
            print(f"Catalogue refresh failed, will retry: {exc}")


def serve():
    engine, story_client = build_engine()

    # gzip compression to match StoryClient's channel — see story_client.py
    # for rationale. Negligible benefit for the small RecommenderService
    # responses today, but keeps both directions consistent.
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=10), compression=grpc.Compression.Gzip
    )
    recommender_pb2_grpc.add_RecommenderServiceServicer_to_server(
        RecommenderServicer(engine, story_client), server
    )

    server.add_insecure_port(f"[::]:{GRPC_SERVER_PORT}")
    print(f"ORIGIN Recommender Service started on port {GRPC_SERVER_PORT}...")
    server.start()

    refresh_thread = threading.Thread(
        target=refresh_catalogue_loop,
        args=(engine, story_client, CATALOGUE_REFRESH_SECONDS),
        daemon=True,
    )
    refresh_thread.start()

    try:
        while True:
            time.sleep(86400)
    except KeyboardInterrupt:
        server.stop(0)


if __name__ == "__main__":
    serve()
