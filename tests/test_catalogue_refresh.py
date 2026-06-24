import threading
import time

from recommender.catalogue import Catalogue
from recommender.engine import RecommenderEngine
from recommender.models import Story
from server import refresh_catalogue_loop


class GrowingStoryClient:
    """Simulates the catalogue gaining a story between refreshes — e.g.
    tags/stories changing during the trial.
    """

    def fetch_catalogue(self):
        catalogue = Catalogue()
        catalogue.load([
            Story(story_id="s1", title="S1", tags=["old"]),
            Story(story_id="s2", title="S2", tags=["new"]),
        ])
        return catalogue


class FlakyStoryClient:
    def fetch_catalogue(self):
        raise ConnectionError("StoryService unreachable")


def test_refresh_picks_up_catalogue_changes():
    catalogue = Catalogue()
    catalogue.load([Story(story_id="s1", title="S1", tags=["old"])])
    engine = RecommenderEngine(catalogue)

    t = threading.Thread(
        target=refresh_catalogue_loop,
        args=(engine, GrowingStoryClient(), 0.05),
        daemon=True,
    )
    t.start()
    time.sleep(0.2)

    assert len(engine.catalogue) == 2
    assert engine.catalogue.get("s2") is not None


def test_refresh_survives_storyservice_outage():
    catalogue = Catalogue()
    catalogue.load([Story(story_id="s1", title="S1", tags=["old"])])
    engine = RecommenderEngine(catalogue)

    t = threading.Thread(
        target=refresh_catalogue_loop,
        args=(engine, FlakyStoryClient(), 0.05),
        daemon=True,
    )
    t.start()
    time.sleep(0.2)

    assert t.is_alive()  # didn't crash on a failed fetch
    assert len(engine.catalogue) == 1  # catalogue unchanged, not wiped
