# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""`tl schema` — change the project's own schema through the tool (SR-0181),
refuse a change that would invalidate existing items (SR-0182), leave the rest of
the file as it was (SR-0183), and record why (SR-0184).

Before this command `throughline.toml` was hand-edited, which meant an author had
to know the schema's shape to add a status, and nothing checked the edit against
the items already in the graph — a narrowed vocabulary turned a green graph red at
the next `tl check`, away from the change that caused it. These tests drive the
CLI rather than the functions beneath it, because the obligations are about what
an author at a terminal is allowed to do and what they are told when they are not.
"""
from __future__ import annotations

from pathlib import Path

import tomllib

import pytest

from throughline.cli import main as _cli
from throughline.tomledit import TomlDocument, TomlEditError


# --------------------------------------------------------------- the editor

def test_editing_one_key_leaves_every_comment_and_other_key_alone():
    """SR-0183. A serialiser round-trip would drop all of this."""
    text = (
        "# The file's opening rationale.\n"
        "[project]\n"
        "name = \"demo\"          # the short name\n"
        "\n"
        "# Why the statuses are what they are.\n"
        "[status]\n"
        "values = [\"draft\", \"agreed\"]\n"
    )
    doc = TomlDocument(text)
    doc.set_key("status", "values", ["draft", "agreed", "retired"])
    out = doc.text()

    assert "# The file's opening rationale." in out
    assert "# Why the statuses are what they are." in out
    assert 'values = ["draft", "agreed", "retired"]' in out
    assert tomllib.loads(out)["project"]["name"] == "demo"


def test_a_dotted_key_is_rewritten_as_a_path_not_a_quoted_name():
    """``attrs.origin`` addresses ``origin`` inside ``attrs``. Re-emitting it as
    the single quoted key ``"attrs.origin"`` keeps the file parsing while quietly
    meaning something else — found by sweeping the estate's real configs."""
    text = ('[types.intent]\n'
            'attrs.origin = { type = "enum", values = ["human"] }\n')
    doc = TomlDocument(text)
    doc.set_key("types.intent", "attrs.origin",
                {"type": "enum", "values": ["human", "ai"]})
    out = doc.text()

    assert '"attrs.origin"' not in out
    assert tomllib.loads(out)["types"]["intent"]["attrs"]["origin"]["values"] \
        == ["human", "ai"]


def test_a_name_containing_a_dot_stays_one_key():
    """The other half of the same distinction: ``"attrs.origin"`` is one name, and
    the quotes are what say so. A key is addressed by its parts, so a one-member
    tuple names the literal and a plain string names the path."""
    doc = TomlDocument('[types.intent]\n"attrs.origin" = "kept whole"\n')
    doc.set_key("types.intent", ("attrs.origin",), "still whole")
    out = doc.text()

    assert '"attrs.origin" = "still whole"' in out
    assert tomllib.loads(out)["types"]["intent"]["attrs.origin"] == "still whole"

    # and the dotted path is a different key entirely, so it is added, not hit
    doc.set_key("types.intent", "attrs.origin", "a path")
    parsed = tomllib.loads(doc.text())["types"]["intent"]
    assert parsed["attrs.origin"] == "still whole"
    assert parsed["attrs"]["origin"] == "a path"


def test_adding_to_an_array_leaves_the_members_already_in_it_untouched():
    """SR-0183. Real vocabularies group their members under comments — this shape
    is from the estate. An addition does not disturb what is above it, so it is
    written in place and the grouping survives; re-rendering the value would have
    had nowhere to put those comments back."""
    text = ("[status]\n"
            "values = [\n"
            "  # This project's own lifecycle.\n"
            "  \"draft\", \"backlog\", \"done\",\n"
            "  # Declared only so borrowed items are valid in the union.\n"
            "  \"deferred\", \"rejected\",\n"
            "]\n")
    doc = TomlDocument(text)
    doc.add_to_array("status", "values", ["parked"])
    out = doc.text()

    assert "# This project's own lifecycle." in out
    assert "# Declared only so borrowed items are valid in the union." in out
    assert '  "deferred", "rejected",\n  "parked",\n]' in out
    assert tomllib.loads(out)["status"]["values"][-1] == "parked"


def test_an_addition_that_would_run_past_the_margin_breaks_the_line():
    doc = TomlDocument('[links]\ntypes = ["implements", "verifies", "relates"]\n')
    doc.add_to_array("links", "types", ["supersedes_an_earlier_statement"])
    out = doc.text()

    assert all(len(ln) <= 79 for ln in out.splitlines())
    assert tomllib.loads(out)["links"]["types"][-1] \
        == "supersedes_an_earlier_statement"


def test_a_comment_beside_one_member_of_a_value_is_refused_not_dropped():
    """SR-0183. The comment describes a member, and once the value is re-rendered
    there is no member to put it back beside — so the edit is declined."""
    text = ("[links]\n"
            "types = [\"implements\",\n"
            "         # Borrowed graphs use this one between their own items.\n"
            "         \"relates\"]\n")
    doc = TomlDocument(text)
    with pytest.raises(TomlEditError) as e:
        doc.set_key("links", "types", ["implements", "relates", "serves"])

    assert "drop that comment" in str(e.value)
    assert doc.text() == text                       # and the file is untouched


def test_a_trailing_note_on_the_key_is_carried_over():
    """That comment follows the whole value, so it belongs to the key."""
    doc = TomlDocument('[status]\nvalues = ["draft"]  # kept deliberately short\n')
    doc.set_key("status", "values", ["draft", "agreed"])

    assert "# kept deliberately short" in doc.text()


def test_a_long_array_is_wrapped_rather_than_emitted_as_one_long_line():
    doc = TomlDocument("[links]\ntypes = [\"a\"]\n")
    doc.set_key("links", "types", [f"link_type_number_{i}" for i in range(12)])
    lines = [ln for ln in doc.text().splitlines() if ln.strip().startswith(("types", '"'))]

    assert len(lines) > 1
    assert all(len(ln) <= 79 for ln in doc.text().splitlines())


def test_an_array_of_tables_bounds_a_table_without_blocking_the_edit():
    """``[[rules.coverage]]`` is in every default config; it is refused only when
    it is the thing being edited, not when it merely sits in the file."""
    text = ("[status]\nvalues = [\"draft\"]\n\n"
            "[[rules.coverage]]\ntype = \"requirement\"\n")
    doc = TomlDocument(text)
    doc.set_key("status", "values", ["draft", "agreed"])

    assert "[[rules.coverage]]" in doc.text()
    with pytest.raises(TomlEditError, match="array-of-tables"):
        doc.set_key("rules.coverage", "type", "other")


# ------------------------------------------------------------- the command

def _scaffold(tmp_path) -> Path:
    """A bare project with one root and one grounded requirement, so a narrowing
    has something real to invalidate."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--name", "t", "--bare"]) == 0
    assert _cli(["-C", str(root), "register", "new", "INT", "vision"]) == 0
    assert _cli(["-C", str(root), "register", "new", "FR", "features"]) == 0
    assert _cli(["-C", str(root), "new", "INT", "--type", "intent",
                 "--title", "why"]) == 0
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "what", "--ground", "INT-0001",
                 "--ground-type", "derives_from"]) == 0
    return root


def _config(root: Path) -> dict:
    return tomllib.loads((root / "throughline.toml").read_text())


def test_a_widening_is_applied_and_records_why(tmp_path):
    """SR-0181 and SR-0184."""
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "schema", "status", "add", "parked",
                 "--because", "Work can be parked without being withdrawn."]) == 0

    assert "parked" in _config(root)["status"]["values"]
    assert "Work can be parked" in (root / "throughline.toml").read_text()


def test_because_is_required(tmp_path):
    """SR-0184. A schema change with no reason is a usage error, not a default."""
    root = _scaffold(tmp_path)
    with pytest.raises(SystemExit) as e:
        _cli(["-C", str(root), "schema", "status", "add", "parked"])
    assert e.value.code == 2


def test_a_narrowing_that_would_invalidate_an_item_is_refused(tmp_path):
    """SR-0182. The refusal names the item and what would fix it, and the file is
    left as it was — the point is that the author can act on it, not merely that
    the tool declined."""
    root = _scaffold(tmp_path)
    before = (root / "throughline.toml").read_text()

    code = _cli(["-C", str(root), "schema", "grounding", "remove",
                 "ground_link_types", "derives_from",
                 "--because", "Trying to simplify the vocabulary."])

    assert code == 1
    assert (root / "throughline.toml").read_text() == before


def test_the_first_endpoint_rule_on_a_link_type_narrows_it(tmp_path):
    """SR-0182, amended. An unconstrained link type admits every endpoint, so the
    first `allow` is not a widening however much the verb sounds like one — it
    admits one type and excludes the rest."""
    root = _scaffold(tmp_path)
    assert "derives_from" not in _config(root).get("link_rules", {})

    code = _cli(["-C", str(root), "schema", "linkrule", "allow", "derives_from",
                 "--from", "intent",
                 "--because", "Only an intent derives from anything."])

    assert code == 1                       # the existing requirement uses it
    assert "derives_from" not in _config(root).get("link_rules", {})


def test_declaring_the_first_member_of_an_absent_vocabulary_is_refused(tmp_path):
    """SR-0182. An absent `[links] types` admits every link type, so `linktype
    add` would not add one — it would forbid all the others. The refusal proposes
    the list the graph actually uses, so the author can declare it and then add
    to it. Found by sweeping the estate's real configs, several of which leave a
    vocabulary undeclared."""
    root = _scaffold(tmp_path)
    doc = TomlDocument((root / "throughline.toml").read_text())
    doc.remove_key("links", "types")
    (root / "throughline.toml").write_text(doc.text())
    assert "types" not in _config(root).get("links", {})

    code = _cli(["-C", str(root), "schema", "linktype", "add", "supersedes",
                 "--because", "One item replacing another."])

    assert code == 2
    assert "types" not in _config(root).get("links", {})

    # and the refusal names the way through: the proposal it prints is what the
    # graph relies on today, so declaring it and then adding both succeed
    assert _cli(["-C", str(root), "schema", "linktype", "declare",
                 "derives_from", "implements", "mitigates", "verifies",
                 "--because", "The links this project actually relies on."]) == 0
    assert _cli(["-C", str(root), "schema", "linktype", "add", "supersedes",
                 "--because", "One item replacing another."]) == 0
    assert _config(root)["links"]["types"][-1] == "supersedes"


def test_declaring_a_vocabulary_that_strands_a_link_is_refused(tmp_path):
    """SR-0182. `declare` is the way past the refusal above, not a way around it:
    it narrows from 'every link type is legal' to a list, so it is measured
    against the graph like any other narrowing."""
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "link", "FR-0001", "INT-0001",
                 "--type", "relates"]) == 0
    doc = TomlDocument((root / "throughline.toml").read_text())
    doc.remove_key("links", "types")
    (root / "throughline.toml").write_text(doc.text())
    before = (root / "throughline.toml").read_text()

    code = _cli(["-C", str(root), "schema", "linktype", "declare",
                 "derives_from", "implements", "mitigates", "verifies",
                 "--because", "Only the grounding links, to keep it tight."])

    assert code == 1                       # FR-0001 still relates to INT-0001
    assert (root / "throughline.toml").read_text() == before


def test_a_change_the_schema_itself_rejects_is_a_usage_error(tmp_path):
    """A status still named in [transitions] cannot simply be withdrawn; that is
    the schema refusing to build, which is a different failure from a narrowing
    that builds and invalidates items."""
    root = _scaffold(tmp_path)
    code = _cli(["-C", str(root), "schema", "status", "remove", "draft",
                 "--because", "No longer used."])

    assert code == 2


# ------------------------------------------- coverage rules (SR-0192)

def test_a_coverage_rule_can_be_added_and_records_why(tmp_path):
    """SR-0192. `[[rules.coverage]]` was the one part of the schema with no verb,
    so a project wanting a rule other than the seeded one had to edit the file."""
    root = _scaffold(tmp_path)
    code = _cli(["-C", str(root), "schema", "rule", "add",
                 "--filter", "type == 'requirement' and status != 'deleted'",
                 "--needs", "incoming:verifies",
                 "--because", "A requirement nothing tests is not finished."])

    assert code == 0
    rules = _config(root)["rules"]["coverage"]
    assert {"filter": "type == 'requirement' and status != 'deleted'",
            "needs": "incoming:verifies"} in rules
    assert "A requirement nothing tests" in (root / "throughline.toml").read_text()


def test_adding_a_rule_whose_filter_cannot_be_evaluated_is_refused(tmp_path):
    """SR-0191 and SR-0192 together: this is why the verb exists. The expression
    is the one that made a real project's gate inert — a bare attribute name
    where the language requires attrs.get(...). It must not reach the file."""
    root = _scaffold(tmp_path)
    before = (root / "throughline.toml").read_text()

    code = _cli(["-C", str(root), "schema", "rule", "add",
                 "--filter", "type == 'requirement' and verification == 'test'",
                 "--needs", "incoming:verifies",
                 "--because", "Tested requirements need a test."])

    assert code == 2
    assert (root / "throughline.toml").read_text() == before


def test_adding_a_rule_with_an_unusable_needs_clause_is_refused(tmp_path):
    root = _scaffold(tmp_path)
    before = (root / "throughline.toml").read_text()
    code = _cli(["-C", str(root), "schema", "rule", "add",
                 "--filter", "type == 'requirement'", "--needs", "verifies",
                 "--because", "x"])
    assert code == 2
    assert (root / "throughline.toml").read_text() == before


def test_a_coverage_rule_can_be_removed_by_position(tmp_path):
    """SR-0192. A coverage rule has no name to be called by, so it is withdrawn
    by the position `tl context` prints it at."""
    root = _scaffold(tmp_path)
    base = len(_config(root).get("rules", {}).get("coverage", []))
    for ltype in ("verifies", "implements"):
        assert _cli(["-C", str(root), "schema", "rule", "add",
                     "--filter", f"type == '{ltype}-ish'",
                     "--needs", f"incoming:{ltype}",
                     "--because", f"Cover {ltype}."]) == 0
    assert len(_config(root)["rules"]["coverage"]) == base + 2

    code = _cli(["-C", str(root), "schema", "rule", "remove", str(base + 1),
                 "--because", "Superseded by the second rule."])

    assert code == 0
    remaining = _config(root)["rules"]["coverage"]
    assert len(remaining) == base + 1
    assert remaining[-1]["needs"] == "incoming:implements"
    # The reason survives the thing it explains (SR-0184).
    assert "Superseded by the second rule." in (root / "throughline.toml").read_text()


def test_removing_a_rule_that_is_not_there_is_a_usage_error(tmp_path):
    root = _scaffold(tmp_path)
    beyond = len(_config(root).get("rules", {}).get("coverage", [])) + 1
    assert _cli(["-C", str(root), "schema", "rule", "remove", str(beyond),
                 "--because", "x"]) == 2


def test_adding_a_rule_leaves_the_rest_of_the_file_alone(tmp_path):
    """SR-0183. An array-of-tables is appended, so no existing block moves."""
    root = _scaffold(tmp_path)
    before = (root / "throughline.toml").read_text()
    assert _cli(["-C", str(root), "schema", "rule", "add",
                 "--filter", "type == 'requirement'",
                 "--needs", "incoming:verifies",
                 "--because", "Requirements need tests."]) == 0
    after = (root / "throughline.toml").read_text()
    assert after.startswith(before.rstrip("\n"))
