"""Core data structures for the recommender's in-memory state.

UserModel is the thing we serialize to/from the opaque JSON string exchanged
with C# via SaveUserModelRequest/LoadUserModelResponse.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field


# Recommender type codes, matching the EngagementType enum values used for
# recommender_type in RecommendationResult.
CONTENT_BASED = 1
COLLABORATIVE = 2
TOPICAL = 3
WILDCARD = 4

# Connectedness decay half-life. An interaction this many seconds old
# contributes half the weight of a fresh one. 14 days, tuned for a trial
# running over months with engagement a few times a week.
DECAY_HALF_LIFE_SECONDS = 14 * 24 * 3600


def decay_weight(event_timestamp: float, now: float | None = None) -> float:
    now = now if now is not None else time.time()
    age = max(0.0, now - event_timestamp)
    return 0.5 ** (age / DECAY_HALF_LIFE_SECONDS)


@dataclass
class Story:
    story_id: str
    title: str
    tags: list[str]
    subtitle: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0


@dataclass
class StoryHistoryEntry:
    connectedness: float | None = None  # scores[0], rescaled 0-1; None if never answered
    viewed_pct: float = 0.0
    timestamp: float = 0.0
    # Open question #5: questions 2-4 (scores[1:4], raw 1-9) are
    # uncharacterized and not used by any logic yet, but kept here
    # (rather than discarded) so the capability to use them later doesn't
    # require a model change.
    secondary_scores: list[int] = field(default_factory=list)


@dataclass
class UserModel:
    user_id: str
    tag_affinity: dict[str, float] = field(default_factory=dict)
    story_history: dict[str, StoryHistoryEntry] = field(default_factory=dict)
    # Bookmarked but not necessarily read/scored — story_id -> bookmark timestamp.
    # Stories may also reach a user via search or bookmarks rather than only
    # via recommendations, so this is tracked independently of story_history.
    bookmarked_story_ids: dict[str, float] = field(default_factory=dict)
    last_recommendations: list[str] = field(default_factory=list)  # slot order
    recommended_story_ids: set[str] = field(default_factory=set)  # ever shown
    last_updated: float = 0.0

    def to_json(self) -> str:
        return json.dumps({
            "user_id": self.user_id,
            "tag_affinity": self.tag_affinity,
            "story_history": {
                sid: {
                    "connectedness": e.connectedness,
                    "viewed_pct": e.viewed_pct,
                    "timestamp": e.timestamp,
                    "secondary_scores": e.secondary_scores,
                }
                for sid, e in self.story_history.items()
            },
            "bookmarked_story_ids": self.bookmarked_story_ids,
            "last_recommendations": self.last_recommendations,
            "recommended_story_ids": sorted(self.recommended_story_ids),
            "last_updated": self.last_updated,
        })

    @classmethod
    def from_json(cls, user_id: str, blob: str) -> "UserModel":
        data = json.loads(blob)
        history = {
            sid: StoryHistoryEntry(**vals)
            for sid, vals in data.get("story_history", {}).items()
        }
        return cls(
            user_id=user_id,
            tag_affinity=data.get("tag_affinity", {}),
            story_history=history,
            bookmarked_story_ids=data.get("bookmarked_story_ids", {}),
            last_recommendations=data.get("last_recommendations", []),
            recommended_story_ids=set(data.get("recommended_story_ids", [])),
            last_updated=data.get("last_updated", 0.0),
        )
