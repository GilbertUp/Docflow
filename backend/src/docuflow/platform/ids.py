"""Time-ordered identifiers. Python 3.12 has no uuid7, so it is implemented per RFC 9562.

Ids made by one process always increase, even inside the same millisecond. Code orders things by
time and then by id, and a random tail inside one millisecond would put events made together in
a random order, most visibly on machines with a coarse clock.
"""

from __future__ import annotations

import secrets
import threading
import time
import uuid

_lock = threading.Lock()
_last_ms = 0
_counter = 0
_COUNTER_MAX = (1 << 12) - 1


def uuid7() -> uuid.UUID:
    global _last_ms, _counter
    with _lock:
        timestamp_ms = time.time_ns() // 1_000_000
        if timestamp_ms > _last_ms:
            # A new millisecond starts the counter at a random point with room to count up.
            _last_ms = timestamp_ms
            _counter = secrets.randbits(11)
        else:
            # The same millisecond, or the clock stepped back: keep counting from the last id.
            _counter += 1
            if _counter > _COUNTER_MAX:
                _last_ms += 1
                _counter = 0
        stamp, counter = _last_ms, _counter
    value = (stamp & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= counter << 64
    value |= 0b10 << 62
    value |= secrets.randbits(62)
    return uuid.UUID(int=value)
