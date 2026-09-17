"""Bridge for consuming teammates' service protocols that are sync or async.

Several reviewed interfaces owned by other workstreams are synchronous
Protocols today (reading positions, evidence resolution, source lookup) while
others are async. M1 integration code awaits through this helper so a
teammate can switch an implementation to async without an M1 change.
"""

from __future__ import annotations

import inspect
from typing import Any


async def maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value
