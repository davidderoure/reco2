"""Tests for EmbeddingIndex and its integration with ContentBasedStrategy."""

from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import pytest

from recommender.catalogue import Catalogue
from recommender.embeddings import EmbeddingIndex
from recommender.engine import RecommenderEngine
from recommender.models import CONTENT_BASED, Story


def make_catalogue(stories: list[tuple[str, list[str]]]) -> Catalogue:
    cat = Catalogue()
    cat.load([Story(story_id=sid, title=sid, tags=tags, created_at=float(i)) for i, (sid, tags) in enumerate(stories)])
    return cat


# ---------------------------------------------------------------------------
# Small synthetic embedding matrix (3-dimensional for readability)
# ---------------------------------------------------------------------------

# Three story clusters: identity, history, sport — deliberately orthogonal.
RAW_EMBEDDINGS = {
    "identity-1": [1.0, 0.0, 0.0],
    "identity-2": [0.9, 0.1, 0.0],
    "history-1":  [0.0, 1.0, 0.0],
    "history-2":  [0.1, 0.9, 0.0],
    "sport-1":    [0.0, 0.0, 1.0],
    "sport-2":    [0.1, 0.0, 0.9],
}


def make_index(raw: dict = RAW_EMBEDDINGS) -> EmbeddingIndex:
    return EmbeddingIndex(raw)


# ---------------------------------------------------------------------------
# EmbeddingIndex unit tests
# ---------------------------------------------------------------------------

class TestEmbeddingIndex:
    def test_similarity_same_story(self):
        idx = make_index()
        assert idx.similarity("identity-1", "identity-1") == pytest.approx(1.0, abs=1e-6)

    def test_similarity_orthogonal_stories(self):
        idx = make_index()
        assert idx.similarity("identity-1", "history-1") == pytest.approx(0.0, abs=1e-6)

    def test_similarity_close_stories(self):
        idx = make_index()
        close = idx.similarity("identity-1", "identity-2")
        far = idx.similarity("identity-1", "sport-1")
        assert close > far

    def test_similarity_unknown_story_returns_zero(self):
        idx = make_index()
        assert idx.similarity("identity-1", "unknown-xyz") == 0.0
        assert idx.similarity("unknown-xyz", "unknown-abc") == 0.0

    def test_mean_similarity_single_reference(self):
        idx = make_index()
        score = idx.mean_similarity("identity-2", ["identity-1"])
        assert score > 0.95  # very close

    def test_mean_similarity_empty_references(self):
        idx = make_index()
        assert idx.mean_similarity("identity-1", []) == 0.0

    def test_nearest_returns_closest(self):
        idx = make_index()
        results = idx.nearest(["identity-1"], n=2, excluded={"identity-1"})
        assert results[0] == "identity-2"

    def test_nearest_excludes_specified_ids(self):
        idx = make_index()
        results = idx.nearest(["identity-1"], n=10, excluded={"identity-1", "identity-2"})
        assert "identity-1" not in results
        assert "identity-2" not in results

    def test_nearest_empty_references_returns_empty(self):
        idx = make_index()
        assert idx.nearest([], n=3, excluded=set()) == []

    def test_story_ids(self):
        idx = make_index()
        assert idx.story_ids() == set(RAW_EMBEDDINGS.keys())

    def test_load_from_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(RAW_EMBEDDINGS, f)
            path = f.name
        idx = EmbeddingIndex.load(path)
        assert idx.similarity("identity-1", "history-1") == pytest.approx(0.0, abs=1e-6)

    def test_normalisation_handles_unit_vectors(self):
        # Pre-normalised input should remain valid.
        idx = EmbeddingIndex({"a": [1.0, 0.0], "b": [0.0, 1.0]})
        assert idx.similarity("a", "b") == pytest.approx(0.0, abs=1e-6)
        assert idx.similarity("a", "a") == pytest.approx(1.0, abs=1e-6)

    def test_normalisation_handles_unnormalised_vectors(self):
        # Vectors with different magnitudes should still give correct cosine.
        idx = EmbeddingIndex({"a": [2.0, 0.0], "b": [5.0, 0.0]})
        assert idx.similarity("a", "b") == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Integration: engine with embeddings enabled
# ---------------------------------------------------------------------------

class TestEngineWithEmbeddings:
    def _make_engine_with_embeddings(self):
        """Engine with 6 real stories matching the embedding clusters."""
        stories = [
            ("identity-1", ["LGBTQ+ Experience", "Hiding Self"]),
            ("identity-2", ["Gender", "Hiding Self"]),
            ("history-1",  ["WWII", "Heritage"]),
            ("history-2",  ["Archaeology", "Heritage"]),
            ("sport-1",    ["Sports", "Adventure"]),
            ("sport-2",    ["Sports", "Being Judged"]),
        ]
        catalogue = make_catalogue(stories)
        engine = RecommenderEngine(catalogue, rng=__import__("random").Random(42))
        engine.load_embeddings(make_index())
        return engine

    def test_embeddings_loaded_does_not_crash(self):
        engine = self._make_engine_with_embeddings()
        # Newly registered user — cold start, embeddings don't help yet.
        recs = engine.get_recommendations("user-1")
        assert len(recs) == 6

    def test_embeddings_bias_content_based_toward_cluster(self):
        engine = self._make_engine_with_embeddings()
        user_id = "user-embed"
        ts = 1_000_000.0

        # User engages positively with identity stories only.
        for story_id in ("identity-1", "identity-2"):
            engine.record_answered_question(user_id, story_id, [5, None, None, None], timestamp=ts)
            engine.record_engagement_stop(user_id, story_id, progress_percentage=100.0, timestamp=ts)
            ts += 86400

        user = engine.get_or_create_user(user_id)
        content_based_strategy = engine.strategies[CONTENT_BASED]

        # Get candidates — both identity stories are excluded (already seen),
        # so the remaining candidates should favour history over sport.
        candidates = content_based_strategy.candidates(
            user,
            engine.catalogue,
            engine.population,
            excluded={"identity-1", "identity-2"},
        )

        # history stories should appear before sport stories.
        pos = {sid: candidates.index(sid) for sid in ("history-1", "history-2", "sport-1", "sport-2") if sid in candidates}
        history_positions = [pos[sid] for sid in ("history-1", "history-2") if sid in pos]
        sport_positions = [pos[sid] for sid in ("sport-1", "sport-2") if sid in pos]
        # At least one history story should rank ahead of at least one sport story.
        assert min(history_positions) < max(sport_positions)

    def test_load_embeddings_replaces_strategy(self):
        """load_embeddings() should not double-load or corrupt the strategy."""
        stories = [("s1", ["Tag"]), ("s2", ["Tag"])]
        catalogue = make_catalogue(stories)
        engine = RecommenderEngine(catalogue)
        idx = EmbeddingIndex({"s1": [1.0, 0.0], "s2": [0.0, 1.0]})
        engine.load_embeddings(idx)
        engine.load_embeddings(idx)  # idempotent
        # Should not raise; small catalogue gives fewer than 6 recs.
        recs = engine.get_recommendations("u1")
        assert len(recs) == len(stories)
        assert engine.strategies[CONTENT_BASED]._embeddings is idx

    def test_embeddings_off_by_default(self):
        """Without load_embeddings(), ContentBasedStrategy has no index."""
        stories = [("s1", ["Tag"])]
        catalogue = make_catalogue(stories)
        engine = RecommenderEngine(catalogue)
        strategy = engine.strategies[CONTENT_BASED]
        assert strategy._embeddings is None
