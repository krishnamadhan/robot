"""Atomic file writes for persistent state (KI-020 / AB-012).

Write to a temp file in the SAME directory, then os.replace() — atomic on the
same filesystem, so power loss mid-write leaves either the old file or the new
file, never a truncated one. Readers must still tolerate a missing/corrupt
file (start fresh + log), since files written before this helper existed may
already be corrupt.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically replace *path* contents with *text*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, obj: Any, *, indent: int | None = None) -> None:
    """Atomically write *obj* as JSON to *path*."""
    atomic_write_text(path, json.dumps(obj, indent=indent))
