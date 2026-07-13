"""Recommendation engine: turns events into user-model updates, and turns
user-model + catalogue + population state into a set of 6 recommendations.

This is a first-pass implementation against the gRPC spec discussed at
the design meeting. Open design questions (see project notes) are handled
with conservative defaults, called out in comments below.
"""

from __future__ import annotations

import math
import random
import threading
import time

from .catalogue import Catalogue
from .models import (
    COLLABORATIVE,
    CONTENT_BASED,
    TOPICAL,
    WILDCARD,
    StoryHistoryEntry,
    UserModel,
    decay_weight,
)
from .strategies import (
    CollaborativeStrategy,
    ContentBasedStrategy,
    TopicalStrategy,
    WildcardStrategy,
)

SLOT_COUNTS = {
    CONTENT_BASED: 2,
    COLLABORATIVE: 2,
    TOPICAL: 1,
    WILDCARD: 1,
}

# Open question #7, decided at the 2026-06-22 design meeting: bookmarking
# alone does not currently affect tag_affinity, but the capability is kept
# for future use — flip this to re-enable it.
BOOKMARKS_AFFECT_AFFINITY = False
# Bookmark-only signal strength on the 0-1 connectedness scale, used only
# if BOOKMARKS_AFFECT_AFFINITY is turned on. Below a strong score (e.g.
# 9/9 -> 1.0) but clearly positive.
BOOKMARK_AFFINITY_WEIGHT = 0.65

# Open question #1, decided at the 2026-06-22 design meeting: for cold-start
# users, pick at random from a pool of the most popular stories rather than
# always handing out the single most popular ones, so one story doesn't get
# over-promoted to every new user.
COLD_START_POPULAR_POOL_SIZE = 12

# Open question #4, decided at the 2026-06-22 design meeting: don't wait
# until the unread pool is fully exhausted to start re-recommending prior
# high-connectedness stories — ramp it in as the unread pool shrinks below
# this many stories (3x a single response's worth), reaching full
# re-engagement once nothing unread is left.
REENGAGEMENT_RAMP_THRESHOLD = 18

# Open question #6, decided at the 2026-06-22 design meeting: each batch of
# 6 must contain at least this many stories never recommended to this user
# before, even though repeats are otherwise allowed.
MIN_FRESH_PER_BATCH = 2

# Number of recent batches whose stories are excluded from fresh
# recommendations, preventing short-term cycling. N=2 means the last 2
# batches (12 stories) are avoided; relaxes automatically if the catalogue
# is too small to honour it.
RECENT_BATCHES_TO_EXCLUDE = 2


def _normalize_score(score_1_to_9: int) -> float:
    """Map a 1-9 connectedness score onto 0-1."""
    return (score_1_to_9 - 1) / 8.0


class RecommenderEngine:
    def __init__(
        self,
        catalogue: Catalogue,
        boosted_story_ids: list[str] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.catalogue = catalogue
        self.population: dict[str, UserModel] = {}
        self._population_lock = threading.Lock()
        self.rng = rng or random.Random()
        self._rng_lock = threading.Lock()
        self.strategies = {
            CONTENT_BASED: ContentBasedStrategy(rng=random.Random(rng.randint(0, 2**32) if rng else None)),
            COLLABORATIVE: CollaborativeStrategy(),
            TOPICAL: TopicalStrategy(boosted_story_ids),
            WILDCARD: WildcardStrategy(rng=random.Random(rng.randint(0, 2**32) if rng else None)),
        }

    # -- population / persistence -----------------------------------------

    def load_population(self, users: list[UserModel]) -> None:
        with self._population_lock:
            self.population = {u.user_id: u for u in users}

    def get_or_create_user(self, user_id: str) -> UserModel:
        with self._population_lock:
            if user_id not in self.population:
                self.population[user_id] = UserModel(user_id=user_id)
            return self.population[user_id]

    # -- event handlers -----------------------------------------------------

    def record_answered_question(
        self, user_id: str, story_id: str, scores: list[int], timestamp: float | None = None
    ) -> None:
        timestamp = timestamp if timestamp is not None else time.time()
        user = self.get_or_create_user(user_id)

        # Open question #5, decided at the 2026-06-22 design meeting:
        # scores[0] (compulsory) is the connectedness signal used by all
        # logic here. scores[1:4] are stored on the entry, unused, so the
        # capability to use them later doesn't require a model change.
        connectedness = _normalize_score(scores[0])

        entry = user.story_history.setdefault(story_id, StoryHistoryEntry())
        entry.connectedness = connectedness
        entry.timestamp = timestamp
        entry.secondary_scores = list(scores[1:4])

        self._recompute_tag_affinity(user)
        user.has_new_score_since_last_request = True
        user.last_updated = timestamp

    def record_engagement_stop(
        self,
        user_id: str,
        story_id: str,
        progress_percentage: float,
        timestamp: float | None = None,
    ) -> None:
        timestamp = timestamp if timestamp is not None else time.time()
        user = self.get_or_create_user(user_id)
        entry = user.story_history.setdefault(story_id, StoryHistoryEntry())
        entry.viewed_pct = progress_percentage
        entry.timestamp = timestamp
        user.last_updated = timestamp

    def record_engagement_progress(
        self,
        user_id: str,
        story_id: str,
        progress_percentage: float,
        timestamp: float | None = None,
    ) -> None:
        # Fired periodically: per chapter-open for multi-chapter stories,
        # per % played for audio/video. Updates viewed_pct so that if a
        # subsequent stop/abort event is lost we still have the last known
        # position. Does not trigger tag-affinity recompute or count as
        # "seen" — open question #8: only a scored answer counts for that.
        timestamp = timestamp if timestamp is not None else time.time()
        user = self.get_or_create_user(user_id)
        entry = user.story_history.setdefault(story_id, StoryHistoryEntry())
        entry.viewed_pct = progress_percentage
        entry.timestamp = timestamp

    def record_abort(
        self,
        user_id: str,
        story_id: str,
        timestamp: float | None = None,
    ) -> None:
        # UserEngagementStoryAbort ("get me out of here"). Records that the
        # user explicitly exited this story, distinct from a normal early stop.
        # Avoidance logic (how many aborts on similar content triggers
        # de-prioritisation, with what recency weighting) is not yet
        # implemented — parameters TBD. The flag is captured here so that
        # when that logic is added no historical data is lost.
        timestamp = timestamp if timestamp is not None else time.time()
        user = self.get_or_create_user(user_id)
        entry = user.story_history.setdefault(story_id, StoryHistoryEntry())
        entry.aborted = True
        entry.timestamp = timestamp
        user.last_updated = timestamp

    def record_bookmark(self, user_id: str, story_id: str, timestamp: float | None = None) -> None:
        # Open question #7: a bookmark is a preference signal even if the
        # story is never opened or scored (and stories reach users via
        # search/bookmarks as well as recommendations, not just the
        # recommendation flow). Default: contribute a moderate positive
        # weight to tag_affinity, below an actual connectedness score, so
        # it nudges the profile without drowning out real feedback.
        timestamp = timestamp if timestamp is not None else time.time()
        user = self.get_or_create_user(user_id)
        user.bookmarked_story_ids[story_id] = timestamp
        self._recompute_tag_affinity(user)
        user.last_updated = timestamp

    def record_unbookmark(self, user_id: str, story_id: str, timestamp: float | None = None) -> None:
        timestamp = timestamp if timestamp is not None else time.time()
        user = self.get_or_create_user(user_id)
        user.bookmarked_story_ids.pop(story_id, None)
        self._recompute_tag_affinity(user)
        user.last_updated = timestamp

    def record_mood(self, user_id: str, mood_score: int, story_id: str = "", timestamp: float | None = None) -> None:
        # Explicitly not used by recommender logic per spec; pass-through
        # for trial-data storage happens outside this engine.
        self.get_or_create_user(user_id)

    def _recompute_tag_affinity(self, user: UserModel) -> None:
        """Rebuild tag_affinity from story_history (and bookmarks), with
        exponential decay on event age. Recomputing from the (bounded)
        history each time avoids drift from incremental EMA updates, and
        history size is capped by catalogue size, not interaction count.
        """
        now = time.time()
        tag_weighted_sum: dict[str, float] = {}
        tag_weight_total: dict[str, float] = {}

        def add(story_id: str, score: float, timestamp: float) -> None:
            story = self.catalogue.get(story_id)
            if not story or not story.tags:
                return
            w = decay_weight(timestamp, now)
            for tag in story.tags:
                tag_weighted_sum[tag] = tag_weighted_sum.get(tag, 0.0) + w * score
                tag_weight_total[tag] = tag_weight_total.get(tag, 0.0) + w

        for story_id, entry in user.story_history.items():
            if entry.connectedness is None:
                continue
            add(story_id, entry.connectedness, entry.timestamp)

        # Open question #7, decided at the 2026-06-22 design meeting:
        # bookmarking alone does NOT contribute to tag_affinity. Bookmarks
        # are still tracked on the user model (see record_bookmark) so this
        # can be turned on later without a model/schema change — just flip
        # BOOKMARKS_AFFECT_AFFINITY and this loop activates.
        if BOOKMARKS_AFFECT_AFFINITY:
            for story_id, timestamp in user.bookmarked_story_ids.items():
                # Don't double-count: an actual connectedness score for
                # this story already carries more signal than the
                # bookmark alone.
                if story_id in user.story_history and user.story_history[story_id].connectedness is not None:
                    continue
                add(story_id, BOOKMARK_AFFINITY_WEIGHT, timestamp)

        user.tag_affinity = {
            tag: tag_weighted_sum[tag] / tag_weight_total[tag]
            for tag in tag_weighted_sum
            if tag_weight_total[tag] > 0
        }

    # -- recommendations ------------------------------------------------

    def get_recommendations(self, user_id: str, timestamp: float | None = None) -> list[tuple[str, int]]:
        """Returns up to 6 (story_id, recommender_type) pairs."""
        timestamp = timestamp if timestamp is not None else time.time()
        user = self.get_or_create_user(user_id)

        seen = self._seen_story_ids(user)
        previously_recommended = set(user.recommended_story_ids)
        has_new_score = user.has_new_score_since_last_request
        user.has_new_score_since_last_request = False

        # Batch preservation: if the user hasn't answered a connectedness
        # question since their last visit, return the previous batch minus
        # any story they interacted with but didn't score (stopped early or
        # interrupted). This gives them a stable set — the app holds their
        # place rather than reshuffling on a quick exit or interruption.
        # Exception: if the user hit "Get me out of here" (abort) since their
        # last visit, bypass preservation and generate a fresh set — abort is
        # a deliberate signal that they want something different, distinct from
        # an accidental or contextual early exit.
        has_aborted_since_last_request = self._has_aborted_since_last_request(user)
        if not has_new_score and not has_aborted_since_last_request and user.recent_batches:
            preserved = self._preserved_batch(user, seen)
            if len(preserved) == 6:
                user.last_recommendation_request_at = timestamp
                return preserved
            # Fewer than 6 (some removed) — top up with fresh picks below,
            # treating preserved stories as already chosen. Also exclude the
            # dropped stories so the topup doesn't immediately re-add them.
            preserved_ids = {sid for sid, _ in preserved}
            removed_ids = set(user.recent_batches[0]) - preserved_ids
            topup_excluded = seen | preserved_ids | removed_ids
            topup = self._topup_for_preserved(user, seen, topup_excluded, 6 - len(preserved))
            results = preserved + topup
            self._finalise_batch(user, results, timestamp)
            return results

        # Fresh recommendations — build exclusion window from recent batches
        # to prevent short-term cycling (RECENT_BATCHES_TO_EXCLUDE = 2).
        # Relaxes automatically when the catalogue is too small.
        recently_recommended = {
            sid
            for batch in user.recent_batches[:RECENT_BATCHES_TO_EXCLUDE]
            for sid in batch
        }

        # Cold start = no scored stories yet.
        is_cold_start = not seen

        if is_cold_start:
            results = self._cold_start_recommendations(user, seen)
        else:
            results = self._steady_state_recommendations(user, seen, recently_recommended)

        if len(results) < 6:
            results = self._fill_with_reengagement(user, seen, results)

        results = self._ensure_minimum_freshness(user, seen, previously_recommended, results)

        self._finalise_batch(user, results, timestamp)
        return results

    def _preserved_batch(self, user: UserModel, seen: set[str]) -> list[tuple[str, int]]:
        """Previous batch minus stories the user interacted with but didn't score."""
        last_batch = user.recent_batches[0]
        preserved = []
        for story_id in last_batch:
            entry = user.story_history.get(story_id)
            interacted_without_scoring = (
                entry is not None
                and entry.connectedness is None
                and entry.timestamp > user.last_recommendation_request_at
            )
            if not interacted_without_scoring:
                preserved.append((story_id, self._infer_rec_type(story_id, user)))
        return preserved

    def _has_aborted_since_last_request(self, user: UserModel) -> bool:
        """True if the user hit 'Get me out of here' on any story since their
        last GetRecommendations call. Distinguishes a deliberate exit from an
        accidental or contextual early stop."""
        for entry in user.story_history.values():
            if entry.aborted and entry.timestamp > user.last_recommendation_request_at:
                return True
        return False

    def _infer_rec_type(self, story_id: str, user: UserModel) -> int:
        """Re-use the wildcard type as a neutral fallback for preserved slots
        where the original type is no longer tracked."""
        return WILDCARD

    def _topup_for_preserved(
        self, user: UserModel, seen: set[str], excluded: set[str], needed: int
    ) -> list[tuple[str, int]]:
        """Fill remaining slots after batch preservation with fresh picks."""
        results = []
        chosen = set(excluded)
        for rec_type in [CONTENT_BASED, TOPICAL, WILDCARD, COLLABORATIVE]:
            if len(results) >= needed:
                break
            strategy = self.strategies[rec_type]
            for story_id in strategy.candidates(user, self.catalogue, self.population, chosen):
                if len(results) >= needed:
                    break
                results.append((story_id, rec_type))
                chosen.add(story_id)
        return results

    def _finalise_batch(self, user: UserModel, results: list[tuple[str, int]], timestamp: float) -> None:
        """Update user model bookkeeping after producing a batch."""
        for story_id, _ in results:
            user.recommended_story_ids.add(story_id)
        new_batch = [sid for sid, _ in results]
        user.recent_batches = ([new_batch] + user.recent_batches)[:RECENT_BATCHES_TO_EXCLUDE]
        user.last_recommendation_request_at = timestamp

    def _seen_story_ids(self, user: UserModel) -> set[str]:
        return {sid for sid, e in user.story_history.items() if e.connectedness is not None}

    def _steady_state_recommendations(
        self, user: UserModel, seen: set[str], recently_recommended: set[str] | None = None
    ) -> list[tuple[str, int]]:
        recently_recommended = recently_recommended or set()
        results: list[tuple[str, int]] = []
        chosen: set[str] = set()

        # Exclude recently recommended stories (N-batch window) from the
        # candidate pool so the same stories don't cycle back too quickly.
        # Falls back to including them if the catalogue is too small.
        excluded = seen | recently_recommended

        for rec_type, count in SLOT_COUNTS.items():
            strategy = self.strategies[rec_type]
            candidates = strategy.candidates(user, self.catalogue, self.population, excluded | chosen)
            picked = 0
            for story_id in candidates:
                if story_id in chosen:
                    continue
                results.append((story_id, rec_type))
                chosen.add(story_id)
                picked += 1
                if picked == count:
                    break

        # Fill any shortfall with stories not in the recent-batch window
        # (COLLABORATIVE underproduces early in the trial — don't relax the
        # window just because collaborative was thin).
        if len(results) < 6:
            for topup_type in (CONTENT_BASED, TOPICAL, WILDCARD):
                if len(results) == 6:
                    break
                for story_id in self.strategies[topup_type].candidates(
                    user, self.catalogue, self.population, excluded | chosen
                ):
                    if len(results) == 6:
                        break
                    if story_id in chosen:
                        continue
                    results.append((story_id, topup_type))
                    chosen.add(story_id)

        # Only now relax the recent-batch exclusion if we still can't fill 6.
        if len(results) < 6 and recently_recommended:
            for rec_type in (CONTENT_BASED, TOPICAL, WILDCARD, COLLABORATIVE):
                if len(results) == 6:
                    break
                for story_id in self.strategies[rec_type].candidates(
                    user, self.catalogue, self.population, seen | chosen
                ):
                    if len(results) == 6:
                        break
                    if story_id in chosen:
                        continue
                    results.append((story_id, rec_type))
                    chosen.add(story_id)

        results = self._apply_reengagement_ramp(user, seen, results)
        return results

    def _apply_reengagement_ramp(
        self, user: UserModel, seen: set[str], results: list[tuple[str, int]]
    ) -> list[tuple[str, int]]:
        """Open question #4, decided 2026-06-22: start blending in
        re-engagement on prior high-connectedness stories as the unread
        pool shrinks, rather than waiting for it to run out completely.
        Ramps from 0 slots at REENGAGEMENT_RAMP_THRESHOLD unread stories
        remaining up to all 6 slots at 0 unread remaining.
        """
        if len(results) < 6:
            return results  # exhaustion fallback will handle this case

        unread_remaining = len(self.catalogue) - len(seen)
        if unread_remaining >= REENGAGEMENT_RAMP_THRESHOLD:
            return results

        ramp_fraction = 1 - max(unread_remaining, 0) / REENGAGEMENT_RAMP_THRESHOLD
        reengagement_count = max(1, min(6, math.ceil(6 * ramp_fraction)))

        chosen = {sid for sid, _ in results}
        reengagement_pool = sorted(
            (
                (entry.connectedness, story_id)
                for story_id, entry in user.story_history.items()
                if story_id in seen and story_id not in chosen
            ),
            reverse=True,
        )

        new_results = list(results)
        for _, story_id in reengagement_pool:
            if reengagement_count == 0 or not new_results:
                break
            new_results.pop()  # drop the lowest-priority pick to make room
            new_results.append((story_id, CONTENT_BASED))
            chosen.add(story_id)
            reengagement_count -= 1

        return new_results

    def _ensure_minimum_freshness(
        self,
        user: UserModel,
        seen: set[str],
        previously_recommended: set[str],
        results: list[tuple[str, int]],
    ) -> list[tuple[str, int]]:
        fresh_count = sum(1 for sid, _ in results if sid not in previously_recommended)
        needed = MIN_FRESH_PER_BATCH - fresh_count
        if needed <= 0:
            return results

        chosen = {sid for sid, _ in results}
        added: list[tuple[str, int]] = []
        seen_in_add: set[str] = set()

        for rec_type in (CONTENT_BASED, TOPICAL, WILDCARD, COLLABORATIVE):
            if len(added) == needed:
                break
            strategy = self.strategies[rec_type]
            for story_id in strategy.candidates(user, self.catalogue, self.population, seen | chosen):
                if story_id in previously_recommended or story_id in chosen or story_id in seen_in_add:
                    continue
                seen_in_add.add(story_id)
                added.append((story_id, rec_type))
                if len(added) == needed:
                    break

        if not added:
            return results  # nothing fresh left anywhere; accept fewer than the minimum

        new_results = list(results)
        for story_id, rec_type in added:
            if not new_results:
                break
            new_results.pop()
            new_results.append((story_id, rec_type))
            chosen.add(story_id)

        return new_results

    def _cold_start_recommendations(
        self, user: UserModel, excluded: set[str]
    ) -> list[tuple[str, int]]:
        """Open question #1, decided at the 2026-06-22 design meeting:
        default for a brand-new user is topical (newest) + a random draw
        from the most popular stories so far (highest mean connectedness
        across the population) + wildcard, in roughly the same 2/2/1/1
        spirit but without relying on personal history that doesn't exist
        yet. The popularity slot samples randomly from a pool of the top
        COLD_START_POPULAR_POOL_SIZE stories rather than always returning
        the single most popular ones, to avoid over-promoting one story
        to every new user.
        """
        results: list[tuple[str, int]] = []
        chosen: set[str] = set()

        topical = self.strategies[TOPICAL].candidates(user, self.catalogue, self.population, excluded)
        for story_id in topical[:1]:
            results.append((story_id, TOPICAL))
            chosen.add(story_id)

        cohort_best = self._cohort_average_ranking(excluded | chosen)
        pool = cohort_best[:COLD_START_POPULAR_POOL_SIZE]
        sample_size = min(4, len(pool))
        with self._rng_lock:
            sample = self.rng.sample(pool, k=sample_size)
        for story_id in sample:
            results.append((story_id, COLLABORATIVE))
            chosen.add(story_id)

        wildcard = self.strategies[WILDCARD].candidates(user, self.catalogue, self.population, excluded | chosen)
        for story_id in wildcard[:1]:
            results.append((story_id, WILDCARD))
            chosen.add(story_id)

        return results

    def _cohort_average_ranking(self, excluded: set[str]) -> list[str]:
        sums: dict[str, float] = {}
        counts: dict[str, int] = {}
        for other in self.population.values():
            for story_id, entry in other.story_history.items():
                if story_id in excluded or entry.connectedness is None:
                    continue
                sums[story_id] = sums.get(story_id, 0.0) + entry.connectedness
                counts[story_id] = counts.get(story_id, 0) + 1
        averages = [(sums[sid] / counts[sid], sid) for sid in sums]
        averages.sort(reverse=True)
        return [sid for _, sid in averages]

    def _fill_with_reengagement(
        self, user: UserModel, excluded: set[str], results: list[tuple[str, int]]
    ) -> list[tuple[str, int]]:
        """Open question #4: catalogue exhaustion. Relax unseen-only and
        re-rank previously-seen stories by remembered connectedness,
        favouring re-engagement over degrading to wildcard-only.
        """
        chosen = {sid for sid, _ in results}
        needed = 6 - len(results)
        if needed <= 0:
            return results

        ranked_seen = sorted(
            (
                (entry.connectedness, story_id)
                for story_id, entry in user.story_history.items()
                if entry.connectedness is not None and story_id not in chosen
            ),
            reverse=True,
        )

        for _, story_id in ranked_seen:
            if needed == 0:
                break
            results.append((story_id, CONTENT_BASED))
            chosen.add(story_id)
            needed -= 1

        if needed > 0:
            # Truly nothing left to distinguish (e.g. day-one cold start
            # with no population history at all yet) — pad with random
            # catalogue stories, not catalogue order, so different users
            # (and different calls) aren't all handed the same padding.
            remaining_pool = self.catalogue.all_ids()
            with self._rng_lock:
                self.rng.shuffle(remaining_pool)
            for story_id in remaining_pool:
                if needed == 0:
                    break
                if story_id in chosen or story_id in excluded:
                    continue
                results.append((story_id, WILDCARD))
                chosen.add(story_id)
                needed -= 1

        return results
