"""Personas built around the real ORIGIN tag vocabulary.

These replace the synthetic personas for --real simulation runs.
Theme and format weights use actual tags from the catalogue so that
simulated_connectedness produces meaningful signal.

Format tags: Written, Audio, Visual, Video
Theme tags: the 43 tags present in the real catalogue (see real_catalogue.py)

Each persona has a primary theme cluster (high affinity) and a secondary
cluster (moderate affinity). Everything else defaults to 0.3 (mild interest).
"""

from __future__ import annotations

from .personas import Persona

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _weights(*high_tags, moderate_tags=(), low_tags=()) -> dict[str, float]:
    w = {}
    for t in high_tags:
        w[t] = 0.85
    for t in moderate_tags:
        w[t] = 0.55
    for t in low_tags:
        w[t] = 0.1
    return w


# ---------------------------------------------------------------------------
# Realistic personas — distinct interest profiles likely in a 16-24 audience
# ---------------------------------------------------------------------------

REAL_PERSONAS: list[Persona] = [

    Persona(
        name="identity_and_pride",
        description=(
            "Drawn to stories about gender, LGBTQ+ experience and hiding/revealing "
            "the self — exploring identity and authenticity."
        ),
        theme_weights=_weights(
            "LGBTQ+ Experience", "Hiding Self", "Gender",
            moderate_tags=("Being Judged", "Activism"),
            low_tags=("WWII", "Industry"),
        ),
        format_weights={"Written": 0.7, "Visual": 0.65, "Audio": 0.4, "Video": 0.5},
    ),

    Persona(
        name="social_justice",
        description=(
            "Interested in activism, working-class struggle, unions and "
            "feeling unheard — stories about fighting for change."
        ),
        theme_weights=_weights(
            "Activism", "Feeling Unheard", "Working Class Experience", "Unions",
            moderate_tags=("Community", "Being Judged", "Women's Suffrage"),
            low_tags=("Craftsmanship", "Heritage"),
        ),
        format_weights={"Written": 0.75, "Audio": 0.65, "Video": 0.5, "Visual": 0.4},
    ),

    Persona(
        name="creative_arts",
        description=(
            "Passionate about visual arts, craftsmanship, literature and "
            "performing arts — stories about making things."
        ),
        theme_weights=_weights(
            "Visual Arts", "Craftsmanship", "Literature", "Performing Arts",
            moderate_tags=("Music", "Poetry", "Feeling Lonely"),
            low_tags=("WWI", "Industry"),
        ),
        format_weights={"Written": 0.7, "Video": 0.7, "Visual": 0.75, "Audio": 0.5},
    ),

    Persona(
        name="migration_and_belonging",
        description=(
            "Connects strongly with migration, refugee experience and heritage — "
            "stories about finding or building a home."
        ),
        theme_weights=_weights(
            "Migration", "Refugee Experience", "Heritage", "Community",
            moderate_tags=("Feeling Unsupported", "Being Judged", "Hiding Self"),
            low_tags=("Adventure", "Sports"),
        ),
        format_weights={"Written": 0.7, "Audio": 0.7, "Video": 0.55, "Visual": 0.5},
    ),

    Persona(
        name="mental_health_and_support",
        description=(
            "Drawn to stories about mental health, feeling unsupported or lonely, "
            "and finding inner strength — looking for connection and hope."
        ),
        theme_weights=_weights(
            "Mental Health", "Feeling Unsupported", "Feeling Lonely",
            moderate_tags=("Experience of Disability", "Spirituality", "Community"),
            low_tags=("WWII", "Archaeology"),
        ),
        format_weights={"Written": 0.75, "Audio": 0.7, "Video": 0.6, "Visual": 0.45},
    ),

    Persona(
        name="history_and_discovery",
        description=(
            "Fascinated by history: WWII, archaeology, academia and missed "
            "chances — stories of people lost to history."
        ),
        theme_weights=_weights(
            "WWII", "Archaeology", "Academia", "Missed Chances",
            moderate_tags=("Heritage", "WWI", "Literature"),
            low_tags=("Sports", "Music"),
        ),
        format_weights={"Written": 0.8, "Video": 0.65, "Audio": 0.55, "Visual": 0.4},
    ),

    Persona(
        name="sport_and_adventure",
        description=(
            "Energised by stories of athletes, explorers and people who pushed "
            "physical limits — sport, adventure and determination."
        ),
        theme_weights=_weights(
            "Sports", "Adventure",
            moderate_tags=("Being Judged", "Feeling Unsupported", "African Experience"),
            low_tags=("Literature", "Archaeology"),
        ),
        format_weights={"Visual": 0.8, "Video": 0.75, "Audio": 0.5, "Written": 0.55},
    ),

    Persona(
        name="science_and_medicine",
        description=(
            "Curious about science, medicine and academia — stories of "
            "discovery and knowledge often overlooked by history."
        ),
        theme_weights=_weights(
            "Science", "Medicine", "Academia",
            moderate_tags=("Being Judged", "Gender", "Missed Chances"),
            low_tags=("Music", "Sports"),
        ),
        format_weights={"Written": 0.8, "Audio": 0.6, "Video": 0.55, "Visual": 0.4},
    ),

]

# ---------------------------------------------------------------------------
# Robustness personas using real tags
# ---------------------------------------------------------------------------

REAL_ROBUSTNESS_PERSONAS: list[Persona] = [
    Persona(
        name="always_first_real",
        description="Always opens the first recommendation. Normal score distribution.",
        theme_weights=_weights("Being Judged", "LGBTQ+ Experience",
                               moderate_tags=("Heritage", "Migration")),
        format_weights={"Written": 0.7, "Audio": 0.5},
        selection="first",
    ),
    Persona(
        name="always_random_real",
        description="Always opens a randomly chosen recommendation. Normal score distribution.",
        theme_weights=_weights("Activism", "Community",
                               moderate_tags=("Feeling Unheard",)),
        format_weights={"Written": 0.6, "Audio": 0.6},
        selection="random",
    ),
    Persona(
        name="always_low_score_real",
        description="Always scores 1 — chronically low connectedness regardless of content.",
        theme_weights=_weights("Mental Health", moderate_tags=("Feeling Lonely",)),
        format_weights={"Audio": 0.7},
        fixed_score=1,
    ),
    Persona(
        name="always_high_score_real",
        description="Always scores 5 — everything connects, no discrimination.",
        theme_weights=_weights("Visual Arts", moderate_tags=("Craftsmanship",)),
        format_weights={"Visual": 0.8, "Video": 0.7},
        fixed_score=5,
    ),
    Persona(
        name="always_middle_score_real",
        description="Always scores 3 — flat neutral response to all content.",
        theme_weights=_weights("Heritage", moderate_tags=("Migration",)),
        format_weights={"Written": 0.6},
        fixed_score=3,
    ),
    Persona(
        name="consistent_aborter_real",
        description=(
            "Aborts any story tagged 'Being Judged' or 'Feeling Unsupported' — "
            "tests abort behaviour with real trigger tags."
        ),
        theme_weights=_weights("LGBTQ+ Experience", "Hiding Self",
                               low_tags=("Being Judged", "Feeling Unsupported")),
        format_weights={"Written": 0.6, "Visual": 0.7},
        abort_tags={"Being Judged", "Feeling Unsupported"},
    ),
]
