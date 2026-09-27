# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""A composing project is read from its own tree only (SR-0239).

A project nested in a consumer's tree is not read as part of the consumer. The
composed check names it in its summary, unless the consumer composes it as a path
source, when it was read after all, as the source it is declared to be.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from throughline.cli import main as tl_main
from throughline.storage import load_project


def _check(consumer: Path, capsys) -> tuple[int, str, str]:
    capsys.readouterr()
    rc = tl_main(["-C", str(consumer), "check", "--base", ""])
    out = capsys.readouterr()
    return rc, out.out, out.err

def test_a_composed_check_names_a_nested_project_it_did_not_read(consumer_dir,
                                                                source_dir, capsys):
    shutil.copytree(source_dir, consumer_dir / "archive")
    rc, _out, err = _check(consumer_dir, capsys)
    assert rc == 0
    assert "not read: archive/ holds its own throughline.toml" in err


def test_a_nested_project_composed_as_a_path_source_is_not_named(consumer_dir,
                                                                source_dir, capsys):
    shutil.copytree(source_dir, consumer_dir / "spec")
    cfg = consumer_dir / "throughline.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace(
        'path = "../toy-source"', 'path = "spec"'), encoding="utf-8")
    # Read once, as the source it is declared to be, and never as registers of the
    # consumer that happens to contain it.
    consumer = load_project(consumer_dir)
    assert consumer.nested_projects == [consumer_dir / "spec"]
    assert "UR" not in consumer.registers
    rc, out, err = _check(consumer_dir, capsys)
    assert rc == 0, out
    assert "not read" not in err
