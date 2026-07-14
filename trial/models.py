"""Typed dataclasses for the Trial API response.

Matches the schema in the Trial API Access Guide exactly, including
nullable fields and the recommenderType integer enum.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


# recommenderType integer enum values from the API spec
RECOMMENDER_TYPE_NAMES = {
    0: "Unspecified",
    1: "ContentBased",
    2: "Collaborative",
    3: "Topical",
    4: "Wildcard",
    5: "Recents",
    6: "Saved",
    7: "Search",
}


@dataclass
class EngagementRecord:
    story_id: str
    time_start: datetime
    recommender_type: Optional[int] = None
    percent_complete: Optional[float] = None
    time_end: Optional[datetime] = None
    viewpoint_text: Optional[str] = None
    mood: Optional[int] = None
    mood_time: Optional[datetime] = None
    question1_rating: Optional[int] = None
    question2_rating: Optional[int] = None
    question3_rating: Optional[int] = None
    question4_rating: Optional[int] = None

    @property
    def recommender_type_name(self) -> str:
        return RECOMMENDER_TYPE_NAMES.get(self.recommender_type, "Unknown")

    @classmethod
    def from_dict(cls, d: dict) -> "EngagementRecord":
        return cls(
            story_id=d["storyId"],
            time_start=_parse_dt(d["timeStart"]),
            recommender_type=d.get("recommenderType"),
            percent_complete=d.get("percentComplete"),
            time_end=_parse_dt(d["timeEnd"]) if d.get("timeEnd") else None,
            viewpoint_text=d.get("viewpointTextString"),
            mood=d.get("mood"),
            mood_time=_parse_dt(d["moodTime"]) if d.get("moodTime") else None,
            question1_rating=d.get("question1Rating"),
            question2_rating=d.get("question2Rating"),
            question3_rating=d.get("question3Rating"),
            question4_rating=d.get("question4Rating"),
        )


@dataclass
class ParticipantEngagement:
    origin_id: str
    records: list[EngagementRecord]

    @classmethod
    def from_dict(cls, d: dict) -> "ParticipantEngagement":
        return cls(
            origin_id=d["originId"],
            records=[EngagementRecord.from_dict(r) for r in d.get("engagementData", [])],
        )


def _parse_dt(s: str) -> datetime:
    """Parse ISO 8601 datetime string, handling Z suffix."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))
