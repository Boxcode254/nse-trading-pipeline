"""JSON file cache for market-intel data.

The cache is intentionally simple — a per-key JSON file in a
configurable directory. TTL is enforced at read time. The interface
mirrors a dict so callers can ``set``/``get``/``clear`` without
thinking about persistence.

Why not SQLite? The data we cache is small (a few KB per key) and
the access pattern is one-key-at-a-time. SQLite would add a
dependency and a migration story for no real win.

Key safety: keys are hashed if they contain characters that are
unsafe in filenames. This is best-effort — we don't need a perfect
hash, just a unique-enough one to avoid collisions in a single
user's cache directory.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Optional, Union

PathLike = Union[str, os.PathLike]


def _safe_key(key: str) -> str:
    """Make ``key`` safe to use as a filename component.

    Replaces path separators and NUL with underscores, then appends
    a short hash of the original key for uniqueness.
    """
    # Strip path separators; replace anything that's not a "normal"
    # filename char with ``_``.
    cleaned = "".join(
        c if (c.isalnum() or c in "._-") else "_"
        for c in key
    ).strip("._-")
    if not cleaned:
        cleaned = "key"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
    return f"{cleaned}-{digest}.json"


class Cache:
    """A small JSON file cache with TTL semantics.

    Parameters
    ----------
    directory : path-like
        Where to store the cache files. Created on first write if
        missing. The directory is *not* namespaced — callers should
        use a per-feature prefix in the key (e.g. ``news:SCOM:date``)
        to keep concerns separated.
    """

    def __init__(self, directory: PathLike) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    # ── Public API ─────────────────────────────────────────────────

    def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        """Persist ``value`` under ``key``.

        ``ttl_seconds`` is advisory — stored alongside the value but
        not enforced here. The caller passes it back to ``get`` when
        it cares.
        """
        payload = {
            "value": value,
            "stored_at": time.time(),
            "ttl": ttl_seconds,
        }
        path = self._path(key)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, default=str)
        os.replace(tmp, path)

    def get(self, key: str, ttl_seconds: Optional[int] = None) -> Any:
        """Return the cached value, or ``None`` if missing or stale.

        If ``ttl_seconds`` is given and the entry's age exceeds it
        (measured by the file's mtime), the entry is treated as
        missing. The file is left in place — call ``clear`` if you
        want it removed.
        """
        path = self._path(key)
        if not path.exists():
            return None
        if ttl_seconds is not None:
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                return None
            if (time.time() - mtime) > ttl_seconds:
                return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
        return payload.get("value")

    def has(self, key: str) -> bool:
        return self._path(key).exists()

    def clear(self, key: Optional[str] = None) -> None:
        """Remove a single key (if given) or every entry in the cache."""
        if key is not None:
            p = self._path(key)
            if p.exists():
                p.unlink()
            return
        for child in self.directory.iterdir():
            if child.is_file() and child.suffix == ".json":
                child.unlink()

    # ── Internals ──────────────────────────────────────────────────

    def _path(self, key: str) -> Path:
        return self.directory / _safe_key(key)
