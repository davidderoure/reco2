"""Tests for population reload: deleted-account eviction and periodic loop."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

from recommender.catalogue import Catalogue
from recommender.engine import RecommenderEngine
from recommender.models import Story, UserModel
from server import population_reload_loop, reload_population


def _make_engine():
    stories = [Story(story_id=f"s{i}", title=f"Story {i}", tags=["a"]) for i in range(10)]
    catalogue = Catalogue()
    catalogue.load(stories)
    return RecommenderEngine(catalogue)


def _fake_story_client(users: list[UserModel]):
    client = MagicMock()
    client.load_all_user_models.return_value = users
    return client


def test_reload_replaces_population():
    engine = _make_engine()
    engine.get_or_create_user("u1")
    engine.get_or_create_user("u2")
    assert set(engine.population) == {"u1", "u2"}

    new_users = [UserModel(user_id="u1")]  # u2 deleted
    client = _fake_story_client(new_users)

    reload_population(engine, client)

    assert set(engine.population) == {"u1"}
    assert "u2" not in engine.population


def test_reload_adds_new_users():
    engine = _make_engine()
    new_users = [UserModel(user_id="u1"), UserModel(user_id="u3")]
    client = _fake_story_client(new_users)

    reload_population(engine, client)

    assert set(engine.population) == {"u1", "u3"}


def test_reload_logs_dropped_count(capsys):
    engine = _make_engine()
    engine.get_or_create_user("u1")
    engine.get_or_create_user("u2")
    engine.get_or_create_user("u3")
    client = _fake_story_client([UserModel(user_id="u1")])

    reload_population(engine, client)

    out = capsys.readouterr().out
    assert "2 dropped" in out


def test_reload_handles_story_client_error(capsys):
    engine = _make_engine()
    engine.get_or_create_user("u1")
    client = MagicMock()
    client.load_all_user_models.side_effect = RuntimeError("StoryService unreachable")

    reload_population(engine, client)  # must not raise

    # Population unchanged after a failed reload
    assert "u1" in engine.population
    out = capsys.readouterr().out
    assert "failed" in out.lower()


def test_reload_is_atomic_under_concurrent_events():
    """A concurrent get_or_create_user must not deadlock or corrupt state."""
    engine = _make_engine()
    for i in range(50):
        engine.get_or_create_user(f"u{i}")

    new_users = [UserModel(user_id=f"u{i}") for i in range(25)]  # drop half
    client = _fake_story_client(new_users)
    errors = []

    def write_loop():
        for i in range(200):
            try:
                engine.get_or_create_user(f"concurrent-{i}")
            except Exception as exc:
                errors.append(exc)

    writer = threading.Thread(target=write_loop)
    writer.start()
    reload_population(engine, client)
    writer.join()

    assert not errors


def test_population_reload_loop_fires_periodically():
    engine = _make_engine()
    engine.get_or_create_user("u1")
    engine.get_or_create_user("u2")

    call_count = [0]
    original_reload = __import__("server").reload_population

    def counting_reload(eng, cli):
        call_count[0] += 1
        original_reload(eng, cli)

    import server
    original = server.reload_population
    server.reload_population = counting_reload

    client = _fake_story_client([UserModel(user_id="u1")])
    t = threading.Thread(
        target=population_reload_loop,
        args=(engine, client, 0.05),  # 50ms interval for test speed
        daemon=True,
    )
    t.start()
    time.sleep(0.18)
    server.reload_population = original

    assert call_count[0] >= 2


def test_population_reload_loop_disabled_when_zero():
    engine = _make_engine()
    engine.get_or_create_user("u1")
    client = _fake_story_client([])  # would wipe population if called

    t = threading.Thread(
        target=population_reload_loop,
        args=(engine, client, 0),  # disabled
        daemon=True,
    )
    t.start()
    time.sleep(0.05)

    # Population unchanged — loop exited immediately
    assert "u1" in engine.population
