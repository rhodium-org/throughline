# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""A ratification covers the item's signed links (SR-0242, SR-0243, SR-0244,
SR-0245, SR-0246; issue 77)."""
from __future__ import annotations

from pathlib import Path

import pytest

from throughline.brief import context_markdown as render_context
from throughline.cli import main as cli_main
from throughline.fingerprint import fingerprint
from throughline.model import Item, Link, Project, Register
from throughline.ratification import (
    CHANGED,
    RECORD,
    UNCHANGED,
    change_since_ratification,
    render_change,
)
from throughline.schema import Schema, SchemaError
from throughline.signed_links import LINKS_ATTR, link_drift
from throughline.storage import load_project, migrate_project, write_item
from throughline.union import build_union
from throughline.validate import validate
from throughline.worklist import entry_for, ratification_progress, signature_is_stale

SIGNED = '\n[ratify]\nsigned_links = ["produces", "uses"]\n'


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _declare(root: Path, text: str = SIGNED) -> None:
    cfg = root / "throughline.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + text, encoding="utf-8")


def _graph(tmp_path: Path, *, signed: bool = True) -> Path:
    """An intent, three quantities and one derivation, not yet ratified."""
    root = tmp_path / "proj"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    for name in ("produces", "uses"):
        assert _cli(["-C", root, "schema", "linktype", "add", name,
                     "--because", "the fixture follows these links"]) == 0
    if signed:
        _declare(root)
    assert _cli(["-C", root, "new", "INT", "--type", "intent", "--title", "Why",
                 "--text", "The vision.", "--no-interactive"]) == 0
    for n in range(4):
        assert _cli(["-C", root, "new", "REQ", "--title", f"Thing {n + 1}",
                     "--text", f"The Tool shall hold thing {n + 1}.",
                     "--ground", "INT-0001", "--ground-type", "implements",
                     "--origin", "ai", "--no-interactive"]) == 0
    return root


def _link(root, dst, kind="produces", src="REQ-0001") -> int:
    return _cli(["-C", root, "link", src, dst, "--type", kind])


def _unlink(root, dst, kind="produces", src="REQ-0001") -> int:
    return _cli(["-C", root, "unlink", src, dst, "--type", kind])


def _ratify(root, *extra, uid="REQ-0001") -> int:
    return _cli(["-C", root, "ratify", uid, "--by", "Ada Lovelace", *extra])


def _req(root: Path, uid="REQ-0001"):
    return load_project(root).get(uid)


def _stale(root: Path, uid="REQ-0001") -> list[str]:
    return [f.message for f in validate(load_project(root))
            if f.uid == uid and f.rule == "ratified-stale"]


def _signed(tmp_path: Path) -> Path:
    """REQ-0001 ratified carrying produces -> REQ-0002."""
    root = _graph(tmp_path)
    assert _link(root, "REQ-0002") == 0
    assert _ratify(root) == 0
    return root


# ------------------------------------------------------- the declaration (SR-0242)

def test_a_project_that_declares_none_writes_and_reports_as_before(tmp_path):
    root = _graph(tmp_path, signed=False)
    assert _link(root, "REQ-0002") == 0
    assert _ratify(root) == 0
    assert LINKS_ATTR not in _req(root).attrs
    assert _link(root, "REQ-0003") == 0
    assert _unlink(root, "REQ-0002") == 0
    assert _stale(root) == []
    assert Schema.from_config({}).signed_link_types == frozenset()


@pytest.mark.parametrize("value", ['"produces"', "true", "[1]", '[""]'])
def test_a_value_that_is_not_a_list_of_names_is_refused(value):
    import tomllib
    cfg = tomllib.loads(f"[ratify]\nsigned_links = {value}\n")
    with pytest.raises(SchemaError, match="signed_links must be a list"):
        Schema.from_config(cfg)


def test_a_name_that_is_not_a_declared_link_type_is_refused():
    with pytest.raises(SchemaError, match=r"signed_links \['produces'\]"):
        Schema.from_config({"links": {"types": ["relates"]},
                            "grounding": {"ground_link_types": ["relates"]},
                            "ratify": {"signed_links": ["produces"]}})


def test_the_brief_names_the_signed_link_types(tmp_path):
    root = _graph(tmp_path)
    brief = render_context(load_project(root))
    assert "Signed link types" in brief and "`produces`" in brief
    assert "Signed link types" not in render_context(
        load_project(_graph(tmp_path / "other", signed=False)))


# ------------------------------------------------------------ the record (SR-0243)

def test_ratify_records_the_signed_links_sorted_and_without_stamps(tmp_path):
    root = _graph(tmp_path)
    assert _cli(["-C", root, "link", "REQ-0001", "REQ-0003", "--type", "uses",
                 "--stamp"]) == 0
    assert _link(root, "REQ-0002") == 0
    before = fingerprint(_req(root), load_project(root).schema)
    assert _ratify(root) == 0
    item = _req(root)
    # The grounding link is not of a signed type, so it is not held.
    assert item.attrs[LINKS_ATTR] == [{"type": "produces", "target": "REQ-0002"},
                                      {"type": "uses", "target": "REQ-0003"}]
    assert item.attrs["ratified_fingerprint"] == before


def test_the_order_of_the_links_does_not_change_the_record_or_the_signature(tmp_path):
    root = _graph(tmp_path)
    assert _link(root, "REQ-0002") == 0
    assert _link(root, "REQ-0003") == 0
    assert _ratify(root) == 0
    item = _req(root)
    record = list(item.attrs[LINKS_ATTR])
    item.links.reverse()
    write_item(item)
    assert _stale(root) == []
    assert link_drift(_req(root), load_project(root).schema) == ([], [])
    assert record == sorted(record, key=lambda e: (e["type"], e["target"]))


def test_an_item_with_no_signed_link_gets_no_record_and_an_old_one_is_removed(tmp_path):
    root = _signed(tmp_path)
    assert LINKS_ATTR in _req(root).attrs
    assert _unlink(root, "REQ-0002") == 0
    assert _ratify(root, "--accept-change") == 0
    assert LINKS_ATTR not in _req(root).attrs
    assert _stale(root) == []


def test_only_ratify_writes_the_record(tmp_path):
    root = _signed(tmp_path)
    assert _cli(["-C", root, "amend", "REQ-0001", "--attr",
                 f"{LINKS_ATTR}=[]"]) != 0
    assert _cli(["-C", root, "withdraw", "REQ-0001", "--reason", "signed in error",
                 "--by", "Ada Lovelace"]) == 0
    assert LINKS_ATTR not in _req(root).attrs


def test_migrate_never_writes_a_link_record(tmp_path):
    root = _signed(tmp_path)
    item = _req(root)
    del item.attrs[LINKS_ATTR]              # a signature made before the record
    write_item(item)
    path = root / "requirements" / "REQ-0001.yml"
    before = path.read_text(encoding="utf-8")
    migrate_project(root)
    assert path.read_text(encoding="utf-8") == before
    assert len(_stale(root)) == 1


# ---------------------------------------------------------- the finding (SR-0244)

def test_adding_a_signed_link_makes_the_signature_stale_and_names_it(tmp_path):
    root = _signed(tmp_path)
    assert _stale(root) == []
    assert _link(root, "REQ-0003", "uses") == 0
    (message,) = _stale(root)
    assert "carries uses → REQ-0003, which the signature does not cover" in message
    assert "Ada Lovelace" in message


def test_removing_a_signed_link_makes_the_signature_stale_and_names_it(tmp_path):
    root = _signed(tmp_path)
    assert _unlink(root, "REQ-0002") == 0
    (message,) = _stale(root)
    assert "no longer carries produces → REQ-0002, which the signature covers" in message


def test_retargeting_a_signed_link_names_both_links(tmp_path):
    root = _signed(tmp_path)
    assert _unlink(root, "REQ-0002") == 0
    assert _link(root, "REQ-0003") == 0
    (message,) = _stale(root)
    assert "carries produces → REQ-0003" in message
    assert "no longer carries produces → REQ-0002" in message


def test_other_link_types_and_restamps_never_make_a_signature_stale(tmp_path):
    root = _signed(tmp_path)
    assert _cli(["-C", root, "link", "REQ-0001", "REQ-0004", "--type",
                 "implements"]) == 0
    assert _cli(["-C", root, "link", "REQ-0001", "REQ-0002", "--type", "produces",
                 "--stamp"]) == 0
    assert _req(root).links[-2].stamp or any(ln.stamp for ln in _req(root).links)
    assert _stale(root) == []


def test_a_signature_made_before_the_record_is_stale_only_with_a_signed_link(tmp_path):
    root = _graph(tmp_path, signed=False)
    assert _link(root, "REQ-0002") == 0
    assert _ratify(root) == 0                       # carries a link, no record
    assert _ratify(root, uid="REQ-0003") == 0       # carries none
    _declare(root)
    assert len(_stale(root)) == 1
    assert _stale(root, "REQ-0003") == []
    assert _link(root, "REQ-0004", "uses", src="REQ-0003") == 0
    assert len(_stale(root, "REQ-0003")) == 1


def test_content_and_links_moving_together_give_one_finding(tmp_path):
    root = _signed(tmp_path)
    assert _cli(["-C", root, "amend", "REQ-0001", "--text", "Something else."]) == 0
    assert _link(root, "REQ-0003") == 0
    (message,) = _stale(root)
    assert "normative content and signed links" in message


def test_the_worklist_and_the_progress_count_agree_with_the_finding(tmp_path):
    root = _signed(tmp_path)
    project = load_project(root)
    assert entry_for(project, project.get("REQ-0001")).concern == "ratified"
    accepted, _ = ratification_progress(project)
    assert _link(root, "REQ-0003") == 0
    project = load_project(root)
    entry = entry_for(project, project.get("REQ-0001"))
    assert entry.stale and entry.concern == "stale" and entry.ratifiable
    assert ratification_progress(project)[0] == accepted - 1


def test_a_malformed_link_record_is_an_error(tmp_path):
    root = _signed(tmp_path)
    item = _req(root)
    item.attrs[LINKS_ATTR] = ["produces REQ-0002"]
    write_item(item)
    findings = [f for f in validate(load_project(root)) if f.uid == "REQ-0001"]
    (finding,) = [f for f in findings if f.rule.startswith("ratified-")]
    assert finding.rule == "ratified-content-mismatch" and finding.severity == "error"


# ------------------------------------------------------- ratifying again (SR-0246)

def test_the_change_since_ratification_holds_the_links_that_moved(tmp_path):
    root = _signed(tmp_path)
    project = load_project(root)
    assert change_since_ratification(project, project.get("REQ-0001")).outcome == UNCHANGED
    assert _unlink(root, "REQ-0002") == 0
    assert _link(root, "REQ-0003") == 0
    project = load_project(root)
    change = change_since_ratification(project, project.get("REQ-0001"))
    assert change.outcome == CHANGED and change.stale and change.source == RECORD
    assert [(c.field, c.was, c.now) for c in change.changes] == [
        ("links.produces", "REQ-0002", None), ("links.produces", None, "REQ-0003")]
    assert change.as_dict()["changes"][0] == {
        "field": "links.produces", "was": "REQ-0002", "now": None}
    text = "\n".join(render_change(change, ratifier="Ada Lovelace"))
    assert "no longer carries produces → REQ-0002" in text
    assert "carries produces → REQ-0003, which the signature does not cover" in text


def test_content_and_link_changes_are_reported_together(tmp_path):
    root = _signed(tmp_path)
    assert _cli(["-C", root, "amend", "REQ-0001", "--text", "Something else."]) == 0
    assert _link(root, "REQ-0003") == 0
    project = load_project(root)
    change = change_since_ratification(project, project.get("REQ-0001"))
    assert [c.field for c in change.changes] == ["text", "links.produces"]


def test_ratify_refuses_a_link_change_nobody_was_shown_then_accepts_it(tmp_path, capsys):
    root = _signed(tmp_path)
    assert _ratify(root) != 0                        # unchanged: nothing to accept
    assert "signed links have not changed" in capsys.readouterr().err
    assert _link(root, "REQ-0003") == 0
    assert _ratify(root) != 0
    assert "--accept-change" in capsys.readouterr().err
    assert _ratify(root, "--accept-change") == 0
    assert _req(root).attrs[LINKS_ATTR] == [
        {"type": "produces", "target": "REQ-0002"},
        {"type": "produces", "target": "REQ-0003"}]
    assert _stale(root) == []


def test_link_and_unlink_say_when_the_signature_is_stale(tmp_path, capsys):
    root = _signed(tmp_path)
    capsys.readouterr()
    assert _cli(["-C", root, "link", "REQ-0001", "REQ-0004", "--type",
                 "implements"]) == 0
    assert "no longer covers" not in capsys.readouterr().out
    assert _link(root, "REQ-0003") == 0
    assert "ratification by Ada Lovelace no longer covers" in capsys.readouterr().out
    assert _unlink(root, "REQ-0003") == 0
    assert "no longer covers" not in capsys.readouterr().out   # back as signed
    assert _unlink(root, "REQ-0002") == 0
    assert "no longer covers" in capsys.readouterr().out


# --------------------------------------------------- composed sources (SR-0245)

def _source(signed: list[str]) -> Project:
    """A source graph holding one item signed with ``uses -> Q-0001`` and
    ``cites -> law:SEC-0001`` into a source of its own."""
    config = {"project": {"name": "src", "format_version": 3},
              "ratify": {"signed_links": signed}}
    project = Project(path=".", config=config)
    reg = Register(prefix="QTY", title="q")
    reg.items["QTY-0001"] = Item(uid="QTY-0001", type="thing", text="one")
    reg.items["QTY-0002"] = Item(uid="QTY-0002", type="thing", text="two")
    signed_item = Item(uid="QTY-0003", type="thing", text="three", links=[
        Link(target="QTY-0001", type="uses"), Link(target="law:SEC-0001", type="cites")])
    signed_item.attrs.update({
        "ratified_by": "Ada Lovelace",
        "ratified_fingerprint": fingerprint(signed_item, project.schema),
        LINKS_ATTR: [{"type": "cites", "target": "law:SEC-0001"},
                     {"type": "uses", "target": "QTY-0001"}]})
    reg.items["QTY-0003"] = signed_item
    project.registers["QTY"] = reg
    return project


def _law() -> Project:
    project = Project(path=".", config={"project": {"name": "law"}})
    reg = Register(prefix="SEC", title="s")
    reg.items["SEC-0001"] = Item(uid="SEC-0001", type="thing", text="a section")
    project.registers["SEC"] = reg
    return project


def _consumer(signed: list[str]) -> Project:
    return Project(path=".", config={"project": {"name": "c", "format_version": 3},
                                     "ratify": {"signed_links": signed}})


@pytest.mark.parametrize("namespace,law_ns,consumer_signed", [
    ("src", "law", ["uses", "cites"]),
    ("other", "statute", []),              # another namespace, an alias, none signed
    ("other", "statute", ["relates"]),     # other signed types
])
def test_a_source_signature_reads_as_current_however_it_is_composed(
        namespace, law_ns, consumer_signed):
    union = build_union(_consumer(consumer_signed),
                        {namespace: _source(["uses", "cites"]), law_ns: _law()},
                        labels={namespace: {"law": law_ns}})
    borrowed = [it for it in union.project.items() if it.authored_uid == "QTY-0003"][0]
    assert borrowed.links[0].target != "QTY-0001"          # rewritten in the union
    assert link_drift(borrowed, union.project.schema) == ([], [])
    assert not signature_is_stale(union.project.schema, borrowed)


def test_a_link_retargeted_in_the_source_is_stale_in_the_consumer():
    source = _source(["uses", "cites"])
    source.get("QTY-0003").links[0].target = "QTY-0002"
    union = build_union(_consumer([]), {"src": source, "law": _law()},
                        labels={"src": {"law": "law"}})
    borrowed = [it for it in union.project.items() if it.authored_uid == "QTY-0003"][0]
    assert link_drift(borrowed, union.project.schema) == (
        [("uses", "QTY-0002")], [("uses", "QTY-0001")])


def test_a_local_item_signed_with_a_qualified_target_is_current_in_the_union():
    consumer = _consumer(["cites"])
    reg = Register(prefix="RUL", title="r")
    item = Item(uid="RUL-0001", type="thing", text="local",
                links=[Link(target="law:SEC-0001", type="cites")])
    item.attrs.update({"ratified_by": "Ada Lovelace",
                       "ratified_fingerprint": fingerprint(item, consumer.schema),
                       LINKS_ATTR: [{"type": "cites", "target": "law:SEC-0001"}]})
    reg.items["RUL-0001"] = item
    consumer.registers["RUL"] = reg
    union = build_union(consumer, {"law": _law()})
    local = union.project.get("RUL-0001")
    assert local.links[0].target == "LAWSEC-0001"
    assert link_drift(local, union.project.schema) == ([], [])
