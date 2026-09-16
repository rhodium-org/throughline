# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""What changed since a ratification (SR-0165, SR-0166, SR-0167).

These need real git history: the stamped content is recovered by finding the
revision that reproduces the stamp, so a fake history would test nothing.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from throughline.cli import main as cli_main
from throughline.ratification import (
    ADDED,
    CHANGED,
    KEPT,
    REMOVED,
    REVISION_ATTR,
    UNCHANGED,
    UNRATIFIED,
    UNRESOLVABLE,
    change_since_ratification,
    render_change,
    resolve_revision,
    diff_prose,
    is_prose,
    wrap_words,
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



def _declare_intent_origin(root: Path) -> None:
    """The scaffold declares `[types.intent]` (SR-0202), so the attribute these
    fixtures need is added through the tool rather than by appending a second
    table the config already holds."""
    assert _cli(["-C", root, "schema", "attr", "add", "intent", "origin",
                 "--kind", "enum", "--values", "human,ai,hybrid",
                 "--because", "the fixture records who authored each intent"]) == 0


@pytest.fixture
def graph(tmp_path) -> Path:
    """A git-backed project holding one ratified, committed requirement whose
    normative content is known."""
    root = tmp_path / "proj"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    _declare_intent_origin(root)

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


# ------------------------------------------- showing a changed paragraph (SR-0198)

PARAGRAPH = ("The Tool shall evict a cached widget after 3600 seconds. "
             "It shall record the eviction in the audit log with the widget's key. "
             "A widget evicted twice in one minute is a fault.")
REWORDED = PARAGRAPH.replace("after 3600 seconds", "when it is an hour old")


def _ratified_paragraph(graph) -> None:
    assert _cli(["-C", graph, "new", "REQ", "--title", "Eviction",
                 "--text", PARAGRAPH, "--ground", "INT-0001",
                 "--ground-type", "implements", "--origin", "ai",
                 "--no-interactive"]) == 0
    assert _cli(["-C", graph, "ratify", "REQ-0002", "--by", "Ada Lovelace"]) == 0
    _commit(graph, "ratified REQ-0002")
    assert _cli(["-C", graph, "amend", "REQ-0002", "--text", REWORDED]) == 0
    _commit(graph, "reworded REQ-0002")


def test_a_changed_paragraph_is_shown_sentence_by_sentence(graph):
    """The removed sentence sits above its replacement inside the paragraph, and
    the sentences that stayed are shown once, unmarked (SR-0198)."""
    _ratified_paragraph(graph)
    project = load_project(graph)
    change = change_since_ratification(project, project.get("REQ-0002"))
    lines = render_change(change, ratifier="Ada Lovelace", columns=72)
    rendered = "\n".join(lines)

    assert "was '" not in rendered and "now '" not in rendered
    marks = [ln.lstrip()[0] for ln in lines[2:] if ln.strip()]
    assert marks.count("-") == 1 and marks.count("+") == 1
    minus = next(ln for ln in lines if ln.lstrip().startswith("- "))
    plus = next(ln for ln in lines if ln.lstrip().startswith("+ "))
    assert "3600 seconds" in minus
    assert "an hour old" in plus
    assert lines.index(minus) < lines.index(plus)
    assert rendered.count("audit log") == 1            # a kept sentence, once
    assert rendered.count("twice in one minute") == 1


def test_the_paragraph_is_wrapped_to_the_terminal(graph):
    """No line runs past the width the caller gave, and a wrapped sentence keeps
    its mark on the first line only (SR-0198)."""
    _ratified_paragraph(graph)
    project = load_project(graph)
    change = change_since_ratification(project, project.get("REQ-0002"))
    narrow = render_change(change, columns=48)[2:]
    wide = render_change(change, columns=72)[2:]
    assert all(len(ln) <= 48 for ln in narrow), narrow
    assert all(len(ln) <= 72 for ln in wide), wide
    assert len(narrow) > len(wide) > 4          # four units, some wrapped
    marked = [ln for ln in narrow if ln.lstrip().startswith(("- ", "+ "))]
    assert len(marked) == 2                      # a wrapped unit is marked once


def test_a_short_value_still_reads_as_was_and_now(graph):
    """Only prose is diffed; a token like a priority stays on one line (SR-0198)."""
    _drift(graph)
    project = load_project(graph)
    rendered = "\n".join(render_change(
        change_since_ratification(project, project.get("REQ-0001"))))
    assert "attrs.priority: was 'should', now 'must'" in rendered
    assert "- The Tool shall evict a cached widget after 3600 seconds." in rendered
    assert "+ The Tool shall evict a cached widget after 900 seconds." in rendered


def _marked(line: str) -> list[str]:
    """The runs of a painted line shown in reverse video: the words that moved."""
    return re.findall("\033\\[7m(.*?)\033\\[27m", line)


def test_the_words_that_moved_are_marked_when_painted(graph):
    """On a terminal the removed sentence is red, its replacement green, and the
    words that differ between them stand out in reverse video; the words the two
    sentences share are painted with the line, not marked (SR-0198)."""
    _ratified_paragraph(graph)
    project = load_project(graph)
    change = change_since_ratification(project, project.get("REQ-0002"))
    lines = render_change(change, columns=200, colour=True)
    minus = next(ln for ln in lines if "\033[31m" in ln)
    plus = next(ln for ln in lines if "\033[32m" in ln)
    assert minus.startswith("\033[31m") and minus.endswith("\033[0m")
    assert plus.startswith("\033[32m") and plus.endswith("\033[0m")
    assert _marked(minus) == ["after 3600 seconds."]   # one run, spaces included
    assert _marked(plus) == ["when it is an hour old."]
    kept = [ln for ln in lines if "audit log" in ln]
    assert kept and "\033[" not in kept[0]      # a kept sentence is not painted


def test_no_escape_codes_unless_colour_is_asked_for(graph):
    """A pipe, a log, or a reader who set NO_COLOR sees the marks alone (SR-0198)."""
    _ratified_paragraph(graph)
    project = load_project(graph)
    change = change_since_ratification(project, project.get("REQ-0002"))
    assert not any("\033[" in ln for ln in render_change(change))


def test_a_sentence_replaced_by_two_pairs_with_the_one_it_became(graph):
    """Within a hunk each removed sentence is compared with the added one it most
    resembles, so a sentence split in two still shows which words it lost; the
    sentence that is wholly new carries no marks, and neither does a sentence
    rewritten beyond recognition (SR-0198)."""
    _ratified_paragraph(graph)
    split = REWORDED.replace(
        "It shall record the eviction in the audit log with the widget's key.",
        "It shall record the eviction in the audit log. The record shall name "
        "the widget's key.")
    assert _cli(["-C", graph, "amend", "REQ-0002", "--text", split]) == 0
    project = load_project(graph)
    change = change_since_ratification(project, project.get("REQ-0002"))
    lines = render_change(change, columns=200, colour=True)
    assert any("hour" in run for ln in lines for run in _marked(ln))  # the 1:1 pair
    minus_audit = next(ln for ln in lines if "\033[31m" in ln and "audit" in ln)
    assert _marked(minus_audit) == ["log with the widget's key."]   # the lost words
    plus_audit = next(ln for ln in lines if "\033[32m" in ln and "audit" in ln)
    assert _marked(plus_audit) == ["log."]                  # only the full stop
    wholly_new = next(ln for ln in lines if "The record shall" in ln)
    assert wholly_new.startswith("\033[32m") and "\033[7m" not in wholly_new


# --------------------------------------------- the diff offered as data (SR-0200)

def test_the_diff_is_offered_as_units_with_the_moved_words_marked():
    """A consumer with a screen of its own reads the kept, removed and added units
    in order, each word carrying whether it moved, and derives nothing (SR-0200)."""
    units = diff_prose(PARAGRAPH, REWORDED)
    assert [u.mark for u in units] == [REMOVED, ADDED, KEPT, KEPT]
    removed, added, kept, _last = units
    assert removed.text == "The Tool shall evict a cached widget after 3600 seconds."
    assert added.text == "The Tool shall evict a cached widget when it is an hour old."
    assert [w for w, moved in removed.words if moved] == ["after", "3600", "seconds."]
    assert [w for w, moved in added.words if moved] == ["when", "it", "is", "an", "hour", "old."]
    assert kept.text.startswith("It shall record") and not kept.marked


def test_the_terminal_rendering_is_built_from_the_same_data(graph):
    """What tl ratify paints and what the data says are one account: every unit
    the rendering shows is a unit the data names, in the same order with the same
    words, and the words it highlights are the words the data marks (SR-0200)."""
    _ratified_paragraph(graph)
    project = load_project(graph)
    change = change_since_ratification(project, project.get("REQ-0002"))
    (field,) = change.changes
    units = diff_prose(field.was, field.now)

    plain = render_change(change, columns=400)[2:]           # one line per unit
    assert [ln.strip()[0] if ln.strip()[0] in "-+" else " " for ln in plain] \
        == [u.mark for u in units]
    assert [ln.strip().lstrip("-+ ") for ln in plain] == [u.text for u in units]

    painted = render_change(change, columns=400, colour=True)[2:]
    for line, unit in zip(painted, units):
        marked_words = " ".join(w for w, moved in unit.words if moved)
        assert _marked(line) == ([marked_words] if marked_words else [])


def test_only_prose_is_diffed():
    """A token — a priority, a flag — is before and after on one line, so the
    consumer asks the same question the renderer does before drawing a diff."""
    assert not is_prose("should", "must")
    assert not is_prose(None, "must")
    assert is_prose("The Tool shall.", "The Tool must.")
    assert is_prose("", "Two words.")


def test_a_unit_wraps_without_losing_a_word_or_its_mark():
    """The wrap a consumer draws with keeps every word once, keeps each word's
    mark, and never runs past the width once the prefixes are counted (SR-0200)."""
    (removed, *_rest) = diff_prose(PARAGRAPH, REWORDED)
    lines = wrap_words(removed.words, first="    - ", rest="      ", width=30)
    assert len(lines) > 1
    assert [w for ln in lines for w in ln] == list(removed.words)
    for n, ln in enumerate(lines):
        prefix = "    - " if n == 0 else "      "
        assert len(prefix) + len(" ".join(w for w, _m in ln)) <= 30


# ------------------------------------------------------ the revision cache (SR-0166)

def test_migrate_caches_the_resolved_revision(graph):
    """`tl migrate` populates the cache for records that predate it (SR-0166)."""
    assert REVISION_ATTR not in load_project(graph).get("REQ-0001").attrs
    assert _cli(["-C", graph, "migrate"]) == 0
    sha = load_project(graph).get("REQ-0001").attrs[REVISION_ATTR]
    assert sha == _git(graph, "rev-parse", "HEAD").strip()


def test_migrate_reports_that_it_wrote_to_those_records(graph, capsys):
    """Migrate names every other thing it writes, so a silent write to an
    accountability record would be the one change an operator cannot audit
    afterwards. A count suffices here (SR-0166)."""
    assert _cli(["-C", graph, "migrate"]) == 0
    out = capsys.readouterr().out
    assert "cached the ratified revision for 1 record(s)" in out
    assert "nothing to migrate" not in out       # it did write something


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
    _declare_intent_origin(root)
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
