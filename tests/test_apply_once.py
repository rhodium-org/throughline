# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""A change set applied twice changes the graph once (UR-0042, SR-0280, SR-0281).

A whole set applied a second time used to create every new item again under the
next UIDs, and the duplicates passed check. These tests hold the remedy to its
requirements: a new operation, and `tl new`, may carry a key recorded on the item,
outside its fingerprint, and the same key asked for again creates nothing
(SR-0280); and an operation whose result the graph already holds is skipped
whatever revision it quotes, so a set applied again writes nothing (SR-0281).
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from throughline import apply_change_set, parse_change_set
from throughline.apply import ChangeSetError
from throughline.cli import main as _cli
from throughline.fingerprint import fingerprint, revision
from throughline.storage import load_project, write_item
from throughline.validate import validate

K1, K2 = str(uuid.uuid4()), str(uuid.uuid4())


def _scaffold(tmp_path) -> Path:
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--name", "t", "--bare"]) == 0
    assert _cli(["-C", str(root), "register", "new", "INT", "vision"]) == 0
    assert _cli(["-C", str(root), "register", "new", "FR", "features"]) == 0
    assert _cli(["-C", str(root), "new", "INT", "--type", "intent", "--title", "why"]) == 0
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "first", "--text", "first text", "--ground", "INT-0001"]) == 0
    return root


def _apply(root: Path, ops: list[dict]):
    text = json.dumps({"change_set_format": 2, "operations": ops})
    return apply_change_set(load_project(root), parse_change_set(text))


def _rev(root: Path, uid: str) -> str:
    return revision(load_project(root).get(uid))


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*.yml")}


# --------------------------------------------------------------------------- #
# SR-0280 — a new operation may carry a key, recorded on the item it creates
# --------------------------------------------------------------------------- #

def _keyed_set(root: Path) -> list[dict]:
    return [
        {"op": "new", "register": "FR", "type": "requirement", "title": "second",
         "text": "second text", "key": K1, "label": "two",
         "links": [{"target": "INT-0001", "type": "derives_from"}]},
        {"op": "new", "register": "FR", "type": "requirement", "title": "third",
         "text": "third text", "key": K2, "label": "three",
         "links": [{"target": "$two", "type": "derives_from"}]},
        {"op": "link", "uid": "$three", "target": "FR-0001", "type": "relates"},
    ]


def test_a_keyed_set_applied_twice_creates_once(tmp_path):
    root = _scaffold(tmp_path)
    first = _apply(root, _keyed_set(root))
    assert first.created == ["FR-0002", "FR-0003"]
    project = load_project(root)
    assert project.get("FR-0002").key == K1 and project.get("FR-0003").key == K2
    snapshot = _files(root)
    again = _apply(root, _keyed_set(root))
    assert again.created == [] and again.changed == [] and again.files == []
    assert [o.outcome for o in again.operations] == ["already applied"] * 3
    # The labels named the items the keys found, so the link was judged on them.
    assert [o.uid for o in again.operations] == ["FR-0002", "FR-0003", "FR-0003"]
    assert _files(root) == snapshot


def test_the_key_is_outside_the_fingerprint_and_in_the_dump(tmp_path, capsys):
    root = _scaffold(tmp_path)
    _apply(root, _keyed_set(root)[:1])
    item = load_project(root).get("FR-0002")
    keyed = fingerprint(item)
    item.key = None
    assert fingerprint(item) == keyed
    capsys.readouterr()
    assert _cli(["-C", str(root), "dump"]) == 0
    dump = json.loads(capsys.readouterr().out)
    assert next(i for i in dump["items"] if i["uid"] == "FR-0002")["key"] == K1


def test_a_key_must_be_a_canonical_uuid4(tmp_path):
    root = _scaffold(tmp_path)
    for bad in ("not-a-uuid", str(uuid.uuid1()), K1.upper()):
        ops = [{"op": "new", "register": "FR", "type": "requirement", "title": "x",
                "key": bad, "links": [{"target": "INT-0001", "type": "derives_from"}]}]
        with pytest.raises(ChangeSetError, match="SR-0280"):
            _apply(root, ops)


def test_one_key_twice_in_a_set_is_refused(tmp_path):
    root = _scaffold(tmp_path)
    ops = _keyed_set(root)
    ops[1]["key"] = K1
    with pytest.raises(ChangeSetError, match="already used in this set"):
        _apply(root, ops)


def test_tl_new_with_a_held_key_names_the_item_and_creates_nothing(tmp_path, capsys):
    root = _scaffold(tmp_path)
    argv = ["-C", str(root), "new", "FR", "--type", "requirement", "--title", "k",
            "--ground", "INT-0001", "--key", K1]
    assert _cli(argv) == 0
    capsys.readouterr()
    assert _cli(argv) == 0
    out = capsys.readouterr().out
    assert out.startswith("exists FR-0002") and "nothing created" in out
    assert load_project(root).get("FR-0003") is None


def test_a_key_on_two_items_is_an_error(tmp_path):
    root = _scaffold(tmp_path)
    _apply(root, _keyed_set(root)[:1])
    project = load_project(root)
    other = project.get("FR-0001")
    other.key = K1
    write_item(other, project.register_of("FR-0001"))
    found = [f for f in validate(load_project(root)) if f.rule == "key-collision"]
    assert sorted(f.uid for f in found) == ["FR-0001", "FR-0002"]
    assert all(f.severity == "error" for f in found)


# --------------------------------------------------------------------------- #
# SR-0281 — apply skips an operation whose result the graph already holds
# --------------------------------------------------------------------------- #

def test_held_results_are_skipped_whatever_revision_they_quote(tmp_path):
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement", "--title",
                 "gone", "--ground", "INT-0001"]) == 0
    old = {uid: _rev(root, uid) for uid in ("FR-0001", "FR-0002")}
    ops = [
        {"op": "link", "uid": "FR-0001", "revision": old["FR-0001"],
         "target": "FR-0002", "type": "relates"},
        {"op": "amend", "uid": "FR-0001", "revision": old["FR-0001"],
         "title": "renamed"},
        {"op": "unlink", "uid": "FR-0001", "revision": old["FR-0001"],
         "target": "FR-0002", "type": "refines"},
        {"op": "delete", "uid": "FR-0002", "revision": old["FR-0002"],
         "reason": "superseded"},
    ]
    first = _apply(root, ops)
    assert [o.outcome for o in first.operations][:2] == ["linked", "amended"]
    snapshot = _files(root)
    # Every revision quoted is now stale, and every result is already there.
    again = _apply(root, ops)
    assert [o.outcome for o in again.operations] == ["already applied"] * 4
    assert again.files == [] and _files(root) == snapshot


def test_a_set_that_ran_and_creates_unkeyed_items_is_refused(tmp_path):
    root = _scaffold(tmp_path)
    ops = [
        {"op": "new", "register": "FR", "type": "requirement", "title": "unkeyed",
         "links": [{"target": "INT-0001", "type": "derives_from"}]},
        {"op": "amend", "uid": "FR-0001", "revision": _rev(root, "FR-0001"),
         "title": "renamed"},
    ]
    _apply(root, ops)
    with pytest.raises(ChangeSetError) as refused:
        _apply(root, ops)
    message = str(refused.value)
    assert "changed since the export" in message and "carry no key" in message
    assert load_project(root).get("FR-0003") is None


def test_an_operation_undone_earlier_in_a_fresh_set_is_carried_out(tmp_path):
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "link", "FR-0001", "INT-0001", "--type", "relates"]) == 0
    rev = _rev(root, "FR-0001")
    ops = [
        {"op": "unlink", "uid": "FR-0001", "revision": rev, "target": "INT-0001",
         "type": "relates"},
        {"op": "link", "uid": "FR-0001", "revision": rev, "target": "INT-0001",
         "type": "relates"},
    ]
    result = _apply(root, ops)
    assert [o.outcome for o in result.operations] == ["unlinked", "linked"]
    links = [(l.target, l.type) for l in load_project(root).get("FR-0001").links]
    assert ("INT-0001", "relates") in links
