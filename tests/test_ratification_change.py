# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""What changed since a ratification (SR-0165, SR-0166, SR-0167).

These need real git history: the stamped content is recovered by finding the
revision that reproduces the stamp, so a fake history would test nothing.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from throughline.cli import main as cli_main
from throughline.ratification import (
    CHANGED,
    REVISION_ATTR,
    UNCHANGED,
    UNRATIFIED,
    UNRESOLVABLE,
    change_since_ratification,
    render_change,
    resolve_revision,
)
from throughline.storage import load_project


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True).stdout


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-m", message)
    return _git(root, "rev-parse", "HEAD").strip()


CONFIG_EXTRA = '''
[types.intent]
attrs.origin = { type = "enum", values = ["human", "ai", "hybrid"] }
'''


@pytest.fixture
def graph(tmp_path) -> Path:
    """A git-backed project holding one ratified, committed requirement whose
    normative content is known."""
    root = tmp_path / "proj"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    cfg = root / "throughline.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + CONFIG_EXTRA, encoding="utf-8")

    _git(root, "init")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")

    assert _cli(["-C", root, "new", "INT", "--type", "intent", "--title", "Why",
                 "--text", "The vision.", "--origin", "human",
                 "--no-interactive"]) == 0
    assert _cli(["-C", root, "new", "REQ", "--title", "Cache expires hourly",
                 "--text", "The Tool shall evict a cached widget after 3600 seconds.",
                 "--ground", "INT-0001", "--ground-type", "implements",
                 "--origin", "ai", "--attr", "priority=should",
                 "--no-interactive"]) == 0
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada Lovelace"]) == 0
    _commit(root, "ratified REQ-0001")
    return root


def _drift(root: Path, *, commit: str | None = "drift") -> None:
    """Move REQ-0001's normative content after its ratification."""
    assert _cli(["-C", root, "amend", "REQ-0001", "--text",
                 "The Tool shall evict a cached widget after 900 seconds.",
                 "--attr", "priority=must"]) == 0
    if commit:
        _commit(root, commit)


# ------------------------------------------------ resolving the change (SR-0165)

def test_reports_the_change_field_by_field_against_the_stamp(graph):
    """The difference is resolved against the content the stamp was taken over,
    and reported per field (SR-0165)."""
    _drift(graph)
    project = load_project(graph)
    change = change_since_ratification(project, project.get("REQ-0001"))

    assert change.outcome == CHANGED
    assert change.stale
    assert change.revision and len(change.revision) == 40
    fields = {c.field: (c.was, c.now) for c in change.changes}
    assert fields["text"] == (
        "The Tool shall evict a cached widget after 3600 seconds.",
        "The Tool shall evict a cached widget after 900 seconds.")
    assert fields["attrs.priority"] == ("should", "must")


def test_unchanged_is_not_the_same_answer_as_cannot_show(graph):
    """An empty difference claims the content still stands as ratified, so it must
    never stand in for being unable to say (SR-0165)."""
    project = load_project(graph)
    intact = change_since_ratification(project, project.get("REQ-0001"))
    assert intact.outcome == UNCHANGED
    assert not intact.stale
    assert intact.changes == ()

    # A stamp no revision reproduces: the ratified text never reached a commit.
    assert _cli(["-C", graph, "new", "REQ", "--title", "Uncommitted",
                 "--text", "Ratified but never committed.",
                 "--ground", "INT-0001", "--ground-type", "implements",
                 "--origin", "ai", "--no-interactive"]) == 0
    assert _cli(["-C", graph, "ratify", "REQ-0002", "--by", "Ada Lovelace"]) == 0
    assert _cli(["-C", graph, "amend", "REQ-0002", "--text", "Reworded."]) == 0
    project = load_project(graph)
    lost = change_since_ratification(project, project.get("REQ-0002"))
    assert lost.outcome == UNRESOLVABLE
    assert lost.stale                       # undescribable is not absent
    assert lost.changes == ()
    assert "never been committed" in lost.reason


def test_refuses_rather_than_taking_the_nearest_revision(graph):
    """Matched by content, never by time (SR-0165). Here history exists and one
    revision sits right beside the ratification, but none reproduces the stamp —
    so the answer is that it cannot be shown, not the neighbour."""
    _drift(graph, commit="drift")
    # Rewrite the stamp to something no revision can reproduce, leaving a full
    # history in place. Taking the nearest revision would answer confidently.
    path = graph / "requirements" / "REQ-0001.yml"
    path.write_text(path.read_text(encoding="utf-8").replace(
        load_project(graph).get("REQ-0001").attrs["ratified_fingerprint"],
        "sha256:" + "f" * 64), encoding="utf-8")
    project = load_project(graph)
    change = change_since_ratification(project, project.get("REQ-0001"))
    assert change.outcome == UNRESOLVABLE
    assert change.revision is None
    assert "no revision of this item reproduces the stamp" in change.reason


def test_reports_only_the_fields_the_fingerprint_covers(graph):
    """A moved title or status did not cause the staleness, so naming it here
    would report something the reader is not being asked to accept (SR-0165)."""
    assert _cli(["-C", graph, "amend", "REQ-0001", "--title", "A better name"]) == 0
    assert _cli(["-C", graph, "status", "REQ-0001", "implemented"]) == 0
    _drift(graph)
    project = load_project(graph)
    change = change_since_ratification(project, project.get("REQ-0001"))
    assert change.outcome == CHANGED
    reported = {c.field for c in change.changes}
    assert "title" not in reported
    assert "status" not in reported
    assert reported == {"text", "attrs.priority"}


def test_an_unratified_item_has_no_change_to_report(graph):
    """No stamp is its own outcome, distinct from both answers about one."""
    assert _cli(["-C", graph, "new", "REQ", "--title", "Fresh", "--text", "New.",
                 "--ground", "INT-0001", "--ground-type", "implements",
                 "--origin", "ai", "--no-interactive"]) == 0
    project = load_project(graph)
    change = change_since_ratification(project, project.get("REQ-0002"))
    assert change.outcome == UNRATIFIED
    assert not change.stale
    assert render_change(change) == []


def test_offers_the_result_as_structured_data(graph):
    """Structured as well as rendered (SR-0165): a surface that lays the fields out
    must never have to take formatted text back apart."""
    _drift(graph)
    project = load_project(graph)
    data = change_since_ratification(project, project.get("REQ-0001")).as_dict()
    assert data["outcome"] == CHANGED
    assert data["uid"] == "REQ-0001"
    assert data["stamp"].startswith("sha256:")
    assert {c["field"] for c in data["changes"]} == {"text", "attrs.priority"}
    assert all({"field", "was", "now"} == set(c) for c in data["changes"])


def test_rendered_text_names_the_stamp_and_never_a_date(graph):
    """The earlier content is identified by reproducing the stamp (SR-0165)."""
    _drift(graph)
    project = load_project(graph)
    change = change_since_ratification(project, project.get("REQ-0001"))
    rendered = "\n".join(render_change(change, ratifier="Ada Lovelace"))
    assert change.stamp in rendered
    assert change.revision[:9] in rendered
    assert "Ada Lovelace" in rendered
    assert "3600" in rendered and "900" in rendered


def test_unresolvable_rendering_says_nobody_can_state_what_is_accepted(graph):
    """The ratifier is owed the fact that nobody can say what they are accepting
    (SR-0165, SR-0167)."""
    assert _cli(["-C", graph, "new", "REQ", "--title", "Lost", "--text", "Gone.",
                 "--ground", "INT-0001", "--ground-type", "implements",
                 "--origin", "ai", "--no-interactive"]) == 0
    assert _cli(["-C", graph, "ratify", "REQ-0002", "--by", "Ada"]) == 0
    assert _cli(["-C", graph, "amend", "REQ-0002", "--text", "Changed."]) == 0
    project = load_project(graph)
    rendered = "\n".join(render_change(
        change_since_ratification(project, project.get("REQ-0002")), ratifier="Ada"))
    assert "CANNOT BE SHOWN" in rendered
    assert "nobody can state what you would be accepting" in rendered


# ------------------------------------------------------ the revision cache (SR-0166)

def test_migrate_caches_the_resolved_revision(graph):
    """`tl migrate` populates the cache for records that predate it (SR-0166)."""
    assert REVISION_ATTR not in load_project(graph).get("REQ-0001").attrs
    assert _cli(["-C", graph, "migrate"]) == 0
    sha = load_project(graph).get("REQ-0001").attrs[REVISION_ATTR]
    assert sha == _git(graph, "rev-parse", "HEAD").strip()


def test_the_revision_cache_is_idempotent(graph):
    """It runs on every migrate, so it must settle (SR-0166)."""
    assert _cli(["-C", graph, "migrate"]) == 0
    first = load_project(graph).get("REQ-0001").attrs[REVISION_ATTR]
    assert _cli(["-C", graph, "migrate"]) == 0
    assert load_project(graph).get("REQ-0001").attrs[REVISION_ATTR] == first


def test_a_cached_revision_is_reused_without_a_walk(graph):
    """Held on the record, resolution is a single read (SR-0166)."""
    assert _cli(["-C", graph, "migrate"]) == 0
    _drift(graph)
    project = load_project(graph)
    sha, reason, cached = resolve_revision(project, project.get("REQ-0001"))
    assert cached is True
    assert reason == ""
    assert sha == project.get("REQ-0001").attrs[REVISION_ATTR]


def test_a_cached_revision_the_stamp_disagrees_with_is_discarded(graph):
    """Verified rather than trusted: a hint left by rewritten history, a restored
    backup or a hand edit must not produce a confident difference against the
    wrong content (SR-0166)."""
    assert _cli(["-C", graph, "migrate"]) == 0
    correct = load_project(graph).get("REQ-0001").attrs[REVISION_ATTR]
    _drift(graph)                                   # a later commit exists now
    wrong = _git(graph, "rev-parse", "HEAD").strip()
    assert wrong != correct

    path = graph / "requirements" / "REQ-0001.yml"
    path.write_text(path.read_text(encoding="utf-8").replace(
        f"{REVISION_ATTR}: {correct}", f"{REVISION_ATTR}: {wrong}"),
        encoding="utf-8")

    project = load_project(graph)
    item = project.get("REQ-0001")
    assert item.attrs[REVISION_ATTR] == wrong       # the bad hint is on the record
    sha, _reason, cached = resolve_revision(project, item)
    assert cached is False                          # not taken on trust
    assert sha == correct                           # resolved again, correctly
    change = change_since_ratification(project, item)
    assert change.outcome == CHANGED
    assert {c.field for c in change.changes} == {"text", "attrs.priority"}


def test_migrate_leaves_a_drifted_record_uncached(graph):
    """Only a record whose stamp still matches its content is cached — for a
    drifted one the revision is resolved on demand (SR-0166)."""
    _drift(graph)
    assert _cli(["-C", graph, "migrate"]) == 0
    assert REVISION_ATTR not in load_project(graph).get("REQ-0001").attrs


# ------------------------------------- the change gates a re-ratification (SR-0167)

def _tty(monkeypatch, answers):
    import throughline.cli as climod
    monkeypatch.setattr(climod, "_interactive", lambda: True)
    supply = iter(answers)

    def _input(*_a, **_k):
        print("<<asked>>", file=sys.stderr)
        return next(supply)

    monkeypatch.setattr("builtins.input", _input)


def test_re_ratifying_a_stale_item_shows_the_change_before_the_stop(
        graph, monkeypatch, capsys):
    """The change goes in the path of the acceptance, because someone who has to
    know to go looking is the person who signs without looking (SR-0167)."""
    _drift(graph)
    _tty(monkeypatch, ["y"])
    assert _cli(["-C", graph, "ratify", "REQ-0001", "--by", "Ada Lovelace"]) == 0
    err = capsys.readouterr().err
    assert "changed since it was ratified by Ada Lovelace" in err
    assert "3600" in err and "900" in err
    assert "attrs.priority" in err
    assert err.index("changed since it was ratified") < err.index("<<asked>>")


def test_declining_after_seeing_the_change_records_nothing(graph, monkeypatch):
    """A refusal rather than a warning: no record is written at all (SR-0167)."""
    _drift(graph)
    before = load_project(graph).get("REQ-0001").attrs["ratified_fingerprint"]
    _tty(monkeypatch, ["n"])
    assert _cli(["-C", graph, "ratify", "REQ-0001", "--by", "Ada Lovelace"]) == 0
    assert load_project(graph).get("REQ-0001").attrs["ratified_fingerprint"] == before


def test_non_interactive_refuses_a_stale_item_without_acknowledgement(graph):
    """Where the change cannot be shown to anyone, the signature is refused rather
    than written with a doubt noted beside it (SR-0167)."""
    _drift(graph)
    before = load_project(graph).get("REQ-0001").attrs["ratified_fingerprint"]
    assert _cli(["-C", graph, "ratify", "REQ-0001", "--by", "Ada"]) == 2
    assert load_project(graph).get("REQ-0001").attrs["ratified_fingerprint"] == before


def test_accept_change_is_its_own_flag_and_lets_it_through(graph):
    """Acknowledged explicitly, so an automated re-ratification says a change was
    accepted unseen rather than inheriting permission granted for something else
    (SR-0167)."""
    _drift(graph)
    before = load_project(graph).get("REQ-0001").attrs["ratified_fingerprint"]
    assert _cli(["-C", graph, "ratify", "REQ-0001", "--by", "Ada",
                 "--accept-change"]) == 0
    after = load_project(graph).get("REQ-0001")
    assert after.attrs["ratified_fingerprint"] != before
    assert after.attrs["ratified_by"] == "Ada"


def test_an_unshowable_change_stops_at_least_as_hard(graph, monkeypatch, capsys):
    """Passing the least knowable case through quietly would make it the easiest
    one to sign — the failure this guards, by the back door (SR-0167)."""
    assert _cli(["-C", graph, "new", "REQ", "--title", "Lost", "--text", "Gone.",
                 "--ground", "INT-0001", "--ground-type", "implements",
                 "--origin", "ai", "--no-interactive"]) == 0
    assert _cli(["-C", graph, "ratify", "REQ-0002", "--by", "Ada"]) == 0
    assert _cli(["-C", graph, "amend", "REQ-0002", "--text", "Changed."]) == 0

    # non-interactive: refused, exactly as a showable change is
    assert _cli(["-C", graph, "ratify", "REQ-0002", "--by", "Ada"]) == 2

    # interactive: said plainly, and still confirmed
    _tty(monkeypatch, ["n"])
    assert _cli(["-C", graph, "ratify", "REQ-0002", "--by", "Ada"]) == 0
    err = capsys.readouterr().err
    assert "CANNOT BE SHOWN" in err
    assert "<<asked>>" in err


def test_a_fresh_ratification_is_not_gated(graph):
    """The gate is for a signature being taken again over content that moved; a
    first ratification has nothing to show and must not be refused (SR-0167)."""
    assert _cli(["-C", graph, "new", "REQ", "--title", "First", "--text", "New.",
                 "--ground", "INT-0001", "--ground-type", "implements",
                 "--origin", "ai", "--no-interactive"]) == 0
    assert _cli(["-C", graph, "ratify", "REQ-0002", "--by", "Ada"]) == 0
    assert load_project(graph).get("REQ-0002").attrs["ratified_by"] == "Ada"


def test_the_cache_attribute_is_part_of_the_guarded_record(graph):
    """No verb but the one that owns it may write the ratification record
    (SR-0170). Verification would catch a planted value, but a hand-written one is
    still a claim about an accountability record made by the wrong verb."""
    from throughline.identity import RATIFICATION_ATTRS
    assert RATIFICATION_ATTRS[REVISION_ATTR] == "migrate"
    assert _cli(["-C", graph, "amend", "REQ-0001",
                 "--attr", f"{REVISION_ATTR}=deadbeef"]) == 2
    assert REVISION_ATTR not in load_project(graph).get("REQ-0001").attrs


def test_outside_a_git_work_tree_the_change_cannot_be_shown(tmp_path):
    """No history means no way to recover the stamped content, which is named
    rather than smoothed over (SR-0165)."""
    root = tmp_path / "nogit"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    cfg = root / "throughline.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + CONFIG_EXTRA, encoding="utf-8")
    assert _cli(["-C", root, "new", "INT", "--type", "intent", "--title", "Why",
                 "--text", "V.", "--origin", "human", "--no-interactive"]) == 0
    assert _cli(["-C", root, "new", "REQ", "--title", "R", "--text", "Original.",
                 "--ground", "INT-0001", "--ground-type", "implements",
                 "--origin", "ai", "--no-interactive"]) == 0
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    assert _cli(["-C", root, "amend", "REQ-0001", "--text", "Moved."]) == 0

    project = load_project(root)
    change = change_since_ratification(project, project.get("REQ-0001"))
    assert change.outcome == UNRESOLVABLE
    assert change.reason
