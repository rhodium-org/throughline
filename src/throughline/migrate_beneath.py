# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""``tl migrate --beneath``: every graph beneath some paths brought to the current
format (UR-0045).

Surveying writes nothing (SR-0301): a graph at the current format is behind when
a repair `tl migrate` applies to it would change it, which a dry run on a copy
finds out. Applying migrates only the graphs it is safe
to rewrite — not one with uncommitted work (SR-0302), not one in another branch's
linked worktree (SR-0303) — and commits nothing (SR-0304).
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import discovery
from .storage import (CONFIG_NAME, FORMAT_VERSION, ProjectError,
                      _read_format_version, migrate_project, repaired_copy,
                      repairs_written)


@dataclass(frozen=True)
class Found:
    """A graph the search found, and what its configuration says."""

    root: Path
    repository: Path | None   # the git working tree holding it, if any
    declared: int | None      # its format version, when it could be read
    error: str | None = None  # why it could not be, when it could not
    repairs: tuple[str, ...] = ()  # at the current format: what tl migrate would write

    @property
    def behind(self) -> bool:
        if self.declared is None:
            return False
        return self.declared < FORMAT_VERSION or bool(self.repairs)

    @property
    def ahead(self) -> bool:
        return self.declared is not None and self.declared > FORMAT_VERSION


def survey(paths: list[str | Path]) -> list[Found]:
    """Every graph beneath ``paths``, with its declared format and, for one at the
    current format, the repairs tl migrate would write (SR-0300, SR-0301). Never
    writes to a graph: the repairs are found on a copy."""
    return [_found(root) for root in discovery.graphs_beneath(*paths)]


def _found(root: Path) -> Found:
    repository = discovery.repository_of(root)
    try:
        config = tomllib.loads((root / CONFIG_NAME).read_text(encoding="utf-8"))
        declared = _read_format_version(config, root)
    except (OSError, ValueError, ProjectError) as e:
        return Found(root, repository, None, str(e).splitlines()[0])
    if declared != FORMAT_VERSION:
        return Found(root, repository, declared)
    return Found(root, repository, declared, repairs=_pending_repairs(root))


def _pending_repairs(root: Path) -> tuple[str, ...]:
    """What tl migrate would write to the graph at ``root``, found on a copy. A
    graph the dry run cannot migrate reports none, so it is not listed as behind;
    `tl -C <graph> migrate` reports why."""
    try:
        with repaired_copy(root) as (_, result):
            return repairs_written(result)
    except Exception:  # noqa: BLE001 - one graph's dry run must not stop the survey
        return ()


def skip_reason(found: Found) -> str | None:
    """Why ``--apply`` must leave this graph alone, or None to migrate it."""
    if found.repository is None:
        # Nothing would show its owner what the migration changed (SR-0302).
        return "not in a git repository — migrate it with `tl -C <path> migrate`"
    if discovery.in_linked_worktree(found.root):
        return "in a linked git worktree — another branch's to migrate"
    if discovery.has_uncommitted_changes(found.root):
        return "has uncommitted changes — commit or discard them first"
    return None


@dataclass(frozen=True)
class Outcome:
    found: Found
    skipped: str | None = None   # why it was left alone
    failed: str | None = None    # why its migration failed

    @property
    def migrated(self) -> bool:
        return self.skipped is None and self.failed is None


def apply(graphs: list[Found]) -> list[Outcome]:
    """Migrate each graph that is behind (SR-0301) and safe to rewrite,
    carrying on past any that fails (SR-0304). Sources are not resolved: the
    migration is the one a graph gets alone, which is lossless and offline."""
    outcomes: list[Outcome] = []
    for found in graphs:
        if not found.behind:
            continue
        reason = skip_reason(found)
        if reason is not None:
            outcomes.append(Outcome(found, skipped=reason))
            continue
        try:
            migrate_project(found.root)
        except Exception as e:  # noqa: BLE001 - one graph's failure must not stop the rest
            outcomes.append(Outcome(found, failed=(str(e).splitlines() or
                                                   [type(e).__name__])[0]))
            continue
        outcomes.append(Outcome(found))
    return outcomes
