"""Durable replacements: interrupted writes never replace a complete file."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def publish_file(temporary: Path, destination: Path) -> None:
    with temporary.open("rb") as stream:
        os.fsync(stream.fileno())
    os.replace(temporary, destination)
    # Linux supports syncing directories; some development filesystems do not.
    if os.name == "posix":
        descriptor = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".partial", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        publish_file(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path: Path, value) -> None:
    atomic_write(path, json.dumps(value).encode("utf-8"))


def read_dict(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        if isinstance(value, dict):
            return value
    except (OSError, ValueError, UnicodeError):
        pass
    return {}


async def run_io(function, *args, **kwargs):
    """Run blocking work off-loop and finish it before cancellation cleanup.

    A cancelled to_thread await does not stop its thread. Waiting for that
    thread prevents callers closing/unlinking a file it is still writing.
    """
    import asyncio

    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        finally:
            raise
