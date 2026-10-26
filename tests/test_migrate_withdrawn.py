# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""`tl migrate` removes the withdrawn ``reexport`` key from the project's own
sources and names each source it did it to (SR-0274).

A consumer still carrying the key was refused outright, so it could neither be
checked nor — for a composed graph — fully migrated: the standalone pass ran and
the union pass could not read the sources. table.iddn.uk hit exactly that on
26 October 2026 over a graph its keeper had asked it to bring up to date.
"""
from __future__ import annotations

from pathlib import Path

from throughline.cli import main as cli_main
from throughline.storage import migrate_project


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _consumer(tmp_path: Path, extra: str) -> Path:
    src = tmp_path / "src"
    assert _cli(["-C", src, "init", "--no-demo"]) == 0
    consumer = tmp_path / "consumer"
    assert _cli(["-C", consumer, "init", "--no-demo"]) == 0
    cfg = consumer / "throughline.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8")
                   + "\n# where the baseline comes from\n[[sources]]\n"
                   + 'namespace = "baseline"\npath = "../src"\n' + extra,
                   encoding="utf-8")
    return consumer


def test_migrate_removes_the_withdrawn_key_and_names_the_source(tmp_path, capsys):
    consumer = _consumer(tmp_path, 'reexport = ["platform"]\n')
    # Refused while it carries the key, and the refusal names the remedy.
    assert _cli(["-C", consumer, "check"]) != 0
    cap = capsys.readouterr()
    assert "reexport" in cap.out + cap.err and "tl migrate" in cap.out + cap.err
    # Migrating removes it, says so per source, and leaves the rest of the entry.
    assert _cli(["-C", consumer, "migrate"]) == 0
    out = capsys.readouterr().out
    assert "removed the withdrawn 'reexport' key from 1 source(s)" in out
    assert "  baseline" in out and "alias" in out
    text = (consumer / "throughline.toml").read_text(encoding="utf-8")
    assert "reexport" not in text
    assert 'namespace = "baseline"' in text and 'path = "../src"' in text
    assert "# where the baseline comes from" in text
    # And the project composes again.
    assert _cli(["-C", consumer, "check"]) == 0
    # Idempotent: the second migrate has nothing to remove.
    assert migrate_project(consumer).withdrawn == ()


def test_a_multi_line_array_goes_whole(tmp_path, capsys):
    consumer = _consumer(tmp_path, 'reexport = [\n  "platform",\n  "house",\n]\nref = "main"\n')
    cfg = consumer / "throughline.toml"
    # ref beside a path source is itself refused, so take it out before checking;
    # here it only proves the lines after the array survive the removal.
    assert migrate_project(consumer).withdrawn == ("baseline",)
    text = cfg.read_text(encoding="utf-8")
    assert "reexport" not in text and "platform" not in text and "house" not in text
    assert 'ref = "main"' in text
