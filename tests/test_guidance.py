# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""A standard's guidance for AI agents arrives with every graph that follows it
(UR-0047).

A signature covers the content of the files an item names (SR-0322), so guidance
cannot change after a person signed for it; and `tl init --from-schema` copies the
signed guidance and makes AGENTS.md and CLAUDE.md, at the root a session starts
from, refer to it, adding to a file that exists (SR-0323).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from throughline.cli import main as cli_main
from throughline.storage import load_project
from throughline.validate import validate

GUIDANCE_TYPE = """
[types.guidance]
attrs.file = { type = "string", file = true, required = true }
attrs.copy_to = { type = "string", required = true }
"""
TEXT = "# How to write a deck\n\nOne claim a slide.\n"


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _standard(tmp_path: Path, *, sign: bool = True, copy_to: str = "deck-guidance.md") -> Path:
    """A standard holding one guidance item that names guide/how.md."""
    std = tmp_path / "std"
    assert _cli(["-C", std, "init", "--no-demo"]) == 0
    cfg = std / "throughline.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + GUIDANCE_TYPE, encoding="utf-8")
    assert _cli(["-C", std, "register", "new", "GUIDE", "guidance", "--type", "guidance"]) == 0
    (std / "guide").mkdir()
    (std / "guide" / "how.md").write_text(TEXT, encoding="utf-8")
    assert _cli(["-C", std, "new", "INT", "--title", "Why", "--text", "Decks people trust.",
                 "--origin", "human", "--no-interactive"]) == 0
    assert _cli(["-C", std, "new", "GUIDE", "--title", "How", "--text", "The guidance.",
                 "--attr", "file=guide/how.md", "--attr", f"copy_to={copy_to}",
                 "--ground", "INT-0001", "--origin", "human", "--no-interactive"]) == 0
    if sign:
        assert _cli(["-C", std, "ratify", "INT-0001", "--by", "Ada Keeper"]) == 0
        assert _cli(["-C", std, "ratify", "GUIDE-0001", "--by", "Ada Keeper"]) == 0
    return std


def _record(std: Path) -> dict:
    item = yaml.safe_load((std / "guidance" / "GUIDE-0001.yml").read_text(encoding="utf-8"))
    return item["ratifications"][-1]


def _stale(std: Path) -> list[str]:
    return [f.message for f in validate(load_project(std)) if f.rule == "ratified-stale"]


# --------------------------------------------------------------------------- #
# SR-0322 — a signature covers the content of the files an item names
# --------------------------------------------------------------------------- #

def test_ratify_records_a_digest_of_each_named_file(tmp_path):
    import hashlib
    std = _standard(tmp_path)
    want = "sha256:" + hashlib.sha256(TEXT.encode()).hexdigest()
    assert _record(std)["files"] == {"guide/how.md": want}
    assert not _stale(std)


def test_check_reports_the_signature_stale_and_names_the_file_when_it_changes(tmp_path):
    std = _standard(tmp_path)
    how = std / "guide" / "how.md"
    how.write_text(TEXT + "Two claims a slide.\n", encoding="utf-8")
    (message,) = _stale(std)
    assert "guide/how.md" in message and "Ada Keeper" in message
    how.write_text(TEXT, encoding="utf-8")
    assert not _stale(std)


def test_the_same_file_with_another_system_s_line_endings_is_not_stale(tmp_path):
    std = _standard(tmp_path)
    how = std / "guide" / "how.md"
    how.write_bytes(TEXT.encode().replace(b"\n", b"\r\n"))
    assert not _stale(std)
    how.write_bytes(TEXT.encode())
    assert not _stale(std)


def test_a_named_file_that_has_gone_makes_the_signature_stale(tmp_path):
    std = _standard(tmp_path)
    (std / "guide" / "how.md").unlink()
    assert any("guide/how.md" in m for m in _stale(std))


def test_a_signature_taken_before_files_were_covered_is_not_called_stale(tmp_path):
    std = _standard(tmp_path)
    path = std / "guidance" / "GUIDE-0001.yml"
    item = yaml.safe_load(path.read_text(encoding="utf-8"))
    del item["ratifications"][-1]["files"]
    path.write_text(yaml.safe_dump(item, sort_keys=False), encoding="utf-8")
    (std / "guide" / "how.md").write_text("changed\n", encoding="utf-8")
    assert not _stale(std)


def test_a_signature_taken_before_files_were_covered_may_be_given_again(tmp_path, capsys):
    std = _standard(tmp_path)
    path = std / "guidance" / "GUIDE-0001.yml"
    item = yaml.safe_load(path.read_text(encoding="utf-8"))
    del item["ratifications"][-1]["files"]
    path.write_text(yaml.safe_dump(item, sort_keys=False), encoding="utf-8")
    assert _cli(["-C", std, "ratify", "GUIDE-0001", "--by", "Ada Keeper"]) == 0
    assert "guide/how.md" in _record(std)["files"]
    # Covered now, so a third signature over the same content accepts nothing.
    assert _cli(["-C", std, "ratify", "GUIDE-0001", "--by", "Ada Keeper"]) != 0


def test_an_item_that_names_no_file_records_none(tmp_path):
    std = _standard(tmp_path)
    item = yaml.safe_load((std / "vision" / "INT-0001.yml").read_text(encoding="utf-8"))
    assert "files" not in item["ratifications"][-1]


# --------------------------------------------------------------------------- #
# SR-0323 — init copies the signed guidance and refers to it
# --------------------------------------------------------------------------- #

def test_init_copies_signed_guidance_and_writes_both_agent_files(tmp_path, monkeypatch, capsys):
    _standard(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert _cli(["-C", "fol", "init", "--from-schema", "std"]) == 0
    fol = tmp_path / "fol"
    assert (fol / "deck-guidance.md").read_text(encoding="utf-8") == TEXT
    assert "(deck-guidance.md)" in (fol / "AGENTS.md").read_text(encoding="utf-8")
    assert (fol / "CLAUDE.md").read_text(encoding="utf-8") == "@AGENTS.md\n"
    out = capsys.readouterr().out
    for name in ("fol/deck-guidance.md", "fol/AGENTS.md", "fol/CLAUDE.md"):
        assert f"created {name}" in out


def test_unsigned_guidance_is_not_copied_and_init_warns_naming_it(tmp_path, monkeypatch, capsys):
    _standard(tmp_path, sign=False)
    monkeypatch.chdir(tmp_path)
    assert _cli(["-C", "fol", "init", "--from-schema", "std"]) == 0
    assert "GUIDE-0001" in capsys.readouterr().err
    assert sorted(p.name for p in (tmp_path / "fol").iterdir()) == ["throughline.toml"]


def test_guidance_changed_after_it_was_signed_is_not_copied(tmp_path, monkeypatch, capsys):
    std = _standard(tmp_path)
    (std / "guide" / "how.md").write_text("rewritten\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert _cli(["-C", "fol", "init", "--from-schema", "std"]) == 0
    assert "GUIDE-0001" in capsys.readouterr().err
    assert not (tmp_path / "fol" / "deck-guidance.md").exists()
    assert not (tmp_path / "fol" / "AGENTS.md").exists()


def test_existing_agent_files_are_added_to_and_nothing_in_them_is_lost(tmp_path, monkeypatch, capsys):
    _standard(tmp_path)
    fol = tmp_path / "fol"
    fol.mkdir()
    (fol / "AGENTS.md").write_text("# Ours\n\nBe kind.\n", encoding="utf-8")
    (fol / "CLAUDE.md").write_text("Use British spelling.", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert _cli(["-C", "fol", "init", "--from-schema", "std"]) == 0
    agents = (fol / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.startswith("# Ours\n\nBe kind.\n") and "(deck-guidance.md)" in agents
    claude = (fol / "CLAUDE.md").read_text(encoding="utf-8")
    assert claude.startswith("Use British spelling.") and claude.endswith("\n@AGENTS.md\n")
    out = capsys.readouterr().out
    assert "added to fol/AGENTS.md" in out and "added to fol/CLAUDE.md" in out


def test_agent_files_that_already_refer_to_the_guidance_are_left_as_they_are(tmp_path, monkeypatch, capsys):
    _standard(tmp_path)
    fol = tmp_path / "fol"
    fol.mkdir()
    (fol / "AGENTS.md").write_text("See deck-guidance.md first.\n", encoding="utf-8")
    (fol / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert _cli(["-C", "fol", "init", "--from-schema", "std"]) == 0
    assert (fol / "AGENTS.md").read_text(encoding="utf-8") == "See deck-guidance.md first.\n"
    assert (fol / "CLAUDE.md").read_text(encoding="utf-8") == "@AGENTS.md\n"
    assert "AGENTS.md" not in capsys.readouterr().out


def test_a_copy_to_path_that_holds_something_else_is_kept_and_init_carries_on(tmp_path, monkeypatch, capsys):
    _standard(tmp_path)
    fol = tmp_path / "fol"
    fol.mkdir()
    (fol / "deck-guidance.md").write_text("mine\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert _cli(["-C", "fol", "init", "--from-schema", "std"]) == 0
    assert (fol / "deck-guidance.md").read_text(encoding="utf-8") == "mine\n"
    assert "fol/deck-guidance.md" in capsys.readouterr().err
    assert (fol / "throughline.toml").is_file() and not (fol / "AGENTS.md").exists()


def test_a_copy_to_that_leaves_the_new_project_is_refused(tmp_path, monkeypatch, capsys):
    _standard(tmp_path, copy_to="../escaped.md")
    monkeypatch.chdir(tmp_path)
    assert _cli(["-C", "fol", "init", "--from-schema", "std"]) == 0
    assert "GUIDE-0001" in capsys.readouterr().err
    assert not (tmp_path / "escaped.md").exists()


def test_in_a_git_working_tree_the_agent_files_are_the_ones_at_its_root(tmp_path, monkeypatch, capsys):
    _standard(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    if subprocess.run(["git", "init", "-q", str(repo)], capture_output=True).returncode != 0:
        pytest.skip("git is not available")
    (repo / "AGENTS.md").write_text("# Ours\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    assert _cli(["-C", "idd", "init", "--from-schema", "../std"]) == 0
    assert (repo / "idd" / "deck-guidance.md").read_text(encoding="utf-8") == TEXT
    agents = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.startswith("# Ours\n") and "(idd/deck-guidance.md)" in agents and "`idd/`" in agents
    assert (repo / "CLAUDE.md").read_text(encoding="utf-8") == "@AGENTS.md\n"
    assert not (repo / "idd" / "AGENTS.md").exists()
    out = capsys.readouterr().out
    assert "added to AGENTS.md" in out and "created CLAUDE.md" in out
