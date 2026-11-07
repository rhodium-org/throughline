# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""SR-0316, SR-0317 — migrating a graph from before 5.0 says what to do next.

5.0 retired approved and draft without a new format version, so `tl migrate
--beneath` took a format-4 graph holding them for current (SR-0316), and a
migration listed the items it moved but said nothing of the others the strict
gate now fails on, nor how to sign them (SR-0317). Asked on 7 November 2026.
"""
from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

from test_retire_unsigned_agreement import _old_graph
from throughline.cli import main as cli_main
from throughline.migrate_beneath import survey


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _in_repo(root: Path) -> Path:
    for args in (["init", "-q"], ["config", "user.email", "grace@example.com"],
                 ["config", "user.name", "Grace"], ["add", "."],
                 ["commit", "-qm", "baseline"]):
        subprocess.run(["git", "-C", str(root), *args], check=True,
                       capture_output=True)
    return root


# ----------------------------------------------------------------- SR-0316

def test_a_graph_at_the_current_format_holding_approved_is_listed(tmp_path, capsys):
    root = _in_repo(_old_graph(tmp_path))
    [found] = survey([tmp_path])
    assert found.retires == ("draft", "approved") and found.due

    assert _cli(["migrate", "--beneath", tmp_path]) == 0
    out = capsys.readouterr().out
    assert "1 graph(s) at format version 4 that still hold" in out
    assert f"{root}  draft, approved" in out
    assert "0 graph(s) already at format version 4 with nothing to migrate" in out


def test_apply_retires_them_and_a_second_survey_finds_nothing(tmp_path, capsys):
    root = _in_repo(_old_graph(tmp_path))
    assert _cli(["migrate", "--beneath", tmp_path, "--apply"]) == 0
    values = tomllib.loads((root / "throughline.toml").read_text(encoding="utf-8"))
    assert not {"approved", "draft"} & set(values["status"]["values"])
    [found] = survey([tmp_path])
    assert not found.retires and not found.due
    capsys.readouterr()
    assert _cli(["migrate", "--beneath", tmp_path]) == 0
    assert "1 graph(s) already at format version 4\n" in capsys.readouterr().out


# ----------------------------------------------------------------- SR-0317

def test_migrate_counts_what_awaits_a_signature_and_says_how_to_sign(tmp_path, capsys):
    root = _old_graph(tmp_path)
    assert _cli(["-C", root, "migrate"]) == 0
    out = capsys.readouterr().out
    # INT-0001, FR-0001, FR-0002 moved to proposed; FR-0003 implemented unsigned.
    # FR-0004's legacy signature stands, so it does not wait.
    assert "next: 4 item(s) await a person's signature" in out
    assert "none was signed for you" in out
    assert f"tl -C {root} check" in out
    assert f'tl-ratify -C {root} --by "<your name>"' in out
    assert f'tl -C {root} ratify <UID> --by "<your name>"' in out


def test_migrate_says_nothing_of_signing_when_nothing_waits(tmp_path, capsys):
    root = tmp_path / "new"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    capsys.readouterr()
    assert _cli(["-C", root, "migrate"]) == 0
    assert "await a person's signature" not in capsys.readouterr().out


def test_apply_gives_each_migrated_graph_its_count(tmp_path, capsys):
    root = _in_repo(_old_graph(tmp_path))
    assert _cli(["migrate", "--beneath", tmp_path, "--apply"]) == 0
    out = capsys.readouterr().out
    assert "next: 4 item(s) in 1 graph(s) await a person's signature" in out
    assert f"{root}  4 item(s)" in out


def test_a_path_is_shown_as_typed_and_quoted_only_where_it_must_be():
    from throughline.cli import _shown_path
    assert _shown_path("idd") == "idd"
    assert _shown_path(r"C:\Users\grace\repo\idd") == r"C:\Users\grace\repo\idd"
    assert _shown_path("/home/grace/my repo/idd") == '"/home/grace/my repo/idd"'
