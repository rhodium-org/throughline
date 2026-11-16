# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Changes to several files that happen whole or not at all (SR-0356).

Two shapes of change are served. One knows every file's new content before it
writes any (:func:`write_all`): each is staged beside its target and the staged
files are then moved into place, and a failure at any point puts back what had
been replaced. The other is a sequence of steps that each rewrite the tree
(:class:`Snapshot`), which a migration is: the files the steps may touch are
remembered first, and put back as they were if a step fails.

Neither survives the process being killed between two renames; that is the gap
any change to several files has without a journal the next run reads, and
nothing here pretends otherwise.
"""
from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path

# The suffix of a file staged beside its target. No reader of a project matches
# it: registers are read by `*.yml`, documents by the names a project declares.
STAGED = ".tl-staged"


def _staged(path: Path) -> Path:
    return path.with_name(f".{path.name}{STAGED}")


def _put_back(path: Path, was: bytes | None) -> None:
    if was is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(was)


def write_all(files: Mapping[Path, str | bytes]) -> None:
    """Write every file in ``files`` or none of them.

    Text is written as UTF-8 as a file opened for text writes it, a newline
    becoming the platform's line ending, so a caller that wrote each file with
    ``write_text`` writes the same bytes through here; bytes are written as they
    are, for a caller that keeps a file's own line endings. Each file is
    staged in its target's directory, so the move into place never crosses a
    filesystem; a file that cannot be staged stops the change before any target
    is touched, and one that cannot be moved into place has every target moved
    before it put back as it was."""
    staged: list[tuple[Path, Path]] = []
    replaced: list[tuple[Path, bytes | None]] = []
    try:
        for path, content in files.items():
            path = Path(path)
            data = (content.replace("\n", os.linesep).encode("utf-8")
                    if isinstance(content, str) else content)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = _staged(path)
            tmp.write_bytes(data)
            staged.append((tmp, path))
        for tmp, path in staged:
            was = path.read_bytes() if path.is_file() else None
            os.replace(tmp, path)
            replaced.append((path, was))
    except BaseException:
        for path, was in reversed(replaced):
            _put_back(path, was)
        raise
    finally:
        for tmp, _ in staged:
            tmp.unlink(missing_ok=True)


class Snapshot:
    """The files under some directories as they stood, to put back if a change
    made in steps fails part-way.

    ``files`` are remembered whether or not they exist; each directory in
    ``folders`` is remembered as the files matching ``pattern`` directly inside
    it, so a file a step adds there is removed again and one it renames or
    deletes returns."""

    def __init__(self, files: Iterable[Path] = (), folders: Iterable[Path] = (),
                 pattern: str = "*.yml"):
        self._pattern = pattern
        self._folders = [Path(f) for f in folders]
        self._held: dict[Path, bytes | None] = {}
        for path in files:
            self._hold(Path(path))
        for folder in self._folders:
            for path in folder.glob(pattern):
                self._hold(path)

    def _hold(self, path: Path) -> None:
        self._held[path] = path.read_bytes() if path.is_file() else None

    def restore(self) -> None:
        """Put every remembered file back as it stood."""
        for folder in self._folders:
            if not folder.is_dir():
                continue
            for path in folder.glob(self._pattern):
                if path not in self._held and path.is_file():
                    path.unlink()
        for path, was in self._held.items():
            now = path.read_bytes() if path.is_file() else None
            if now != was:
                if was is not None:
                    path.parent.mkdir(parents=True, exist_ok=True)
                _put_back(path, was)


__all__ = ["STAGED", "Snapshot", "write_all"]
