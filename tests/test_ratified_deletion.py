# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Deletion is a ratified act (SR-0267 to SR-0273, amending SR-0012).

`tl delete` used to tombstone any item, ratified ones included, the moment it ran
and unsigned: a ratified item could leave scope by a machine's act, and the only
downstream signal was the error on every dependent still linking to it. Now the
tombstone is proposed until a human ratifies the deletion (SR-0267); the signature
is taken over the deletion record, not the content (SR-0269); ratifying it marks
the dependents suspect by the walk `invalidate` makes (SR-0270); links into a
retired tombstone stay and are reported (SR-0271); an unsigned tombstone can be
restored (SR-0268); an item nobody signed, with no signed dependent, is retired
outright (SR-0272); and `tl migrate` signs existing tombstones from history
(SR-0273).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from throughline.cli import main as cli_main
from throughline.fingerprint import content_fingerprint, deletion_content, deletion_fingerprint, fingerprint
from throughline.grounding import Cascade, GroundingError, invalidate, ratify, withdraw
from throughline.items import delete_item, restore_item
from throughline.signatures import BACKFILLED, BY, CONTENT, REVISION, STAMP, relevant, retired, signatures
from throughline.storage import load_project, migrate_project, read_baseline, write_item
from throughline.validate import query_items, validate
from throughline.worklist import entry_for, worklist


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _rules(findings, uid: str) -> set[str]:
    """The rules reported against ``uid``, less the scaffold's own coverage rule
    (a requirement needs a verifying test), which every fixture item trips."""
    return {f.rule for f in findings if f.uid == uid and f.rule != "coverage"}


def _file(root: Path, uid: str) -> Path:
    return next(root.rglob(f"{uid}.yml"))


def _raw(root: Path, uid: str) -> dict:
    return yaml.safe_load(_file(root, uid).read_text(encoding="utf-8"))


@pytest.fixture
def graph(tmp_path) -> Path:
    """One intent; REQ-0001 ratified; REQ-0002 ratified and grounded on REQ-0001
    (derives_from is a withdrawing link type); REQ-0003 proposed, grounded on the
    intent, with a `relates` link to REQ-0001 (not a withdrawing type)."""
    root = tmp_path / "proj"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    assert _cli(["-C", root, "new", "INT", "--type", "intent", "--title", "Why",
                 "--no-interactive"]) == 0
    assert _cli(["-C", root, "new", "REQ", "--title", "Base", "--text", "Base.",
                 "--ground", "INT-0001", "--origin", "ai", "--no-interactive"]) == 0
    assert _cli(["-C", root, "new", "REQ", "--title", "Dependent", "--text", "Dep.",
                 "--ground", "REQ-0001", "--origin", "ai", "--no-interactive"]) == 0
    assert _cli(["-C", root, "new", "REQ", "--title", "Neighbour", "--text", "Nb.",
                 "--ground", "INT-0001", "--origin", "ai", "--no-interactive"]) == 0
    assert _cli(["-C", root, "link", "REQ-0003", "REQ-0001", "--type", "relates"]) == 0
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    assert _cli(["-C", root, "ratify", "REQ-0002", "--by", "Ada"]) == 0
    return root


# ------------------------------------------------------ SR-0267: proposed until ratified

def test_deleting_a_ratified_item_proposes_the_deletion(graph, capsys):
    """SR-0267: the tombstone status and record are written, the status the item
    held is kept, and the tombstone does not stand."""
    assert _cli(["-C", graph, "delete", "REQ-0001", "--reason", "superseded"]) == 0
    out = capsys.readouterr().out
    assert "proposed the deletion of REQ-0001 (was ratified)" in out
    assert "REQ-0002" in out                      # the dependent ratifying it marks
    project = load_project(graph)
    item = project.get("REQ-0001")
    assert item.is_deleted
    assert item.deleted["from"] == "ratified"
    assert item.deleted["reason"] == "superseded"
    assert "outright" not in item.deleted
    assert not retired(item, project.schema)


def test_the_gate_reports_a_proposed_deletion_unratified_and_nothing_else_moves(graph):
    """SR-0267: `unratified` on the tombstone, exactly as on a proposed item; the
    dependent keeps its grounding and is not told its target is deleted."""
    before = validate(load_project(graph))
    assert _cli(["-C", graph, "delete", "REQ-0001", "--reason", "superseded"]) == 0
    project = load_project(graph)
    findings = validate(project)
    assert _rules(findings, "REQ-0001") == {"unratified"}
    message = next(f.message for f in findings if f.uid == "REQ-0001")
    assert "deletion awaiting human ratification" in message
    assert "was 'ratified'" in message
    # The dependents read exactly as they did before the proposal.
    for uid in ("REQ-0002", "REQ-0003"):
        assert _rules(findings, uid) == _rules(before, uid)
    assert _rules(findings, "REQ-0002") == set()
    # Live scope: the summary and the query still count it.
    assert "REQ-0001" in [it.uid for it in query_items(project)]


def test_an_ungrounded_item_proposed_for_deletion_is_not_an_orphan(graph):
    """SR-0267: deleting an orphan is the remedy for it, and the remedy awaits a
    human, so the gate does not also report the orphan."""
    # Signed first, so that its deletion is a proposal rather than an outright
    # retirement (SR-0272); then its grounding is taken away.
    assert _cli(["-C", graph, "ratify", "REQ-0003", "--by", "Ada"]) == 0
    project = load_project(graph)
    item = project.get("REQ-0003")
    item.links = [l for l in item.links if l.target != "INT-0001"]
    write_item(item)
    assert "orphan" in _rules(validate(load_project(graph)), "REQ-0003")
    assert _cli(["-C", graph, "delete", "REQ-0003", "--reason", "orphan"]) == 0
    assert _rules(validate(load_project(graph)), "REQ-0003") == {"unratified"}


def test_a_proposed_deletion_cannot_be_amended_or_invalidated_or_deleted_again(graph, capsys):
    """SR-0267: the proposal is settled by ratify or restore, by nothing else."""
    assert _cli(["-C", graph, "delete", "REQ-0001", "--reason", "superseded"]) == 0
    before = _file(graph, "REQ-0001").read_bytes()
    assert _cli(["-C", graph, "amend", "REQ-0001", "--text", "Changed."]) != 0
    assert "proposed for deletion" in capsys.readouterr().err
    with pytest.raises(GroundingError, match="ratify the deletion"):
        invalidate(load_project(graph), "REQ-0001")
    capsys.readouterr()
    assert _cli(["-C", graph, "delete", "REQ-0001", "--reason", "again"]) == 0
    assert "already proposed for deletion" in capsys.readouterr().out
    assert _file(graph, "REQ-0001").read_bytes() == before


def test_the_worklist_offers_a_proposed_deletion_to_sign(graph):
    """SR-0267: a reviewer meets the proposed deletion as a proposal, ratifiable."""
    assert _cli(["-C", graph, "delete", "REQ-0001", "--reason", "superseded"]) == 0
    project = load_project(graph)
    entry = entry_for(project, project.get("REQ-0001"))
    assert entry.concern == "proposed" and entry.ratifiable and not entry.dead
    assert "REQ-0001" in [e.uid for e in worklist(project)]


# ------------------------------------------------------------------ SR-0268: restore

def test_restore_returns_an_unsigned_tombstone_to_the_status_it_held(graph, capsys):
    """SR-0268: the record goes, the status comes back, the signatures are kept."""
    assert _cli(["-C", graph, "delete", "REQ-0001", "--reason", "superseded"]) == 0
    assert _cli(["-C", graph, "restore", "REQ-0001"]) == 0
    assert "restored REQ-0001 to ratified" in capsys.readouterr().out
    project = load_project(graph)
    item = project.get("REQ-0001")
    assert item.status == "ratified" and item.deleted is None
    assert [e[BY] for e in signatures(item)] == ["Ada"]
    assert _rules(validate(project), "REQ-0001") == set()


def test_restore_is_refused_once_the_tombstone_stands(graph, capsys):
    """SR-0268, SR-0093: a ratified deletion is permanent until its signature is
    withdrawn; an outright retirement is permanent full stop."""
    assert _cli(["-C", graph, "delete", "REQ-0001", "--reason", "superseded"]) == 0
    assert _cli(["-C", graph, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    assert _cli(["-C", graph, "restore", "REQ-0001"]) != 0
    assert "permanent (SR-0093)" in capsys.readouterr().err
    assert _cli(["-C", graph, "delete", "REQ-0003", "--reason", "dropped"]) == 0
    assert _cli(["-C", graph, "restore", "REQ-0003"]) != 0
    assert "retired outright" in capsys.readouterr().err
    with pytest.raises(GroundingError, match="not deleted"):
        restore_item(load_project(graph), "REQ-0002")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, check=True).stdout


def _repo(root: Path, *, name: str = "Grace", email: str = "grace@example.com") -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", email)
    _git(root, "config", "user.name", name)
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "baseline")


def test_the_transition_gate_accepts_a_restore_of_a_committed_proposed_deletion(graph):
    """SR-0268: a tombstone unsigned at the baseline, live now, is not a lifecycle
    move the transitions table has to declare."""
    assert _cli(["-C", graph, "delete", "REQ-0001", "--reason", "superseded"]) == 0
    _repo(graph)
    assert _cli(["-C", graph, "restore", "REQ-0001"]) == 0
    project = load_project(graph)
    baseline = read_baseline(project)
    assert baseline.statuses["REQ-0001"] == "deleted"
    assert "REQ-0001" in baseline.restorable
    findings = validate(project, baseline=baseline.statuses, restorable=baseline.restorable)
    assert "bad-transition" not in _rules(findings, "REQ-0001")
    # The same move without the baseline's say-so is still refused.
    findings = validate(project, baseline=baseline.statuses)
    assert "bad-transition" in _rules(findings, "REQ-0001")


# ------------------------------------------------ SR-0269: the record is what is signed

def test_ratifying_a_tombstone_signs_the_deletion_record(graph, capsys):
    """SR-0269: the stamp is the record's digest, the content is the record, the
    content signatures stay as history, and the tombstone then stands."""
    assert _cli(["-C", graph, "delete", "REQ-0001", "--reason", "superseded"]) == 0
    assert _cli(["-C", graph, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    assert "REQ-0001 deletion ratified by Ada — retired" in capsys.readouterr().out
    project = load_project(graph)
    item = project.get("REQ-0001")
    assert item.status == "deleted"
    assert retired(item, project.schema)
    held = signatures(item)
    assert len(held) == 2, "the content signature is kept beside the deletion signature"
    content_sig, deletion_sig = held
    assert content_sig[STAMP] == fingerprint(item, project.schema)
    assert deletion_sig[STAMP] == deletion_fingerprint(item)
    assert deletion_sig[CONTENT] == deletion_content(item)
    assert deletion_sig[CONTENT]["deleted"]["from"] == "ratified"
    assert content_fingerprint("REQ-0001", deletion_sig[CONTENT]) == deletion_sig[STAMP]
    assert [e[STAMP] for e in relevant(item)] == [deletion_sig[STAMP]]
    assert _rules(validate(project), "REQ-0001") == set()


def test_a_deletion_signature_is_stale_when_the_record_moves(graph):
    """SR-0269: the record is what was signed; a rewritten record no longer
    reproduces the stamp and the tombstone no longer stands."""
    assert _cli(["-C", graph, "delete", "REQ-0001", "--reason", "superseded"]) == 0
    assert _cli(["-C", graph, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    path = _file(graph, "REQ-0001")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["deleted"]["reason"] = "a different reason"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    project = load_project(graph)
    assert not retired(project.get("REQ-0001"), project.schema)
    assert "ratified-stale" in _rules(validate(project), "REQ-0001")


def test_the_items_own_ratifier_has_not_already_accepted_its_deletion(graph):
    """SR-0269: Ada signed the content; that is not Ada's signature over the
    deletion, so ratify asks her again rather than refusing as already signed."""
    assert _cli(["-C", graph, "delete", "REQ-0001", "--reason", "superseded"]) == 0
    project = load_project(graph)
    item = ratify(project, "REQ-0001", by="Ada", cascade=Cascade())
    assert [e[BY] for e in relevant(item)] == ["Ada"]


def test_withdrawing_a_deletion_signature_leaves_the_tombstone_proposed(graph):
    """SR-0269: the signature goes, the status stays, the dependents the cascade
    marked are not touched, and the deletion awaits a human again."""
    assert _cli(["-C", graph, "delete", "REQ-0001", "--reason", "superseded"]) == 0
    assert _cli(["-C", graph, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    assert _cli(["-C", graph, "withdraw", "REQ-0001", "--reason", "wrong person",
                 "--by", "Bob"]) == 0
    project = load_project(graph)
    item = project.get("REQ-0001")
    assert item.status == "deleted" and not retired(item, project.schema)
    assert [e[BY] for e in signatures(item)] == ["Ada"]      # the content signature
    assert item.withdrawals[-1]["reason"] == "wrong person"
    assert _rules(validate(project), "REQ-0001") == {"unratified"}
    assert project.get("REQ-0002").status == "suspect"
    # And it can now be restored.
    assert _cli(["-C", graph, "restore", "REQ-0001"]) == 0
    assert load_project(graph).get("REQ-0001").status == "ratified"


def test_the_signer_is_shown_the_deletion_record(graph, monkeypatch, capsys):
    """SR-0269: where the Tool shows an item to its signer it shows the deletion
    record in place of the content."""
    from throughline.views import render_for_ratification

    assert _cli(["-C", graph, "delete", "REQ-0001", "--reason", "superseded"]) == 0
    project = load_project(graph)
    lines: list[str] = []
    render_for_ratification(project, project.get("REQ-0001"), emit=lines.append)
    text = "\n".join(lines)
    assert "DELETION of REQ-0001" in text
    assert "reason: superseded" in text
    assert "undoes the acceptance of: Ada" in text
    assert "REQ-0002" in text


# ------------------------------------------------------------ SR-0270: the cascade

def test_ratifying_a_tombstone_marks_dependents_suspect_along_withdrawing_links(graph, capsys):
    """SR-0270: REQ-0002 derives from REQ-0001 and is marked suspect with the reason
    recorded; REQ-0003 only relates to it and is untouched."""
    assert _cli(["-C", graph, "delete", "REQ-0001", "--reason", "superseded"]) == 0
    assert _cli(["-C", graph, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    out = capsys.readouterr().out
    assert "1 dependent(s) marked suspect" in out and "REQ-0002" in out
    project = load_project(graph)
    dep = project.get("REQ-0002")
    assert dep.status == "suspect"
    assert dep.attrs["suspect_reasons"] == ["upstream REQ-0001 deleted"]
    assert project.get("REQ-0003").status == "proposed"


def test_a_caller_that_cannot_write_the_cascade_is_refused_before_signing(graph):
    """SR-0270: a front end built before this rule fails loudly rather than sign a
    deletion whose consequences it never writes."""
    assert _cli(["-C", graph, "delete", "REQ-0001", "--reason", "superseded"]) == 0
    project = load_project(graph)
    with pytest.raises(GroundingError, match="pass `cascade=`"):
        ratify(project, "REQ-0001", by="Ada")
    assert not relevant(project.get("REQ-0001"))
    # With no live dependent there is nothing to write, and no cascade is needed.
    assert _cli(["-C", graph, "ratify", "REQ-0003", "--by", "Ada"]) == 0
    assert _cli(["-C", graph, "delete", "REQ-0003", "--reason", "dropped"]) == 0
    project = load_project(graph)
    item = ratify(project, "REQ-0003", by="Ada")
    assert retired(item, project.schema)


def test_a_refused_move_is_recorded_as_invalidate_records_it(graph, capsys):
    """SR-0270, SR-0173: a dependent whose lifecycle will not reach suspect is
    reported and the run does not exit clean."""
    assert _cli(["-C", graph, "schema", "transition", "deny", "ratified", "suspect",
                 "--because", "the fixture needs a route that is not there"]) == 0
    assert _cli(["-C", graph, "delete", "REQ-0001", "--reason", "superseded"]) == 0
    capsys.readouterr()
    rc = _cli(["-C", graph, "ratify", "REQ-0001", "--by", "Ada"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "could not be marked suspect" in captured.err
    assert "REQ-0002: ratified -> suspect" in captured.err
    project = load_project(graph)
    assert retired(project.get("REQ-0001"), project.schema)
    assert project.get("REQ-0002").status == "ratified"


# --------------------------------------------------- SR-0271: the links stay, reported

def test_links_into_a_retired_tombstone_stay_and_are_reported_on_the_dependent(graph):
    """SR-0271: nothing is unlinked; `deleted-link-target` only once it stands."""
    assert _cli(["-C", graph, "delete", "REQ-0001", "--reason", "superseded"]) == 0
    project = load_project(graph)
    assert "deleted-link-target" not in _rules(validate(project), "REQ-0002")
    assert "deleted-link-target" not in _rules(validate(project), "REQ-0003")
    assert _cli(["-C", graph, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    project = load_project(graph)
    findings = validate(project)
    assert "deleted-link-target" in _rules(findings, "REQ-0002")
    assert "deleted-link-target" in _rules(findings, "REQ-0003")
    assert [l.target for l in project.get("REQ-0002").links] == ["REQ-0001"]
    assert "REQ-0001" in [l.target for l in project.get("REQ-0003").links]
    assert _rules(findings, "REQ-0001") == set()          # the retired item is held to nothing


# ---------------------------------------------------------- SR-0272: retired outright

def test_a_never_ratified_item_with_no_signed_dependent_is_retired_outright(graph, capsys):
    """SR-0272: REQ-0003 is proposed and unsigned, and nothing signed depends on it
    (its link to REQ-0001 is its own): the tombstone stands at once."""
    assert _cli(["-C", graph, "delete", "REQ-0003", "--reason", "dropped"]) == 0
    assert "retired outright" in capsys.readouterr().out
    project = load_project(graph)
    item = project.get("REQ-0003")
    assert item.deleted["outright"] is True and item.deleted["from"] == "proposed"
    assert retired(item, project.schema) and not signatures(item)
    assert _rules(validate(project), "REQ-0003") == set()
    assert "REQ-0003" not in [it.uid for it in query_items(project)]
    with pytest.raises(GroundingError, match="retired outright"):
        ratify(project, "REQ-0003", by="Ada")


def test_a_signed_dependent_keeps_an_unsigned_item_from_outright_retirement(tmp_path):
    """SR-0272: REQ-0001 is unsigned, but REQ-0002, which derives from it, is
    signed — retiring REQ-0001 would take signed ground away, so it is proposed."""
    root = tmp_path / "proj"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    assert _cli(["-C", root, "new", "INT", "--type", "intent", "--title", "Why",
                 "--no-interactive"]) == 0
    assert _cli(["-C", root, "new", "REQ", "--title", "Base", "--text", "Base.",
                 "--ground", "INT-0001", "--no-interactive"]) == 0
    assert _cli(["-C", root, "new", "REQ", "--title", "Dep", "--text", "Dep.",
                 "--ground", "REQ-0001", "--origin", "ai", "--no-interactive"]) == 0
    assert _cli(["-C", root, "ratify", "REQ-0002", "--by", "Ada"]) == 0
    project = load_project(root)
    done = delete_item(project, "REQ-0001", reason="dropped")
    assert not done.outright and done.dependents == ["REQ-0002"]
    assert "outright" not in project.get("REQ-0001").deleted


# --------------------------------------------------- SR-0273: ratified by history

def test_migrate_signs_an_existing_tombstone_from_the_commit_that_wrote_it(graph, capsys):
    """SR-0273: the author and date of the deletion commit, over the record as it
    stands, marked backfilled; idempotent."""
    assert _cli(["-C", graph, "delete", "REQ-0001", "--reason", "superseded"]) == 0
    _repo(graph)
    sha = _git(graph, "rev-parse", "HEAD").strip()
    result = migrate_project(graph)
    assert result.tombstones == {"REQ-0001": "Grace"}
    assert result.unattributed == {}
    project = load_project(graph)
    item = project.get("REQ-0001")
    assert retired(item, project.schema)
    entry = relevant(item)[-1]
    assert entry[BY] == "Grace" and entry["id"] == "email:grace@example.com"
    assert entry[BACKFILLED] is True and entry[REVISION] == sha
    assert entry[STAMP] == deletion_fingerprint(item)
    assert entry["at"].endswith("Z")
    assert _rules(validate(project), "REQ-0001") == set()
    before = _file(graph, "REQ-0001").read_bytes()
    assert migrate_project(graph).tombstones == {}
    assert _file(graph, "REQ-0001").read_bytes() == before
    assert _cli(["-C", graph, "migrate"]) == 0
    assert "already at format version" in capsys.readouterr().out


def test_migrate_leaves_a_tombstone_no_commit_holds_proposed_and_says_so(graph, capsys):
    """SR-0273: an uncommitted tombstone names nobody; no identity is invented."""
    _repo(graph)
    assert _cli(["-C", graph, "delete", "REQ-0001", "--reason", "superseded"]) == 0
    result = migrate_project(graph)
    assert result.tombstones == {}
    assert "REQ-0001" in result.unattributed
    assert not signatures(load_project(graph).get("REQ-0001"))[1:]
    capsys.readouterr()
    assert _cli(["-C", graph, "migrate"]) == 0
    assert "left 1 tombstone(s) proposed" in capsys.readouterr().out
