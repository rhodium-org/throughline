# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""A change set names an item it made earlier by its label wherever an operation
names an item, a link or unlink's target included (SR-0352).

Found laying down a site graph that composes the sitemap standard: `tl apply`
resolved a link's ``$label`` target to the UID the set had just created, then
looked for it in the union as it stood before the set began, and refused the set
as naming an item that does not exist. Every test here runs over the same graph
twice, once on its own and once composing a source, because the plain project
already worked and the composed one is where the set's own items went missing.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from throughline import build_dump
from throughline.cli import main as tl_main
from throughline.storage import load_project


def _without_sources(root: Path) -> Path:
    """The consumer without its source: the same graph, judged over itself."""
    toml = root / "throughline.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace(
        '[[sources]]\nnamespace = "toy"\npath = "../toy-source"\n', ""),
        encoding="utf-8")
    sr = root / "system-requirements" / "SR-0001.yml"
    sr.write_text(sr.read_text(encoding="utf-8").replace(
        "- target: toy:SR-0001\n  type: relates\n", ""), encoding="utf-8")
    assert "namespace = \"toy\"" not in toml.read_text(encoding="utf-8")
    return root


@pytest.fixture(params=["plain", "composed"])
def root(request, consumer_dir: Path) -> Path:
    return _without_sources(consumer_dir) if request.param == "plain" else consumer_dir


def _rev(root: Path, uid: str) -> str:
    dump = build_dump(load_project(root), "9.9.9")
    return next(i["revision"] for i in dump["items"] if i["uid"] == uid)


def _new(label: str, *links: tuple[str, str]) -> dict:
    return {"op": "new", "register": "SR", "type": "system_requirement",
            "title": f"item {label}", "text": f"The item {label}.",
            "attrs": {"priority": "must"}, "label": label,
            "links": [{"target": t, "type": k} for t, k in links]}


def _apply(root: Path, tmp_path: Path, ops: list, *flags: str, capsys) -> tuple[int, dict]:
    path = tmp_path / "set.json"
    path.write_text(json.dumps(ops), encoding="utf-8")
    capsys.readouterr()
    rc = tl_main(["-C", str(root), "apply", str(path), "--format", "json", *flags])
    return rc, json.loads(capsys.readouterr().out)


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _links(root: Path, uid: str) -> list[tuple[str, str]]:
    return [(l.target, l.type) for l in load_project(root).get(uid).links]


def test_a_link_targets_an_item_made_earlier_in_the_set(root, tmp_path, capsys):
    ops = [_new("a", ("INT-0001", "derives_from")),
           {"op": "link", "uid": "SR-0001", "revision": _rev(root, "SR-0001"),
            "target": "$a", "type": "relates"}]
    rc, out = _apply(root, tmp_path, ops, capsys=capsys)
    assert rc == 0, out
    made = out["created"][0]
    assert ("linked", f"SR-0001 --relates--> {made}") == (
        out["operations"][1]["outcome"], out["operations"][1]["detail"])
    assert (made, "relates") in _links(root, "SR-0001")


def test_an_unlink_targets_an_item_made_earlier_in_the_set(root, tmp_path, capsys):
    rev = _rev(root, "SR-0001")
    ops = [_new("a", ("INT-0001", "derives_from")),
           {"op": "link", "uid": "SR-0001", "revision": rev,
            "target": "$a", "type": "relates"},
           {"op": "unlink", "uid": "SR-0001", "revision": rev,
            "target": "$a", "type": "relates"}]
    rc, out = _apply(root, tmp_path, ops, capsys=capsys)
    assert rc == 0, out
    made = out["created"][0]
    assert out["operations"][2]["outcome"] == "unlinked"
    assert (made, "relates") not in _links(root, "SR-0001")


def test_an_unlink_to_a_label_is_judged_over_the_graph_the_set_has_built(
        root, tmp_path, capsys):
    """The item made earlier is in the graph the unlink is judged over, so an
    unlink that would leave an item of the set reaching no root is refused here
    as it is anywhere; the frozen union let it through."""
    ops = [_new("a", ("INT-0001", "derives_from")),
           _new("b", ("$a", "derives_from")),
           {"op": "unlink", "uid": "$b", "target": "$a", "type": "derives_from"}]
    before = _tree(root)
    rc, out = _apply(root, tmp_path, ops, capsys=capsys)
    assert rc == 1
    assert out["problems"][0].startswith("#3 unlink $b: ")
    assert "reaching no root" in out["problems"][0]
    assert _tree(root) == before


def test_a_new_items_link_to_a_label_is_stamped(root, tmp_path, capsys):
    """The new item's link to an item made earlier finds it, so it carries that
    item's fingerprint, in a composed project as in a plain one (SR-0343)."""
    ops = [_new("a", ("INT-0001", "derives_from")),
           _new("b", ("$a", "derives_from"))]
    rc, out = _apply(root, tmp_path, ops, capsys=capsys)
    assert rc == 0, out
    b = load_project(root).get(out["created"][1])
    assert [l.stamp is not None for l in b.links] == [True]


def test_a_label_made_later_in_the_set_is_refused_naming_the_operation(
        root, tmp_path, capsys):
    ops = [{"op": "link", "uid": "SR-0001", "revision": _rev(root, "SR-0001"),
            "target": "$a", "type": "relates"},
           _new("a", ("INT-0001", "derives_from"))]
    before = _tree(root)
    rc, out = _apply(root, tmp_path, ops, capsys=capsys)
    assert rc == 1
    assert out["problems"] == [
        "#1 link: $a names no item created earlier in this set"]
    assert _tree(root) == before


def test_a_dry_run_reports_what_the_real_run_does(root, tmp_path, capsys):
    rev = _rev(root, "SR-0001")
    ops = [_new("a", ("INT-0001", "derives_from")),
           _new("b", ("$a", "derives_from"), ("INT-0001", "derives_from")),
           {"op": "link", "uid": "SR-0001", "revision": rev,
            "target": "$a", "type": "relates"},
           {"op": "link", "uid": "SR-0001", "revision": rev,
            "target": "$b", "type": "relates"},
           {"op": "unlink", "uid": "$b", "target": "$a", "type": "derives_from"}]
    before = _tree(root)
    rc, dry = _apply(root, tmp_path, ops, "--dry-run", capsys=capsys)
    assert rc == 0, dry
    assert _tree(root) == before
    rc, real = _apply(root, tmp_path, ops, capsys=capsys)
    assert rc == 0, real
    assert dry["dry_run"] is True and real["dry_run"] is False
    for key in ("operations", "files", "created", "changed"):
        assert dry[key] == real[key]
    assert _tree(root) != before
