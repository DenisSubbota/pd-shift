from __future__ import annotations

import sys

from rich.console import Console

EMPTY = "-"


def ensure_utf8_stdio() -> None:
    """Avoid UnicodeEncodeError when locale encoding is iso8859-* (common on macOS)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError, AttributeError):
            pass


def make_console() -> Console:
    ensure_utf8_stdio()
    return Console()
