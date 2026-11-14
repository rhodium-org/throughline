# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Defects found by reading release 5.2.0 against its own requirements.

On 14 November 2026 the source of 5.2.0 was read area by area to write the rules
of an independent engine. Each test here is a place where that reading found the
code doing something other than a ratified requirement says, and names the
requirement. Every one of them failed before the change that came with it.
"""
from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

import pytest

from throughline.cli import main as cli_main
from throughline.filters import FilterError, check_filter, safe_eval
from throughline.graph import Index
from throughline.grounding import GroundingError, ratify
from throughline.inject import InjectError, inject_text
from throughline.model import Item, Link, Project, Register
from throughline.storage import load_project, load_project_at_ref
from throughline.tomledit import TomlDocument
from throughline.union import ComposeError, build_union
from throughline.validate import validate


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _bare(tmp_path: Path) -> Path:
    """A project with no transitions table, one root and one requirement."""
    root = tmp_path / "proj"
    assert _cli(["-C", root, "init", "--name", "t", "--bare"]) == 0
    assert _cli(["-C", root, "register", "new", "INT", "vision"]) == 0
    assert _cli(["-C", root, "register", "new", "FR", "features"]) == 0
    assert _cli(["-C", root, "new", "INT", "--type", "intent", "--title", "why",
                 "--no-interactive"]) == 0
    assert _cli(["-C", root, "new", "FR", "--type", "requirement", "--title", "what",
                 "--ground", "INT-0001", "--ground-type", "derives_from",
                 "--no-interactive"]) == 0
    return root


def _demo(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    assert _cli(["-C", root, "init", "--name", "t"]) == 0
    return root


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t",
                    "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
                    *args], check=True, capture_output=True)


def _toml(root: Path) -> str:
    return (root / "throughline.toml").read_text(encoding="utf-8")


def _append(root: Path, text: str) -> None:
    (root / "throughline.toml").write_text(_toml(root) + text, encoding="utf-8")


# ------------------------------------------------------- the filter language

NS = {"type": "requirement", "title": "A title", "attrs": {"score": 3},
      "true": True, "false": False, "none": None}


@pytest.mark.parametrize("expr", [
    "type == b'requirement'",          # bytes
    "type == r'requirement'",          # a raw string
    "type == '''requirement'''",       # triple quotes
    'type == """requirement"""',
    "type == 'require' 'ment'",        # two strings joined by standing together
    "attrs['score'] == 0x3",           # hexadecimal
    "attrs['score'] == 0o3",           # octal
    "attrs['score'] == 0b11",          # binary
    "attrs['score'] < 1_000",          # an underscored integer
    "attrs['score'] == 3j",            # an imaginary number
    "attrs['score'] == 3e0",           # an exponent
    "attrs['score'] == ...",           # the ellipsis
])
def test_a_literal_outside_the_grammar_is_refused(expr):
    """SR-0104: the literals are quoted strings, integers and decimals, and every
    other construct is rejected, never evaluated."""
    with pytest.raises(FilterError):
        check_filter(expr)
    with pytest.raises(FilterError):
        safe_eval(expr, NS)


@pytest.mark.parametrize("expr", [
    "  type == 'requirement'", "\ttype == 'requirement'  ",
    "\n type == 'requirement'\n",
])
def test_white_space_around_a_filter_is_no_part_of_it(expr):
    """SR-0104: a filter is a boolean expression. Leading white space was read as
    Python indentation and refused as 'unexpected indent'."""
    check_filter(expr)
    assert safe_eval(expr, NS) is True


@pytest.mark.parametrize("expr", [
    "attrs['score'] == 3", "attrs['score'] < 3.5", "attrs['score'] > -1",
    "type == 'requirement'", 'type == "requirement"', "title == 'A title'",
    "type in ['requirement', 'intent']", "type in (\n 'requirement',\n 'intent')",
])
def test_the_grammar_s_own_literals_still_pass(expr):
    check_filter(expr)
    assert safe_eval(expr, NS) is True


# ------------------------------------------------- schema changes (SR-0182, SR-0184)

def _no_transitions(tmp_path: Path) -> Path:
    """A committed project that declares no transitions, so every move is legal."""
    root = _bare(tmp_path)
    text = _toml(root)
    start = text.index("[transitions]")
    end = text.find("\n[", start + 1)
    (root / "throughline.toml").write_text(
        text[:start] + (text[end + 1:] if end != -1 else ""), encoding="utf-8")
    assert "transitions" not in tomllib.loads(_toml(root))
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "as it stood")
    return root


def test_a_first_transitions_table_is_evaluated_before_it_is_written(tmp_path):
    """SR-0182: a constraint appearing where there was none narrows. The first
    transitions table forbids every move it does not list, so it is measured
    against the items, and refused where it forbids a move one has made."""
    root = _no_transitions(tmp_path)
    assert _cli(["-C", root, "status", "FR-0001", "deferred"]) == 0
    before = _toml(root)

    code = _cli(["-C", root, "schema", "transition", "allow", "deferred",
                 "proposed", "--because", "Parked work can come back."])

    assert code == 1
    assert _toml(root) == before


def test_a_first_transitions_table_that_forbids_no_move_made_is_written(tmp_path):
    root = _no_transitions(tmp_path)
    assert _cli(["-C", root, "status", "FR-0001", "deferred"]) == 0
    assert _cli(["-C", root, "schema", "transition", "allow", "proposed",
                 "deferred", "--because", "A proposal can be parked."]) == 0
    assert tomllib.loads(_toml(root))["transitions"] == {"proposed": ["deferred"]}


def test_forbidding_a_move_an_item_has_made_is_refused(tmp_path):
    """SR-0182: the evaluation reads the baseline the check reads, so the move
    is found before the change is written and not at the next check."""
    root = _bare(tmp_path)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "as it stood")
    assert _cli(["-C", root, "status", "FR-0001", "deferred"]) == 0
    before = _toml(root)
    assert _cli(["-C", root, "schema", "transition", "deny", "proposed",
                 "deferred", "--because", "Nothing is parked."]) == 1
    assert _toml(root) == before


def _with_roles(tmp_path: Path) -> Path:
    root = _demo(tmp_path)
    for role, who in (("author", "email:ada@example.com"),
                      ("reviewer", "email:bob@example.com")):
        assert _cli(["-C", root, "schema", "role", "add", role, who,
                     "--because", f"{role} signs."]) == 0
    return root


def test_withdrawing_a_role_records_why(tmp_path):
    """SR-0184."""
    root = _with_roles(tmp_path)
    assert _cli(["-C", root, "schema", "role", "remove", "reviewer",
                 "--because", "Reviews moved to the pull request."]) == 0
    assert "reviewer" not in tomllib.loads(_toml(root))["ratify"]["roles"]
    assert "# Reviews moved to the pull request." in _toml(root)


def test_clearing_a_requirement_records_why(tmp_path):
    """SR-0184."""
    root = _with_roles(tmp_path)
    assert _cli(["-C", root, "schema", "requires", "set", "requirement",
                 "--roles", "author", "--because", "An author signs."]) == 0
    assert _cli(["-C", root, "schema", "requires", "clear", "requirement",
                 "--because", "One signature is enough again."]) == 0
    assert "requires" not in tomllib.loads(_toml(root))["ratify"]
    assert "# One signature is enough again." in _toml(root)


def test_setting_a_requirement_with_roles_and_a_count_records_why_once(tmp_path):
    """SR-0184: the statement is written as a comment on the table altered, not
    once for each key of it."""
    root = _with_roles(tmp_path)
    assert _cli(["-C", root, "schema", "requires", "set", "requirement",
                 "--roles", "author", "--signatures", "1",
                 "--because", "An author signs, alone."]) == 0
    assert _toml(root).count("# An author signs, alone.") == 1
    assert tomllib.loads(_toml(root))["ratify"]["requires"]["requirement"] == {
        "roles": ["author"], "signatures": 1}


def test_removing_a_type_records_why_where_no_types_table_stands(tmp_path):
    """SR-0184. A file normally holds [types.<name>] tables and no bare [types],
    which was the only place the reason was written."""
    root = _demo(tmp_path)
    assert "\n[types]\n" not in _toml(root)
    assert _cli(["-C", root, "schema", "type", "add", "note",
                 "--because", "Notes are kept."]) == 0
    assert _cli(["-C", root, "schema", "type", "remove", "note",
                 "--because", "Notes moved to the wiki."]) == 0
    assert "note" not in tomllib.loads(_toml(root))["types"]
    assert "# Notes moved to the wiki." in _toml(root)


def test_a_reason_left_where_a_table_stood_introduces_nothing_after_it():
    doc = TomlDocument("[a]\nx = 1\n\n# about b\n[b]\ny = 2\n")
    doc.remove_table("a", because="a is gone")
    assert doc.text() == "# a is gone\n\n# about b\n[b]\ny = 2\n"
    assert tomllib.loads(doc.text()) == {"b": {"y": 2}}


def test_a_follower_adding_a_grounding_member_writes_only_what_it_adds(tmp_path):
    """UR-0041: a graph that follows a standard declares only what it adds. The
    operation wrote the whole inherited list into the follower, which migration
    then names redundant and removes (SR-0277)."""
    std = tmp_path / "std"
    assert _cli(["-C", std, "init", "--no-demo"]) == 0
    fol = tmp_path / "fol"
    assert _cli(["-C", fol, "init", "--from-schema", std]) == 0
    assert "ground_link_types" not in (tomllib.loads(_toml(fol)).get("grounding") or {})
    inherited = load_project(fol).schema.ground_link_types

    assert _cli(["-C", fol, "schema", "grounding", "add", "ground_link_types",
                 "satisfies", "--because", "A clause is satisfied here."]) == 0

    assert tomllib.loads(_toml(fol))["grounding"]["ground_link_types"] == ["satisfies"]
    assert load_project(fol).schema.ground_link_types == inherited | {"satisfies"}


# ------------------------------------------------------- the blast radius (SR-0035)

def _chain() -> Project:
    """INT-1 <- FR-1 (derives_from) <- FR-2 (refines), and NFR-1 relates to INT-1."""
    items = [
        Item(uid="INT-1", type="intent", status="ratified"),
        Item(uid="FR-1", type="requirement", status="ratified",
             links=[Link(target="INT-1", type="derives_from")]),
        Item(uid="FR-2", type="requirement", status="ratified",
             links=[Link(target="FR-1", type="refines")]),
        Item(uid="NFR-1", type="nfr", status="ratified",
             links=[Link(target="INT-1", type="relates")]),
    ]
    project = Project(path=Path("."), config={})
    for it in items:
        prefix = it.uid.split("-")[0]
        reg = project.registers.setdefault(prefix, Register(prefix=prefix))
        it._register_prefix = prefix
        reg.items[it.uid] = it
    return project


def test_the_blast_radius_says_how_each_dependent_was_reached():
    """SR-0035: the set reachable by incoming links, by link type and depth."""
    idx = Index.build(_chain())
    reached = idx.impact_reached("INT-1")
    assert [uid for uid, _d, _k in reached] == idx.impact("INT-1")
    assert set(reached) == {("FR-1", 1, "derives_from"), ("NFR-1", 1, "relates"),
                            ("FR-2", 2, "refines")}


def test_blast_groups_its_report_by_depth_and_link_type(tmp_path, capsys):
    """SR-0035."""
    root = _demo(tmp_path)
    capsys.readouterr()
    assert _cli(["-C", root, "blast", "INT-0001", "--format", "json"]) == 0
    reached = json.loads(capsys.readouterr().out)
    assert reached and all(set(r) == {"uid", "depth", "link"} for r in reached)
    assert [(r["depth"], r["link"]) for r in reached] == sorted(
        (r["depth"], r["link"]) for r in reached)
    assert {"uid": "REQ-0001", "depth": 1, "link": "implements"} in reached

    assert _cli(["-C", root, "blast", "INT-0001"]) == 0
    text = capsys.readouterr().out
    assert "depth 1 · implements" in text and "depth 2 · verifies" in text


# ---------------------------------------------------------- change sets (SR-0252)

def _apply(root: Path, ops: list, capsys) -> tuple[int, str]:
    file = root / "set.json"
    file.write_text(json.dumps(ops), encoding="utf-8")
    capsys.readouterr()
    code = _cli(["-C", root, "apply", file])
    out = capsys.readouterr()
    return code, out.out + out.err


def test_a_bad_label_is_named_in_the_refusal(tmp_path, capsys):
    """SR-0252: the refusal names the offending operation. The message held the
    unfilled text {label!r}."""
    root = _demo(tmp_path)
    code, said = _apply(root, [{"op": "new", "register": "REQ", "title": "x",
                                "label": "no spaces"}], capsys)
    assert code != 0
    assert "'no spaces'" in said and "{label" not in said


def test_a_new_item_that_links_to_its_own_label_is_refused(tmp_path, capsys):
    """SR-0252: a malformed operation refuses the set. This one was carried out,
    writing a link with an empty target."""
    root = _demo(tmp_path)
    before = sorted(p.name for p in root.rglob("*.yml"))
    code, said = _apply(root, [{"op": "new", "register": "REQ", "title": "x",
                                "label": "me",
                                "links": [{"target": "$me", "type": "relates"}]}],
                        capsys)
    assert code != 0
    assert "$me" in said
    assert sorted(p.name for p in root.rglob("*.yml")) == before


# ------------------------------------------------------------------- documents

def test_an_opener_with_no_space_before_its_close_is_refused_by_name(tmp_path):
    """SR-0094: a marked region is rendered or the document fails. This marker
    balanced with its tl:end, so it passed, and was never rendered."""
    project = load_project(_demo(tmp_path))
    with pytest.raises(InjectError, match=r"<!--tl:stats-->"):
        inject_text(project, "<!--tl:stats-->\nold\n<!-- tl:end -->\n")
    with pytest.raises(InjectError, match=r"tl:count\.inline"):
        inject_text(project, "<!-- tl:count.inline--><!-- tl:end -->")


def test_a_chart_grouped_by_a_key_nothing_declares_fails(tmp_path):
    """SR-0116: an unknown key fails injection. A misspelt attribute rendered the
    placeholder for a key no item exhibits."""
    project = load_project(_demo(tmp_path))
    with pytest.raises(InjectError, match="priorty"):
        inject_text(project, "<!-- tl:chart priorty -->\n<!-- tl:end -->\n")


@pytest.mark.parametrize("key", ["type", "status", "degree", "priority", "origin"])
def test_a_chart_grouped_by_a_field_or_a_declared_attribute_renders(tmp_path, key):
    """SR-0116: a declared key no item exhibits is a placeholder, not a failure."""
    project = load_project(_demo(tmp_path))
    out = inject_text(project, f"<!-- tl:chart {key} -->\n<!-- tl:end -->\n")
    assert "mermaid" in out or "no data to chart" in out


def test_rendering_at_a_revision_finds_the_configured_documents(tmp_path):
    """SR-0090: the document is rendered as the graph stood at a revision. The
    graph was read into a directory that was then removed, and `[docs] paths` was
    looked for there, so no configured document was ever found."""
    root = _demo(tmp_path)
    doc = root / "docs" / "count.md"
    doc.write_text("<!-- tl:count type == 'requirement' -->\n<!-- tl:end -->\n",
                   encoding="utf-8")
    assert load_project(root).schema.docs_paths
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "one requirement")
    assert _cli(["-C", root, "new", "REQ", "--title", "second", "--ground",
                 "INT-0001", "--no-interactive"]) == 0

    project, _sha = load_project_at_ref(root, "HEAD")
    from throughline.inject import document_paths
    assert doc in document_paths(project)

    assert _cli(["-C", root, "docs", "--at", "HEAD"]) == 0
    assert "\n1\n" in doc.read_text(encoding="utf-8")
    assert _cli(["-C", root, "docs"]) == 0
    assert "\n2\n" in doc.read_text(encoding="utf-8")


# ------------------------------------------------------------------ composition

def _project(*items: Item, config: dict | None = None) -> Project:
    project = Project(path=Path("."), config=config or {})
    for it in items:
        prefix = it.uid.rsplit("-", 1)[0]
        reg = project.registers.setdefault(prefix, Register(prefix=prefix))
        it._register_prefix = prefix
        reg.items[it.uid] = it
    return project


def test_a_borrowed_prefix_the_consumer_already_uses_is_refused():
    """SR-0230: a borrowed item is named namespace:UID in everything reported. The
    refusal for a source whose items take one of the consumer's prefixes could not
    be reached, so they were merged into the consumer's register and the
    consumer's own items then read as borrowed."""
    source = _project(Item(uid="REQ-0001", type="requirement", status="ratified"))
    sample = build_union(_project(), {"std": source})
    mangled = next(iter(sample.project.registers))
    consumer = _project(Item(uid=f"{mangled}-0007", type="requirement",
                             status="ratified"))

    with pytest.raises(ComposeError, match="consumer already uses"):
        build_union(consumer, {"std": source})


def test_the_composed_summary_counts_live_items_one_way(tmp_path, capsys):
    """SR-0078: live item counts. A proposed deletion is live until it is signed
    (SR-0012); the Items line counted it and the Local line did not."""
    _demo(tmp_path)
    con = tmp_path / "con"
    assert _cli(["-C", con, "init", "--name", "c"]) == 0
    _append(con, '\n[[sources]]\nnamespace = "std"\npath = "../demo"\n')
    assert _cli(["-C", con, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    assert _cli(["-C", con, "delete", "REQ-0001", "--reason", "gone"]) == 0
    capsys.readouterr()
    _cli(["-C", con, "check"])
    said = capsys.readouterr()
    out = said.out + said.err
    items = next(l for l in out.splitlines() if l.startswith("  Items"))
    local = next(l for l in out.splitlines() if l.startswith("  Local"))
    live = int(items.split()[1])
    assert f"of {live} local" in local


# ---------------------------------------------------------------------- signing

def test_ratify_itself_refuses_a_signature_with_no_name(tmp_path):
    """SR-0098: the identity recorded is a person's. Only the command line made
    sure one was given; the operation beneath it recorded an empty name."""
    root = _demo(tmp_path)
    project = load_project(root)
    for by in ("", "   "):
        with pytest.raises(GroundingError, match="who"):
            ratify(project, "REQ-0001", by)
    assert not project.get("REQ-0001").ratifications


# ---------------------------------------------------------------------- loading

def test_attributes_that_are_not_a_mapping_are_a_named_finding(tmp_path):
    """SR-0134: a malformed structure is a named finding with the file and the
    reason, never an unhandled error."""
    root = _demo(tmp_path)
    file = root / "requirements" / "REQ-0001.yml"
    file.write_text(file.read_text(encoding="utf-8").split("attrs:")[0]
                    + "attrs:\n- must\n", encoding="utf-8")
    findings = [f for f in validate(load_project(root)) if f.uid == "REQ-0001"]
    assert any("'attrs' must be a mapping" in f.message and f.file.endswith(
        "REQ-0001.yml") for f in findings)


# ===========================================================================
# Requirements ratified for what the reading found that none covered
# (SR-0324 to SR-0333).
# ===========================================================================

def _said(capsys) -> str:
    out = capsys.readouterr()
    return out.out + out.err


# ---------------------------------------------- unreadable files (SR-0324)

@pytest.mark.parametrize("content, why", [
    ("type: requirement\ntext: x\n", "no 'uid'"),
    ("uid: REQ-0009\ntext: x\n", "no 'type'"),
    ("- a\n- b\n", "not a mapping"),
    ("just words\n", "not a mapping"),
    ("", "empty"),
    ('uid: REQ-0009\ntype: requirement\ntext: "x\n', "not valid YAML"),
])
def test_a_file_that_cannot_be_read_as_an_item_is_refused_by_name(
        tmp_path, capsys, content, why):
    """SR-0324: the project is not loaded, the file and what is wrong with it are
    named, and no command stops with an unhandled error."""
    root = _demo(tmp_path)
    (root / "requirements" / "REQ-0009.yml").write_text(content, encoding="utf-8")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    capsys.readouterr()

    for command in (["check"], ["new", "REQ", "--title", "x", "--no-interactive"]):
        assert _cli(["-C", root, *command]) == 2
        said = _said(capsys)
        assert "REQ-0009.yml" in said and why in said

    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_a_manifest_with_no_prefix_is_refused_by_name(tmp_path, capsys):
    """SR-0324."""
    root = _demo(tmp_path)
    (root / "requirements" / ".register.yml").write_text("title: x\n", encoding="utf-8")
    capsys.readouterr()
    assert _cli(["-C", root, "check"]) == 2
    said = _said(capsys)
    assert ".register.yml" in said and "no 'prefix'" in said


# ------------------------------------------- tombstones by role (SR-0325)

def _renamed_tombstone(tmp_path: Path) -> Path:
    """The demo project with the tombstone role bound to 'binned'."""
    root = _demo(tmp_path)
    text = _toml(root).replace('"deleted"', '"binned"').replace(
        "'deleted'", "'binned'")
    import re
    text = re.sub(r"\bdeleted\b", "binned", text)
    (root / "throughline.toml").write_text(text, encoding="utf-8")
    assert load_project(root).schema.status_role("tombstone") == "binned"
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "as it stood")
    return root


def test_a_tombstone_is_whatever_status_the_role_names(tmp_path, capsys):
    """SR-0325: delete writes the role's status, and every rule and view then
    reads the item as a tombstone, which they did only for the word 'deleted'."""
    root = _renamed_tombstone(tmp_path)
    assert _cli(["-C", root, "delete", "TEST-0001", "--reason", "gone"]) == 0
    project = load_project(root)
    item = project.get("TEST-0001")
    assert item.status == "binned" and item.is_deleted
    # Never signed and with no signed dependent, so it was retired outright and
    # is no longer live scope: no rule reports it.
    assert not [f for f in validate(project) if f.uid == "TEST-0001"]
    capsys.readouterr()
    assert _cli(["-C", root, "query"]) == 0
    assert "TEST-0001" not in capsys.readouterr().out


def test_an_item_whose_status_is_the_word_deleted_is_no_tombstone_elsewhere(tmp_path):
    """SR-0325: never the word deleted."""
    root = _renamed_tombstone(tmp_path)
    file = root / "tests" / "TEST-0001.yml"
    file.write_text(file.read_text(encoding="utf-8").replace(
        "status: proposed", "status: deleted"), encoding="utf-8")
    item = load_project(root).get("TEST-0001")
    assert item.status == "deleted" and not item.is_deleted


def test_an_erased_tombstone_is_reported_under_the_role_s_status(tmp_path):
    """SR-0325: in the baseline it compares against, too."""
    root = _renamed_tombstone(tmp_path)
    assert _cli(["-C", root, "delete", "TEST-0001", "--reason", "gone"]) == 0
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "retired")
    (root / "tests" / "TEST-0001.yml").unlink()
    from throughline.storage import read_baseline
    project = load_project(root)
    base = read_baseline(project)
    found = validate(project, baseline=base.statuses, restorable=base.restorable)
    assert any(f.rule == "tombstone-deleted" and f.uid == "TEST-0001" for f in found)


# ------------------------------------------ an explicit UID (SR-0326)

@pytest.mark.parametrize("uid", ["REQ-1", "REQ-01", "REQ-00001", "REQ-000001"])
def test_an_explicit_uid_whose_number_is_used_is_refused(tmp_path, capsys, uid):
    """SR-0326: by number, whatever the padding of either."""
    root = _demo(tmp_path)
    capsys.readouterr()
    assert _cli(["-C", root, "new", "REQ", "--uid", uid, "--title", "x",
                 "--no-interactive"]) == 2
    assert "REQ-0001" in _said(capsys)
    assert not list(root.rglob(f"{uid}.yml"))


def test_an_explicit_uid_whose_number_is_reserved_is_refused(tmp_path, capsys):
    """SR-0326."""
    root = _demo(tmp_path)
    manifest = root / "requirements" / ".register.yml"
    manifest.write_text(manifest.read_text(encoding="utf-8") + "reserved:\n- 7\n",
                        encoding="utf-8")
    capsys.readouterr()
    assert _cli(["-C", root, "new", "REQ", "--uid", "REQ-0007", "--title", "x",
                 "--no-interactive"]) == 2
    assert "reserved" in _said(capsys)
    assert _cli(["-C", root, "new", "REQ", "--uid", "REQ-0008", "--title", "x",
                 "--no-interactive"]) == 0


def test_a_register_given_more_digits_goes_on_from_the_same_numbers(tmp_path, capsys):
    """SR-0326, SR-0005: widening a register breaks nothing. The next number
    follows the highest used, the items already there keep their names, and the
    old numbers cannot be taken again under the new width."""
    root = _demo(tmp_path)
    manifest = root / "requirements" / ".register.yml"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace(
        "digits: 4", "digits: 6"), encoding="utf-8")
    assert load_project(root).registers["REQ"].digits == 6

    assert _cli(["-C", root, "new", "REQ", "--title", "next", "--ground", "INT-0001",
                 "--no-interactive"]) == 0
    project = load_project(root)
    assert project.get("REQ-0001") is not None and project.get("REQ-000002") is not None
    assert [f for f in validate(project) if f.severity == "error"] == []
    capsys.readouterr()
    assert _cli(["-C", root, "new", "REQ", "--uid", "REQ-000001", "--title", "x",
                 "--no-interactive"]) == 2
    assert "REQ-0001" in _said(capsys)


# ------------------------------------ the tombstone status (SR-0327)

def test_only_delete_moves_an_item_to_the_tombstone_status(tmp_path, capsys):
    """SR-0327: tl status wrote it with no deletion record, leaving an item that
    tl restore refused."""
    root = _demo(tmp_path)
    capsys.readouterr()
    assert _cli(["-C", root, "status", "REQ-0001", "deleted"]) == 2
    assert "tl delete" in _said(capsys)
    assert load_project(root).get("REQ-0001").status == "proposed"

    assert _cli(["-C", root, "new", "REQ", "--title", "x", "--status", "deleted",
                 "--no-interactive"]) == 2
    assert "tl delete" in _said(capsys)

    assert _cli(["-C", root, "delete", "REQ-0001", "--reason", "gone"]) == 0
    assert load_project(root).get("REQ-0001").deleted["reason"] == "gone"


def test_the_status_operation_itself_refuses_what_the_command_line_did(tmp_path):
    """SR-0327: by the operation, not only by the command line."""
    from throughline.grounding import set_status
    from throughline.items import new_item
    project = load_project(_demo(tmp_path))
    item = project.get("REQ-0001")
    with pytest.raises(GroundingError, match="not a declared status"):
        set_status(project.schema, item, "nonsense")
    with pytest.raises(GroundingError, match="tl delete"):
        set_status(project.schema, item, "deleted")
    with pytest.raises(GroundingError, match="not a declared status"):
        new_item(project, "REQ", item_type="requirement", status="nonsense")
    assert item.status == "proposed"


# ------------------------------------------- numbers as text (SR-0328)

def _numbers(tmp_path: Path) -> Path:
    root = _demo(tmp_path)
    _append(root, '\n[types.measure]\nattrs.count = { type = "int" }\n'
                  'attrs.ratio = { type = "float" }\n')
    return root


@pytest.mark.parametrize("attr", [
    "count=1_000", "count=1.0", "count=0x10", "count=١٢", "count=",
    "ratio=inf", "ratio=nan", "ratio=1_0.5", "ratio=-Infinity", "ratio=1.", "ratio=.5",
])
def test_a_number_outside_the_stated_form_is_refused(tmp_path, attr):
    """SR-0328."""
    root = _numbers(tmp_path)
    assert _cli(["-C", root, "new", "REQ", "--type", "measure", "--title", "m",
                 "--attr", attr, "--no-interactive"]) == 2


@pytest.mark.parametrize("attr, value", [
    ("count=12", 12), ("count=-3", -3), ("count=+4", 4), ("count= 7 ", 7),
    ("ratio=2", 2.0), ("ratio=2.5", 2.5), ("ratio=-0.25", -0.25),
    ("ratio=1e3", 1000.0), ("ratio=2.5E-1", 0.25),
])
def test_a_number_in_the_stated_form_is_read(tmp_path, attr, value):
    """SR-0328."""
    root = _numbers(tmp_path)
    assert _cli(["-C", root, "new", "REQ", "--type", "measure", "--title", "m",
                 "--attr", attr, "--no-interactive"]) == 0
    held = load_project(root).get("REQ-0002").attrs[attr.split("=")[0]]
    assert held == value and type(held) is type(value)


# ------------------------------------------ the ambiguity flag (SR-0329)

def test_only_true_flags_an_item_as_ambiguous(tmp_path):
    """SR-0329: the text 'false' was read as a flag."""
    root = _demo(tmp_path)
    file = root / "requirements" / "REQ-0001.yml"
    text = file.read_text(encoding="utf-8")
    assert "\nattrs:\n" in text
    file.write_text(text.replace("\nattrs:\n", "\nattrs:\n  ambiguous: 'false'\n"),
                    encoding="utf-8")
    found = [f for f in validate(load_project(root)) if f.uid == "REQ-0001"]
    assert not [f for f in found if f.rule == "ambiguous"]
    assert any(f.rule == "attribute-kind" and "ambiguous" in f.message for f in found)

    file.write_text(text.replace("\nattrs:\n", "\nattrs:\n  ambiguous: true\n"),
                    encoding="utf-8")
    found = [f for f in validate(load_project(root)) if f.uid == "REQ-0001"]
    assert [f for f in found if f.rule == "ambiguous"]
    assert not [f for f in found if f.rule == "attribute-kind"]


# ------------------------------------- schema values of the wrong kind (SR-0330)

@pytest.mark.parametrize("extra, names", [
    ('\n[types.measure]\nattrs.count = { type = "int", required = "no" }\n',
     ("count", "required", '"no"')),
    ('\n[types.measure]\nattrs.count = { type = "int", normative = 1 }\n',
     ("count", "normative", "1")),
    ('\n[rules]\norphan = "eror"\n', ("orphan", '"eror"')),
    ('\n[rules]\norphan = true\n', ("orphan", "true")),
])
def test_a_schema_value_of_the_wrong_kind_is_refused_when_read(
        tmp_path, capsys, extra, names):
    """SR-0330: the key and the value are named."""
    root = _demo(tmp_path)
    text = _toml(root)
    if extra.startswith("\n[rules]"):
        text = text.replace("[[rules.coverage]]", extra.strip() + "\n\n[[rules.coverage]]", 1)
    else:
        text += extra
    (root / "throughline.toml").write_text(text, encoding="utf-8")
    capsys.readouterr()
    assert _cli(["-C", root, "check"]) == 2
    said = _said(capsys)
    assert all(n in said for n in names)


@pytest.mark.parametrize("severity", ["error", "warning", "off"])
def test_the_three_severities_are_still_read(tmp_path, severity):
    root = _demo(tmp_path)
    (root / "throughline.toml").write_text(_toml(root).replace(
        "[[rules.coverage]]", f'[rules]\nunratified = "{severity}"\n\n[[rules.coverage]]',
        1), encoding="utf-8")
    assert load_project(root).schema.rule_overrides["unratified"] == severity


# ------------------------------------- a link of an undeclared type (SR-0331)

def test_a_link_of_an_undeclared_type_is_refused_when_it_is_made(tmp_path, capsys):
    """SR-0331: it was written, and reported only by the next check."""
    root = _demo(tmp_path)
    before = (root / "requirements" / "REQ-0001.yml").read_text(encoding="utf-8")
    capsys.readouterr()

    assert _cli(["-C", root, "link", "REQ-0001", "NFR-0001", "--type", "bogus"]) == 2
    assert "bogus" in (said := _said(capsys)) and "relates" in said
    assert _cli(["-C", root, "link", "REQ-0001", "INT-0001", "--type", "bogus",
                 "--retype"]) == 2
    assert (root / "requirements" / "REQ-0001.yml").read_text(encoding="utf-8") == before

    assert _cli(["-C", root, "new", "REQ", "--title", "x", "--ground", "INT-0001",
                 "--ground-type", "bogus", "--no-interactive"]) == 2
    assert not (root / "requirements" / "REQ-0002.yml").exists()

    code, said = _apply(root, [{"op": "new", "register": "REQ", "title": "x",
                                "links": [{"target": "INT-0001", "type": "bogus"}]}],
                        capsys)
    assert code != 0 and "bogus" in said
    assert not (root / "requirements" / "REQ-0002.yml").exists()


# ------------------------------- a borrowed item with a malformed UID (SR-0332)

def test_a_borrowed_item_whose_uid_is_not_well_formed_is_refused_by_name():
    """SR-0332: it stopped with an unhandled error."""
    source = _project(Item(uid="REQ-0001", type="requirement", status="ratified"))
    odd = Item(uid="req_one", type="requirement", status="ratified")
    source.registers["REQ"].items[odd.uid] = odd
    with pytest.raises(ComposeError, match=r"'std'.*'req_one'"):
        build_union(_project(), {"std": source})


# --------------------------------------- the wording of a finding (SR-0333)

def test_a_finding_names_values_without_the_language_s_renderings(tmp_path):
    """SR-0333: a list as its members, text in double quotes, a kind by the
    format's name. The rule, the severity and the item are unchanged."""
    root = _demo(tmp_path)
    _append(root, '\n[types.measure]\nattrs.count = { type = "int" }\n'
                  '\n[link_rules]\nverifies = { from = ["test", "nfr"] }\n')
    file = root / "requirements" / "REQ-0001.yml"
    text = file.read_text(encoding="utf-8").replace("priority: must", "priority: urgent")
    assert "priority: urgent" in text
    file.write_text(text.replace("\nlinks:\n", "\nlinks:\n- target: TEST-0001\n"
                                 "  type: verifies\n"), encoding="utf-8")
    assert _cli(["-C", root, "new", "REQ", "--type", "measure", "--title", "m",
                 "--no-interactive"]) == 0
    measure = root / "requirements" / "REQ-0002.yml"
    measure.write_text(measure.read_text(encoding="utf-8")
                       + "attrs:\n  count: three\nlinks: nothing\n", encoding="utf-8")

    found = {(f.rule, f.uid): f for f in validate(load_project(root))}
    said = " | ".join(f.message for f in found.values())
    assert "['" not in said and "NoneType" not in said and "'three'" not in said

    assert found[("schema", "REQ-0001")].message.endswith("must, should, could")
    assert '"urgent"' in found[("schema", "REQ-0001")].message
    assert '"three"' in found[("attribute-kind", "REQ-0002")].message
    assert "got string" in found[("malformed-link", "REQ-0002")].message
    assert "nfr, test item" in found[("bad-link-shape", "REQ-0001")].message
    assert found[("schema", "REQ-0001")].severity == "error"


def test_a_chart_labels_a_list_by_its_members(tmp_path):
    """SR-0333."""
    root = _demo(tmp_path)
    file = root / "requirements" / "REQ-0001.yml"
    file.write_text(file.read_text(encoding="utf-8").replace(
        "\nattrs:\n", "\nattrs:\n  tags:\n  - a\n  - b\n"), encoding="utf-8")
    out = inject_text(load_project(root), "<!-- tl:chart tags -->\n<!-- tl:end -->\n")
    assert '"a, b"' in out and "['a'" not in out
