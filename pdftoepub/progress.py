from __future__ import annotations

from collections.abc import Callable

ProgressFn = Callable[[int, str], None]


def report(progress: ProgressFn | None, percent: int, message: str) -> None:
    if progress is None:
        return
    progress(max(0, min(100, int(percent))), message)
