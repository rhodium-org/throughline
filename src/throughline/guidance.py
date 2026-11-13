# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""A standard's guidance for the agents that write the graphs following it
(SR-0323).

A standard holds guidance items (meta-standard REG-0010): each names a file of
the standard and where a new follower takes its copy. ``tl init --from-schema``
copies the signed ones, then makes the agent files a session loads by itself
refer to them: ``AGENTS.md``, and ``CLAUDE.md`` importing it, at the root of the
git working tree that holds the new project, or at the project's root where it is
in none. A file that exists is added to, never replaced.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .signatures import FILES, current, file_digest, standing

GUIDANCE_TYPE = "guidance"
AGENTS_NAME = "AGENTS.md"
CLAUDE_NAME = "CLAUDE.md"
CLAUDE_IMPORT = "@AGENTS.md"

# What a note says happened.
CREATED = "created"
ADDED = "added to"
WARNING = "warning"


@dataclass(frozen=True)
class Note:
    """One thing init did or declined to do with a standard's guidance."""
    kind: str      # CREATED | ADDED | WARNING
    text: str      # the file, as written from the working directory, or the warning


def _inside(root: Path, rel: object) -> Path | None:
    """The path ``rel`` names under ``root``, or None where it is not a plain
    relative path that stays inside it."""
    if not isinstance(rel, str) or not rel or "\\" in rel or rel.startswith("/"):
        return None
    if ".." in Path(rel).parts:
        return None
    path = root / rel
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return path


def _signed_over_file(standard, item, rel: str) -> bool:
    """Whether ``item`` is ratified and a signature that still holds covers the
    content of ``rel`` as it stands (SR-0322)."""
    schema = standard.schema
    if not standing(item, schema).met:
        return False
    digest = file_digest(standard.path, rel)
    if digest is None:
        return False
    return any((entry.get(FILES) or {}).get(rel) == digest
               for entry in current(item, schema))


def _shown(path: Path) -> str:
    try:
        return Path(os.path.relpath(path)).as_posix()
    except ValueError:        # another drive, on Windows
        return path.as_posix()


def _append(path: Path, lines: list[tuple[str, str]], heading: str) -> str | None:
    """Make ``path`` hold every one of ``lines``, each a pair of what it must
    mention and the line that mentions it: create the file, under ``heading``,
    where there is none, and add to the end of one that exists only the lines for
    what it does not mention. Returns CREATED, ADDED, or None when nothing was
    written."""
    if not path.exists():
        path.write_text(heading + "\n".join(line for _, line in lines) + "\n",
                        encoding="utf-8")
        return CREATED
    text = path.read_text(encoding="utf-8")
    lacking = [line for needle, line in lines if needle not in text]
    if not lacking:
        return None
    gap = "" if text.endswith("\n\n") or not text else ("\n" if text.endswith("\n") else "\n\n")
    path.write_text(text + gap + "\n".join(lacking) + "\n", encoding="utf-8")
    return ADDED


def copy_guidance(standard, root: Path, *, agents_root: Path | None = None) -> list[Note]:
    """Copy ``standard``'s signed guidance into the new project at ``root`` and
    make the agent files at ``agents_root`` (default ``root``) refer to it
    (SR-0323). Returns what was written and what was left alone, in order."""
    notes: list[Note] = []
    dead = standard.schema.dead_statuses()
    copied: list[Path] = []
    for item in sorted(standard.items(), key=lambda it: it.uid):
        if item.type != GUIDANCE_TYPE or item.is_deleted or item.status in dead:
            continue
        rel, to = item.attrs.get("file"), item.attrs.get("copy_to")
        src = _inside(standard.path, rel)
        dest = _inside(root, to)
        if src is None or dest is None or not src.is_file():
            notes.append(Note(WARNING, f"guidance {item.uid} of the standard was not "
                              f"copied: its file ({rel!r}) or its copy_to ({to!r}) is "
                              "not a path inside the graph"))
            continue
        if not _signed_over_file(standard, item, rel):
            notes.append(Note(WARNING, f"guidance {item.uid} of the standard was not "
                              f"copied: no signature that still holds covers {rel} "
                              "as it stands — its keeper ratifies it again"))
            continue
        content = src.read_bytes()
        if dest.exists():
            if not dest.is_file() or dest.read_bytes() != content:
                notes.append(Note(WARNING, f"{_shown(dest)} already holds something "
                                  f"else, so it was kept and guidance {item.uid} "
                                  "of the standard was not copied"))
                continue
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
            notes.append(Note(CREATED, _shown(dest)))
        copied.append(dest)
    if not copied:
        return notes
    base = (agents_root or root).resolve()
    graph = Path(os.path.relpath(root.resolve(), base)).as_posix()
    where = "this folder" if graph == "." else f"`{graph}/`"
    lines = []
    for dest in copied:
        rel = Path(os.path.relpath(dest.resolve(), base)).as_posix()
        lines.append((rel, f"- Before changing the throughline graph in {where}, read "
                     f"[{rel}]({rel}): the guidance of the standard it follows."))
    for path, body, heading in (
            (base / AGENTS_NAME, lines, "# Guidance for AI agents\n\n"),
            (base / CLAUDE_NAME, [(CLAUDE_IMPORT, CLAUDE_IMPORT)], "")):
        did = _append(path, body, heading)
        if did is not None:
            notes.append(Note(did, _shown(path)))
    return notes
