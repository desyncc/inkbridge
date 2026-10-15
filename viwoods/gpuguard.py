"""Don't start local-model work while the PC's GPU is busy with something else (a game).

A background sync can queue dozens of pages for a vision model, and Ollama keeps the model in
video memory afterwards. Neither is urgent, so a sync waits for the GPU to be quiet first.

Reads AMD's `gpu_busy_percent` from sysfs (no root, no extra tools). Where that isn't there, the
GPU is assumed quiet: this never blocks work on a machine it can't measure.
"""

import glob
import time
from typing import Callable, Optional

SYSFS = "/sys/class/drm/card*/device/gpu_busy_percent"


def busy_percent(pattern: str = SYSFS) -> Optional[int]:
    """The busiest GPU's utilisation now, or None when no card reports it."""
    best: Optional[int] = None
    for path in glob.glob(pattern):
        try:
            with open(path) as f:
                value = int(f.read().strip())
        except (OSError, ValueError):
            continue
        best = value if best is None else max(best, value)
    return best


def is_busy(limit: int, samples: int = 3, gap: float = 0.3, pattern: str = SYSFS,
            sleep: Callable[[float], None] = time.sleep) -> bool:
    """True when the GPU's mean utilisation over a short window is at or above `limit` percent.
    `limit` <= 0 turns the check off. One reading can land between a game's frames."""
    if limit <= 0:
        return False
    seen = []
    for i in range(samples):
        value = busy_percent(pattern)
        if value is None:
            return False
        seen.append(value)
        if i + 1 < samples:
            sleep(gap)
    return sum(seen) / len(seen) >= limit


def wait_until_quiet(limit: int, poll: float = 30.0, announce: Callable[[str], None] = print,
                     pattern: str = SYSFS, sleep: Callable[[float], None] = time.sleep) -> float:
    """Block while the GPU is busy; returns the seconds waited (0 when it was already quiet)."""
    waited = 0.0
    while is_busy(limit, pattern=pattern, sleep=sleep):
        if waited == 0.0:
            announce(f"GPU busy (>= {limit}%): waiting before the next model call...")
        sleep(poll)
        waited += poll
    return waited
