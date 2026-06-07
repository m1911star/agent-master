"""UUID helper.

doc/01-data-model.md §命名约定 calls for uuid v7 ("含时间戳，自然按时间排序").

Reality on Python 3.12 (our floor): no `uuid.uuid7` in stdlib — that landed in
3.13. We don't want to pin a third-party `uuid6` library for V0.1, so the
helper does the best-it-can ordering:

1. If `uuid.uuid7` exists (Py 3.13+), use it.
2. Otherwise, fall back to `uuid4` (random, not time-ordered).

When we move to 3.13+ as the floor, the fallback drops out automatically and
the IDs become naturally time-ordered without any other code changes.

The string form (`str(...)`) is what gets persisted; SQLite stores TEXT.
"""

from __future__ import annotations

import uuid

try:
    _uuid7 = uuid.uuid7  # type: ignore[attr-defined]
except AttributeError:  # Python 3.12 and earlier
    _uuid7 = None


def new_id() -> str:
    """Generate a fresh primary-key value.

    Returns a uuid7 string if the host Python provides it, otherwise a uuid4
    string. Either way the caller gets a 36-char hex form.
    """
    if _uuid7 is not None:
        return str(_uuid7())
    return str(uuid.uuid4())
