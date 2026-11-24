"""Generation counter: the single cancellation mechanism.

Every new user turn, barge-in, or Esc calls bump(). Any async work captures
the generation when it starts and checks is_current() before it speaks or
acts. Stale work quietly stops instead of talking over the user.
"""
from __future__ import annotations

import threading


class Generation:
    def __init__(self) -> None:
        self._value = 0
        self._lock = threading.Lock()

    def bump(self) -> int:
        with self._lock:
            self._value += 1
            return self._value

    @property
    def value(self) -> int:
        return self._value

    def is_current(self, g: int) -> bool:
        return g == self._value
