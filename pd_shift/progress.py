from __future__ import annotations

from rich.console import Console


class ProgressLine:
    """Single updating status line; cleared before normal output."""

    def __init__(self, console: Console):
        self.console = console
        self._status = None

    def update(self, message: str) -> None:
        if self._status is None:
            self._status = self.console.status(message, spinner="dots")
            self._status.__enter__()
        else:
            self._status.update(message)

    def done(self) -> None:
        if self._status is not None:
            self._status.__exit__(None, None, None)
            self._status = None
