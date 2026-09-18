# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Publication coverage is judged over the union, not left inert (SR-0038).

Core's validator does not read the published documents for itself — `check` reads
them and hands the set in, and a caller that omits it gets `published=None`, which
the rule reads as "not configured" and stays silent. `tl-compose check` omitted it,
so a composed project could configure `[docs] paths`, watch both gates pass, and be
told nothing. These tests hold the rule from both ends: it fires on a local item the
documents do not name, and it goes quiet when they do — because a rule that only
ever passes is indistinguishable from one that was never asked.
"""
from __future__ import annotations

from throughline.cli import main as tlc_main


def _run(path, capsys, *args) -> tuple[int, str]:
    rc = tlc_main(["-C", str(path), "check", *args])
    captured = capsys.readouterr()
    return rc, captured.out + captured.err


def test_an_unpublished_local_item_is_reported(publishing_consumer_dir, capsys):
    """The bug this closes, stated as behaviour."""
    _, out = _run(publishing_consumer_dir, capsys)
    assert "unpublished" in out
    assert "SR-0001" in out


def test_publishing_the_item_clears_it(covered_consumer_dir, capsys):
    """The other end. Without this the test above would pass against a rule that
    fires on everything, which is no gate either."""
    rc, out = _run(covered_consumer_dir, capsys, "--strict")
    assert "unpublished" not in out
    assert rc == 0


def test_it_can_fail_the_build_under_strict(publishing_consumer_dir, capsys):
    """The point of the exercise: a coverage obligation the project declared has to
    be able to gate CI, not merely print."""
    rc, _ = _run(publishing_consumer_dir, capsys, "--strict")
    assert rc == 1


def test_a_borrowed_item_stays_suppressed(publishing_consumer_dir, capsys):
    """The seam does its ordinary work, unchanged. The source's clauses are absent
    from the consumer's documents too, but the remedy for that lies in the graph
    that owns them, so SR-0026 drops those findings."""
    _, out = _run(publishing_consumer_dir, capsys)
    assert "toy:SR-0001" not in out
    assert "toy:UR-0001" not in out


def test_a_consumer_without_docs_configured_is_unaffected(consumer_dir, capsys):
    """The default. `[docs] paths` is what turns the rule on, so a project that
    configures none inherits nothing by upgrading."""
    rc, out = _run(consumer_dir, capsys, "--strict")
    assert "unpublished" not in out
    assert rc == 0
