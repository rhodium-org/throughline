# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""The content a ratification was taken over, recorded beside its stamp (SR-0215,
SR-0216, SR-0217, SR-0218, SR-0219).

Several of these deliberately run with no git repository at all: showing what
changed without version control is the point of recording the content.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from throughline.cli import main as cli_main
from throughline.fingerprint import content_fingerprint, fingerprint
from throughline.ratification import (
    CHANGED,
    CONTENT_ATTR,
    HISTORY,
    RECORD,
    STAMP_ATTR,
    change_since_ratification,
    render_change,
)
from throughline.storage import load_project, migrate_project, write_item
from throughline.validate import validate

ORIGINAL = "The Tool shall evict a cached widget after 3600 seconds."
REWORDED = "The Tool shall evict a cached widget after 900 seconds."


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True).stdout


def _commit(root: Path, message: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)


def _project(tmp_path: Path, *, git: bool) -> Path:
    """One human intent and one AI requirement with a normative priority, ratified."""
    root = tmp_path / "proj"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    assert _cli(["-C", root, "schema", "attr", "add", "intent", "origin",
                 "--kind", "enum", "--values", "human,ai,hybrid",
                 "--because", "the fixture records who authored each intent"]) == 0
    if git:
        _git(root, "init", "-q")
        _git(root, "config", "user.email", "t@t")
        _git(root, "config", "user.name", "t")
    assert _cli(["-C", root, "new", "INT", "--type", "intent", "--title", "Why",
                 "--text", "The vision.", "--origin", "human", "--no-interactive"]) == 0
    assert _cli(["-C", root, "new", "REQ", "--title", "Cache expires",
                 "--text", ORIGINAL, "--ground", "INT-0001", "--ground-type",
                 "implements", "--origin", "ai", "--attr", "priority=should",
                 "--no-interactive"]) == 0
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada Lovelace"]) == 0
    if git:
        _commit(root, "ratified REQ-0001")
    return root


def _drift(root: Path) -> None:
    assert _cli(["-C", root, "amend", "REQ-0001", "--text", REWORDED,
                 "--attr", "priority=must"]) == 0


def _req(root: Path):
    return load_project(root).get("REQ-0001")


def _set_attr(root: Path, name: str, value) -> None:
    """Stand in for a record written by an older tool, or edited by hand."""
    item = _req(root)
    if value is None:
        item.attrs.pop(name, None)
    else:
        item.attrs[name] = value
    write_item(item)


def _rules(root: Path) -> list[str]:
    project = load_project(root)
    return [f.rule for f in validate(project) if f.uid == "REQ-0001"]


# ------------------------------------------------- recording at signature (SR-0215)

def test_a_ratification_records_the_content_its_stamp_was_taken_over(tmp_path):
    root = _project(tmp_path, git=False)
    item = _req(root)
    content = item.attrs[CONTENT_ATTR]

    assert content["text"] == ORIGINAL
    assert content["type"] == item.type
    assert content["normative"] is True and content["derived"] is False
    assert content["attrs"] == {"priority": "should"}
    # With the UID it reproduces the stamp beside it, and the stamp is the item's.
    assert content_fingerprint(item.authored_uid, content) == item.attrs[STAMP_ATTR]
    assert item.attrs[STAMP_ATTR] == fingerprint(item, load_project(root).schema)


def test_a_re_ratification_records_the_new_content(tmp_path):
    root = _project(tmp_path, git=False)
    _drift(root)
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada Lovelace",
                 "--accept-change"]) == 0
    item = _req(root)

    assert item.attrs[CONTENT_ATTR]["text"] == REWORDED
    assert item.attrs[CONTENT_ATTR]["attrs"] == {"priority": "must"}
    assert content_fingerprint(item.authored_uid, item.attrs[CONTENT_ATTR]) \
        == item.attrs[STAMP_ATTR]


def test_a_corrected_ratifier_keeps_content_that_reproduces_the_stamp(tmp_path):
    root = _project(tmp_path, git=True)
    assert _cli(["-C", root, "new", "REQ", "--title", "Uncommitted", "--text",
                 "Signed and corrected before any commit.", "--ground", "INT-0001",
                 "--ground-type", "implements", "--origin", "ai",
                 "--no-interactive"]) == 0
    assert _cli(["-C", root, "ratify", "REQ-0002", "--by", "Ada Lovelase"]) == 0
    assert _cli(["-C", root, "ratify", "REQ-0002", "--by", "Ada Lovelace",
                 "--replacing"]) == 0
    item = load_project(root).get("REQ-0002")

    assert item.attrs["ratified_by"] == "Ada Lovelace"
    assert content_fingerprint(item.authored_uid, item.attrs[CONTENT_ATTR]) \
        == item.attrs[STAMP_ATTR]


def test_migration_binding_an_unstamped_record_records_its_content(tmp_path):
    root = _project(tmp_path, git=False)
    item = _req(root)
    for name in (STAMP_ATTR, CONTENT_ATTR):
        item.attrs.pop(name)
    write_item(item)

    result = migrate_project(root)
    item = _req(root)

    assert list(result.bound) == ["REQ-0001"]
    assert item.attrs["ratified_backfilled"] is True
    assert content_fingerprint(item.authored_uid, item.attrs[CONTENT_ATTR]) \
        == item.attrs[STAMP_ATTR]


# ---------------------------------------- what changed, read from the record (SR-0216)

def test_what_changed_is_shown_with_no_version_control_at_all(tmp_path):
    root = _project(tmp_path, git=False)
    _drift(root)
    project = load_project(root)
    change = change_since_ratification(project, project.get("REQ-0001"))

    assert change.outcome == CHANGED
    assert change.source == RECORD
    assert change.revision is None
    fields = {c.field: (c.was, c.now) for c in change.changes}
    assert fields["text"] == (ORIGINAL, REWORDED)
    assert fields["attrs.priority"] == ("should", "must")
    assert change.as_dict()["source"] == RECORD

    rendered = "\n".join(render_change(change, ratifier="Ada Lovelace"))
    assert "ratified content from the record" in rendered


def test_a_record_without_content_resolves_from_history_as_before(tmp_path):
    root = _project(tmp_path, git=True)
    _set_attr(root, CONTENT_ATTR, None)
    _commit(root, "an older record")
    _drift(root)
    project = load_project(root)
    change = change_since_ratification(project, project.get("REQ-0001"))

    assert change.outcome == CHANGED
    assert change.source == HISTORY
    assert change.revision and len(change.revision) == 40
    assert {c.field: c.was for c in change.changes}["text"] == ORIGINAL


def test_content_that_does_not_reproduce_its_stamp_is_never_shown(tmp_path):
    root = _project(tmp_path, git=True)
    forged = dict(_req(root).attrs[CONTENT_ATTR], text="Words nobody signed.")
    _set_attr(root, CONTENT_ATTR, forged)
    _commit(root, "a hand edit to the record")
    _drift(root)
    project = load_project(root)
    change = change_since_ratification(project, project.get("REQ-0001"))

    assert change.source == HISTORY
    was = {c.field: c.was for c in change.changes}["text"]
    assert was == ORIGINAL
    assert "Words nobody signed." not in "\n".join(render_change(change))


# ------------------------------------------------- the mismatch finding (SR-0217)

def test_recorded_content_that_does_not_reproduce_the_stamp_is_an_error(tmp_path, capsys):
    root = _project(tmp_path, git=False)
    forged = dict(_req(root).attrs[CONTENT_ATTR], text="Words nobody signed.")
    _set_attr(root, CONTENT_ATTR, forged)

    findings = [f for f in validate(load_project(root)) if f.uid == "REQ-0001"]
    mismatch = [f for f in findings if f.rule == "ratified-content-mismatch"]
    assert len(mismatch) == 1 and mismatch[0].severity == "error"
    assert _cli(["-C", root, "check"]) == 1


@pytest.mark.parametrize("malformed", ["just a string", {"text": ORIGINAL}, []])
def test_malformed_recorded_content_is_reported(tmp_path, malformed):
    root = _project(tmp_path, git=False)
    _set_attr(root, CONTENT_ATTR, malformed)
    assert "ratified-content-mismatch" in _rules(root)


def test_a_record_holding_no_content_gains_no_finding(tmp_path):
    root = _project(tmp_path, git=False)
    _set_attr(root, CONTENT_ATTR, None)
    assert "ratified-content-mismatch" not in _rules(root)
    _drift(root)                                     # stale, but still no mismatch
    assert "ratified-content-mismatch" not in _rules(root)


def test_a_sound_record_gains_no_finding_even_when_the_item_is_stale(tmp_path):
    root = _project(tmp_path, git=False)
    _drift(root)
    rules = _rules(root)
    assert "ratified-stale" in rules
    assert "ratified-content-mismatch" not in rules


# ----------------------------------- migration completes older records (SR-0218)

def test_migration_records_content_from_the_item_when_it_still_reproduces_the_stamp(tmp_path):
    root = _project(tmp_path, git=False)
    _set_attr(root, CONTENT_ATTR, None)
    before = _req(root).attrs.copy()

    result = migrate_project(root)
    item = _req(root)

    assert result.contents == {"REQ-0001": "current"}
    assert result.unprovable == []
    assert item.attrs[CONTENT_ATTR]["text"] == ORIGINAL
    for name in ("ratified_by", STAMP_ATTR):
        assert item.attrs[name] == before[name]
    assert item.status == load_project(root).schema.status_role("ratified")


def test_migration_records_content_from_the_revision_the_stamp_reproduces(tmp_path):
    root = _project(tmp_path, git=True)
    _set_attr(root, CONTENT_ATTR, None)
    _commit(root, "an older record")
    _drift(root)
    _commit(root, "drift")

    result = migrate_project(root)
    item = _req(root)

    assert len(result.contents["REQ-0001"]) == 40     # the revision it came from
    assert item.attrs[CONTENT_ATTR]["text"] == ORIGINAL
    assert item.attrs[CONTENT_ATTR]["attrs"] == {"priority": "should"}
    assert item.text == REWORDED                      # the item itself is untouched
    assert content_fingerprint(item.authored_uid, item.attrs[CONTENT_ATTR]) \
        == item.attrs[STAMP_ATTR]


def test_migration_leaves_a_record_whose_content_cannot_be_proved(tmp_path, capsys):
    root = _project(tmp_path, git=False)             # no history to prove it from
    _set_attr(root, CONTENT_ATTR, None)
    _drift(root)

    result = migrate_project(root)

    assert result.contents == {}
    assert result.unprovable == ["REQ-0001"]
    assert CONTENT_ATTR not in _req(root).attrs

    assert _cli(["-C", root, "migrate"]) == 0
    out = capsys.readouterr().out
    assert "could not prove the signed content of 1 ratification record(s)" in out
    assert "nothing to migrate" not in out


def test_migration_keeps_the_backfilled_marking_and_is_idempotent(tmp_path, capsys):
    root = _project(tmp_path, git=False)
    _set_attr(root, CONTENT_ATTR, None)
    _set_attr(root, "ratified_backfilled", True)

    assert _cli(["-C", root, "migrate"]) == 0
    assert "recorded the signed content on 1 ratification record(s)" in capsys.readouterr().out
    assert _req(root).attrs["ratified_backfilled"] is True

    second = migrate_project(root)
    assert second.contents == {} and second.unprovable == []


def test_migration_never_overwrites_recorded_content(tmp_path):
    root = _project(tmp_path, git=False)
    forged = dict(_req(root).attrs[CONTENT_ATTR], text="Words nobody signed.")
    _set_attr(root, CONTENT_ATTR, forged)

    result = migrate_project(root)

    assert result.contents == {}
    assert _req(root).attrs[CONTENT_ATTR]["text"] == "Words nobody signed."


# ---------------------------------------- part of the ratification record (SR-0219)

def test_no_other_operation_may_write_the_recorded_content(tmp_path, capsys):
    root = _project(tmp_path, git=False)

    assert _cli(["-C", root, "amend", "REQ-0001", "--attr",
                 "ratified_content=forged"]) != 0
    assert "tl ratify" in capsys.readouterr().err
    assert _cli(["-C", root, "amend", "REQ-0001", "--unset", "ratified_content"]) != 0
    assert _req(root).attrs[CONTENT_ATTR]["text"] == ORIGINAL


def test_withdrawing_a_ratification_removes_the_recorded_content(tmp_path):
    root = _project(tmp_path, git=False)
    assert _cli(["-C", root, "withdraw", "REQ-0001", "--reason",
                 "signed in error", "--by", "Grace Hopper"]) == 0
    item = _req(root)

    assert STAMP_ATTR not in item.attrs
    assert CONTENT_ATTR not in item.attrs


# ------------------------------------------------ regressions found in review

def test_a_shared_value_is_copied_so_an_edit_to_the_item_leaves_the_record_intact(tmp_path):
    """A list or date held by both the item and its record was written with a YAML
    anchor, so editing the item rewrote what was signed (SR-0215, SR-0216)."""
    root = _project(tmp_path, git=False)
    assert _cli(["-C", root, "schema", "attr", "add", "requirement", "tags",
                 "--normative", "--because", "a list-valued normative attribute"]) == 0
    item = _req(root)
    item.attrs["tags"] = ["a", "b"]
    item.attrs.pop(STAMP_ATTR)
    item.attrs.pop(CONTENT_ATTR)
    item.attrs.pop("ratified_by")
    write_item(item)
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada Lovelace"]) == 0
    path = next((root / "requirements").glob("REQ-0001.yml"))
    assert "&id" not in path.read_text(encoding="utf-8")

    item = _req(root)
    item.attrs["tags"].append("c")
    write_item(item)
    project = load_project(root)
    change = change_since_ratification(project, project.get("REQ-0001"))
    assert "ratified-content-mismatch" not in _rules(root)
    assert change.source == RECORD
    assert {c.field: (c.was, c.now) for c in change.changes}["attrs.tags"] \
        == (["a", "b"], ["a", "b", "c"])


@pytest.mark.parametrize("field,value", [("text", None), ("derived", 1)])
def test_the_tool_never_records_content_its_own_check_rejects(tmp_path, field, value):
    """Whatever the fingerprint accepts, the record reproduces (SR-0215, SR-0217)."""
    root = _project(tmp_path, git=False)
    item = _req(root)
    for name in (STAMP_ATTR, CONTENT_ATTR, "ratified_by"):
        item.attrs.pop(name)
    setattr(item, field, value)
    write_item(item)
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada Lovelace"]) == 0
    assert "ratified-content-mismatch" not in _rules(root)


def test_migration_records_content_before_repairing_a_normative_flag(tmp_path):
    """The flag repair changes a fingerprint input; content the item proved before it
    must not be lost to it (SR-0218)."""
    root = _project(tmp_path, git=False)
    _set_attr(root, CONTENT_ATTR, None)
    assert _cli(["-C", root, "schema", "type", "normative", "requirement", "false",
                 "--because", "requirements in this fixture are not binding"]) == 0

    result = migrate_project(root)
    assert result.contents == {"REQ-0001": "current"}
    assert result.normative == {"REQ-0001": False}

    project = load_project(root)
    change = change_since_ratification(project, project.get("REQ-0001"))
    assert change.source == RECORD
    assert {c.field: (c.was, c.now) for c in change.changes}["normative"] == (True, False)


def test_an_attribute_made_normative_after_signing_is_never_reported_as_unset(tmp_path):
    """The record holds no value for it, so it cannot say what was signed (SR-0165)."""
    root = _project(tmp_path, git=False)
    assert _cli(["-C", root, "schema", "attr", "add", "requirement", "zeta",
                 "--because", "a non-normative attribute"]) == 0
    assert _cli(["-C", root, "new", "REQ", "--title", "Z", "--text", "Zeta.",
                 "--ground", "INT-0001", "--ground-type", "implements", "--origin",
                 "ai", "--attr", "zeta=q", "--no-interactive"]) == 0
    assert _cli(["-C", root, "ratify", "REQ-0002", "--by", "Ada Lovelace"]) == 0
    cfg = root / "throughline.toml"
    text = cfg.read_text(encoding="utf-8")
    assert "attrs.zeta = {}" in text
    cfg.write_text(text.replace("attrs.zeta = {}", "attrs.zeta = { normative = true }"),
                   encoding="utf-8")

    project = load_project(root)
    change = change_since_ratification(project, project.get("REQ-0002"))
    assert change.source == RECORD
    assert all(c.field != "attrs.zeta" for c in change.changes)
    assert change.outcome == "unresolvable"
    assert "zeta became normative after the signature" in change.reason


def test_a_record_attribute_cannot_be_declared_or_defaulted(tmp_path, capsys):
    """A declared default would write the record at birth (SR-0170, SR-0219)."""
    root = _project(tmp_path, git=False)
    assert _cli(["-C", root, "schema", "attr", "add", "requirement", "ratified_content",
                 "--default", "forged", "--because", "an attempt"]) != 0
    assert "ratification record" in capsys.readouterr().err

    cfg = root / "throughline.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + '\n[types.requirement.attrs]\n'
                   'ratified_content = { default = "forged" }\n', encoding="utf-8")
    try:
        load_project(root)
    except Exception:                       # the config itself refused the table
        return
    assert _cli(["-C", root, "new", "REQ", "--title", "X", "--text", "X.",
                 "--ground", "INT-0001", "--ground-type", "implements",
                 "--no-interactive"]) == 0
    assert CONTENT_ATTR not in load_project(root).get("REQ-0002").attrs


@pytest.mark.parametrize("content", ["just a string",
                                     {"type": "requirement", "text": ORIGINAL,
                                      "normative": True, "derived": False, "attrs": {}}])
def test_recorded_content_with_no_stamp_is_reported(tmp_path, content):
    """Content with no signature beside it shows a signature that is not there, and
    malformed content is no exception (SR-0217)."""
    root = _project(tmp_path, git=False)
    item = _req(root)
    item.attrs.pop(STAMP_ATTR)
    item.attrs[CONTENT_ATTR] = content
    write_item(item)
    findings = [f for f in validate(load_project(root))
                if f.uid == "REQ-0001" and f.rule == "ratified-content-mismatch"]
    assert len(findings) == 1
    assert "no ratification stamp" in findings[0].message


def test_the_mismatch_advice_names_a_route_the_tool_allows(tmp_path):
    root = _project(tmp_path, git=False)
    forged = dict(_req(root).attrs[CONTENT_ATTR], text="Words nobody signed.")
    _set_attr(root, CONTENT_ATTR, forged)
    [finding] = [f for f in validate(load_project(root))
                 if f.rule == "ratified-content-mismatch"]
    assert "tl withdraw" in finding.message
    assert _cli(["-C", root, "withdraw", "REQ-0001", "--reason", "record edited",
                 "--by", "Ada Lovelace"]) == 0
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada Lovelace"]) == 0
    assert "ratified-content-mismatch" not in _rules(root)
    assert _req(root).attrs[CONTENT_ATTR]["text"] == ORIGINAL


# ---------------------------------------- documents leave the copy out (SR-0220)

def _publish(root: Path, marker: str) -> str:
    docs = root / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "spec.md").write_text(f"# Spec\n\n{marker}\n<!-- tl:end -->\n", encoding="utf-8")
    cfg = root / "throughline.toml"
    text = cfg.read_text(encoding="utf-8")
    if "[docs]" not in text:
        cfg.write_text(text + '\n[docs]\npaths = ["docs/*.md"]\n', encoding="utf-8")
    assert _cli(["-C", root, "docs"]) == 0
    return (docs / "spec.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("marker", ["<!-- tl:item REQ-0001 -->",
                                    "<!-- tl:catalog type == 'requirement' -->"])
def test_an_item_block_leaves_the_recorded_content_out(tmp_path, marker):
    root = _project(tmp_path, git=False)
    doc = _publish(root, marker)

    assert "ratified_content" not in doc
    assert "**ratified_by**: Ada Lovelace" in doc
    assert "**ratified_fingerprint**: sha256:" in doc
    assert "**priority**: should" in doc
    assert ORIGINAL in doc


def test_the_recorded_content_stays_in_the_structured_export(tmp_path, capsys):
    root = _project(tmp_path, git=False)
    capsys.readouterr()
    assert _cli(["-C", root, "dump"]) == 0
    import json
    items = {i["uid"]: i for i in json.loads(capsys.readouterr().out)["items"]}
    assert items["REQ-0001"]["attrs"]["ratified_content"]["text"] == ORIGINAL
