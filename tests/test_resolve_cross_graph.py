# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""`tl resolve` across graphs in one repository (SR-0250).

A consumer that sources a graph by `path` names its items `namespace:UID`. When
`tl resolve` renumbers one of them, the consumer's links that the moved side wrote
must follow, in whichever order the graphs are resolved. Every scenario is a real
git merge stopped on a real add/add conflict.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from throughline.cli import main as cli_main
from throughline.collision import _names, _state_file


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), "-c", "user.email=t@t",
                           "-c", "user.name=t", *args],
                          capture_output=True, text=True, check=check)


def _new(graph: Path, prefix: str, title: str, *, ground: str) -> None:
    assert _cli(["-C", graph, "new", prefix, "--title", title, "--text", "t.",
                 "--ground", ground, "--no-interactive"]) == 0


def _source(consumer: Path, namespace: str, path: str, extra: str = "") -> None:
    with (consumer / "throughline.toml").open("a", encoding="utf-8") as f:
        f.write(f'\n[[sources]]\nnamespace = "{namespace}"\npath = "{path}"\n{extra}')


def _text(graph: Path, uid: str) -> str:
    return (graph / "requirements" / f"{uid}.yml").read_text(encoding="utf-8")


def _commit(root: Path, msg: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", msg)


@pytest.fixture
def repo(tmp_path) -> Path:
    """`anchor` and `team` (sourcing anchor as `subject`) on main; `feat` branches."""
    root = tmp_path / "repo"
    anchor, team = root / "anchor", root / "team"
    assert _cli(["-C", anchor, "init", "--no-demo", "--name", "anchor"]) == 0
    assert _cli(["-C", team, "init", "--no-demo", "--name", "team"]) == 0
    _source(team, "subject", "../anchor")
    _git(root, "init", "-q", "-b", "main")
    assert _cli(["-C", anchor, "new", "INT", "--type", "intent", "--title", "Why",
                 "--no-interactive"]) == 0
    _commit(root, "base")
    _git(root, "branch", "feat")
    return root


def _merge_main_into_feat(root: Path) -> None:
    _git(root, "checkout", "-q", "feat")
    done = _git(root, "merge", "main", check=False)
    assert done.returncode != 0 and "CONFLICT (add/add)" in done.stdout


def test_resolving_a_source_rewrites_its_consumer(repo, capsys):
    anchor, team = repo / "anchor", repo / "team"
    _git(repo, "checkout", "-q", "feat")
    _new(anchor, "REQ", "Feat subject", ground="INT-0001")
    _new(team, "REQ", "Feat team", ground="subject:REQ-0001")
    _commit(repo, "feat")
    _git(repo, "checkout", "-q", "main")
    _new(anchor, "REQ", "Main subject", ground="INT-0001")
    _commit(repo, "main")
    _merge_main_into_feat(repo)

    assert _cli(["-C", anchor, "resolve"]) == 0
    out = capsys.readouterr().out
    assert "move REQ-0001 -> REQ-0002" in out
    assert "relink REQ-0001 in team: 1 link(s)" in out
    assert "also changed: team" in out
    assert "target: subject:REQ-0002" in _text(team, "REQ-0001")
    assert _git(repo, "diff", "--cached", "--name-only").stdout.count("team/") == 1

    assert _cli(["-C", team, "resolve"]) == 0          # nothing of its own to do
    assert "nothing to resolve" in capsys.readouterr().out
    _git(repo, "commit", "-qm", "merge main")
    assert _cli(["-C", team, "check"]) == 0
    trace = subprocess.run(["tl", "-C", str(team), "trace", "REQ-0001"],
                           capture_output=True, text=True).stdout
    assert "subject:REQ-0002" in trace and "Feat subject" in trace


def _both_graphs_collide(repo: Path) -> None:
    """Both graphs allocate REQ-0001 on both sides; feat's team item cites feat's
    anchor item, main's team item cites main's."""
    anchor, team = repo / "anchor", repo / "team"
    for branch, who in (("feat", "Feat"), ("main", "Main")):
        _git(repo, "checkout", "-q", branch)
        _new(anchor, "REQ", f"{who} subject", ground="INT-0001")
        _new(team, "REQ", f"{who} team", ground="subject:REQ-0001")
        _commit(repo, branch)
    _merge_main_into_feat(repo)


def _assert_both_resolved(repo: Path) -> None:
    anchor, team = repo / "anchor", repo / "team"
    assert "title: Main subject" in _text(anchor, "REQ-0001")
    assert "title: Feat subject" in _text(anchor, "REQ-0002")
    kept, moved = _text(team, "REQ-0001"), _text(team, "REQ-0002")
    assert "title: Main team" in kept and "target: subject:REQ-0001" in kept
    assert "title: Feat team" in moved and "target: subject:REQ-0002" in moved
    assert _git(repo, "ls-files", "-u").stdout == ""


def test_source_first_then_consumer(repo, capsys):
    _both_graphs_collide(repo)
    assert _cli(["-C", repo / "anchor", "resolve"]) == 0
    out = capsys.readouterr().out
    assert "REQ-0001 in team is itself a UID collision" in out
    assert "tl -C team resolve" in out

    assert _cli(["-C", repo / "team", "resolve"]) == 0
    _assert_both_resolved(repo)


def test_consumer_first_then_source(repo, capsys):
    _both_graphs_collide(repo)
    assert _cli(["-C", repo / "team", "resolve"]) == 0
    # The source has not moved anything yet, so the moved team item still cites
    # the old UID until the source is resolved.
    assert "target: subject:REQ-0001" in _text(repo / "team", "REQ-0002")

    assert _cli(["-C", repo / "anchor", "resolve"]) == 0
    assert "relink REQ-0002 in team" in capsys.readouterr().out
    _assert_both_resolved(repo)


def test_a_consumer_item_both_sides_linked_is_refused(repo, capsys):
    anchor, team = repo / "anchor", repo / "team"
    _git(repo, "checkout", "-q", "main")
    _new(team, "REQ", "Shared team", ground="subject:INT-0001")   # on both sides
    _commit(repo, "shared")
    _git(repo, "branch", "-f", "feat", "main")
    for branch, who in (("feat", "Feat"), ("main", "Main")):
        _git(repo, "checkout", "-q", branch)
        _new(anchor, "REQ", f"{who} subject", ground="INT-0001")
        assert _cli(["-C", team, "link", "REQ-0001", "subject:REQ-0001",
                     "--type", "relates"]) == 0
        _commit(repo, branch)
    _merge_main_into_feat(repo)
    before = _git(repo, "status", "--porcelain").stdout

    assert _cli(["-C", anchor, "resolve"]) == 2
    assert "links to subject:REQ-0001 on both sides" in capsys.readouterr().err
    assert _git(repo, "status", "--porcelain").stdout == before
    assert not _state_file(repo).exists()


def test_a_record_from_another_merge_is_ignored(repo, capsys):
    _both_graphs_collide(repo)
    _state_file(repo).write_text(json.dumps({
        "merge": "0" * 40 + "..x",
        "graphs": {"anchor": {"moves": {"REQ-0001": "REQ-0099"}, "paths": {}}}}))
    assert _cli(["-C", repo / "team", "resolve"]) == 0
    assert "subject:REQ-0099" not in _text(repo / "team", "REQ-0002")


def test_names_follow_transitive_sources_and_alias(tmp_path):
    def graph(name: str) -> Path:
        assert _cli(["-C", tmp_path / name, "init", "--no-demo", "--name", name]) == 0
        return (tmp_path / name).resolve()
    std, mid, top, other = graph("std"), graph("mid"), graph("top"), graph("other")
    _source(mid, "base", "../std")
    _source(top, "m", "../mid", 'alias = { base = "standard" }\n')
    _source(other, "m", "../mid")
    assert _names(mid, std) == {"base"}
    assert _names(top, std) == {"standard"}
    assert _names(other, std) == {"base"}
    assert _names(std, mid) == set()
