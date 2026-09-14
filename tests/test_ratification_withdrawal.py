# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""SR-0197 — withdrawing a ratification returns the item for signature and records
who withdrew it.

UR-0030 corrects a record only while it is unpublished, because that is the only
case the Tool can hold without authenticating anybody. Every published record is
therefore beyond correction — and the way out is not a stronger power to overwrite
but a weaker one that asks a human to sign again. Withdrawal separates saying that
a signature no longer stands from saying who signs instead; only the second is a
claim about a person, so anyone may withdraw, and a withdrawal cannot ratify.

Distinguished from invalidation: ``tl invalidate`` says an item is false and
cascades suspicion into its dependents; this says nothing about the item. The words
have not moved, so nothing resting on them moves either.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from throughline.cli import main as cli_main
from throughline.fingerprint import fingerprint
from throughline.grounding import GroundingError, withdraw
from throughline.identity import (
    RATIFICATION_ATTRS,
    WITHDRAWN_BY_ATTR,
    WITHDRAWN_ID_ATTR,
    WITHDRAWN_RATIFIER_ATTR,
    WITHDRAWN_REASON_ATTR,
)
from throughline.storage import load_project

CONFIG_EXTRA = '''
[types.intent]
attrs.origin = { type = "enum", values = ["human", "ai", "hybrid"] }
'''

WITHDRAWAL = [WITHDRAWN_BY_ATTR, WITHDRAWN_ID_ATTR, WITHDRAWN_REASON_ATTR,
              WITHDRAWN_RATIFIER_ATTR]
SIGNATURE = [name for name, owner in RATIFICATION_ATTRS.items()
             if owner != "withdraw"]


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


@pytest.fixture
def graph(tmp_path) -> Path:
    """Two requirements ratified under a misspelled name, the second grounded on
    the first, and a third left proposed."""
    root = tmp_path / "proj"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    cfg = root / "throughline.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + CONFIG_EXTRA, encoding="utf-8")
    assert _cli(["-C", root, "new", "INT", "--type", "intent", "--title", "Why",
                 "--text", "V.", "--origin", "human", "--no-interactive"]) == 0
    for n, ground in (("R1", "INT-0001"), ("R2", "REQ-0001"), ("R3", "INT-0001")):
        assert _cli(["-C", root, "new", "REQ", "--title", n, "--text",
                     f"The Tool shall do {n}.", "--ground", ground,
                     "--ground-type", "implements", "--origin", "ai",
                     "--no-interactive"]) == 0
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "AAda Lovelace"]) == 0
    assert _cli(["-C", root, "ratify", "REQ-0002", "--by", "AAda Lovelace"]) == 0
    return root


def _item(root: Path, uid: str):
    return load_project(root).get(uid)


# ------------------------------------------------------------ what a withdrawal does

def test_a_withdrawal_returns_the_item_to_the_suspect_role(graph):
    """The status the project binds to the suspect role, resolved by role and never
    a literal (SR-0131): it already means 'a human must look again' (SR-0197)."""
    assert _cli(["-C", graph, "withdraw", "REQ-0001", "--reason", "misspelled",
                 "--by", "Ada Lovelace"]) == 0
    item = _item(graph, "REQ-0001")
    assert item.status == load_project(graph).schema.status_role("suspect")


def test_a_withdrawal_records_who_why_and_whose(graph):
    """Recorded rather than silent — that is what makes removing a signature as
    accountable as giving one."""
    assert _cli(["-C", graph, "withdraw", "REQ-0001", "--reason", "misspelled",
                 "--by", "Ada Lovelace", "--by-id", "github:ada"]) == 0
    a = _item(graph, "REQ-0001").attrs
    assert a[WITHDRAWN_BY_ATTR] == "Ada Lovelace"
    assert a[WITHDRAWN_ID_ATTR] == "github:ada"
    assert a[WITHDRAWN_REASON_ATTR] == "misspelled"
    assert a[WITHDRAWN_RATIFIER_ATTR] == "AAda Lovelace"


def test_a_withdrawal_clears_the_signature_it_withdrew(graph):
    """The signature no longer stands, so the record the ratifying verbs own is
    gone — the withdrawn identity lives on in the withdrawal record, not there."""
    assert _cli(["-C", graph, "withdraw", "REQ-0001", "--reason", "misspelled",
                 "--by", "Ada Lovelace"]) == 0
    a = _item(graph, "REQ-0001").attrs
    assert not any(name in a for name in SIGNATURE), a


def test_content_is_untouched(graph):
    """Nothing the item says has moved: this is not invalidation."""
    before = _item(graph, "REQ-0001")
    stamp = fingerprint(before, load_project(graph).schema)
    assert _cli(["-C", graph, "withdraw", "REQ-0001", "--reason", "misspelled",
                 "--by", "Ada Lovelace"]) == 0
    project = load_project(graph)
    after = project.get("REQ-0001")
    assert (after.title, after.text) == (before.title, before.text)
    assert fingerprint(after, project.schema) == stamp


def test_dependents_are_unmoved(graph):
    """REQ-0002 rests on REQ-0001. Its footing has not gone — the words it rests on
    are the same words — so it stays ratified and its link stamp still holds."""
    assert _cli(["-C", graph, "withdraw", "REQ-0001", "--reason", "misspelled",
                 "--by", "Ada Lovelace"]) == 0
    dep = _item(graph, "REQ-0002")
    assert dep.status == "ratified"
    assert dep.attrs["ratified_by"] == "AAda Lovelace"
    assert "suspect_reasons" not in dep.attrs


def test_the_reason_is_also_readable_as_a_suspect_reason(graph):
    """The suspect role's existing explanation channel carries it, so anything
    that already reads suspect_reasons sees why without learning a new field."""
    assert _cli(["-C", graph, "withdraw", "REQ-0001", "--reason", "misspelled",
                 "--by", "Ada Lovelace"]) == 0
    reasons = _item(graph, "REQ-0001").attrs["suspect_reasons"]
    assert any("AAda Lovelace" in r and "Ada Lovelace" in r and "misspelled" in r
               for r in reasons)


# -------------------------------------------------------------------- a set at once

def test_a_batch_withdraws_every_item_named(graph):
    """The motivating case is a set; a supported route slower than a hand edit is
    one people stop using."""
    assert _cli(["-C", graph, "withdraw", "REQ-0001", "REQ-0002",
                 "--reason", "signed under a hand-typed name",
                 "--by", "Ada Lovelace"]) == 0
    for uid in ("REQ-0001", "REQ-0002"):
        a = _item(graph, uid).attrs
        assert a[WITHDRAWN_RATIFIER_ATTR] == "AAda Lovelace"
        assert a[WITHDRAWN_REASON_ATTR] == "signed under a hand-typed name"
        assert "ratified_by" not in a


def test_a_batch_is_all_or_nothing(graph):
    """A mistyped UID in a batch is a failure the caller sees, not a run that
    withdrew half of what it was asked to."""
    assert _cli(["-C", graph, "withdraw", "REQ-0001", "REQ-9999",
                 "--reason", "r", "--by", "Ada Lovelace"]) == 2
    assert _item(graph, "REQ-0001").attrs["ratified_by"] == "AAda Lovelace"
    assert _item(graph, "REQ-0001").status == "ratified"


# -------------------------------------------------------------------- refusals

def test_an_item_with_no_ratification_is_refused(graph, capsys):
    """Nothing to withdraw is an error, not a no-op read as success."""
    assert _cli(["-C", graph, "withdraw", "REQ-0003", "--reason", "r",
                 "--by", "Ada Lovelace"]) == 2
    assert "no ratification to withdraw" in capsys.readouterr().err
    assert _item(graph, "REQ-0003").status == "proposed"


def test_a_reason_is_required(graph, capsys):
    assert _cli(["-C", graph, "withdraw", "REQ-0001", "--by", "Ada Lovelace"]) == 2
    assert "--reason" in capsys.readouterr().err
    assert _item(graph, "REQ-0001").attrs["ratified_by"] == "AAda Lovelace"


def test_an_empty_reason_is_refused_at_the_library(graph):
    project = load_project(graph)
    with pytest.raises(GroundingError, match="reason"):
        withdraw(project, ["REQ-0001"], by="Ada Lovelace", reason="   ")
    assert project.get("REQ-0001").attrs["ratified_by"] == "AAda Lovelace"


def test_a_withdrawer_is_required_when_nobody_can_be_asked(graph, capsys):
    assert _cli(["-C", graph, "withdraw", "REQ-0001", "--reason", "r"]) == 2
    assert "--by" in capsys.readouterr().err


def test_a_malformed_identifier_is_refused(graph, capsys):
    assert _cli(["-C", graph, "withdraw", "REQ-0001", "--reason", "r",
                 "--by", "Ada Lovelace", "--by-id", "ada"]) == 2
    assert "scheme" in capsys.readouterr().err
    assert _item(graph, "REQ-0001").attrs["ratified_by"] == "AAda Lovelace"


def test_a_uid_named_twice_is_refused(graph):
    assert _cli(["-C", graph, "withdraw", "REQ-0001", "REQ-0001",
                 "--reason", "r", "--by", "Ada Lovelace"]) == 2


# ------------------------------------------------- awaiting a human, and signable again

def test_the_item_is_reported_as_awaiting_ratification_again(graph, capsys):
    """Validation names the withdrawal as the cause rather than reading the item
    as one that dodged the gate."""
    assert _cli(["-C", graph, "withdraw", "REQ-0001", "--reason", "misspelled",
                 "--by", "Ada Lovelace"]) == 0
    capsys.readouterr()
    assert _cli(["-C", graph, "check", "--strict"]) != 0
    out = capsys.readouterr()
    text = out.out + out.err
    assert "REQ-0001" in text
    assert "withdrawn by Ada Lovelace" in text
    assert "awaiting human ratification again" in text


def test_the_item_can_be_ratified_again(graph, capsys):
    """Whoever now signs pays the cost, and that is the point: a signature
    reinstated without anyone reading the item would be blind re-ratification."""
    assert _cli(["-C", graph, "withdraw", "REQ-0001", "--reason", "misspelled",
                 "--by", "Ada Lovelace"]) == 0
    assert _cli(["-C", graph, "ratify", "REQ-0001", "--by", "Ada Lovelace"]) == 0
    item = _item(graph, "REQ-0001")
    assert item.status == "ratified"
    assert item.attrs["ratified_by"] == "Ada Lovelace"
    assert item.attrs["ratified_fingerprint"]
    # The withdrawal stays on record; a signature that follows one does not erase it.
    assert item.attrs[WITHDRAWN_RATIFIER_ATTR] == "AAda Lovelace"
    # And it is no longer reported as awaiting anyone (the fixture leaves other
    # items unratified and every item uncovered, so only this finding is asserted).
    capsys.readouterr()
    _cli(["-C", graph, "check", "--strict"])
    out = capsys.readouterr()
    assert not [line for line in (out.out + out.err).splitlines()
                if "REQ-0001" in line and "unratified" in line]


def test_a_withdrawal_cannot_ratify(graph):
    """The withdrawer is never credited with acceptance."""
    assert _cli(["-C", graph, "withdraw", "REQ-0001", "--reason", "misspelled",
                 "--by", "Bob Malory"]) == 0
    a = _item(graph, "REQ-0001").attrs
    assert "ratified_by" not in a
    assert a[WITHDRAWN_BY_ATTR] == "Bob Malory"


# ------------------------------------------------------------- the record is guarded

@pytest.mark.parametrize("name", WITHDRAWAL)
def test_the_withdrawal_record_is_owned_by_withdraw(graph, name, capsys):
    """A hand-written withdrawal would claim that somebody set a signature aside
    when nobody did (SR-0170 applied to SR-0197)."""
    assert RATIFICATION_ATTRS[name] == "withdraw"
    assert _cli(["-C", graph, "amend", "REQ-0001", "--attr", f"{name}=x"]) == 2
    assert "tl withdraw" in capsys.readouterr().err
    assert name not in _item(graph, "REQ-0001").attrs


def test_withdraw_cannot_write_the_signature(graph):
    """The verb clears the signature and never writes one."""
    assert _cli(["-C", graph, "withdraw", "REQ-0001", "--reason", "r",
                 "--by", "Ada Lovelace"]) == 0
    a = _item(graph, "REQ-0001").attrs
    assert "ratified_by" not in a and "ratified_fingerprint" not in a
