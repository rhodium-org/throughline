# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""``tl migrate --beneath``: every graph beneath some paths brought to the current
format (UR-0045), and every graph at it that still holds a status between proposal
and signature (SR-0316).

Surveying writes nothing (SR-0301). Applying migrates only the graphs it is safe
to rewrite — not one with uncommitted work (SR-0302), not one in another branch's
linked worktree (SR-0303) — and commits nothing (SR-0304).
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import discovery
from .storage import (CONFIG_NAME, FORMAT_VERSION, ProjectError,
                      _read_format_version, migrate_project,
                      unsigned_agreement_statuses)


@dataclass(frozen=True)
class Found:
    """A graph the search found, and what its configuration says."""

    root: Path
    repository: Path | None   # the git working tree holding it, if any
    declared: int | None      # its format version, when it could be read
    error: str | None = None  # why it could not be, when it could not
    # The statuses between proposal and signature it still declares (SR-0316):
    # 5.0 retired them without a new format, so its format alone cannot say.
    retires: tuple[str, ...] = ()

    @property
    def behind(self) -> bool:
        return self.declared is not None and self.declared < FORMAT_VERSION

    @property
    def due(self) -> bool:
        """Something ``tl migrate`` would change: below the current format, or at
        it with statuses still to retire (SR-0316)."""
        return self.behind or (self.declared == FORMAT_VERSION and bool(self.retires))

    @property
    def ahead(self) -> bool:
        return self.declared is not None and self.declared > FORMAT_VERSION


def survey(paths: list[str | Path]) -> list[Found]:
    """Every graph beneath ``paths``, with its declared format (SR-0300, SR-0301).
    Reads configurations only; never writes."""
    return [_found(root) for root in discovery.graphs_beneath(*paths)]


def _found(root: Path) -> Found:
    repository = discovery.repository_of(root)
    try:
        config = tomllib.loads((root / CONFIG_NAME).read_text(encoding="utf-8"))
        return Found(root, repository, _read_format_version(config, root),
                     retires=unsigned_agreement_statuses(config))
    except (OSError, ValueError, ProjectError) as e:
        return Found(root, repository, None, str(e).splitlines()[0])


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
    """Migrate each graph that is due (behind the current format, or holding a
    status to retire, SR-0316) and safe to rewrite,
    carrying on past any that fails (SR-0304). Sources are not resolved: the
    migration is the one a graph gets alone, which is lossless and offline."""
    outcomes: list[Outcome] = []
    for found in graphs:
        if not found.due:
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
