"""Simulated engagement noise: real-world interruptions that produce
misleading signals regardless of the user's true affinity for a story.

Four interruption types, applied with a configurable probability to
each simulated interaction:

  - STOP_EARLY    phone call / someone walking in — stop event fires
                  at low progress, no connectedness answer
  - ABORT_LOW     same context but abort event fires at low progress
                  (looks like rejection, isn't)
  - ABORT_HIGH    battery flat / network lost late in the story —
                  abort fires at high progress (strong apparent rejection
                  of content the user may actually like)
  - NO_EVENT      app force-closed, battery flat before any event fires
                  — neither stop nor abort arrives

These are distinct from a genuine early exit (a user who truly
disliked the story and stopped). The point of injecting this noise
is to verify that the recommender degrades gracefully — personas
should still converge toward preferred content, just more slowly —
and to inform calibration of the avoidance mechanism's threshold
(how many abort signals before acting).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum


class InterruptionType(Enum):
    NONE = "none"
    STOP_EARLY = "stop_early"
    ABORT_LOW = "abort_low"
    ABORT_HIGH = "abort_high"
    NO_EVENT = "no_event"


@dataclass
class NoiseConfig:
    """Controls how often and what kind of interruptions are injected.

    interruption_probability: chance any given interaction is interrupted
    weights: relative probability of each interruption type when one occurs
    """
    interruption_probability: float = 0.15
    weights: dict[InterruptionType, float] = None

    def __post_init__(self):
        if self.weights is None:
            self.weights = {
                InterruptionType.STOP_EARLY: 0.35,
                InterruptionType.ABORT_LOW:  0.30,
                InterruptionType.ABORT_HIGH: 0.20,
                InterruptionType.NO_EVENT:   0.15,
            }


NO_NOISE = NoiseConfig(interruption_probability=0.0)


def sample_interruption(rng: random.Random, config: NoiseConfig) -> InterruptionType:
    if rng.random() >= config.interruption_probability:
        return InterruptionType.NONE
    types = list(config.weights.keys())
    weights = [config.weights[t] for t in types]
    return rng.choices(types, weights=weights, k=1)[0]


def low_progress(rng: random.Random) -> float:
    return rng.uniform(5.0, 30.0)


def high_progress(rng: random.Random) -> float:
    return rng.uniform(70.0, 95.0)
