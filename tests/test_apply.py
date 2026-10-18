# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""`tl dump` states each item's fingerprint and revision (SR-0251) and `tl apply`
carries out a change set of the Tool's own operations, all or nothing (SR-0252).

An assistant that reads a few files, cannot run commands and returns only text
works from an export and sends back a change set quoting the revision each item
carried. These tests hold the two halves to their requirements: the dump
carries the fingerprint the Tool computes, and apply refuses a set whole — on a
stale revision, a malformed operation, an operation an assistant may not take,
or a refusal from the operation itself — changing nothing, and otherwise carries
out every operation through the matching command's own function, recording what
it creates or changes with origin ai.
"""
from __future__ import annotations

import json
from pathlib import Path

from throughline import apply_change_set, build_dump, parse_change_set
from throughline.apply import ChangeSetError
from throughline.cli import main as _cli
from throughline.dump import DUMP_SCHEMA_VERSION
from throughline.fingerprint import fingerprint, revision
from throughline.storage import load_project


def _scaffold(tmp_path) -> Path:
    """A bare project with an intent root and one grounded requirement."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--name", "t", "--bare"]) == 0
    assert _cli(["-C", str(root), "register", "new", "INT", "vision"]) == 0
    assert _cli(["-C", str(root), "register", "new", "FR", "features"]) == 0
    assert _cli(["-C", str(root), "new", "INT", "--type", "intent",
                 "--title", "why"]) == 0
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "original title", "--text", "original text",
                 "--ground", "INT-0001"]) == 0
    return root


def _item(root: Path, uid: str):
    return load_project(root).get(uid)


def _fp(root: Path, uid: str) -> str:
    """The fingerprint the export gives — read from the dump, as an assistant
    would, not computed by the test."""
    dump = build_dump(load_project(root), "9.9.9")
    return next(i["fingerprint"] for i in dump["items"] if i["uid"] == uid)


def _rev(root: Path, uid: str) -> str:
    """The revision the export gives, read from the dump as an assistant would."""
    dump = build_dump(load_project(root), "9.9.9")
    return next(i["revision"] for i in dump["items"] if i["uid"] == uid)


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


def _set(root: Path, operations, name="set.json") -> Path:
    path = root.parent / name
    path.write_text(json.dumps(operations), encoding="utf-8")
    return path


def _sound(root: Path) -> list[dict]:
    return [
        {"op": "new", "register": "FR", "type": "requirement", "title": "a second",
         "text": "The Tool shall do a second thing.", "rationale": "because",
         "links": [{"target": "INT-0001", "type": "derives_from"}], "label": "fr2"},
        {"op": "amend", "uid": "FR-0001", "revision": _rev(root, "FR-0001"),
         "text": "rewritten by an assistant"},
        {"op": "link", "uid": "$fr2", "target": "FR-0001", "type": "derives_from"},
        {"op": "unlink", "uid": "$fr2", "target": "FR-0001", "type": "derives_from"},
    ]


# --------------------------------------------------------------------------- #
# SR-0251 — the dump carries each item's current fingerprint
# --------------------------------------------------------------------------- #

def test_the_dump_states_each_items_fingerprint_under_its_documented_key(tmp_path):
    root = _scaffold(tmp_path)
    project = load_project(root)
    dump = build_dump(project, "9.9.9")
    assert dump["throughline_dump"]["dump_schema_version"] == DUMP_SCHEMA_VERSION
    assert dump["items"], "the scaffold has items"
    for entry in dump["items"]:
        assert entry["fingerprint"] == fingerprint(project.get(entry["uid"]),
                                                   project.schema)


def test_the_dump_states_each_items_revision_beside_its_fingerprint(tmp_path):
    root = _scaffold(tmp_path)
    project = load_project(root)
    for entry in build_dump(project, "9.9.9")["items"]:
        assert entry["revision"] == revision(project.get(entry["uid"]))
    before = _rev(root, "FR-0001"), _fp(root, "FR-0001")
    assert _cli(["-C", str(root), "amend", "FR-0001", "--title", "moved"]) == 0
    # a title is not normative: the fingerprint stands and the revision moves
    assert _fp(root, "FR-0001") == before[1]
    assert _rev(root, "FR-0001") != before[0]


def test_the_command_line_dump_carries_it_too(tmp_path, capsys):
    root = _scaffold(tmp_path)
    capsys.readouterr()                      # the scaffold's own output
    assert _cli(["-C", str(root), "dump"]) == 0
    dump = json.loads(capsys.readouterr().out)
    assert all(i["fingerprint"].startswith("sha256:") for i in dump["items"])
    assert all(i["revision"].startswith("sha256:") for i in dump["items"])


# --------------------------------------------------------------------------- #
# SR-0252 — tl apply
# --------------------------------------------------------------------------- #

def test_describe_prints_the_form_an_assistant_is_given(tmp_path, capsys):
    assert _cli(["-C", str(tmp_path), "apply", "--describe"]) == 0
    out = capsys.readouterr().out
    for op in ("new", "amend", "link", "unlink", "delete"):
        assert f'"op": "{op}"' in out
    assert '"revision": "sha256:...' in out and "all or nothing" in out
    # what an assistant is told to write is format 2 only: no fingerprint key
    assert '"fingerprint"' not in out


def test_a_sound_set_is_carried_out_in_order_and_everything_is_proposed_origin_ai(
        tmp_path, capsys):
    root = _scaffold(tmp_path)
    path = _set(root, _sound(root))
    assert _cli(["-C", str(root), "apply", str(path)]) == 0
    out = capsys.readouterr().out
    assert "applied 4 operation(s)" in out
    second = _item(root, "FR-0002")
    assert second is not None
    assert second.title == "a second"
    assert second.rationale == "because"
    assert second.status == "proposed"
    assert second.attrs["origin"] == "ai"
    # linked in #3 and unlinked in #4, so only the grounding link remains
    assert [(l.target, l.type) for l in second.links] == [("INT-0001", "derives_from")]
    first = _item(root, "FR-0001")
    assert first.text == "rewritten by an assistant"
    assert first.attrs["origin"] == "ai"
    assert first.title == "original title"


def test_a_stale_revision_refuses_the_whole_set_and_names_the_operation(
        tmp_path, capsys):
    root = _scaffold(tmp_path)
    before = _tree(root)
    stale = "sha256:" + "0" * 64
    ops = _sound(root)
    ops[1]["revision"] = stale
    path = _set(root, ops)
    assert _cli(["-C", str(root), "apply", str(path)]) == 1
    out = capsys.readouterr().out
    assert "refused" in out and "#2 amend" in out and "changed since the export" in out
    assert _tree(root) == before          # the sound #1 was not carried out either


def test_a_malformed_set_names_every_offending_operation(tmp_path, capsys):
    root = _scaffold(tmp_path)
    before = _tree(root)
    rev = _rev(root, "FR-0001")
    path = _set(root, [
        {"op": "amend", "revision": rev, "text": "no uid"},
        {"op": "link", "uid": "FR-0001", "revision": rev, "target": "INT-0001",
         "type": "derives_from", "colour": "red"},
        {"op": "ratify", "uid": "FR-0001"},
        {"op": "delete", "uid": "FR-0001"},
        {"op": "amend", "uid": "$nobody", "text": "a label nothing created"},
    ])
    assert _cli(["-C", str(root), "apply", str(path)]) == 1
    out = capsys.readouterr().out
    assert "#1 amend: missing uid" in out
    assert "#2 link: unknown key(s) colour" in out
    assert "#3: 'ratify' is a decision" in out
    assert "#4 delete FR-0001: an operation on an existing item carries the" in out
    assert "#5 amend: $nobody names no item created earlier" in out
    assert _tree(root) == before


def test_a_set_that_is_not_a_list_is_refused(tmp_path, capsys):
    root = _scaffold(tmp_path)
    path = root.parent / "bad.json"
    path.write_text('{"uid": "FR-0001"}', encoding="utf-8")
    assert _cli(["-C", str(root), "apply", str(path)]) == 1
    assert "must be a JSON list" in capsys.readouterr().out
    path.write_text("not json", encoding="utf-8")
    assert _cli(["-C", str(root), "apply", str(path)]) == 1
    assert "not JSON" in capsys.readouterr().out


def test_a_refusal_from_an_operation_restores_every_file(tmp_path, capsys):
    """The amend in #1 is sound and is written; the unlink in #2 would leave
    FR-0001 reaching no root, which `tl unlink` refuses — so the whole set is
    refused and #1 is undone."""
    root = _scaffold(tmp_path)
    before = _tree(root)
    rev = _rev(root, "FR-0001")
    path = _set(root, [
        {"op": "amend", "uid": "FR-0001", "revision": rev, "text": "changed"},
        {"op": "unlink", "uid": "FR-0001", "revision": rev, "target": "INT-0001"},
    ])
    assert _cli(["-C", str(root), "apply", str(path)]) == 1
    out = capsys.readouterr().out
    assert "#2 unlink FR-0001" in out and "reaching no root" in out
    assert _tree(root) == before


def test_a_dry_run_reports_the_set_and_changes_nothing(tmp_path, capsys):
    root = _scaffold(tmp_path)
    before = _tree(root)
    path = _set(root, _sound(root))
    assert _cli(["-C", str(root), "apply", str(path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "dry run" in out and "#1 created FR-0002" in out
    assert _tree(root) == before


def test_json_output_reports_each_operation_and_the_files(tmp_path, capsys):
    root = _scaffold(tmp_path)
    path = _set(root, _sound(root))
    capsys.readouterr()                      # the scaffold's own output
    assert _cli(["-C", str(root), "apply", str(path), "--format", "json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["applied"] is True
    assert [o["outcome"] for o in report["operations"]] == \
        ["created", "amended", "linked", "unlinked"]
    assert report["created"] == ["FR-0002"] and report["changed"] == ["FR-0001"]
    assert sorted(report["files"]) == ["features/FR-0001.yml", "features/FR-0002.yml"]
    # a refusal is reported the same way
    path = _set(root, [{"op": "delete", "uid": "FR-0009", "revision": "sha256:" + "a" * 64}])
    assert _cli(["-C", str(root), "apply", str(path), "--format", "json"]) == 1
    refusal = json.loads(capsys.readouterr().out)
    assert refusal["applied"] is False and "FR-0009 does not exist" in refusal["problems"][0]


def test_a_delete_tombstones_and_an_operation_on_a_tombstone_is_refused(tmp_path):
    root = _scaffold(tmp_path)
    rev = _rev(root, "FR-0001")
    path = _set(root, [{"op": "delete", "uid": "FR-0001", "revision": rev,
                        "reason": "withdrawn by the assistant"}])
    assert _cli(["-C", str(root), "apply", str(path)]) == 0
    gone = _item(root, "FR-0001")
    assert gone.is_deleted and gone.deleted["reason"] == "withdrawn by the assistant"
    assert gone.attrs["origin"] == "ai"
    path = _set(root, [{"op": "amend", "uid": "FR-0001", "revision": rev,
                        "text": "back from the dead"}])
    assert _cli(["-C", str(root), "apply", str(path)]) == 1


def test_the_wrapped_form_is_accepted_and_the_library_needs_no_command_line(tmp_path):
    root = _scaffold(tmp_path)
    text = json.dumps({"change_set_format": 2, "operations": _sound(root)})
    operations = parse_change_set(text)
    project = load_project(root)
    result = apply_change_set(project, operations)
    assert result.created == ["FR-0002"] and result.changed == ["FR-0001"]
    assert [o.outcome for o in result.operations] == \
        ["created", "amended", "linked", "unlinked"]
    # the same set again is stale: FR-0001 moved
    try:
        apply_change_set(load_project(root), parse_change_set(text))
    except ChangeSetError as e:
        assert any("FR-0001 changed since the export" in p for p in e.problems)
    else:
        raise AssertionError("a stale set was applied")


# --------------------------------------------------------------------------- #
# SR-0252 — a change to any field since the export refuses the set
# --------------------------------------------------------------------------- #

import pytest  # noqa: E402

_EDITS = {
    "title": ["amend", "FR-0001", "--title", "moved by someone else"],
    "rationale": ["amend", "FR-0001", "--rationale", "a newer reason"],
    "attribute": ["amend", "FR-0001", "--attr", "owner=someone"],
    "link": ["link", "FR-0001", "FR-0002", "--type", "relates"],
    "status": ["status", "FR-0001", "deferred"],
}


@pytest.mark.parametrize("edit", sorted(_EDITS))
def test_an_edit_after_the_export_that_moves_no_fingerprint_still_refuses_the_set(
        tmp_path, capsys, edit):
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "other", "--text", "other", "--ground", "INT-0001"]) == 0
    if edit == "attribute":
        assert _cli(["-C", str(root), "schema", "attr", "add", "requirement",
                     "owner", "--kind", "string", "--because", "a test"]) == 0
    exported = _rev(root, "FR-0001"), _fp(root, "FR-0001")
    assert _cli(["-C", str(root), *_EDITS[edit]]) == 0
    assert _fp(root, "FR-0001") == exported[1], "the edit is not normative"
    before = _tree(root)
    path = _set(root, [{"op": "amend", "uid": "FR-0001", "revision": exported[0],
                        "title": "the assistant's title"}])
    capsys.readouterr()
    assert _cli(["-C", str(root), "apply", str(path)]) == 1
    out = capsys.readouterr().out
    assert "#1 amend: FR-0001 changed since the export" in out
    assert _tree(root) == before


def test_a_format_1_set_quoting_fingerprints_is_still_read(tmp_path):
    root = _scaffold(tmp_path)
    fp = _fp(root, "FR-0001")
    for text in (json.dumps([{"op": "amend", "uid": "FR-0001", "fingerprint": fp,
                              "title": "bare list"}]),
                 json.dumps({"change_set_format": 1, "operations": [
                     {"op": "amend", "uid": "FR-0001", "fingerprint": fp,
                      "title": "wrapped"}]})):
        result = apply_change_set(load_project(root), parse_change_set(text))
        assert result.changed == ["FR-0001"]
    assert _item(root, "FR-0001").title == "wrapped"


def test_format_2_takes_only_the_revision_and_format_1_only_the_fingerprint(tmp_path):
    root = _scaffold(tmp_path)
    fp, rev = _fp(root, "FR-0001"), _rev(root, "FR-0001")
    cases = [
        ({"change_set_format": 2, "operations": [
            {"op": "amend", "uid": "FR-0001", "fingerprint": fp, "title": "x"}]},
         "unknown key(s) fingerprint"),
        ({"change_set_format": 1, "operations": [
            {"op": "amend", "uid": "FR-0001", "revision": rev, "title": "x"}]},
         "unknown key(s) revision"),
        ([{"op": "amend", "uid": "FR-0001", "revision": rev, "title": "x"},
          {"op": "amend", "uid": "FR-0001", "fingerprint": fp, "title": "y"}],
         "#2 amend: unknown key(s) fingerprint"),
        ({"change_set_format": 3, "operations": []}, "is not one this release reads"),
    ]
    for data, expected in cases:
        with pytest.raises(ChangeSetError) as e:
            parse_change_set(json.dumps(data))
        assert any(expected in p for p in e.value.problems), (expected, e.value.problems)
