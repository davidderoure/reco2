"""End-to-end smoke test: spin up the real gRPC server (server.py) against
an in-process channel, with a fake StoryClient standing in for the C#
backend, and drive it through a client stub. Confirms the proto<->engine
wiring actually works, not just the engine in isolation.
"""

from __future__ import annotations

import os
import sys
from concurrent import futures

import grpc
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "generated"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import recommender_pb2
import recommender_pb2_grpc

from recommender.catalogue import Catalogue
from recommender.engine import RecommenderEngine
from recommender.models import Story
from server import RecommenderServicer


class FakeStoryClient:
    """Stands in for the C# StoryService during tests."""

    def __init__(self) -> None:
        self.saved_models: dict[str, str] = {}

    def save_user_model(self, user) -> None:
        self.saved_models[user.user_id] = user.to_json()


@pytest.fixture
def grpc_channel():
    catalogue = Catalogue()
    catalogue.load([
        Story(story_id=f"s{i}", title=f"Story {i}", tags=["a", "b"] if i % 2 == 0 else ["c", "d"])
        for i in range(20)
    ])
    engine = RecommenderEngine(catalogue)
    fake_story_client = FakeStoryClient()

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    recommender_pb2_grpc.add_RecommenderServiceServicer_to_server(
        RecommenderServicer(engine, fake_story_client), server
    )
    port = server.add_insecure_port("[::]:0")
    server.start()

    channel = grpc.insecure_channel(f"localhost:{port}")
    yield channel, fake_story_client, engine

    server.stop(None)


def test_get_recommendations_over_grpc_returns_six(grpc_channel):
    channel, _, _ = grpc_channel
    stub = recommender_pb2_grpc.RecommenderServiceStub(channel)

    response = stub.GetRecommendations(
        recommender_pb2.GetRecommendationsRequest(user_id="u1")
    )
    assert len(response.recommendations) == 6
    ids = [r.story_id for r in response.recommendations]
    assert len(set(ids)) == 6


def test_answered_question_event_updates_model_and_persists(grpc_channel):
    channel, fake_story_client, engine = grpc_channel
    stub = recommender_pb2_grpc.RecommenderServiceStub(channel)

    stub.UserAnsweredQuestion(
        recommender_pb2.UserAnsweredQuestionRequest(
            user_id="u1", story_id="s0", scores=[8, 5, 5, 5]
        )
    )

    user = engine.population["u1"]
    assert user.story_history["s0"].connectedness is not None
    # event handler should have pushed the updated model to the story client
    assert "u1" in fake_story_client.saved_models


def test_bookmark_event_does_not_affect_tag_affinity(grpc_channel):
    channel, _, engine = grpc_channel
    stub = recommender_pb2_grpc.RecommenderServiceStub(channel)

    stub.UserBookmarkedStory(
        recommender_pb2.UserBookmarkedStoryRequest(user_id="u1", story_id="s0")
    )

    user = engine.population["u1"]
    assert "s0" in user.bookmarked_story_ids
    assert user.tag_affinity == {}  # decided #7: bookmarks alone don't affect affinity


def test_engagement_progress_event_is_accepted_as_noop(grpc_channel):
    channel, _, _ = grpc_channel
    stub = recommender_pb2_grpc.RecommenderServiceStub(channel)

    response = stub.UserEngagementStoryProgress(
        recommender_pb2.UserEngagementProgressRequest(
            user_id="u1", story_id="s0", progress_percentage=42.0
        )
    )
    assert response is not None
