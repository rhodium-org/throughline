# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""SR-0229 — the model a review interface draws belongs to the Tool.

The cockpit computed which items could be signed and why; the browser editor
imported that computation from the cockpit's package, because the Tool did not
offer it. Here it is the Tool's, with the same vocabulary of concerns, and the
reason an item cannot be signed is the one ratify itself would give.
"""
from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

import pytest

from throughline import (
    CONCERNS,
    Index,
    Link,
    amend_item,
    birth_item,
    flag,
    init_project,
    load_project,
    ratification_progress,
    ratify,
    withdraw,
    worklist,
    write_item,
)


def _cli(argv) -> int:
    from throughline.cli import main
    return main([str(a) for a in argv])


def _add(project, prefix: str, uid: str, **kw) -> None:
    reg = project.registers[prefix]
    ground = kw.pop("ground", None)
    item = birth_item(project.schema, reg, uid, **kw)
    if ground:
        item.links.append(Link(target=ground, type="derives_from"))
    reg.items[uid] = item
    write_item(item, reg)


@pytest.fixture
def graph(tmp_path) -> Path:
    """An intent, a machine-authored item awaiting a signature, one that cannot be
    signed from where it stands, one flagged ambiguous, and one grounded nowhere."""
    root = tmp_path / "proj"
    init_project(root, demo=False)
    project = load_project(root)
    _add(project, "INT", "INT-0001", item_type="intent", title="Why", text="V.")
    project = load_project(root)
    _add(project, "REQ", "REQ-0001", item_type="requirement", title="Proposed",
         text="The Tool shall do A.", origin="ai", ground="INT-0001")
    project = load_project(root)
    _add(project, "REQ", "REQ-0002", item_type="requirement", title="Drafted",
         text="The Tool shall do B.", ground="INT-0001")
    project = load_project(root)
    _add(project, "REQ", "REQ-0003", item_type="requirement", title="Vague",
         text="The Tool shall be fast.", origin="ai", ground="INT-0001")
    project = load_project(root)
    _add(project, "REQ", "REQ-0004", item_type="requirement", title="Orphan",
         text="The Tool shall do D.", origin="ai")
    project = load_project(root)
    flag(project, "REQ-0003", by="Scout", reason="'fast' is not measurable")
    write_item(project.get("REQ-0003"), project.register_of("REQ-0003"))
    return root


def _by_uid(entries):
    return {e.uid: e for e in entries}


def test_every_item_is_placed_and_the_reason_is_the_tools_own(graph):
    project = load_project(graph)
    entries = _by_uid(worklist(project))
    assert entries["REQ-0001"].concern == "proposed"
    assert entries["REQ-0001"].ratifiable and entries["REQ-0001"].obstacle is None
    # A human-authored item is born in a status the shipped lifecycle cannot move
    # straight to ratified, so it is blocked, and the obstacle says why.
    assert entries["REQ-0002"].concern == "blocked"
    assert not entries["REQ-0002"].ratifiable
    assert "not an allowed transition" in entries["REQ-0002"].obstacle
    assert entries["REQ-0003"].concern == "ambiguous"
    assert "flagged ambiguous" in entries["REQ-0003"].obstacle
    assert entries["REQ-0004"].concern == "ungrounded"
    assert "not grounded to a root" in entries["REQ-0004"].obstacle


def test_the_order_is_most_actionable_first_then_closest_to_the_intent(graph):
    project = load_project(graph)
    order = [e.concern for e in worklist(project)]
    assert order == sorted(order, key=CONCERNS.index)
    assert all(e.depth in (0, 1, None) for e in worklist(project))
    assert _by_uid(worklist(project))["REQ-0004"].depth is None   # reaches no root


def test_a_signed_item_leaves_the_worklist_and_a_rewritten_one_returns(graph):
    project = load_project(graph)
    ratify(project, "REQ-0001", by="Ada", index=Index.build(project))
    write_item(project.get("REQ-0001"), project.register_of("REQ-0001"))

    project = load_project(graph)
    assert "REQ-0001" not in _by_uid(worklist(project))
    settled = _by_uid(worklist(project, include_settled=True))["REQ-0001"]
    assert settled.concern == "ratified" and settled.ratified and not settled.stale

    project = load_project(graph)
    amend_item(project, "REQ-0001", text="The Tool shall do A, within 200 ms.")
    write_item(project.get("REQ-0001"), project.register_of("REQ-0001"))
    back = _by_uid(worklist(load_project(graph)))["REQ-0001"]
    assert back.concern == "stale" and back.stale and back.ratified


def test_a_withdrawn_signature_puts_the_item_back(graph):
    project = load_project(graph)
    ratify(project, "REQ-0001", by="Ada", index=Index.build(project))
    write_item(project.get("REQ-0001"), project.register_of("REQ-0001"))
    project = load_project(graph)
    withdraw(project, ["REQ-0001"], by="Ada", reason="signed in error")
    write_item(project.get("REQ-0001"), project.register_of("REQ-0001"))
    entry = _by_uid(worklist(load_project(graph)))["REQ-0001"]
    assert entry.concern in ("proposed", "ready") and entry.ratifiable


def test_progress_counts_a_rewritten_signature_as_outstanding(graph):
    project = load_project(graph)
    before = ratification_progress(project)
    ratify(project, "REQ-0001", by="Ada", index=Index.build(project))
    write_item(project.get("REQ-0001"), project.register_of("REQ-0001"))
    project = load_project(graph)
    assert ratification_progress(project) == (before[0] + 1, before[1])
    amend_item(project, "REQ-0001", text="Rewritten after the signature.")
    write_item(project.get("REQ-0001"), project.register_of("REQ-0001"))
    assert ratification_progress(load_project(graph)) == before


def test_a_tombstone_is_never_actionable(graph, capsys):
    assert _cli(["-C", graph, "delete", "REQ-0001", "--reason", "gone"]) == 0
    capsys.readouterr()
    entries = _by_uid(worklist(load_project(graph), include_settled=True))
    assert entries["REQ-0001"].concern == "deleted"
    assert not entries["REQ-0001"].ratifiable


def test_the_model_needs_no_subprocess_socket_or_terminal(graph, monkeypatch):
    """SR-0227: a browser draws this, and a browser has none of the three."""
    def refuse(*_a, **_k):
        raise OSError("this host has no subprocess or socket")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    project = load_project(graph)
    assert {e.uid for e in worklist(project)} == {
        "INT-0001", "REQ-0001", "REQ-0002", "REQ-0003", "REQ-0004"}
    assert ratification_progress(project) == (0, 5)
