# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""A project is read from its own tree only (SR-0239).

The loader once searched every directory under a graph root. The Claude desktop app
checks out git worktrees under ``.claude/worktrees/`` of the directory a session
starts in, so a session started at a graph root left another branch's copy of the
graph inside it. ``.claude`` sorts before any letter, so the copy's registers were
loaded first and the real ones were dropped as prefix clashes: check read a stale
graph, and ``tl new`` allocated a UID already in use and wrote it into the worktree.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from throughline.cli import main as cli_main
from throughline.storage import (
    FORMAT_VERSION,
    ProjectError,
    load_project,
    migrate_project,
    read_baseline,
)


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, check=True).stdout


def _repo(root: Path) -> Path:
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    return root


def _commit(root: Path) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "commit")
    return _git(root, "rev-parse", "HEAD").strip()


def _new_req(root: Path, title: str) -> None:
    assert _cli(["-C", root, "new", "REQ", "--title", title, "--text", "t.",
                 "--ground", "INT-0001", "--no-interactive"]) == 0


def _graph(root: Path, reqs: int) -> Path:
    """A graph at ``root``: one intent, and ``reqs`` requirements grounded in it."""
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    assert _cli(["-C", root, "new", "INT", "--type", "intent", "--title", "Why",
                 "--no-interactive"]) == 0
    for n in range(reqs):
        _new_req(root, f"R{n + 1}")
    return root


def _reqs(root: Path) -> list[str]:
    return sorted(uid for uid in (i.uid for i in load_project(root).items())
                  if uid.startswith("REQ-"))


# -------------------------------------------------- the reproduction, as a fixture

@pytest.fixture(params=[False, True], ids=["standalone", "composing"])
def shadowed(request, tmp_path) -> Path:
    """The layout found on 27 September 2026, rebuilt: a graph at ``repo/idd`` with
    three requirements, and inside it, where the desktop app puts one, a git worktree
    of the older commit that held only the first. The composing variant declares a
    path source, since a composed check also lost the prefix clashes that would
    otherwise have given the stale read away (SR-0240). Returns the graph root."""
    repo = _repo(tmp_path / "repo")
    idd = _graph(repo / "idd", reqs=1)
    if request.param:
        _graph(repo / "source", reqs=1)
        cfg = idd / "throughline.toml"
        cfg.write_text(cfg.read_text(encoding="utf-8")
                       + '\n[[sources]]\nnamespace = "src"\npath = "../source"\n',
                       encoding="utf-8")
    stale = _commit(repo)
    _new_req(idd, "R2")
    _new_req(idd, "R3")
    _commit(repo)
    _git(repo, "worktree", "add", "-q", "--detach",
         str(idd / ".claude" / "worktrees" / "w"), stale)
    assert (idd / ".claude" / "worktrees" / "w" / "idd" / "requirements"
            / "REQ-0001.yml").is_file()
    return idd


def test_a_worktree_inside_the_graph_root_is_not_read(shadowed, capsys):
    project = load_project(shadowed)
    assert _reqs(shadowed) == ["REQ-0001", "REQ-0002", "REQ-0003"]
    assert project.registers["REQ"].path == shadowed / "requirements"
    # A hidden directory is not looked into, so nothing in it is even noted.
    assert project.prefix_conflicts == {} and project.nested_projects == []
    assert _cli(["-C", shadowed, "check"]) == 0
    out = capsys.readouterr()
    assert "prefix-collision" not in out.out
    assert "not read" not in out.err


def test_new_allocates_from_and_writes_to_the_real_register(shadowed, capsys):
    _new_req(shadowed, "probe")
    assert "created REQ-0004" in capsys.readouterr().out
    assert (shadowed / "requirements" / "REQ-0004.yml").is_file()
    copy = shadowed / ".claude" / "worktrees" / "w" / "idd" / "requirements"
    assert sorted(p.name for p in copy.glob("REQ-*.yml")) == ["REQ-0001.yml"]


def test_a_project_whose_own_root_lies_in_a_hidden_directory_is_read(tmp_path):
    """The boundary is drawn below the root, never above it: the session working in
    that worktree reads its own graph."""
    root = _graph(tmp_path / ".claude" / "worktrees" / "w" / "idd", reqs=2)
    assert _reqs(root) == ["REQ-0001", "REQ-0002"]
    assert _cli(["-C", root, "check", "--base", ""]) == 0


# ------------------------------------------------------- where the boundary lies

def test_a_register_in_a_hidden_directory_is_not_read(tmp_path):
    root = _graph(tmp_path / "proj", reqs=1)
    hidden = root / ".drafts"
    hidden.mkdir()
    (hidden / ".register.yml").write_text("prefix: DRAFT\ndigits: 4\n", encoding="utf-8")
    (hidden / "DRAFT-0001.yml").write_text(
        "uid: DRAFT-0001\ntype: requirement\nstatus: draft\ntitle: d\ntext: t.\n",
        encoding="utf-8")
    project = load_project(root)
    assert "DRAFT" not in project.registers
    assert project.nested_projects == []


def test_a_nested_project_is_not_read_and_check_names_it(tmp_path, capsys):
    root = _graph(tmp_path / "proj", reqs=1)
    inner = root / "archive"
    # init refuses to nest one project in another (SR-0077); the layout arises anyway
    # by copying, so it is forced here.
    assert _cli(["-C", inner, "init", "--no-demo", "--force"]) == 0
    assert _cli(["-C", inner, "new", "INT", "--type", "intent", "--title", "Other",
                 "--no-interactive"]) == 0
    project = load_project(root)
    assert project.nested_projects == [inner]
    assert project.prefix_conflicts == {}
    assert [i.uid for i in project.items()
            if i._path and inner in i._path.parents] == []
    capsys.readouterr()
    # Named in the summary, never as a finding: the exit status is the findings'.
    assert _cli(["-C", root, "check", "--base", ""]) == 0
    out = capsys.readouterr()
    assert ("not read: archive/ holds its own throughline.toml, so it is another "
            "project, not part of this one") in out.err
    assert "archive" not in out.out
    assert _cli(["-C", root, "check", "--base", "", "--quiet"]) == 0
    assert "not read" not in capsys.readouterr().err


def test_register_new_refuses_a_directory_the_project_would_not_read(tmp_path, capsys):
    root = tmp_path / "proj"
    assert _cli(["-C", root, "init", "--bare"]) == 0
    (root / "archive").mkdir()
    (root / "archive" / "throughline.toml").write_text('[project]\nname = "old"\n',
                                                      encoding="utf-8")
    for directory in (".drafts", "archive/system", "../elsewhere"):
        assert _cli(["-C", root, "register", "new", "XX", directory]) == 2
        assert "would never be read" in capsys.readouterr().err
    assert not (root / ".drafts").exists()
    assert not (root / "archive" / "system").exists()
    assert not (tmp_path / "elsewhere").exists()
    assert _cli(["-C", root, "register", "new", "XX", "drafts"]) == 0


# ----------------------------------- every reader of the project's files agrees

def _legacy_v1(root: Path) -> Path:
    """A project in the v1 layout, a `.document.yml` manifest, with no recorded
    format version, so its major must be inferred from what is on disk."""
    assert _cli(["-C", root, "init", "--bare"]) == 0
    assert _cli(["-C", root, "register", "new", "SR", "system"]) == 0
    assert _cli(["-C", root, "new", "SR", "--type", "system_requirement",
                 "--no-interactive", "--title", "legacy item"]) == 0
    (root / "system" / ".register.yml").rename(root / "system" / ".document.yml")
    cfg = root / "throughline.toml"
    cfg.write_text("".join(line for line in cfg.read_text(encoding="utf-8")
                           .splitlines(keepends=True)
                           if not line.startswith("format_version")),
                   encoding="utf-8")
    return root


def test_format_inference_and_migration_read_only_the_projects_own_manifests(tmp_path):
    root = _legacy_v1(tmp_path / "proj")
    # A current-format copy in a worktree must not make this project look current,
    worktree = root / ".claude" / "worktrees" / "w" / "system"
    worktree.mkdir(parents=True)
    (worktree / ".register.yml").write_text("prefix: SR\ndigits: 4\n", encoding="utf-8")
    # and the migration must not rename another project's manifest.
    archived = root / "archive"
    (archived / "system").mkdir(parents=True)
    (archived / "throughline.toml").write_text(
        '[project]\nname = "old"\nformat_version = 1\n', encoding="utf-8")
    (archived / "system" / ".document.yml").write_text("prefix: SR\ndigits: 4\n",
                                                        encoding="utf-8")
    with pytest.raises(ProjectError, match="tl migrate"):
        load_project(root)
    assert migrate_project(root)[:2] == (1, FORMAT_VERSION)
    assert (root / "system" / ".register.yml").is_file()
    assert (archived / "system" / ".document.yml").is_file()
    assert not (archived / "system" / ".register.yml").exists()
    assert [i.uid for i in load_project(root).items()] == ["SR-0001"]


def _strays(root: Path) -> None:
    """Tombstones this graph never held: one in a hidden directory, one inside
    another project. Read as part of the baseline, each would be reported as an
    erased tombstone (SR-0093)."""
    for folder, uid in ((root / ".claude" / "worktrees" / "w" / "requirements",
                         "REQ-0009"),
                        (root / "archive" / "requirements", "REQ-0008")):
        folder.mkdir(parents=True)
        (folder / f"{uid}.yml").write_text(
            f"uid: {uid}\ntype: requirement\nstatus: deleted\ntitle: gone\ntext: t.\n",
            encoding="utf-8")
    (root / "archive" / "throughline.toml").write_text('[project]\nname = "old"\n',
                                                      encoding="utf-8")


def test_a_baseline_directory_reads_only_the_projects_own_tree(tmp_path, capsys):
    root = _graph(_repo(tmp_path / "repo"), reqs=1)
    _commit(root)
    at_base = tmp_path / "at-base"
    shutil.copytree(root, at_base, ignore=shutil.ignore_patterns(".git"))
    _strays(at_base)
    project = load_project(root)
    assert (read_baseline(project, base_dir=at_base).statuses
            == read_baseline(project, ref="HEAD").statuses
            == {"INT-0001": "draft", "REQ-0001": "draft"})
    assert _cli(["-C", root, "check", "--base-dir", at_base]) == 0
    assert "tombstone-deleted" not in capsys.readouterr().out


def test_a_git_baseline_reads_only_the_projects_own_tree(tmp_path, capsys):
    root = _graph(_repo(tmp_path / "repo"), reqs=1)
    _strays(root)
    _commit(root)
    assert read_baseline(load_project(root), ref="HEAD").statuses == {
        "INT-0001": "draft", "REQ-0001": "draft"}
    assert _cli(["-C", root, "check"]) == 0
    assert "tombstone-deleted" not in capsys.readouterr().out
