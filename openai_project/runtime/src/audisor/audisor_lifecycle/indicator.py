"""Presentation-only terminal status for the Audisor preflight."""

from __future__ import annotations

import itertools
import sys
import threading
import time
from typing import TextIO


class AudisorIndicator:
    """Show a transient spinner only when output is an interactive terminal."""

    _frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, label: str = "Audisor checking...", stream: TextIO | None = None) -> None:
        self.label = label
        self._stream = stream if stream is not None else sys.stdout
        self._enabled = bool(self._stream.isatty())
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _spin(self) -> None:
        frames = itertools.cycle(self._frames)
        while not self._stop.is_set():
            self._stream.write(f"\r{next(frames)} {self.label}")
            self._stream.flush()
            self._stop.wait(0.08)
        self._stream.write("\r" + " " * (len(self.label) + 3) + "\r")
        self._stream.flush()

    def __enter__(self) -> "AudisorIndicator":
        if self._enabled:
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc: object) -> bool:
        if self._thread is not None:
            self._stop.set()
            self._thread.join()
            if self._thread.is_alive():
                raise RuntimeError("Audisor indicator thread did not terminate")
        return False
