# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""A filter as a GQL search condition over one item (SR-0336, SR-0338, SR-0340).

These pin the whole accepted grammar and the refusal of everything outside it,
as tests/test_filters.py does for the earlier language, so the surface is stated
here and not inferred from the expressions other tests happen to use.
"""
from __future__ import annotations

import pytest

from throughline.filters import FilterError
from throughline.gql import check_condition, evaluate
from throughline.graph import Index
from throughline.model import Item, Link, Project, Register
from throughline.schema import Schema


def _project() -> Project:
    """INT-1 <-implements- FR-1 <-verifies- TST-1; FR-1 relates to a URL; FR-2
    stands alone. FR-1 carries attributes, one of them named like a field."""
    items = [
        Item(uid="INT-1", type="intent", status="ratified", title="Why"),
        Item(uid="FR-1", type="requirement", status="proposed",
             title="Filter Language", text="one boolean filter language",
             attrs={"priority": "must", "tags": ["security", "query"], "score": 3,
                    "status": "shadow"},
             links=[Link(target="INT-1", type="implements"),
                    Link(target="https://example.org/spec", type="relates")]),
        Item(uid="FR-2", type="requirement", status="rejected", title="Other",
             normative=False),
        Item(uid="TST-1", type="test", status="proposed",
             links=[Link(target="FR-1", type="verifies")]),
    ]
    project = Project(path=__import__("pathlib").Path("."), config={})
    for it in items:
        prefix = it.uid.split("-")[0]
        reg = project.registers.setdefault(prefix, Register(prefix=prefix))
        it._register_prefix = prefix
        reg.items[it.uid] = it
    return project


P = _project()
IDX = Index.build(P)
SCHEMA = Schema.from_config({})


def sel(expr: str) -> list[str]:
    """The UIDs ``expr`` selects, in UID order."""
    return sorted(it.uid for it in P.items()
                  if evaluate(expr, it, IDX, SCHEMA) is True)


# ------------------------------------------------------------------ accepted

@pytest.mark.parametrize("expr, uids", [
    ("item.type = 'requirement'", ["FR-1", "FR-2"]),
    ('item.type = "requirement"', ["FR-1", "FR-2"]),
    ("item.type <> 'requirement'", ["INT-1", "TST-1"]),
    ("item.type = 'requirement' AND item.status <> 'rejected'", ["FR-1"]),
    ("item.type = 'intent' OR item.type = 'test'", ["INT-1", "TST-1"]),
    ("NOT item.type = 'requirement'", ["INT-1", "TST-1"]),
    ("NOT (item.type = 'requirement' AND item.normative)", ["FR-2", "INT-1", "TST-1"]),
    ("item.normative", ["FR-1", "INT-1", "TST-1"]),
    ("NOT item.normative", ["FR-2"]),
    ("item.uid = 'FR-1'", ["FR-1"]),
    ("item.register = 'FR'", ["FR-1", "FR-2"]),
    ("item.status IN ['proposed', 'ratified']", ["FR-1", "INT-1", "TST-1"]),
    ("item.status NOT IN ['proposed', 'ratified']", ["FR-2"]),
    ("item.text CONTAINS 'boolean'", ["FR-1"]),
    ("item.title STARTS WITH 'Filter'", ["FR-1"]),
    ("item.title ENDS WITH 'Language'", ["FR-1"]),
    ("LOWER(item.title) STARTS WITH 'filter'", ["FR-1"]),
    ("UPPER(item.title) = 'WHY'", ["INT-1"]),
    ("TRIM('  Why ') = item.title", ["INT-1"]),
    ("item.priority = 'must'", ["FR-1"]),
    ("item.score >= 3", ["FR-1"]),
    ("item.score < 3.5", ["FR-1"]),
    ("item.score > -1", ["FR-1"]),
    ("'security' IN item.tags", ["FR-1"]),
    ("SIZE(item.tags) = 2", ["FR-1"]),
    ("SIZE(item.title) = 3", ["INT-1"]),
    ("item.priority IS NULL", ["FR-2", "INT-1", "TST-1"]),
    ("item.priority IS NOT NULL", ["FR-1"]),
    ("COALESCE(item.priority, 'none') = 'none'", ["FR-2", "INT-1", "TST-1"]),
    ("TRUE", ["FR-1", "FR-2", "INT-1", "TST-1"]),
    ("FALSE", []),
    ("true and not false", ["FR-1", "FR-2", "INT-1", "TST-1"]),
    ("item.type = 'requirement'\n  AND item.status = 'proposed'", ["FR-1"]),
    ("  item.type = 'intent'  ", ["INT-1"]),
])
def test_accepted_grammar(expr, uids):
    """SR-0336."""
    check_condition(expr)
    assert sel(expr) == uids


def test_a_condition_that_is_unknown_does_not_select(tmp_path):
    """SR-0336: a property the item does not hold is null, a comparison with null
    is unknown, and unknown is not true. Its negation is unknown too."""
    assert sel("item.priority = 'must'") == ["FR-1"]
    assert sel("item.priority <> 'must'") == []
    assert sel("NOT item.priority = 'must'") == []
    assert sel("item.priority IS NULL OR item.priority <> 'must'") == [
        "FR-2", "INT-1", "TST-1"]
    assert evaluate("item.priority = 'must'", P.get("INT-1"), IDX, SCHEMA) is None
    assert evaluate("NULL AND FALSE", P.get("INT-1"), IDX, SCHEMA) is False
    assert evaluate("NULL OR TRUE", P.get("INT-1"), IDX, SCHEMA) is True
    assert evaluate("'x' IN [NULL, 'y']", P.get("INT-1"), IDX, SCHEMA) is None


def test_an_attribute_named_like_a_field_is_read_through_attrs():
    """SR-0338."""
    assert sel("item.status = 'shadow'") == []
    assert sel("item.attrs.status = 'shadow'") == ["FR-1"]
    assert sel("item.attrs.priority = 'must'") == ["FR-1"]


@pytest.mark.parametrize("expr, uids", [
    ("EXISTS { (item)-[:implements]->() }", ["FR-1"]),
    ("EXISTS { (item)<-[:verifies]-() }", ["FR-1"]),
    ("EXISTS { (item)<-[:implements]-() }", ["INT-1"]),
    ("EXISTS { (item)-[]->() }", ["FR-1", "TST-1"]),
    ("EXISTS { (item)-->() }", ["FR-1", "TST-1"]),
    ("EXISTS { (item)<--() }", ["FR-1", "INT-1"]),
    ("NOT EXISTS { (item)<-[]-() }", ["FR-2", "TST-1"]),
    ("EXISTS { (item)-[]->(t WHERE t.uid = 'INT-1') }", ["FR-1"]),
    ("EXISTS { (item)-[:implements]->(t WHERE t.type = 'intent') }", ["FR-1"]),
    ("EXISTS { (item)-[]->(t WHERE t.target STARTS WITH 'https:') }", ["FR-1"]),
    ("EXISTS { (item)<-[:verifies]-(v WHERE v.status = 'proposed') }", ["FR-1"]),
    ("item.type = 'requirement' AND NOT EXISTS { (item)<-[:verifies]-() }", ["FR-2"]),
])
def test_an_edge_pattern_from_or_to_the_item(expr, uids):
    """SR-0336, SR-0338: the other end is the item a link points at or comes
    from, or, for a target that is no item, a node whose one property is target."""
    check_condition(expr)
    assert sel(expr) == uids


def test_the_item_s_standing_is_read_as_properties():
    """SR-0338, SR-0335."""
    check_condition("item.signed AND NOT item.stale AND NOT item.retired")
    assert sel("item.signed") == []
    assert sel("NOT item.signed") == ["FR-1", "FR-2", "INT-1", "TST-1"]
    assert sel("'Ada' IN item.signers") == []


# ------------------------------------------------------------------- refused

@pytest.mark.parametrize("expr", [
    "type = 'requirement'",                 # a bare name: the item is `item`
    "n.type = 'requirement'",               # another variable
    "item.type == 'requirement'",           # the earlier language's equality
    "item.type != 'requirement'",
    "item.type = 'a' && item.normative",
    "item.score + 1 = 4",                   # arithmetic
    "item.score * 2 > 1",
    "item.title || 'x' = 'Whyx'",           # concatenation
    "item.type IS 'requirement'",
    "item.type LIKE 'req%'",
    "item.type IN ('requirement')",         # a list is in square brackets
    "CASE WHEN item.normative THEN TRUE ELSE FALSE END",
    "item.title.lower() = 'why'",           # a method call
    "len(item.title) = 3",                  # a function not in the list
    "ABS(item.score) = 3",
    "MATCH (item) RETURN item",             # a statement, not a condition
    "EXISTS { (item)-[:a]->()-[:b]->() }",  # more than one edge
    "EXISTS { (a)-[:x]->(b) }",             # not from or to the item
    "EXISTS { (item)-[:a|b]->() }",         # a label expression
    "EXISTS { MATCH (item)-[:a]->() }",
    "item.type = 'requirement' AND",        # unfinished
    "item.type = ",
    "item..type = 'x'",
    "item.type = 'unterminated",
    "item.type = 'x' ; DROP",
    "item.attrs.a.b.c = 1",
    "0x10 = 16",
    "1e3 = 1000",
    "item",                                 # the item itself is not a truth value
    "__import__('os')",
    "item.__class__ = 'x'",
])
def test_everything_outside_the_grammar_is_refused(expr):
    """SR-0336: every other construct is refused. SR-0340: by the Tool's own
    parser, never by handing the text to anything that would run it."""
    with pytest.raises(FilterError):
        check_condition(expr)
    with pytest.raises(FilterError):
        evaluate(expr, P.get("FR-1"), IDX, SCHEMA)


def test_a_refusal_names_what_it_refuses_and_where():
    """SR-0336, SR-0340."""
    with pytest.raises(FilterError, match=r"'=='.*item\.type = "):
        check_condition("item.type == 'x'")
    with pytest.raises(FilterError, match="bare name 'type'.*item.type"):
        check_condition("type = 'x'")
    with pytest.raises(FilterError, match="ABS"):
        check_condition("ABS(item.score) = 3")


def test_nothing_in_the_module_reaches_an_interpreter():
    """SR-0340: no eval, exec, compile, import of a name from the text, shell or
    database anywhere in the module that reads a GQL filter."""
    import ast
    import inspect

    import throughline.gql as gql
    tree = ast.parse(inspect.getsource(gql))
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not called & {"eval", "exec", "compile", "__import__", "getattr"}
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                for a in n.names} | {n.module for n in ast.walk(tree)
                                     if isinstance(n, ast.ImportFrom)}
    assert not imported & {"subprocess", "os", "importlib", "sqlite3", "ast"}


def test_an_empty_condition_selects_everything():
    assert sel("") == ["FR-1", "FR-2", "INT-1", "TST-1"]
    assert sel("   ") == ["FR-1", "FR-2", "INT-1", "TST-1"]


# ===========================================================================
# A project says which language its filters are in (SR-0337).
# ===========================================================================

from pathlib import Path  # noqa: E402

from throughline.cli import main as cli_main  # noqa: E402
from throughline.inject import inject_text  # noqa: E402
from throughline.storage import ProjectError, load_project  # noqa: E402
from throughline.validate import query_items, validate  # noqa: E402


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _toml(root: Path) -> str:
    return (root / "throughline.toml").read_text(encoding="utf-8")


def _set(root: Path, old: str, new: str) -> None:
    text = _toml(root)
    assert old in text
    (root / "throughline.toml").write_text(text.replace(old, new), encoding="utf-8")


def _earlier(tmp_path: Path) -> Path:
    """A project as one written before the key: no filter_language, and its rule
    and its document's markers in the earlier language."""
    root = tmp_path / "earlier"
    assert _cli(["-C", root, "init", "--name", "t"]) == 0
    text = _toml(root)
    start = text.index("# The language this project's filters are in")
    end = text.index('filter_language = "gql"\n') + len('filter_language = "gql"\n')
    text = text[:start] + text[end:]
    text = text.replace("item.type = 'requirement' AND item.status <> 'deleted'",
                        "type == 'requirement' and status != 'deleted'")
    (root / "throughline.toml").write_text(text, encoding="utf-8")
    doc = root / "docs" / "overview.md"
    doc.write_text(doc.read_text(encoding="utf-8").replace(
        "item.type = '", "type == '"), encoding="utf-8")
    return root


def test_tl_init_writes_a_project_whose_filters_are_gql(tmp_path, capsys):
    """SR-0337: tl init writes gql, in the key, the rule and the document."""
    root = tmp_path / "new"
    assert _cli(["-C", root, "init", "--name", "t"]) == 0
    project = load_project(root)
    assert project.filter_language == "gql" == project.schema.filter_language
    assert project.schema.coverage[0]["filter"] == \
        "item.type = 'requirement' AND item.status <> 'deleted'"
    assert "tl:table item.type = 'requirement'" in (
        root / "docs" / "overview.md").read_text(encoding="utf-8")
    assert _cli(["-C", root, "check"]) == 0
    assert _cli(["-C", root, "docs", "--check"]) == 0
    capsys.readouterr()
    assert _cli(["-C", root, "query", "item.type = 'requirement'"]) == 0
    assert "REQ-0001" in capsys.readouterr().out


def test_a_project_that_says_nothing_is_read_as_it_was(tmp_path, capsys):
    """SR-0337: where it declares none, its filters are read in the earlier
    language, so a project written before the key answers exactly as it did."""
    root = _earlier(tmp_path)
    project = load_project(root)
    assert project.filter_language == "python"
    assert not [f for f in validate(project) if f.severity == "error"]
    assert _cli(["-C", root, "docs", "--check"]) == 0
    assert [it.uid for it in query_items(project, "type == 'requirement'")] == ["REQ-0001"]
    capsys.readouterr()
    assert _cli(["-C", root, "query", "item.type = 'requirement'"]) == 2
    assert "bad filter" in capsys.readouterr().err


def test_the_language_is_the_project_s_and_never_guessed(tmp_path, capsys):
    """SR-0337: the Tool never tells the language of a filter from its text. A
    filter in the other language is refused, in a rule, a query and a document."""
    root = tmp_path / "new"
    assert _cli(["-C", root, "init", "--name", "t"]) == 0
    project = load_project(root)
    with pytest.raises(FilterError):
        query_items(project, "type == 'requirement'")
    with pytest.raises(Exception, match="filter"):
        inject_text(project, "<!-- tl:count type == 'requirement' -->\n<!-- tl:end -->\n")
    _set(root, "item.type = 'requirement' AND item.status <> 'deleted'",
         "type == 'requirement' and status != 'deleted'")
    capsys.readouterr()
    assert _cli(["-C", root, "check"]) == 2
    assert "unusable filter" in capsys.readouterr().err


def test_a_language_that_is_neither_is_refused(tmp_path, capsys):
    """SR-0337."""
    root = tmp_path / "new"
    assert _cli(["-C", root, "init", "--name", "t"]) == 0
    _set(root, 'filter_language = "gql"', 'filter_language = "sql"')
    capsys.readouterr()
    assert _cli(["-C", root, "check"]) == 2
    said = capsys.readouterr().err
    assert "filter_language" in said and '"sql"' in said


def test_a_coverage_rule_and_a_document_select_the_same_in_gql(tmp_path):
    """SR-0336: the same items in a query, a coverage rule and a document."""
    root = tmp_path / "new"
    assert _cli(["-C", root, "init", "--name", "t"]) == 0
    assert _cli(["-C", root, "new", "REQ", "--title", "second", "--ground",
                 "INT-0001", "--ground-type", "implements", "--no-interactive"]) == 0
    project = load_project(root)
    expr = "item.type = 'requirement' AND NOT EXISTS { (item)<-[:verifies]-() }"
    assert [it.uid for it in query_items(project, expr)] == ["REQ-0002"]
    assert sorted(f.uid for f in validate(project) if f.rule == "coverage") == ["REQ-0002"]
    out = inject_text(project, f"<!-- tl:count {expr} -->\n<!-- tl:end -->\n")
    assert "\n1\n" in out


def test_a_follower_takes_its_standard_s_language(tmp_path, capsys):
    """SR-0337: a project's filters are all in one language, and a follower's
    coverage rules include its standard's."""
    std = tmp_path / "std"
    assert _cli(["-C", std, "init", "--no-demo"]) == 0
    fol = tmp_path / "fol"
    assert _cli(["-C", fol, "init", "--from-schema", std]) == 0
    assert load_project(fol).filter_language == "gql"
    assert _cli(["-C", fol, "check"]) in (0, 1)

    _set(fol, 'filter_language = "gql"', 'filter_language = "python"')
    with pytest.raises(ProjectError, match="one language"):
        load_project(fol)
