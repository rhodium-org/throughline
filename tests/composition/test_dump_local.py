# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""An export of a project's own items resolves no source (SR-0345, issue 66)."""
from __future__ import annotations

import json
import shutil

from throughline.cli import main as tl_main


def _dump(root, capsys, *flags) -> tuple[int, dict | None, str]:
    capsys.readouterr()
    rc = tl_main(["-C", str(root), "dump", *flags])
    out = capsys.readouterr()
    return rc, (json.loads(out.out) if rc == 0 else None), out.err


def test_a_local_export_names_each_source_as_declared_and_counts_nothing_borrowed(
        consumer_dir, capsys):
    rc, data, _ = _dump(consumer_dir, capsys, "--local")
    assert rc == 0
    block = data["composition"]
    assert block["scope"] == "local"
    assert block["sources"] == [{"namespace": "toy", "url": None, "ref": None,
                                 "path": "../toy-source", "subdir": None}]
    assert "borrowed_item_count" not in block
    assert block["local_item_count"] == len(data["items"])


def test_a_local_export_holds_the_items_it_held(consumer_dir, capsys):
    """The items are the project's own, as a composed export gives them."""
    _, whole, _ = _dump(consumer_dir, capsys)
    _, local, _ = _dump(consumer_dir, capsys, "--local")
    own = [it for it in whole["items"] if it["source"] is None]
    assert local["items"] == own and own


def test_a_local_export_does_not_read_a_source_that_is_not_there(
        consumer_dir, source_dir, capsys):
    """What the issue reported: a source that cannot be reached stopped an export
    that needed nothing from it."""
    shutil.rmtree(source_dir)
    assert _dump(consumer_dir, capsys)[0] != 0

    rc, data, err = _dump(consumer_dir, capsys, "--local")

    assert rc == 0, err
    assert data["items"] and all(it["source"] is None for it in data["items"])
