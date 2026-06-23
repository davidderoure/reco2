"""Python gRPC client for the C# StoryService.

Used at startup to load the catalogue and persisted user models, and to
persist user model updates as they happen.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "generated"))

import grpc
import recommender_pb2
import recommender_pb2_grpc

from recommender.catalogue import Catalogue
from recommender.models import Story, UserModel

STORY_SERVICE_ADDR = os.getenv("STORY_SERVICE_ADDR", "story-service:50052")


class StoryClient:
    def __init__(self, addr: str | None = None) -> None:
        self.addr = addr or STORY_SERVICE_ADDR
        # gzip compression on the channel: user-model JSON blobs are highly
        # repetitive (story IDs, field names), so this buys a large amount
        # of headroom against gRPC's 4MB default message size limit —
        # measured ~5-20x reduction depending on how much history a user
        # has. Needs the C# side to also support/accept gzip for this to
        # take effect; falls back to uncompressed if it doesn't.
        self._channel = grpc.insecure_channel(self.addr, compression=grpc.Compression.Gzip)
        self._stub = recommender_pb2_grpc.StoryServiceStub(self._channel)

    def fetch_catalogue(self) -> Catalogue:
        response = self._stub.GetStoryCatalogue(recommender_pb2.GetStoryCatalogueRequest())
        stories = [
            Story(
                story_id=s.story_id,
                title=s.title,
                tags=list(s.tags),
                subtitle=s.subtitle,
                created_at=s.created_at.seconds + s.created_at.nanos / 1e9,
                updated_at=s.updated_at.seconds + s.updated_at.nanos / 1e9,
            )
            for s in response.stories
        ]
        catalogue = Catalogue()
        catalogue.load(stories)
        return catalogue

    def load_all_user_models(self) -> list[UserModel]:
        response = self._stub.LoadUserModel(recommender_pb2.LoadUserModelRequest(user_ids=[]))
        return [
            UserModel.from_json(user_id, blob)
            for user_id, blob in response.user_models.items()
        ]

    def save_user_model(self, user: UserModel) -> None:
        self._stub.SaveUserModel(
            recommender_pb2.SaveUserModelRequest(user_models={user.user_id: user.to_json()})
        )


def seed_mock_stories(n: int = 12) -> list[Story]:
    """A small built-in catalogue for offline/local testing, matching the
    "mock stories, no tags yet" scenario used before the real C# CMS is
    wired up. No tags, by design — see engine.py's handling of untagged
    catalogues.
    """
    now = time.time()
    return [
        Story(
            story_id=f"mock-{i}",
            title=f"Mock Story {i}",
            tags=[],
            created_at=now - (n - i) * 3600,
            updated_at=now - (n - i) * 3600,
        )
        for i in range(1, n + 1)
    ]


class FakeStoryClient:
    """Stands in for the C# StoryService when it isn't available yet.
    Used for local/offline testing (RECOMMENDER_OFFLINE=1) — keeps
    everything in memory, no network calls.
    """

    def __init__(self, stories: list[Story] | None = None) -> None:
        self._catalogue = Catalogue()
        self._catalogue.load(stories if stories is not None else seed_mock_stories())
        self._user_models: dict[str, str] = {}

    def fetch_catalogue(self) -> Catalogue:
        return self._catalogue

    def load_all_user_models(self) -> list[UserModel]:
        return [UserModel.from_json(uid, blob) for uid, blob in self._user_models.items()]

    def save_user_model(self, user: UserModel) -> None:
        self._user_models[user.user_id] = user.to_json()
