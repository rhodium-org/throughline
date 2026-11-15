# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""tl migrate rewrites a project's filters into GQL, or changes nothing (SR-0339).

The move is safe to run on a graph nobody is watching only because every
rewritten filter is shown, on the project's own items, to select what the
original selected before a byte is written. These tests hold the rewriting to
that: each filter of the earlier language and its GQL form are evaluated over the
same items and must agree.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from throughline.cli import main as cli_main
from throughline.filters import FilterError
from throughline.gql import evaluate
from throughline.gql_migrate import Untranslatable, filter_of, plan_move, translate
from throughline.graph import Index
from throughline.model import Item, Link, Project, Register
from throughline.schema import Schema
from throughline.storage import load_project
from throughline.validate import eval_filter, validate


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _fixture() -> Project:
    items = [
        Item(uid="INT-1", type="intent", status="ratified", title="Why"),
        Item(uid="FR-1", type="requirement", status="draft", title="Filter Language",
             text="one boolean filter language",
             attrs={"priority": "must", "tags": ["security", "query"], "score": 3},
             links=[Link(target="INT-1", type="implements"),
                    Link(target="https://example.org/spec", type="relates")]),
        Item(uid="FR-2", type="requirement", status="rejected", title="Other",
             normative=False, attrs={"priority": "could", "score": 9}),
        Item(uid="FR-3", type="requirement", status="approved", title="",
             attrs={"tags": []}),
        Item(uid="TST-1", type="test", status="draft", derived=True,
             links=[Link(target="FR-1", type="verifies")]),
    ]
    project = Project(path=Path("."), config={})
    for it in items:
        prefix = it.uid.split("-")[0]
        reg = project.registers.setdefault(prefix, Register(prefix=prefix))
        it._register_prefix = prefix
        reg.items[it.uid] = it
    return project


P = _fixture()
IDX = Index.build(P)
SCHEMA = Schema.from_config({})
LISTS = frozenset({"tags"})


# Every construct tests/test_filters.py accepts in the earlier language, and the
# forms this estate's graphs use.
EARLIER = [
    "true", "false", "not false",
    "type == 'requirement'", "type=='requirement'", 'type == "requirement"',
    "status != 'draft'", "register == 'FR'", "uid == 'FR-1'",
    "normative", "not derived", "normative and not derived",
    "status == 'draft' or status == 'approved'",
    "not (status == 'draft' and normative)",
    "status in ['approved', 'draft']", "status not in ('rejected', 'deleted')",
    "'boolean' in text", "'x' not in text",
    "attrs.get('priority') == 'must'",
    "attrs.get('priority') != 'must'", "attrs.get('priority') != 'must' and normative",
    "attrs.get('missing') == none", "attrs.get('priority') != none",
    "attrs.get('missing', 'd') == 'd'", "attrs.get('priority', 'none') in ['must', 'none']",
    "attrs.get('score', 0) >= 3", "'security' in attrs.get('tags', [])",
    "attrs.get('score') == 3", "attrs.get('score') != 3",
    "attrs.get('priority') not in ['must']",
    "title.lower().startswith('filter')", "title.upper().endswith('LANGUAGE')",
    "text.strip() == 'one boolean filter language'",
    "links.outgoing('implements')", "links.incoming('verifies')",
    "links.outgoing()", "not links.incoming()", "links.to('INT-1')",
    "links.to('https://example.org/spec')",
    "type == 'requirement' and not links.incoming('verifies')",
    "type == 'requirement' and status != 'deleted'",
    "type == 'a' or (type == 'requirement' and normative)",
    "(type == 'intent' or type == 'test') and status != 'rejected'",
    "signed and not stale", "not retired", "not signed and not retired",
    "title", "not title",
]


@pytest.mark.parametrize("expr", EARLIER)
def test_the_gql_form_selects_what_the_earlier_filter_selects(expr):
    """SR-0339: each into the GQL condition that selects the same items."""
    gql = translate(expr, LISTS)
    earlier = sorted(it.uid for it in P.items() if eval_filter(it, expr, IDX, SCHEMA, "python"))
    now = sorted(it.uid for it in P.items() if evaluate(gql, it, IDX, SCHEMA) is True)
    assert now == earlier, gql


def test_a_test_of_an_absent_attribute_keeps_the_items_without_it():
    """SR-0339: where a property is absent the two languages differ, so the
    rewriting says so in the condition."""
    assert translate("attrs.get('priority') != 'must'") == \
        "(item.priority IS NULL OR item.priority <> 'must')"
    assert translate("attrs.get('priority') == none") == "item.priority IS NULL"
    assert translate("attrs.get('status') == 'x'") == "item.attrs.status = 'x'"
    assert translate("'security' in attrs.get('tags')", LISTS) == "'security' IN item.tags"
    assert translate("'sec' in attrs.get('note')") == "item.note CONTAINS 'sec'"


@pytest.mark.parametrize("expr, names", [
    ("attrs.keys() == ['a']", "keys"),
    ("attrs.values() == []", "values"),
    ("attrs.get('my-key') == 'x'", "my-key"),
    ("links.outgoing('needs review')", "needs review"),
    ("title.startswith(('a', 'b'))", "startswith"),
])
def test_a_filter_with_no_gql_form_is_refused_naming_what_has_none(expr, names):
    """SR-0339: a construct it cannot rewrite is named."""
    with pytest.raises(Untranslatable, match=names):
        translate(expr)


def test_what_is_not_a_filter_of_the_earlier_language_is_refused():
    with pytest.raises(FilterError):
        translate("type = 'requirement'")


@pytest.mark.parametrize("kind, arg, found", [
    ("table", "type == 'requirement'", "type == 'requirement'"),
    ("count", "  status != 'draft'  ", "status != 'draft'"),
    ("table", "", None),
    ("item", "REQ-0001", None),
    ("graph", "collapse-external type == 'x'", "type == 'x'"),
    ("graph", "type == 'x'", "type == 'x'"),
    ("chart", "priority type == 'x'", "type == 'x'"),
    ("chart", "priority", None),
    ("matrix", "incoming:implements type == 'x'", "type == 'x'"),
    ("matrix", "outgoing:satisfies@source_ref(title) type == 'x'", "type == 'x'"),
    ("matrix", "type == 'x'", "type == 'x'"),
    ("sourced", "type == 'x'", "type == 'x'"),
])
def test_the_filter_is_found_inside_each_directive_s_argument(kind, arg, found):
    """SR-0339: every filter in the marked regions of the documents."""
    assert filter_of(kind, arg) == found


def test_a_directive_from_elsewhere_is_refused_by_name():
    with pytest.raises(Untranslatable, match="tl:burndown"):
        filter_of("burndown", "type == 'x'")


# ------------------------------------------------------------- the whole move

def _earlier(tmp_path: Path) -> Path:
    """A project as one written before the key, with a document that uses most
    directives."""
    root = tmp_path / "earlier"
    assert _cli(["-C", root, "init", "--name", "t"]) == 0
    cfg = root / "throughline.toml"
    text = cfg.read_text(encoding="utf-8")
    start = text.index("# The language this project's filters are in")
    end = text.index('filter_language = "gql"\n') + len('filter_language = "gql"\n')
    text = (text[:start] + text[end:]).replace(
        "item.type = 'requirement' AND item.status <> 'deleted'",
        "type == 'requirement' and status != 'deleted'")
    cfg.write_text(text, encoding="utf-8")
    doc = root / "docs" / "overview.md"
    doc.write_text(doc.read_text(encoding="utf-8").replace("item.type = '", "type == '")
                   + "\nThere are <!-- tl:count.inline type == 'requirement' and "
                     "attrs.get('priority') != 'could' --><!-- tl:end --> of them.\n\n"
                     "<!-- tl:matrix incoming:verifies type == 'requirement' -->\n"
                     "<!-- tl:end -->\n\n"
                     "<!-- tl:chart status type != 'intent' -->\n<!-- tl:end -->\n\n"
                     "<!-- tl:graph collapse-external links.outgoing('implements') -->\n"
                     "<!-- tl:end -->\n", encoding="utf-8")
    assert _cli(["-C", root, "docs"]) == 0
    assert load_project(root).filter_language == "python"
    return root


def _files(root: Path) -> dict:
    return {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_the_move_rewrites_every_filter_and_the_project_answers_as_it_did(
        tmp_path, capsys):
    """SR-0339: the configuration, the documents and the key, and how many."""
    root = _earlier(tmp_path)
    before = load_project(root)
    findings = sorted((f.rule, f.uid) for f in validate(before))
    rendered = (root / "docs" / "overview.md").read_text(encoding="utf-8")
    capsys.readouterr()

    assert _cli(["-C", root, "migrate", "--to-gql"]) == 0

    said = capsys.readouterr().out
    assert "9 filter(s) rewritten" in said
    after = load_project(root)
    assert after.filter_language == "gql"
    assert after.schema.coverage[0]["filter"] == \
        "item.type = 'requirement' AND item.status <> 'deleted'"
    assert sorted((f.rule, f.uid) for f in validate(after)) == findings
    doc = (root / "docs" / "overview.md").read_text(encoding="utf-8")
    assert "tl:table item.type = 'requirement'" in doc
    assert "tl:matrix incoming:verifies item.type = 'requirement'" in doc
    assert "tl:chart status item.type <> 'intent'" in doc
    assert "tl:graph collapse-external EXISTS { (item)-[:implements]->() }" in doc
    assert "(item.priority IS NULL OR item.priority <> 'could')" in doc
    assert "type ==" not in doc
    # Only the markers moved: what each region holds is what it held.
    import re
    strip = lambda t: re.sub(r"<!--\s*tl:.*?-->", "", t, flags=re.S)  # noqa: E731
    assert strip(doc) == strip(rendered)
    assert _cli(["-C", root, "check"]) == 0
    assert _cli(["-C", root, "docs", "--check"]) == 0


def test_a_dry_run_shows_the_move_and_writes_nothing(tmp_path, capsys):
    root = _earlier(tmp_path)
    before = _files(root)
    capsys.readouterr()
    assert _cli(["-C", root, "migrate", "--to-gql", "--dry-run"]) == 0
    said = capsys.readouterr().out
    assert "would rewrite" in said and "nothing was written" in said
    assert _files(root) == before


def test_a_project_already_at_gql_is_left_as_it_is(tmp_path, capsys):
    """SR-0339."""
    root = tmp_path / "new"
    assert _cli(["-C", root, "init", "--name", "t"]) == 0
    before = _files(root)
    capsys.readouterr()
    assert _cli(["-C", root, "migrate", "--to-gql"]) == 0
    assert "already GQL" in capsys.readouterr().out
    assert _files(root) == before


def test_one_filter_that_cannot_be_rewritten_stops_the_whole_move(tmp_path, capsys):
    """SR-0339: it changes nothing, and names the filter and what holds it."""
    root = _earlier(tmp_path)
    doc = root / "docs" / "overview.md"
    doc.write_text(doc.read_text(encoding="utf-8")
                   + "\n<!-- tl:count attrs.keys() == ['priority'] -->\n<!-- tl:end -->\n",
                   encoding="utf-8")
    assert _cli(["-C", root, "docs"]) == 0
    before = _files(root)
    capsys.readouterr()

    assert _cli(["-C", root, "migrate", "--to-gql"]) == 1

    said = capsys.readouterr().err
    assert "docs/overview.md: tl:count" in said and "keys" in said
    assert "nothing was written" in said
    assert _files(root) == before
    assert load_project(root).filter_language == "python"


def test_a_rewriting_that_would_select_differently_stops_the_move(tmp_path, capsys):
    """SR-0339: it compares the items selected before writing anything. An
    attribute used as a truth value is the case: held but empty is false in the
    earlier language and held in GQL."""
    root = _earlier(tmp_path)
    item = root / "requirements" / "REQ-0001.yml"
    item.write_text(item.read_text(encoding="utf-8").replace(
        "\nattrs:\n", "\nattrs:\n  note: ''\n"), encoding="utf-8")
    doc = root / "docs" / "overview.md"
    doc.write_text(doc.read_text(encoding="utf-8")
                   + "\n<!-- tl:count attrs.get('note') -->\n<!-- tl:end -->\n",
                   encoding="utf-8")
    assert _cli(["-C", root, "docs"]) == 0
    before = _files(root)
    capsys.readouterr()

    assert _cli(["-C", root, "migrate", "--to-gql"]) == 1

    said = capsys.readouterr().err
    assert "does not select what it did" in said and "REQ-0001" in said
    assert _files(root) == before


def test_tl_migrate_says_filters_are_still_in_the_earlier_language(tmp_path, capsys):
    """SR-0339: tl migrate offers the move; it does not make it unasked."""
    root = _earlier(tmp_path)
    before = _files(root)
    capsys.readouterr()
    assert _cli(["-C", root, "migrate"]) == 0
    said = capsys.readouterr().out
    assert "9 filter(s)" in said and "migrate --to-gql" in said
    assert _files(root) == before
    assert plan_move(root).refusals == []
