# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Finding the graphs beneath a directory, and the git facts about where each
one lives (SR-0300).

Part of the library rather than of one command, so every tool that has to find a
reviewer's graphs — ``tl migrate --beneath`` and tl-ratify's picker among them —
finds the same ones, instead of each keeping its own copy of the rules.
"""
from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path
from types import SimpleNamespace

from .sources import SourceError, parse_sources
from .storage import CONFIG_NAME

# How far beneath a given path the search reaches. Deep enough for the layouts in
# use (``idd/`` at a repository root, ``packages/<name>/idd`` in a monorepo, a
# folder of repositories above either).
MAX_SEARCH_DEPTH = 5


def graphs_beneath(path: str | Path, *more: str | Path) -> list[Path]:
    """The root of every graph at or beneath ``path``, and beneath each of
    ``more``, in the order the walk met them (SR-0300).

    Breadth-first to :data:`MAX_SEARCH_DEPTH`, pruning as the walk descends, so an
    excluded directory costs nothing to skip. A graph reached from two of the
    paths is returned once, and a graph another found graph encloses and declares
    as a local path source is left out: it is part of that graph's composition,
    not a peer of it.
    """
    roots: list[Path] = []
    for base in (_base_of(p) for p in (path, *more)):
        roots.extend(r for r in _walk(base) if r not in roots)
    return _drop_borrowed(roots)


def _base_of(path: str | Path) -> Path:
    base = Path(path).resolve()
    return base if base.is_dir() else base.parent


def _walk(base: Path) -> list[Path]:
    roots = [base] if (base / CONFIG_NAME).exists() else []
    level = [base]
    for _ in range(MAX_SEARCH_DEPTH):
        children: list[Path] = []
        for parent in level:
            try:
                children.extend(c for c in sorted(parent.iterdir())
                                if c.is_dir() and not _is_excluded(c))
            except OSError:
                continue
        if not children:
            break
        ignored = _git_ignored(base, children)
        # Both sides resolved: git echoes a path in its own spelling, which on
        # Windows differs from the walk's.
        children = [c for c in children if c.resolve() not in ignored]
        roots.extend(c for c in children if (c / CONFIG_NAME).exists())
        level = children
    return roots


def _is_excluded(d: Path) -> bool:
    """A directory that cannot hold a graph anyone owns: a dot-directory (which
    covers ``.git`` and ``.venv``), a symbolic link, or a virtualenv not named
    like one."""
    return d.name.startswith(".") or d.is_symlink() or (d / "pyvenv.cfg").exists()


def _git_ignored(base: Path, dirs: list[Path]) -> set[Path]:
    """Which of ``dirs`` git ignores. Outside a repository, or with no git, none
    are: the walk then relies on its other exclusions."""
    try:
        # NUL-separated both ways, so text-mode newline translation and git's
        # quoting of unusual names cannot corrupt a path.
        proc = subprocess.run(
            ["git", "-C", str(base), "check-ignore", "--stdin", "-z"],
            input="".join(f"{d}\0" for d in dirs),
            capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return set()
    return {Path(e).resolve() for e in proc.stdout.split("\0") if e.strip()}


def _drop_borrowed(roots: list[Path]) -> list[Path]:
    borrowed: set[Path] = set()
    for root in roots:
        borrowed |= {src for src in _path_sources(root)
                     if src != root and root in src.parents}
    return [r for r in roots if r not in borrowed]


def _path_sources(root: Path) -> set[Path]:
    """The local directories a graph declares as sources, read from its
    configuration alone. A graph at an older format may still carry the withdrawn
    ``reexport`` key, so it is ignored here rather than refused."""
    try:
        config = tomllib.loads((root / CONFIG_NAME).read_text(encoding="utf-8"))
        declared = parse_sources(SimpleNamespace(config=config), withdrawn="ignore")
    except (OSError, ValueError, SourceError):
        return set()
    return {(root / s.path).resolve() for s in declared if not s.is_remote and s.path}


# --------------------------------------------------------------- where it lives

def _git(root: Path, *args: str) -> str | None:
    """git's answer, run in ``root``, or None where there is no repository (or no
    git) to ask."""
    try:
        proc = subprocess.run(["git", "-C", str(root), *args],
                              capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def repository_of(root: Path) -> Path | None:
    """The top of the git working tree holding ``root``, or None outside one."""
    top = _git(root, "rev-parse", "--show-toplevel")
    return Path(top.strip()).resolve() if top and top.strip() else None


def in_linked_worktree(root: Path) -> bool:
    """Whether ``root`` lies in a linked worktree — one whose git directory is not
    the repository's common directory (SR-0303)."""
    out = _git(root, "rev-parse", "--path-format=absolute",
               "--git-dir", "--git-common-dir")
    if out is None:
        return False
    lines = out.split("\n")
    return len(lines) >= 2 and Path(lines[0]).resolve() != Path(lines[1]).resolve()


def has_uncommitted_changes(root: Path) -> bool:
    """Whether anything under ``root`` differs from its last commit, tracked or
    untracked, ignored files aside (SR-0302)."""
    out = _git(root, "status", "--porcelain", "--untracked-files=all", "--", ".")
    return out is None or bool(out.strip())
