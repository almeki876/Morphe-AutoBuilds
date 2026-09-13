"""Console output that cannot turn successful work into an encoding failure."""

from __future__ import annotations

import sys
from typing import Any


def safe_print(value: Any = "", *, flush: bool = False) -> None:
    """Print through the active console, replacing unsupported glyphs if needed."""

    text = str(value)
    try:
        print(text, flush=flush)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe_text = text.encode(encoding, errors="replace").decode(encoding)
        print(safe_text, flush=flush)
