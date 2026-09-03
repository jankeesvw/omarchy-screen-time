"""A clock that only moves forward.

Setting the system clock back is the first trick anyone tries, so nothing here
trusts the wall clock on its own. CLOCK_BOOTTIME is the reference because it
keeps counting across a suspend, where CLOCK_MONOTONIC does not. The wall clock
is only allowed to nudge the logical time by TOLERANCE per tick; anything wider
is recorded and ignored.
"""

import time

TOLERANCE_SECONDS = 300.0


class Clock:
    def __init__(self, floor=None):
        self._boot = time.clock_gettime(time.CLOCK_BOOTTIME)
        wall = time.time()
        self.logical = max(wall, float(floor or 0.0))
        self.jumps = 0
        self.last_jump = None

    def tick(self):
        """Advance and return (logical_now, elapsed_seconds)."""
        boot = time.clock_gettime(time.CLOCK_BOOTTIME)
        elapsed = max(0.0, boot - self._boot)
        self._boot = boot

        expected = self.logical + elapsed
        wall = time.time()
        drift = wall - expected
        if abs(drift) > TOLERANCE_SECONDS:
            self.jumps += 1
            self.last_jump = {"at": expected, "drift_seconds": round(drift, 1)}
            self.logical = expected
        else:
            self.logical = max(wall, expected - TOLERANCE_SECONDS)
        return self.logical, elapsed

    def now(self):
        """The logical time right now, not just at the last tick.

        The daemon only ticks every few seconds, but quiz timing needs the time
        between ticks too: with a five second tick, "answered within 1.5
        seconds" is otherwise true for every answer in the same tick.
        """
        boot = time.clock_gettime(time.CLOCK_BOOTTIME)
        return self.logical + max(0.0, boot - self._boot)
