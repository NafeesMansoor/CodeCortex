"""Version compatibility guard for applications that embed CodeCortex.

A consumer states its supported range once, at import time:

    from codecortex import require_version

    require_version(minimum="1.1.0", below="2.0.0", consumer="review-bot")

so an incompatible upgrade fails loudly at startup instead of surfacing later
as a confusing AttributeError deep inside the pipeline.
"""

from __future__ import annotations

from typing import Optional

from codecortex.version import SemVer, current


class IncompatibleVersionError(RuntimeError):
    """Installed CodeCortex is outside the range a consumer supports."""


def is_compatible(minimum: Optional[str] = None, below: Optional[str] = None) -> bool:
    installed = current()
    if minimum and installed < SemVer.parse(minimum):
        return False
    if below and installed >= SemVer.parse(below):
        return False
    return True


def require_version(
    minimum: Optional[str] = None,
    below: Optional[str] = None,
    *,
    consumer: Optional[str] = None,
) -> SemVer:
    """Assert the installed CodeCortex satisfies [minimum, below); return it.

    Raises IncompatibleVersionError when it does not.
    """
    if minimum is None and below is None:
        raise ValueError("require_version needs at least one of minimum= or below=")

    installed = current()
    if is_compatible(minimum, below):
        return installed

    bounds = []
    if minimum:
        bounds.append(f">= {minimum}")
    if below:
        bounds.append(f"< {below}")
    who = consumer or "This application"
    raise IncompatibleVersionError(
        f"{who} requires CodeCortex {' and '.join(bounds)}. Installed version: {installed}"
    )
