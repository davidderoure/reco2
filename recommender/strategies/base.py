"""Shared interface for recommendation strategies.

Each strategy takes the current catalogue + user model (+ optionally the
whole population, for collaborative filtering) and returns a ranked list of
candidate story_ids. The engine takes the top unused candidates from each
strategy and dedups across slots.
"""

from __future__ import annotations

from ..catalogue import Catalogue
from ..models import UserModel


class Strategy:
    code: int  # CONTENT_BASED / COLLABORATIVE / TOPICAL / WILDCARD

    def candidates(
        self,
        user: UserModel,
        catalogue: Catalogue,
        population: dict[str, UserModel],
        excluded: set[str],
    ) -> list[str]:
        """Return story_ids ranked best-first, excluding ids in `excluded`."""
        raise NotImplementedError
