"""Load the real ORIGIN story catalogue from a JSON file.

The JSON format matches what the Trial API / StoryService returns:
  [{"story_id": "...", "title": "...", "tags": [...], "created_at": "...", ...}, ...]

Usage:
    from simulation.real_catalogue import load_real_catalogue
    catalogue = load_real_catalogue("/path/to/stories.json")
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from recommender.catalogue import Catalogue
from recommender.models import Story

DEFAULT_PATH = Path(__file__).parent.parent / "stories.json"

FORMAT_TAGS = {"Written", "Audio", "Visual", "Video"}


def load_real_catalogue(path: str | Path | None = None) -> Catalogue:
    """Load stories from a JSON file into a Catalogue.

    Args:
        path: Path to the stories JSON file. Defaults to stories.json in the
              repo root (convenient for local testing; not committed).
    """
    path = Path(path) if path else DEFAULT_PATH
    with open(path) as f:
        data = json.load(f)

    stories = []
    for d in data:
        created_at = _parse_epoch(d.get("created_at", ""))
        stories.append(Story(
            story_id=d["story_id"],
            title=d.get("title", ""),
            tags=d.get("tags", []),
            created_at=created_at,
        ))

    catalogue = Catalogue()
    catalogue.load(stories)
    return catalogue


def real_tag_summary(catalogue: Catalogue) -> dict:
    """Return tag frequency counts, split into format and theme tags."""
    format_counts: dict[str, int] = {}
    theme_counts: dict[str, int] = {}
    for story in catalogue.all_stories():
        for tag in story.tags:
            if tag in FORMAT_TAGS:
                format_counts[tag] = format_counts.get(tag, 0) + 1
            else:
                theme_counts[tag] = theme_counts.get(tag, 0) + 1
    return {"format": format_counts, "theme": theme_counts}


def _parse_epoch(s: str) -> float:
    if not s:
        return 0.0
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.timestamp()
    except ValueError:
        return 0.0
