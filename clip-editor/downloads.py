"""Temp-file manager for rendered clips.

Each render gets a unique token and lives in TEMP_DIR/{token}/. A background
cleanup loop deletes directories older than DOWNLOAD_TTL_HOURS.
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import shutil
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

DEFAULT_TEMP_DIR = "/tmp/clip-editor"
DEFAULT_TTL_HOURS = 24


def _temp_dir() -> str:
    return os.environ.get("TEMP_DIR", DEFAULT_TEMP_DIR)


def _ttl_hours() -> float:
    return float(os.environ.get("DOWNLOAD_TTL_HOURS", DEFAULT_TTL_HOURS))


def _base_url() -> str:
    """Return scheme + host (+ port) only. Strips any path the env var may have
    been misconfigured with — e.g. trailing `/health` would otherwise produce
    download URLs like `.../health/downloads/...` which 404.
    """
    base = os.environ.get("DOWNLOAD_BASE_URL")
    if not base:
        raise RuntimeError("DOWNLOAD_BASE_URL env var is not set")
    parsed = urlparse(base.strip())
    if not parsed.scheme or not parsed.netloc:
        raise RuntimeError(
            f"DOWNLOAD_BASE_URL must include scheme and host (got {base!r})"
        )
    if parsed.path and parsed.path != "/":
        logger.warning(
            "DOWNLOAD_BASE_URL has a path component (%r); stripping it",
            parsed.path,
        )
    return f"{parsed.scheme}://{parsed.netloc}"


@dataclass
class StoredFile:
    token: str
    filename: str
    path: str
    size_bytes: int
    download_url: str


def make_workspace() -> tuple[str, str]:
    """Allocate a fresh (token, directory) for a new render."""
    os.makedirs(_temp_dir(), exist_ok=True)
    token = secrets.token_urlsafe(16)
    directory = os.path.join(_temp_dir(), token)
    os.makedirs(directory, exist_ok=False)
    return token, directory


def register(token: str, output_path: str, filename: str) -> StoredFile:
    """Move the rendered file under the token directory, name it `filename`, return URL info."""
    directory = os.path.join(_temp_dir(), token)
    final_path = os.path.join(directory, filename)
    if os.path.abspath(output_path) != os.path.abspath(final_path):
        shutil.move(output_path, final_path)
    size = os.path.getsize(final_path)
    return StoredFile(
        token=token,
        filename=filename,
        path=final_path,
        size_bytes=size,
        download_url=f"{_base_url()}/downloads/{token}/{filename}",
    )


def resolve(token: str, filename: str) -> Optional[str]:
    """Return the absolute path for a token+filename, or None if missing/expired/escaped."""
    base = os.path.abspath(_temp_dir())
    candidate = os.path.abspath(os.path.join(base, token, filename))
    # Prevent path traversal.
    if not candidate.startswith(base + os.sep):
        return None
    if not os.path.isfile(candidate):
        return None
    return candidate


def cleanup_expired() -> int:
    """Delete token directories older than the TTL. Returns the number removed."""
    base = _temp_dir()
    if not os.path.isdir(base):
        return 0
    cutoff = time.time() - _ttl_hours() * 3600
    removed = 0
    for name in os.listdir(base):
        path = os.path.join(base, name)
        if not os.path.isdir(path):
            continue
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if mtime < cutoff:
            try:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
            except OSError as exc:
                logger.warning("Failed to remove %s: %s", path, exc)
    return removed


async def cleanup_loop(interval_seconds: int = 3600) -> None:
    """Run cleanup_expired() forever on an interval. Cancel-safe."""
    while True:
        try:
            removed = cleanup_expired()
            if removed:
                logger.info("cleanup removed %d expired download dirs", removed)
        except Exception:
            logger.exception("cleanup_loop iteration failed")
        await asyncio.sleep(interval_seconds)
