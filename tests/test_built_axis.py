# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Agreed and built as independent, dated axes (UR-0049 to UR-0052, SR-0377 to
SR-0384)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from throughline import built
from throughline.cli import main as _cli
from throughline.fingerprint import fingerprint
from throughline.schema import Schema, SchemaError
from throughline.storage import init_project, load_project
from throughline.validate import validate


def _git(root: Path, *args: str, date: str | None = None) -> str:
    env = {"GIT_AUTHOR_NAME": "Ada", "GIT_AUTHOR_EMAIL": "ada@example.com",
           "GIT_COMMITTER_NAME": "Ada", "GIT_COMMITTER_EMAIL": "ada@example.com",
           "PATH": __import__("os").environ["PATH"], "HOME": str(root)}
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    out = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                         text=True, env=env, check=True)
    return out.stdout.strip()


def _project(tmp_path: Path, *, git: bool = False) -> Path:
    root = tmp_path / "g"
    init_project(root, name="G", demo=False)
    if git:
        _git(root, "init", "-q")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "init", date="2026-01-01T09:00:00Z")
    return root


def _new(root: Path, title: str = "Thing", ground: str | None = None) -> str:
    project = load_project(root)
    args = ["-C", str(root), "new", "REQ", "--title", title, "--text", "It does a thing.",
            "--origin", "ai", "--no-interactive"]
    if ground:
        args += ["--ground", ground, "--ground-type", "derives_from"]
    assert _cli(args) == 0
    return max(i.uid for i in load_project(root).items() if i.uid.startswith("REQ-"))


def _intent(root: Path) -> str:
    assert _cli(["-C", str(root), "new", "INT", "--title", "Why", "--text", "Because.",
                 "--origin", "ai", "--no-interactive"]) == 0
    return max(i.uid for i in load_project(root).items() if i.uid.startswith("INT-"))


def _findings(root: Path, strict: bool = True) -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list[tuple[str, str]]] = {}
    for f in validate(load_project(root), strict=strict):
        out.setdefault(f.uid, []).append((f.rule, f.severity))
    return out


# --- SR-0377: which statuses mean built is a role a graph may leave out ----------


def test_built_role_is_read_in_its_declared_order():
    schema = Schema.from_config({
        "status": {"values": ["proposed", "shipped", "published"],
                   "roles": {"proposed": "proposed", "built": ["shipped", "published"]}}})
    assert schema.built_order == ("shipped", "published")
    assert schema.built_statuses == {"shipped", "published"}


def test_built_role_must_name_declared_statuses():
    with pytest.raises(SchemaError, match="built names"):
        Schema.from_config({"status": {"values": ["proposed"],
                                       "roles": {"built": ["done"]}}})


def test_a_graph_with_no_built_role_has_no_built_axis(tmp_path):
    schema = Schema.from_config({"status": {"values": ["proposed", "ratified"],
                                            "roles": {"proposed": "proposed"}}})
    assert not built.has_built_axis(schema)
    assert built.describe_for_signing(object(), schema) is None


def test_init_binds_the_built_role(tmp_path):
    root = _project(tmp_path)
    assert load_project(root).schema.built_order == ("implemented", "verified")


# --- SR-0378: moving into a built status writes a dated record --------------------


def test_moving_into_and_out_of_built_is_dated(tmp_path):
    root = _project(tmp_path, git=True)
    uid = _new(root, ground=_intent(root))
    head = _git(root, "rev-parse", "HEAD")
    before = fingerprint(load_project(root).get(uid), load_project(root).schema)
    assert _cli(["-C", str(root), "status", uid, "implemented", "--by", "Bo"]) == 0
    item = load_project(root).get(uid)
    [entry] = item.implementations
    assert entry["by"] == "Bo" and entry["revision"] == head
    assert built.normalise_time(entry["at"]) == entry["at"]
    # Outside the fingerprint: marking work built never stales a signature.
    assert fingerprint(item, load_project(root).schema) == before
    assert _cli(["-C", str(root), "status", uid, "suspect", "--by", "Bo"]) == 0
    entries = load_project(root).get(uid).implementations
    assert len(entries) == 2 and entries[1]["withdrawn"] is True
    assert "revision" not in entries[1]


def test_a_move_inside_or_outside_the_role_writes_nothing(tmp_path):
    root = _project(tmp_path)
    uid = _new(root)
    assert _cli(["-C", str(root), "status", uid, "deferred"]) == 0
    assert load_project(root).get(uid).implementations == []


def test_implementations_survive_a_read_and_a_write(tmp_path):
    root = _project(tmp_path)
    uid = _new(root)
    assert _cli(["-C", str(root), "status", uid, "implemented", "--by", "Bo"]) == 0
    assert _cli(["-C", str(root), "amend", uid, "--rationale", "Why not."]) == 0
    assert len(load_project(root).get(uid).implementations) == 1


# --- SR-0379: an item may be built before it is signed ----------------------------


def test_proposed_may_move_to_implemented(tmp_path):
    root = _project(tmp_path)
    uid = _new(root)
    assert _cli(["-C", str(root), "status", uid, "implemented"]) == 0
    assert load_project(root).get(uid).status == "implemented"


# --- SR-0380: the strict check refuses built and unsigned work --------------------


def test_built_and_unsigned_is_refused_under_strict(tmp_path, capsys):
    root = _project(tmp_path)
    uid = _new(root, ground=_intent(root))
    assert _cli(["-C", str(root), "status", uid, "implemented"]) == 0
    assert ("unratified", "error") in _findings(root)[uid]
    assert _cli(["-C", str(root), "check", "--strict"]) == 1
    assert "is built ('implemented')" in capsys.readouterr().out


def test_work_built_before_adoption_awaits_acceptance_as_a_warning(tmp_path):
    root = _project(tmp_path)
    uid = _new(root, ground=_intent(root))
    assert _cli(["-C", str(root), "adopt", uid, "--at", "2026-01-01T00:00:00Z"]) == 0
    found = _findings(root)[uid]
    assert ("awaiting-acceptance", "warning") in found
    assert not any(rule == "unratified" for rule, _ in found)


def test_work_built_after_adoption_is_refused(tmp_path):
    root = _project(tmp_path)
    old = _new(root, ground=_intent(root))
    assert _cli(["-C", str(root), "adopt", old, "--at", "2026-01-01T00:00:00Z"]) == 0
    new = _new(root, "Later", ground=_intent(root))
    assert _cli(["-C", str(root), "status", new, "implemented"]) == 0
    assert ("unratified", "error") in _findings(root)[new]


# --- SR-0381: tl adopt records when a graph was laid over existing work -----------


def test_adopt_sets_the_baseline_and_marks_the_work(tmp_path):
    root = _project(tmp_path, git=True)
    uid = _new(root)
    assert _cli(["-C", str(root), "adopt", uid, "--at", "2026-02-03T04:05:06Z",
                 "--by", "Cy"]) == 0
    project = load_project(root)
    assert project.schema.adopted == "2026-02-03T04:05:06Z"
    item = project.get(uid)
    assert item.status == "implemented"
    [entry] = item.implementations
    assert entry == {"at": "2026-02-03T04:05:06Z", "by": "Cy",
                     "revision": _git(root, "rev-parse", "HEAD"), "adopted": True}


def test_adopt_again_with_the_same_time_changes_nothing(tmp_path):
    root = _project(tmp_path)
    _new(root)
    assert _cli(["-C", str(root), "adopt", "--all", "--at", "2026-02-03T04:05:06Z"]) == 0
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert _cli(["-C", str(root), "adopt", "--all", "--at", "2026-02-03T04:05:06Z"]) == 0
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_adopt_refuses_a_second_baseline(tmp_path, capsys):
    root = _project(tmp_path)
    uid = _new(root)
    assert _cli(["-C", str(root), "adopt", uid, "--at", "2026-02-03T00:00:00Z"]) == 0
    assert _cli(["-C", str(root), "adopt", uid, "--at", "2026-03-03T00:00:00Z"]) != 0
    assert "already laid over existing work" in capsys.readouterr().err


def test_adopt_refuses_a_graph_with_no_built_axis(tmp_path, capsys):
    root = _project(tmp_path)
    cfg = root / "throughline.toml"
    cfg.write_text(cfg.read_text().replace('built = ["implemented", "verified"]\n', ""))
    uid = _new(root)
    assert _cli(["-C", str(root), "adopt", uid]) != 0
    assert "no built role" in capsys.readouterr().err


# --- SR-0382: a filter can read when an item was built and when it was signed -----


def test_filters_read_built_signed_and_adopted(tmp_path, capsys):
    root = _project(tmp_path)
    a = _new(root, "A")
    b = _new(root, "B")
    assert _cli(["-C", str(root), "adopt", a, "--at", "2026-01-01T00:00:00Z"]) == 0
    assert _cli(["-C", str(root), "status", b, "implemented"]) == 0
    capsys.readouterr()
    assert _cli(["-C", str(root), "query", "item.built AND NOT item.signed"]) == 0
    out = capsys.readouterr().out
    assert a in out and b in out
    assert _cli(["-C", str(root), "query", "item.adopted"]) == 0
    out = capsys.readouterr().out
    assert a in out and b not in out
    assert _cli(["-C", str(root), "query",
                 "item.built_at > '2026-06-01' AND item.signed_at IS NULL"]) == 0
    out = capsys.readouterr().out
    assert b in out and a not in out


# --- SR-0383: tl migrate dates the built work a graph already holds ---------------


def test_migrate_dates_built_work_from_history(tmp_path):
    root = _project(tmp_path, git=True)
    uid = _new(root)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "proposed", date="2026-03-01T10:00:00Z")
    item_file = next(root.rglob(f"{uid}.yml"))
    item_file.write_text(item_file.read_text().replace("status: proposed",
                                                       "status: implemented"))
    _git(root, "commit", "-qam", "built", date="2026-04-02T11:30:00Z")
    built_commit = _git(root, "rev-parse", "HEAD")
    assert _cli(["-C", str(root), "migrate"]) == 0
    [entry] = load_project(root).get(uid).implementations
    assert entry == {"at": "2026-04-02T11:30:00Z", "by": "Ada",
                     "revision": built_commit, "backfilled": True}
    # Idempotent: run again, nothing moves.
    before = item_file.read_bytes()
    assert _cli(["-C", str(root), "migrate"]) == 0
    assert item_file.read_bytes() == before


def test_migrate_names_built_work_history_cannot_date(tmp_path, capsys):
    root = _project(tmp_path)                     # no git
    uid = _new(root)
    item_file = next(root.rglob(f"{uid}.yml"))
    item_file.write_text(item_file.read_text().replace("status: proposed",
                                                       "status: implemented"))
    assert _cli(["-C", str(root), "migrate"]) == 0
    assert uid in capsys.readouterr().out
    assert load_project(root).get(uid).implementations == []


def test_migrate_binds_the_role_and_the_route_for_an_older_lifecycle(tmp_path):
    root = _project(tmp_path)
    cfg = root / "throughline.toml"
    text = cfg.read_text()
    text = text.replace('built = ["implemented", "verified"]\n', "")
    text = text.replace('proposed    = ["ratified", "implemented", ',
                        'proposed    = ["ratified", ')
    cfg.write_text(text)
    assert load_project(root).schema.built_order == ()
    assert _cli(["-C", str(root), "migrate"]) == 0
    schema = load_project(root).schema
    assert schema.built_order == ("implemented", "verified")
    assert schema.allows_transition("proposed", "implemented")


# --- SR-0384: a request for a signature says whether it agrees or accepts ---------


def test_ratify_says_whether_it_agrees_a_plan_or_accepts_what_exists(tmp_path):
    from throughline.views import render_for_ratification
    root = _project(tmp_path)
    plan = _new(root, "Plan")
    done = _new(root, "Done")
    assert _cli(["-C", str(root), "adopt", done, "--at", "2026-01-01T00:00:00Z"]) == 0
    project = load_project(root)
    lines: list[str] = []
    render_for_ratification(project, project.get(plan), emit=lines.append)
    assert "this signature agrees work not yet built" in lines
    lines.clear()
    render_for_ratification(project, project.get(done), emit=lines.append)
    said = [ln for ln in lines if ln.startswith("this signature accepts")]
    assert said and "2026-01-01T00:00:00Z" in said[0] and "existing work" in said[0]


def test_dump_carries_the_built_record(tmp_path, capsys):
    root = _project(tmp_path)
    uid = _new(root)
    assert _cli(["-C", str(root), "status", uid, "implemented", "--by", "Bo"]) == 0
    capsys.readouterr()
    assert _cli(["-C", str(root), "dump"]) == 0
    items = {i["uid"]: i for i in json.loads(capsys.readouterr().out)["items"]}
    assert items[uid]["implementations"][0]["by"] == "Bo"
