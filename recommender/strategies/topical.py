"""Topical: prioritizes stories new *for this user* — added since their
own last visit (catalogue can grow mid-trial) — ahead of merely
globally-newest stories. Falls back to global newest-first once nothing
is new since the user's last visit. Designed as a seam for a future
manual boost list (current-events correlation) without changing callers.
"""

from __future__ import annotations

from ..catalogue import Catalogue
from ..models import TOPICAL, UserModel
from .base import Strategy


class TopicalStrategy(Strategy):
    code = TOPICAL

    def __init__(self, boosted_story_ids: list[str] | None = None) -> None:
        # Manually curated, e.g. by trial staff reacting to current events.
        # Ranked ahead of everything else when present.
        self.boosted_story_ids = boosted_story_ids or []

    def candidates(
        self,
        user: UserModel,
        catalogue: Catalogue,
        population: dict[str, UserModel],
        excluded: set[str],
    ) -> list[str]:
        boosted = [sid for sid in self.boosted_story_ids if sid not in excluded]

        newest_first = [s for s in catalogue.newest(len(catalogue)) if s.story_id not in excluded]
        new_for_user = [
            s.story_id for s in newest_first if s.created_at > user.last_recommendation_request_at
        ]
        rest = [
            s.story_id for s in newest_first if s.created_at <= user.last_recommendation_request_at
        ]

        seen = set(boosted)
        ordered = boosted + [sid for sid in new_for_user + rest if sid not in seen]
        return ordered
