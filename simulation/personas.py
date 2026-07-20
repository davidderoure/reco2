"""Named synthetic-user personas: hidden tag-preference profiles used to
generate a simulated connectedness score, never seen by the recommender
itself. Designed to be interpretable in journey transcripts, not just
random noise.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from recommender.models import Story
from .synthetic_catalogue import THEME_TAGS


@dataclass
class Persona:
    name: str
    description: str
    theme_weights: dict[str, float] = field(default_factory=dict)  # 0-1, default 0.3 (mild baseline)
    format_weights: dict[str, float] = field(default_factory=dict)  # 0-1, default 0.5 (no preference)
    # Robustness overrides — None / "first" = normal behaviour
    selection: str = "first"    # "first" = always opens recs[0]; "random" = picks uniformly at random
    fixed_score: int | None = None  # if set, always returns this score (1-5) regardless of story tags
    abort_tags: set[str] = field(default_factory=set)  # always aborts if opened story contains any of these tags

    def affinity_for(self, tag: str, themes: set[str], formats: set[str]) -> float:
        if tag in themes:
            return self.theme_weights.get(tag, 0.3)
        if tag in formats:
            return self.format_weights.get(tag, 0.5)
        return 0.3


PERSONAS: list[Persona] = [
    Persona(
        name="narrow_preference",
        description="Strongly connects with stories about feeling unheard/unsupported; mild on everything else.",
        theme_weights={"Feeling Unheard": 0.95, "Feeling Unsupported": 0.85},
    ),
    Persona(
        name="broad_mild_preference",
        description="Mild positive connection across most themes, no sharp standout.",
        theme_weights={tag: 0.55 for tag in THEME_TAGS},
    ),
    Persona(
        name="tag_avoider",
        description="Consistently low connectedness with stories about being judged — plausibly triggering content; otherwise average.",
        theme_weights={"Being Judged": 0.1},
    ),
    Persona(
        name="format_audio_lover",
        description="Connection driven mostly by format (prefers Audio) rather than theme.",
        theme_weights={tag: 0.4 for tag in THEME_TAGS},
        format_weights={"Audio": 0.9, "Written": 0.3, "Visual": 0.3, "Video": 0.3},
    ),
    Persona(
        name="identity_explorer",
        description="High affinity for identity/experience themes (LGBTQ+, gender, migration, heritage); low for conflict/war.",
        theme_weights={
            "LGBTQ+ Experience": 0.9,
            "Gender": 0.85,
            "Migration": 0.8,
            "Heritage": 0.75,
            "Hiding Self": 0.8,
            "Community": 0.7,
            "WWI": 0.1,
            "WWII": 0.1,
        },
    ),
    Persona(
        name="community_and_arts",
        description="Drawn to community, activism, and creative arts themes across all formats.",
        theme_weights={
            "Community": 0.9,
            "Activism": 0.85,
            "Performing Arts": 0.85,
            "Visual Arts": 0.8,
            "Music": 0.8,
            "Poetry": 0.75,
            "Literature": 0.7,
            "Friendship": 0.7,
        },
    ),
]


ROBUSTNESS_PERSONAS: list[Persona] = [
    Persona(
        name="always_first",
        description="Always opens the first (highest-ranked) recommendation. Normal score distribution.",
    ),
    Persona(
        name="always_random",
        description="Always opens a randomly chosen recommendation from the batch. Normal score distribution.",
        selection="random",
    ),
    Persona(
        name="always_low_score",
        description="Always opens the first recommendation and returns score 1 — chronically low connectedness regardless of content (min on 1-5 scale).",
        fixed_score=1,
    ),
    Persona(
        name="always_high_score",
        description="Always opens the first recommendation and returns score 5 — everything connects, no discrimination (max on 1-5 scale).",
        fixed_score=5,
    ),
    Persona(
        name="always_middle_score",
        description="Always opens the first recommendation and returns score 3 — flat neutral response to all content (mid on 1-5 scale).",
        fixed_score=3,
    ),
    Persona(
        name="consistent_aborter",
        description=(
            "Opens the first recommendation but immediately aborts if it contains 'Being Judged', "
            "'Feeling Lonely', or 'Hiding Self'; otherwise engages normally. "
            "Tests that abort triggers a fresh batch and that avoidance logic (when implemented) "
            "reduces exposure to these tags over time."
        ),
        abort_tags={"Being Judged", "Feeling Lonely", "Hiding Self"},
    ),
]


def simulated_connectedness(story: Story, persona: Persona, rng: random.Random) -> int:
    """Synthetic connectedness score (1-5) for a story given a hidden persona."""
    themes = set(THEME_TAGS)
    formats = set(story.tags) - themes
    relevant_themes = [t for t in story.tags if t in themes]

    if not relevant_themes:
        theme_component = 0.3
    else:
        theme_component = sum(persona.affinity_for(t, themes, formats) for t in relevant_themes) / len(relevant_themes)

    format_component = 0.5
    for tag in story.tags:
        if tag in persona.format_weights:
            format_component = persona.affinity_for(tag, themes, formats)

    # Theme dominates; format is a secondary modifier.
    base = (0.8 * theme_component + 0.2 * format_component) * 5
    noisy = base + rng.gauss(0, 0.5)
    return max(1, min(5, round(noisy)))
