# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""A dead item grounds nothing in a composed project either (SR-0347).

tl 5.10.0 asked the grounding question of a local orphan a second time, to let a
chain into a source count, and asked it without the rule of SR-0344: the orphan
was dropped from a composed check while ``tl ratify`` refused the same item."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from throughline.cli import main as tl
from throughline.composition import compose
from throughline.graph import Index
from throughline.seam import apply_seam, grounded_at_seam
from throughline.storage import load_project
from throughline.validate import validate


@pytest.fixture
def consumer(consumer_dir: Path) -> Path:
    """The consumer without its link rules, so one requirement may ground in
    another and the only findings are the grounding ones."""
    toml = consumer_dir / "throughline.toml"
    toml.write_text(re.sub(r"\[link_rules\]\n(?:\w.*\n)+", "",
                           toml.read_text(encoding="utf-8")), encoding="utf-8")
    return consumer_dir


def _sr(root: Path, uid: str, links: list[tuple[str, str]]) -> None:
    body = (f"uid: {uid}\ntype: system_requirement\nstatus: proposed\n"
            f"title: {uid} fixture clause\ntext: A clause of the consumer.\n"
            "links:\n" + "".join(f"- target: {t}\n  type: {k}\n" for t, k in links)
            + "attrs:\n  priority: must\n  origin: human\n")
    (root / "system-requirements" / f"{uid}.yml").write_text(body, encoding="utf-8")


def _reject(root: Path, uid: str) -> None:
    assert tl(["-C", str(root), "status", uid, "rejected"]) == 0


def _check(root: Path, capsys) -> tuple[int, list[str], str]:
    capsys.readouterr()
    rc = tl(["-C", str(root), "check", "--base", ""])
    said = capsys.readouterr()
    return rc, [l for l in said.out.splitlines() if l.startswith("[")], said.err


def _named(findings, uid, rule) -> bool:
    return any(re.search(rf"\s{re.escape(uid)}\s+{rule}\b", line) for line in findings)


# ------------------------------------------------ the dead item is borrowed

def test_a_local_item_grounded_only_in_a_rejected_borrowed_item_is_an_orphan(
        consumer, source_dir, capsys):
    """SR-0347: the owner's ruling. What the follower relied on solely is dead."""
    _sr(consumer, "SR-0002", [("toy:UR-0001", "implements")])
    rc, found, _ = _check(consumer, capsys)
    assert rc == 0 and not _named(found, "SR-0002", "orphan")

    _reject(source_dir, "UR-0001")
    rc, found, _ = _check(consumer, capsys)

    assert rc == 1
    assert _named(found, "SR-0002", "orphan")


def test_a_local_item_grounded_only_in_a_retired_borrowed_item_is_an_orphan(
        consumer, source_dir, capsys):
    """SR-0347: a tombstone that stands is dead as a rejected item is."""
    _sr(consumer, "SR-0002", [("toy:UR-0001", "implements")])
    assert tl(["-C", str(source_dir), "delete", "UR-0001", "--reason", "gone"]) == 0
    from throughline.signatures import retired
    source = load_project(source_dir)
    assert retired(source.get("UR-0001"), source.schema)

    _rc, found, _ = _check(consumer, capsys)

    assert _named(found, "SR-0002", "orphan")


def test_nothing_more_is_reported_on_the_borrowed_items(consumer, source_dir, capsys):
    """SR-0347 changes what a local item is told, not what the seam reports on a
    borrowed one: the source's own requirement under its rejected need is an
    orphan in the union and stays the owning graph's to answer (SR-0026)."""
    _reject(source_dir, "UR-0001")

    rc, found, _ = _check(consumer, capsys)

    assert rc == 0
    assert not any("toy:" in line for line in found)


def test_a_chain_that_reaches_a_live_borrowed_item_counts_as_before(
        consumer, source_dir, capsys):
    """SR-0347: whether a live borrowed item is itself grounded inside its source
    is the owning graph's finding. The consumer's item reached the source."""
    _sr(consumer, "SR-0002", [("toy:SR-0001", "derives_from")])
    _reject(source_dir, "UR-0001")           # toy:SR-0001's only parent

    rc, found, _ = _check(consumer, capsys)

    assert rc == 0
    assert not _named(found, "SR-0002", "orphan")


# --------------------------------------------------- the dead item is local

def test_a_local_item_grounded_only_in_a_rejected_local_item_is_an_orphan(
        consumer, capsys):
    """SR-0347: the case tl 5.10.0 reported in a graph alone and dropped in a
    project with sources."""
    _sr(consumer, "SR-0002", [("INT-0001", "derives_from")])
    _sr(consumer, "SR-0003", [("SR-0002", "derives_from")])
    rc, found, _ = _check(consumer, capsys)
    assert rc == 0 and not _named(found, "SR-0003", "orphan")

    _reject(consumer, "SR-0002")
    rc, found, err = _check(consumer, capsys)

    assert rc == 1
    assert _named(found, "SR-0003", "orphan")
    assert not _named(found, "SR-0002", "orphan")        # a record kept (SR-0344)
    # SR-0001, SR-0002 (dead, asked the earlier question) and not SR-0003.
    assert "Grounding  2/3 local non-root items trace to a root" in err


def test_a_second_chain_that_is_live_keeps_the_item_grounded(
        consumer, source_dir, capsys):
    """SR-0347: every chain must pass through a dead item, on either side."""
    _sr(consumer, "SR-0002", [("INT-0001", "derives_from")])
    _sr(consumer, "SR-0003", [("SR-0002", "derives_from"), ("toy:UR-0001", "implements")])
    _reject(consumer, "SR-0002")
    _rc, found, _ = _check(consumer, capsys)
    assert not _named(found, "SR-0003", "orphan")        # the source still answers

    _reject(source_dir, "UR-0001")
    _rc, found, _ = _check(consumer, capsys)

    assert _named(found, "SR-0003", "orphan")


def test_a_dead_local_item_under_a_dead_item_is_a_record_kept(
        consumer, source_dir, capsys):
    """SR-0344's earlier question is asked of a dead item here as well."""
    _sr(consumer, "SR-0002", [("toy:UR-0001", "implements")])
    _reject(source_dir, "UR-0001")
    _reject(consumer, "SR-0002")

    rc, found, _ = _check(consumer, capsys)

    assert rc == 0
    assert not _named(found, "SR-0002", "orphan")


def test_a_local_root_served_only_by_dead_items_is_unserved(consumer, capsys):
    """SR-0344 over a union: the consumer's intent, answered only by a rejected
    requirement, is reported."""
    rc, found, _ = _check(consumer, capsys)
    assert rc == 0 and not _named(found, "INT-0001", "unserved-root")

    _reject(consumer, "SR-0001")
    rc, found, _ = _check(consumer, capsys)

    assert rc == 1
    assert _named(found, "INT-0001", "unserved-root")


# ----------------------------------------- one rule, asked in three places

@pytest.mark.parametrize("parent, dead_in", [
    ("SR-0002", "consumer"),
    ("toy:UR-0001", "source"),
])
def test_ratify_and_check_agree(consumer, source_dir, capsys, parent, dead_in):
    """SR-0347: tl ratify refused the item all along; the check now reports the
    reason it would."""
    _sr(consumer, "SR-0002", [("INT-0001", "derives_from")])
    _sr(consumer, "SR-0003", [(parent, "derives_from")])
    if dead_in == "consumer":
        _reject(consumer, "SR-0002")
    else:
        _reject(source_dir, "UR-0001")

    _rc, found, _ = _check(consumer, capsys)
    rc = tl(["-C", str(consumer), "ratify", "SR-0003", "--by", "tester"])

    assert _named(found, "SR-0003", "orphan")
    assert rc == 2
    assert "SR-0003 is not grounded to a root" in capsys.readouterr().err


def test_the_seam_rescues_nothing_whose_chain_is_dead(consumer, source_dir):
    """SR-0347: the finding and the summary's figure are one question, and the
    orphan the seam is handed is kept, not rescued."""
    _sr(consumer, "SR-0002", [("toy:UR-0001", "implements")])
    _sr(consumer, "SR-0003", [("toy:INT-0001", "derives_from")])
    _reject(source_dir, "UR-0001")
    union = compose(load_project(consumer)).union
    schema, index = union.project.schema, Index.build(union.project)

    kept, _suppressed, rescued = apply_seam(
        validate(union.project), union, schema, index)

    assert [f.uid for f in kept if f.rule == "orphan"] == ["SR-0002"]
    assert "SR-0002" not in {f.uid for f in rescued}
    assert not grounded_at_seam(union, schema, index, "SR-0002")
    assert grounded_at_seam(union, schema, index, "SR-0003")
