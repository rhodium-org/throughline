# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""UR-0039, UR-0040 — more than one person takes accountability for an item, each in
a role the project declares, and every signature says when it was given (SR-0254
to SR-0263).

A project that declares no roles must behave exactly as before: one signature from
anyone ratifies, a second over unchanged content is refused, and nothing in its
configuration changes. The tests below hold that line first, then exercise what a
project gains by declaring roles.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from throughline.cli import main as cli_main
from throughline.fingerprint import fingerprint
from throughline.grounding import GroundingError, ratify, withdraw
from throughline.identity import default_identifier
from throughline.model import Item
from throughline.schema import Schema, SchemaError
from throughline.signatures import (
    AT, BY, ID, ROLE, STAMP, W_AT, W_BY, W_OF, W_OF_ID, W_REASON,
    lift_legacy, requirement_met, standing,
)
from throughline.storage import load_project, migrate_project, read_project, write_item
from throughline.validate import validate
from throughline.worklist import entry_for, worklist

ROLES = ('\n[ratify.roles]\nsenior = ["github:ada"]\n'
         'junior = ["github:bob", "email:carol@example.com"]\n')
REQUIRES = '\n[ratify.requires.requirement]\nroles = ["senior"]\nsignatures = 2\n'


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _project(tmp_path: Path, *, config: str = "") -> Path:
    """One human intent and two AI requirements, nothing signed."""
    root = tmp_path / "proj"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    assert _cli(["-C", root, "schema", "attr", "add", "intent", "origin",
                 "--kind", "enum", "--values", "human,ai,hybrid",
                 "--because", "the fixture records who authored each intent"]) == 0
    if config:
        cfg = root / "throughline.toml"
        cfg.write_text(cfg.read_text(encoding="utf-8") + config, encoding="utf-8")
    assert _cli(["-C", root, "new", "INT", "--type", "intent", "--title", "Why",
                 "--text", "The vision.", "--origin", "human", "--no-interactive"]) == 0
    for n in ("1", "2"):
        assert _cli(["-C", root, "new", "REQ", "--title", f"Thing {n}",
                     "--text", f"The Tool shall do thing {n}.", "--ground", "INT-0001",
                     "--ground-type", "implements", "--origin", "ai",
                     "--attr", "priority=must", "--no-interactive"]) == 0
    return root


def _req(root: Path, uid: str = "REQ-0001"):
    return load_project(root).get(uid)


def _rules(root: Path, uid: str = "REQ-0001") -> list[str]:
    return [f.rule for f in validate(load_project(root)) if f.uid == uid]


def _finding(root: Path, rule: str, uid: str = "REQ-0001") -> str:
    [f] = [f for f in validate(load_project(root)) if f.uid == uid and f.rule == rule]
    return f.message


# ------------------------------------------------ a project with no roles (SR-0254)

def test_one_signature_still_ratifies_where_no_roles_are_declared(tmp_path):
    root = _project(tmp_path)
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada Lovelace"]) == 0
    item = _req(root)
    assert item.status == "ratified"
    [entry] = item.ratifications
    assert entry[BY] == "Ada Lovelace" and entry[STAMP] == fingerprint(
        item, load_project(root).schema)
    assert ROLE not in entry
    assert "unratified" not in _rules(root) and "awaiting-signatures" not in _rules(root)


def test_the_record_is_a_top_level_list_and_no_flat_attribute_is_written(tmp_path):
    root = _project(tmp_path)
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada Lovelace"]) == 0
    text = (root / "requirements" / "REQ-0001.yml").read_text(encoding="utf-8")
    assert "\nratifications:\n- by: Ada Lovelace\n" in text
    assert "ratified_by" not in text and "ratified_fingerprint" not in text
    assert not [k for k in _req(root).attrs if k.startswith("ratified_")]


def test_a_second_person_over_unchanged_content_is_refused_without_roles(tmp_path, capsys):
    """One signature is all an item takes where no roles are declared, so another
    over the same words accepts nothing — exactly as before (SR-0148)."""
    root = _project(tmp_path)
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada Lovelace"]) == 0
    capsys.readouterr()
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Bob Malory"]) == 2
    err = capsys.readouterr().err
    assert "already ratified by Ada Lovelace" in err and "nothing to accept" in err
    assert [e[BY] for e in _req(root).ratifications] == ["Ada Lovelace"]


def test_a_signature_over_changed_content_replaces_the_stale_one_without_roles(tmp_path):
    """The one-slot behaviour of the flat record is kept for a project that never
    asked for more: the new signature stands and the stale one goes."""
    root = _project(tmp_path)
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada Lovelace"]) == 0
    assert _cli(["-C", root, "amend", "REQ-0001", "--text", "The Tool shall do more."]) == 0
    assert "ratified-stale" in _rules(root)
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Bob Malory",
                 "--accept-change"]) == 0
    assert [e[BY] for e in _req(root).ratifications] == ["Bob Malory"]
    assert "ratified-stale" not in _rules(root)


def test_naming_a_role_where_none_is_declared_is_refused(tmp_path, capsys):
    root = _project(tmp_path)
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada", "--as", "senior"]) == 2
    assert "declares no roles" in capsys.readouterr().err
    assert not _req(root).ratifications


# ----------------------------------------------------------- when (SR-0256, UR-0040)

def test_a_signature_and_a_withdrawal_record_when_in_utc_to_the_second(tmp_path):
    root = _project(tmp_path)
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada Lovelace"]) == 0
    stamp = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    assert stamp.match(_req(root).ratifications[-1][AT])
    assert _cli(["-C", root, "withdraw", "REQ-0001", "--reason", "r", "--by", "Grace"]) == 0
    assert stamp.match(_req(root).withdrawals[-1][W_AT])


def test_the_time_can_be_supplied_by_a_host(tmp_path):
    """A parameter, so a host with no clock of its own supplies the moment."""
    root = _project(tmp_path)
    project = load_project(root)
    ratify(project, "REQ-0001", by="Ada", at="2026-01-02T03:04:05Z")
    assert project.get("REQ-0001").ratifications[-1][AT] == "2026-01-02T03:04:05Z"


# -------------------------------------------------- roles in the toml (SR-0257, SR-0258)

@pytest.mark.parametrize("config, says", [
    ({"ratify": {"roles": {"senior": []}}}, "non-empty list"),
    ({"ratify": {"roles": {"senior": ["ada"]}}}, "scheme"),
    ({"ratify": {"roles": {"senior": ["github:a", "github:a"]}}}, "twice"),
    ({"ratify": {"roles": "senior"}}, "table"),
    ({"ratify": {"requires": {"requirement": {"roles": ["senior"]}}}}, "needs [ratify.roles]"),
    ({"types": {"requirement": {}}, "ratify": {"roles": {"s": ["github:a"]},
      "requires": {"other": {"roles": ["s"]}}}}, "does not declare"),
    ({"types": {"requirement": {}}, "ratify": {"roles": {"s": ["github:a"]},
      "requires": {"requirement": {"roles": ["nobody"]}}}}, "does not declare"),
    ({"types": {"requirement": {}}, "ratify": {"roles": {"s": ["github:a"]},
      "requires": {"requirement": {"signatures": 0}}}}, "at least 1"),
    ({"types": {"requirement": {}}, "ratify": {"roles": {"s": ["github:a"]},
      "requires": {"requirement": {"people": 2}}}}, "unknown key"),
])
def test_a_malformed_roles_or_requires_table_is_refused(config, says):
    with pytest.raises(SchemaError, match=re.escape(says)):
        Schema.from_config(config)


def test_a_type_with_no_declaration_requires_one_signature_in_a_declared_role():
    schema = Schema.from_config({"types": {"requirement": {}, "nfr": {}},
                                 "ratify": {"roles": {"senior": ["github:ada"]},
                                            "requires": {"requirement": {"roles": ["senior"]}}}})
    assert schema.requirement("requirement").roles == ("senior",)
    assert schema.requirement("nfr").roles == () and schema.requirement("nfr").signatures == 1
    assert schema.roles_of("github:ada") == ["senior"] and schema.roles_of("github:x") == []


def test_the_brief_names_the_roles_and_what_each_kind_requires(tmp_path, capsys):
    root = _project(tmp_path, config=ROLES + REQUIRES)
    assert _cli(["-C", root, "context"]) == 0
    out = capsys.readouterr().out
    assert "`senior` held by github:ada" in out
    assert "`requirement` needs a signature in `senior` and at least 2 signatures" in out
    assert "awaiting-signatures" in out


# -------------------------------------------------------- signing in a role (SR-0259)

def test_a_role_project_needs_a_role_an_identifier_and_a_holder(tmp_path, capsys):
    root = _project(tmp_path, config=ROLES)
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada"]) == 2
    assert "--as <role>" in capsys.readouterr().err
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada", "--as", "boss"]) == 2
    assert "not a role this project declares" in capsys.readouterr().err
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada", "--as", "senior"]) == 2
    assert "--by-id" in capsys.readouterr().err
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada", "--as", "senior",
                 "--by-id", "github:bob"]) == 2
    err = capsys.readouterr().err
    assert "does not hold the role 'senior'" in err and "it holds junior" in err
    assert not _req(root).ratifications


def test_a_holder_signs_in_their_role_and_the_signature_says_so(tmp_path, capsys):
    root = _project(tmp_path, config=ROLES)
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada", "--as", "senior",
                 "--by-id", "github:ada"]) == 0
    assert "REQ-0001 ratified by Ada (github:ada, as senior)" in capsys.readouterr().out
    [entry] = _req(root).ratifications
    assert (entry[BY], entry[ID], entry[ROLE]) == ("Ada", "github:ada", "senior")
    assert _req(root).status == "ratified"      # the type requires one signature


def test_the_default_identifier_is_the_repository_signing_email(tmp_path):
    assert default_identifier(tmp_path, identity=lambda p: ("Ada", "ada@example.com")) \
        == "email:ada@example.com"
    assert default_identifier(tmp_path, identity=lambda p: ("Ada", None)) is None


def test_a_person_signs_once_and_a_second_signature_renews_their_own(tmp_path):
    root = _project(tmp_path, config=ROLES + REQUIRES)
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada", "--as", "senior",
                 "--by-id", "github:ada"]) == 0
    assert _cli(["-C", root, "amend", "REQ-0001", "--text", "The Tool shall do more."]) == 0
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada Lovelace", "--as",
                 "senior", "--by-id", "github:ada", "--accept-change"]) == 0
    [entry] = _req(root).ratifications                 # one entry, renewed
    assert entry[BY] == "Ada Lovelace" and entry[ID] == "github:ada"


# ------------------------------------------- awaiting the rest (SR-0260), stale (SR-0261)

def test_an_item_counts_as_ratified_only_when_its_kind_s_requirement_is_met(tmp_path, capsys):
    root = _project(tmp_path, config=ROLES + REQUIRES)
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Bob", "--as", "junior",
                 "--by-id", "github:bob"]) == 0
    out = capsys.readouterr().out
    assert "REQ-0001 signed by Bob (github:bob, as junior)" in out
    assert "still requires a signature in the role 'senior'" in out and "not yet ratified" in out
    item = _req(root)
    assert item.status == "proposed"                   # the status has not moved
    assert "awaiting-signatures" in _rules(root) and "unratified" not in _rules(root)
    assert "signed by Bob (github:bob, as junior)" in _finding(root, "awaiting-signatures")
    assert entry_for(load_project(root), item).concern == "awaiting"
    assert [e.uid for e in worklist(load_project(root))][0] in ("REQ-0001", "REQ-0002")

    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada", "--as", "senior",
                 "--by-id", "github:ada"]) == 0
    assert "REQ-0001 ratified by Ada (github:ada, as senior)" in capsys.readouterr().out
    item = _req(root)
    assert item.status == "ratified"
    assert [e[ROLE] for e in item.ratifications] == ["junior", "senior"]
    assert "awaiting-signatures" not in _rules(root)
    assert requirement_met(item, load_project(root).schema)
    assert _cli(["-C", root, "check", "--strict"]) != 0   # REQ-0002 is still unratified
    capsys.readouterr()


def test_strict_check_fails_while_an_item_awaits_signatures(tmp_path, capsys):
    root = _project(tmp_path, config=ROLES + REQUIRES)
    assert _cli(["-C", root, "ratify", "REQ-0001", "REQ-0002", "--by", "Ada", "--as",
                 "senior", "--by-id", "github:ada"]) == 0
    capsys.readouterr()
    assert _cli(["-C", root, "check", "--strict"]) == 1
    text = capsys.readouterr().out
    assert text.count("awaiting-signatures") == 2 and "unratified" not in text


def test_each_signature_is_judged_stale_on_its_own(tmp_path):
    root = _project(tmp_path, config=ROLES + REQUIRES)
    for who, role, ident in (("Bob", "junior", "github:bob"), ("Ada", "senior", "github:ada")):
        assert _cli(["-C", root, "ratify", "REQ-0001", "--by", who, "--as", role,
                     "--by-id", ident]) == 0
    assert _req(root).status == "ratified"
    assert _cli(["-C", root, "amend", "REQ-0001", "--text", "The Tool shall do more."]) == 0
    findings = [f for f in validate(load_project(root)) if f.uid == "REQ-0001"]
    stale = sorted(f.message for f in findings if f.rule == "ratified-stale")
    assert len(stale) == 2 and any("Ada" in m for m in stale) and any("Bob" in m for m in stale)
    # Ada renews hers; Bob's stays stale, so the requirement is still unmet and the
    # worklist says so by name.
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada", "--as", "senior",
                 "--by-id", "github:ada", "--accept-change"]) == 0
    rules = _rules(root)
    assert rules.count("ratified-stale") == 1 and "awaiting-signatures" in rules
    project = load_project(root)
    where = standing(project.get("REQ-0001"), project.schema)
    assert not where.met and [e[BY] for e in where.held] == ["Ada"]
    assert "Bob" in [e[BY] for e in project.get("REQ-0001").ratifications]


def test_a_suspect_item_can_be_signed_again_whatever_it_holds(tmp_path):
    root = _project(tmp_path, config=ROLES)
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada", "--as", "senior",
                 "--by-id", "github:ada"]) == 0
    assert _cli(["-C", root, "invalidate", "INT-0001", "--reason", "superseded"]) == 0
    assert _req(root).status == "suspect"
    assert entry_for(load_project(root), _req(root)).ratifiable
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada", "--as", "senior",
                 "--by-id", "github:ada"]) == 0
    assert _req(root).status == "ratified"


# ------------------------------------------------------ withdrawing one (SR-0262)

def test_withdrawing_names_whose_signature_where_an_item_holds_several(tmp_path, capsys):
    root = _project(tmp_path, config=ROLES + REQUIRES)
    for who, role, ident in (("Bob", "junior", "github:bob"), ("Ada", "senior", "github:ada")):
        assert _cli(["-C", root, "ratify", "REQ-0001", "--by", who, "--as", role,
                     "--by-id", ident]) == 0
    capsys.readouterr()
    assert _cli(["-C", root, "withdraw", "REQ-0001", "--reason", "r", "--by", "Grace"]) == 2
    assert "say whose to withdraw with --of" in capsys.readouterr().err
    assert _cli(["-C", root, "withdraw", "REQ-0001", "--reason", "r", "--by", "Grace",
                 "--of", "nobody"]) == 2
    assert "carries no signature by nobody" in capsys.readouterr().err
    assert _cli(["-C", root, "withdraw", "REQ-0001", "--reason", "signed in error",
                 "--by", "Grace", "--of", "github:bob"]) == 0
    item = _req(root)
    assert [e[BY] for e in item.ratifications] == ["Ada"]
    [record] = item.withdrawals
    assert (record[W_BY], record[W_REASON], record[W_OF], record[W_OF_ID]) == (
        "Grace", "signed in error", "Bob", "github:bob")
    # Two were required and one is left, so the item is no longer ratified.
    assert item.status == "suspect"
    assert "ratification by Bob withdrawn by Grace" in capsys.readouterr().out


def test_a_withdrawal_that_leaves_the_requirement_met_moves_nothing(tmp_path, capsys):
    root = _project(tmp_path, config=ROLES)           # one signature in a role suffices
    for who, role, ident in (("Bob", "junior", "github:bob"), ("Ada", "senior", "github:ada")):
        assert _cli(["-C", root, "ratify", "REQ-0001", "--by", who, "--as", role,
                     "--by-id", ident]) == 0
    capsys.readouterr()
    assert _cli(["-C", root, "withdraw", "REQ-0001", "--reason", "r", "--by", "Grace",
                 "--of", "Bob"]) == 0
    item = _req(root)
    assert item.status == "ratified" and [e[BY] for e in item.ratifications] == ["Ada"]
    assert "still ratified" in capsys.readouterr().out
    assert "suspect_reasons" not in item.attrs


def test_a_withdrawal_from_a_part_signed_item_leaves_its_status_alone(tmp_path):
    """An item that never met its requirement is not made suspect by losing a
    signature it was still gathering."""
    root = _project(tmp_path, config=ROLES + REQUIRES)
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Bob", "--as", "junior",
                 "--by-id", "github:bob"]) == 0
    assert _cli(["-C", root, "withdraw", "REQ-0001", "--reason", "r", "--by", "Grace"]) == 0
    item = _req(root)
    assert item.status == "proposed" and not item.ratifications and item.withdrawals


def test_the_library_withdraw_takes_of_as_a_parameter(tmp_path):
    root = _project(tmp_path, config=ROLES)
    project = load_project(root)
    ratify(project, "REQ-0001", by="Ada", by_id="github:ada", role="senior")
    ratify(project, "REQ-0001", by="Bob", by_id="github:bob", role="junior")
    with pytest.raises(GroundingError, match="--of"):
        withdraw(project, ["REQ-0001"], by="Grace", reason="r")
    withdraw(project, ["REQ-0001"], by="Grace", reason="r", of="Ada")
    assert [e[BY] for e in project.get("REQ-0001").ratifications] == ["Bob"]


# -------------------------------------------------- the guard (SR-0263), the dump

def test_no_other_operation_writes_the_list_and_the_old_names_stay_refused(tmp_path, capsys):
    root = _project(tmp_path)
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    for name in ("ratified_by", "ratified_fingerprint", "withdrawn_by"):
        assert _cli(["-C", root, "amend", "REQ-0001", "--attr", f"{name}=x"]) == 2
        assert "ratification record" in capsys.readouterr().err
        assert _cli(["-C", root, "amend", "REQ-0001", "--unset", name]) == 2
        assert "ratification record" in capsys.readouterr().err
    assert [e[BY] for e in _req(root).ratifications] == ["Ada"]


def test_the_dump_carries_the_list_and_says_so(tmp_path, capsys):
    root = _project(tmp_path)
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    capsys.readouterr()
    assert _cli(["-C", root, "dump"]) == 0
    dump = json.loads(capsys.readouterr().out)
    assert dump["throughline_dump"]["dump_schema_version"] == 2
    assert dump["throughline_dump"]["format_version"] == 4
    item = {i["uid"]: i for i in dump["items"]}["REQ-0001"]
    assert item["ratifications"][0]["by"] == "Ada" and "ratified_by" not in item.get("attrs", {})


def test_a_published_item_block_names_the_signers_and_nothing_else_of_the_record(tmp_path):
    root = _project(tmp_path, config=ROLES)
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada", "--as", "senior",
                 "--by-id", "github:ada"]) == 0
    docs = root / "docs"
    docs.mkdir()
    (docs / "spec.md").write_text("# S\n\n<!-- tl:item REQ-0001 -->\n<!-- tl:end -->\n",
                                  encoding="utf-8")
    cfg = root / "throughline.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + '\n[docs]\npaths = ["docs/*.md"]\n',
                   encoding="utf-8")
    assert _cli(["-C", root, "docs"]) == 0
    doc = (docs / "spec.md").read_text(encoding="utf-8")
    assert "*Ratified by:* Ada (github:ada, as senior)" in doc
    assert "sha256:" not in doc


# ------------------------------------------------------ migration (SR-0255, SR-0133)

def _as_format_3(root: Path) -> Path:
    """Put a signed project into the shape a format-3 Tool wrote: the flat record
    under attrs, and the earlier major declared."""
    item = _req(root)
    [entry] = item.ratifications
    item.ratifications = []
    item.attrs.update({"ratified_by": entry[BY], "ratified_fingerprint": entry[STAMP],
                       "ratified_content": entry["content"], "ratified_id": "github:ada"})
    write_item(item)
    cfg = root / "throughline.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("format_version = 4",
                                                           "format_version = 3"),
                   encoding="utf-8")
    return root / "requirements" / "REQ-0001.yml"


def test_lift_legacy_carries_the_flat_record_verbatim_and_invents_nothing():
    attrs = {"origin": "ai", "ratified_by": "Ada", "ratified_fingerprint": "sha256:x",
             "ratified_backfilled": True, "withdrawn_by": "Grace", "withdrawn_reason": "r",
             "withdrawn_ratifier": "Bob"}
    lifted, withdrawn = lift_legacy(attrs)
    assert attrs == {"origin": "ai"}
    assert lifted == [{"by": "Ada", "fingerprint": "sha256:x", "backfilled": True}]
    assert withdrawn == [{"by": "Grace", "reason": "r", "of": "Bob"}]
    assert "at" not in lifted[0] and "role" not in lifted[0]


def test_a_format_3_project_is_read_only_until_migrated_and_then_lifted(tmp_path, capsys):
    root = _project(tmp_path)
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    stamp = _req(root).ratifications[-1][STAMP]
    path = _as_format_3(root)
    assert "ratified_by: Ada" in path.read_text(encoding="utf-8")
    capsys.readouterr()
    assert _cli(["-C", root, "check"]) == 2                 # refused: run tl migrate
    assert "tl migrate" in capsys.readouterr().err
    # The tolerant read presents the flat record as a list of one (SR-0133).
    item = read_project(root).get("REQ-0001")
    assert [e[BY] for e in item.ratifications] == ["Ada"]
    assert item.ratifications[0][STAMP] == stamp and item.ratifications[0][ID] == "github:ada"

    result = migrate_project(root)
    assert (result.start, result.end, result.lifted) == (3, 4, 1)
    text = path.read_text(encoding="utf-8")
    assert "ratified_by" not in text and "\nratifications:\n- by: Ada\n" in text
    item = _req(root)
    [entry] = item.ratifications
    assert entry[STAMP] == stamp and AT not in entry and ROLE not in entry
    assert item.status == "ratified" and "ratified-stale" not in _rules(root)
    # Idempotent: a second run lifts nothing and reports so.
    assert migrate_project(root).lifted == 0
    assert _cli(["-C", root, "migrate"]) == 0
    assert "nothing to migrate" in capsys.readouterr().out


def test_the_lift_is_reported_by_the_command(tmp_path, capsys):
    root = _project(tmp_path)
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    _as_format_3(root)
    capsys.readouterr()
    assert _cli(["-C", root, "migrate"]) == 0
    out = capsys.readouterr().out
    assert "migrated project from format version 3 to 4" in out
    assert "lifted the ratification record on 1 item(s)" in out


def test_an_item_read_from_a_flat_record_and_written_back_holds_the_list():
    item = Item.from_dict({"uid": "X-0001", "type": "t",
                           "attrs": {"ratified_by": "Ada", "ratified_fingerprint": "sha256:a"}})
    assert item.ratifications == [{"by": "Ada", "fingerprint": "sha256:a"}]
    d = item.to_dict()
    assert "attrs" not in d and d["ratifications"] == [{"by": "Ada", "fingerprint": "sha256:a"}]


# ------------------------------------------- through the schema operation (SR-0264)

def _toml(root: Path) -> str:
    return (root / "throughline.toml").read_text(encoding="utf-8")


def test_roles_and_requirements_are_declared_through_tl_schema(tmp_path, capsys):
    root = _project(tmp_path)
    assert _cli(["-C", root, "schema", "role", "add", "senior", "github:ada",
                 "--because", "Ada reviews"]) == 0
    assert _cli(["-C", root, "schema", "role", "add", "senior", "email:carol@example.com",
                 "--because", "Carol too"]) == 0
    assert _cli(["-C", root, "schema", "role", "add", "junior", "github:bob",
                 "--because", "Bob learns"]) == 0
    assert _cli(["-C", root, "schema", "role", "add", "junior", "github:bob",
                 "--because", "again"]) == 2
    assert "already holds" in capsys.readouterr().err
    assert _cli(["-C", root, "schema", "role", "add", "junior", "bob",
                 "--because", "no scheme"]) == 2
    schema = load_project(root).schema
    assert schema.roles == {"senior": ("github:ada", "email:carol@example.com"),
                            "junior": ("github:bob",)}
    assert "# Ada reviews" in _toml(root) and "[ratify.roles]" in _toml(root)

    assert _cli(["-C", root, "schema", "requires", "set", "requirement", "--roles", "senior",
                 "--signatures", "2", "--because", "two eyes"]) == 0
    req = load_project(root).schema.requirement("requirement")
    assert (req.roles, req.signatures) == (("senior",), 2)
    assert _cli(["-C", root, "schema", "requires", "set", "requirement", "--roles", "boss",
                 "--because", "nobody"]) == 2
    assert "does not declare" in capsys.readouterr().err
    assert _cli(["-C", root, "schema", "role", "remove", "senior", "--because", "gone"]) == 2
    assert "names 'senior'" in capsys.readouterr().err
    assert _cli(["-C", root, "schema", "role", "remove", "junior", "github:bob",
                 "--because", "last"]) == 2
    assert "only holder" in capsys.readouterr().err
    assert _cli(["-C", root, "schema", "role", "remove", "senior", "email:carol@example.com",
                 "--because", "Carol left"]) == 0
    assert load_project(root).schema.roles["senior"] == ("github:ada",)
    assert _cli(["-C", root, "schema", "requires", "clear", "requirement",
                 "--because", "relaxed"]) == 0
    assert load_project(root).schema.requirement("requirement").roles == ()
    assert "[ratify.requires.requirement]" not in _toml(root)
    assert _cli(["-C", root, "schema", "role", "remove", "senior", "--because", "gone"]) == 0
    assert "senior" not in load_project(root).schema.roles


def test_tightening_a_requirement_is_allowed_and_names_what_it_left_short(tmp_path, capsys):
    root = _project(tmp_path, config=ROLES)
    for uid in ("REQ-0001", "REQ-0002"):
        assert _cli(["-C", root, "ratify", uid, "--by", "Ada", "--as", "senior",
                     "--by-id", "github:ada"]) == 0
    assert "awaiting-signatures" not in _rules(root)
    capsys.readouterr()
    assert _cli(["-C", root, "schema", "requires", "set", "requirement",
                 "--roles", "senior,junior", "--because", "a junior must read each one"]) == 0
    out = capsys.readouterr().out
    assert "2 item(s) now await signatures their kind requires: REQ-0001 REQ-0002" in out
    for uid in ("REQ-0001", "REQ-0002"):
        assert _req(root, uid).status == "ratified"          # the status did not move
        assert "awaiting-signatures" in _rules(root, uid)
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Bob", "--as", "junior",
                 "--by-id", "github:bob"]) == 0
    assert "awaiting-signatures" not in _rules(root, "REQ-0001")
