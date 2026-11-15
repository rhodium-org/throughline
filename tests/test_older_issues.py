# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Five behaviours asked for in older issues (SR-0341 to SR-0345)."""
from __future__ import annotations

from pathlib import Path

from throughline.cli import main as cli_main
from throughline.storage import load_project


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _demo(tmp_path: Path) -> Path:
    root = tmp_path / "g"
    assert _cli(["-C", root, "init", "--name", "t"]) == 0
    return root


def _files(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


# ------------------------------------------------------------------ SR-0341

def _add_pattern(root: Path, pattern: str) -> None:
    cfg = root / "throughline.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace(
        'paths = ["docs/*.md"]', f'paths = ["docs/*.md", "{pattern}"]'),
        encoding="utf-8")


def test_the_documents_gate_fails_where_a_configured_document_is_not_there(
        tmp_path, capsys):
    """SR-0341 (issue 57): a renamed or deleted document used to pass the gate,
    which then guarded nothing."""
    root = _demo(tmp_path)
    assert _cli(["-C", root, "docs", "--check"]) == 0
    _add_pattern(root, "docs/spec/README.md")
    capsys.readouterr()

    assert _cli(["-C", root, "docs", "--check"]) != 0

    assert "docs/spec/README.md" in capsys.readouterr().err


def test_tl_docs_refuses_the_same_way_and_writes_nothing(tmp_path, capsys):
    """SR-0341."""
    root = _demo(tmp_path)
    _add_pattern(root, "missing/*.md")
    doc = root / "docs" / "overview.md"
    doc.write_text(doc.read_text(encoding="utf-8")
                   + "\n<!-- tl:count item.type = 'requirement' -->\n<!-- tl:end -->\n",
                   encoding="utf-8")
    before = _files(root)
    capsys.readouterr()

    assert _cli(["-C", root, "docs"]) != 0

    assert "missing/*.md" in capsys.readouterr().err
    assert _files(root) == before


def test_a_pattern_that_matches_and_a_file_named_by_hand_are_as_they_were(
        tmp_path, capsys):
    """SR-0341: only a configured pattern that matches nothing is refused."""
    root = _demo(tmp_path)
    assert _cli(["-C", root, "docs", "--check"]) == 0
    _add_pattern(root, "missing/*.md")
    assert _cli(["-C", root, "docs", root / "docs" / "overview.md"]) == 0


def test_a_project_with_no_documents_configured_still_passes_the_gate(tmp_path):
    """SR-0341 leaves SR-0095's inert gate alone."""
    root = tmp_path / "bare"
    assert _cli(["-C", root, "init", "--name", "t", "--no-demo"]) == 0
    assert _cli(["-C", root, "docs", "--check"]) == 0


# ------------------------------------------------------------------ SR-0342

def test_a_ratify_run_passes_over_an_item_the_same_person_already_signed(
        tmp_path, capsys):
    """SR-0342 (issue 58): one already-signed item used to refuse the whole run."""
    root = _demo(tmp_path)
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    signed = (root / "requirements" / "REQ-0001.yml").read_bytes()
    capsys.readouterr()

    assert _cli(["-C", root, "ratify", "REQ-0001", "NFR-0001", "--by", "Ada"]) == 0

    out = capsys.readouterr()
    assert "REQ-0001 is already signed by Ada" in out.err
    assert "NFR-0001 ratified by Ada" in out.out
    assert (root / "requirements" / "REQ-0001.yml").read_bytes() == signed


def test_a_run_of_only_such_items_changes_nothing_and_says_so(tmp_path, capsys):
    """SR-0342."""
    root = _demo(tmp_path)
    assert _cli(["-C", root, "ratify", "REQ-0001", "NFR-0001", "--by", "Ada"]) == 0
    before = _files(root)
    capsys.readouterr()

    assert _cli(["-C", root, "ratify", "REQ-0001", "NFR-0001", "--by", "Ada"]) == 0

    assert "nothing was signed" in capsys.readouterr().err
    assert _files(root) == before


def test_every_other_refusal_still_refuses_the_whole_run(tmp_path, capsys):
    """SR-0342, SR-0199: an item that does not exist, or one another person
    signed, stops the run before anything is written."""
    root = _demo(tmp_path)
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    before = _files(root)
    assert _cli(["-C", root, "ratify", "REQ-0001", "NFR-0001", "REQ-9999",
                 "--by", "Ada"]) != 0
    assert _cli(["-C", root, "ratify", "REQ-0001", "NFR-0001", "--by", "Grace"]) != 0
    assert "nothing in this run was ratified" in capsys.readouterr().err
    assert _files(root) == before


def test_a_run_that_names_one_signed_item_is_refused_as_before(tmp_path, capsys):
    """SR-0342 speaks of a run that names several; SR-0148 still answers one."""
    root = _demo(tmp_path)
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada"]) != 0


# ------------------------------------------------------------------ SR-0343

def _link(root: Path, uid: str, target: str):
    return next(ln for ln in load_project(root).get(uid).links if ln.target == target)


def test_a_link_made_by_tl_new_is_stamped_as_tl_link_stamps_one(tmp_path):
    """SR-0343 (issue 72): a link made at creation used to carry no stamp, so a
    later change to its parent was never noticed."""
    from throughline.fingerprint import fingerprint
    root = _demo(tmp_path)
    assert _cli(["-C", root, "new", "REQ", "--ground", "INT-0001", "--ground-type",
                 "implements", "--title", "t", "--text", "x", "--no-interactive"]) == 0
    project = load_project(root)
    want = fingerprint(project.get("INT-0001"), project.schema)

    assert _link(root, "REQ-0002", "INT-0001").stamp == want
    # the same value tl link --stamp writes
    assert _cli(["-C", root, "link", "REQ-0001", "INT-0001", "--type", "implements",
                 "--stamp"]) == 0
    assert _link(root, "REQ-0001", "INT-0001").stamp == want


def test_a_link_made_by_a_new_operation_of_a_change_set_is_stamped(tmp_path):
    """SR-0343."""
    import json
    from throughline.fingerprint import fingerprint
    root = _demo(tmp_path)
    change = tmp_path / "set.json"
    change.write_text(json.dumps({"operations": [
        {"op": "new", "register": "REQ", "title": "t", "text": "x",
         "links": [{"target": "INT-0001", "type": "implements"}]}]}),
        encoding="utf-8")
    assert _cli(["-C", root, "apply", change]) == 0
    project = load_project(root)

    assert _link(root, "REQ-0002", "INT-0001").stamp == fingerprint(
        project.get("INT-0001"), project.schema)


def test_a_stamped_link_at_creation_goes_suspect_when_its_parent_changes(
        tmp_path, capsys):
    """SR-0343: what the stamp is for."""
    root = _demo(tmp_path)
    assert _cli(["-C", root, "new", "REQ", "--ground", "INT-0001", "--ground-type",
                 "implements", "--title", "t", "--text", "x", "--no-interactive"]) == 0
    assert _cli(["-C", root, "amend", "INT-0001", "--text", "changed"]) == 0
    capsys.readouterr()
    _cli(["-C", root, "check"])
    assert any("REQ-0002" in line and "suspect-link" in line
               for line in capsys.readouterr().out.splitlines())


# ------------------------------------------------------------------ SR-0344

def _findings(root: Path, capsys) -> list[str]:
    capsys.readouterr()
    _cli(["-C", root, "check"])
    return [line for line in capsys.readouterr().out.splitlines()
            if line.startswith("[")]


def _named(findings, uid, rule) -> bool:
    return any(uid in line and rule in line for line in findings)


def test_a_delivery_root_served_only_by_a_rejected_item_is_unserved(tmp_path, capsys):
    """SR-0344 (issue 70): retiring the only thing that answered a root used to
    leave it reported as served."""
    root = _demo(tmp_path)
    assert not _named(_findings(root, capsys), "INT-0001", "unserved-root")
    for uid in ("REQ-0001", "NFR-0001"):
        assert _cli(["-C", root, "status", uid, "rejected"]) == 0

    assert _named(_findings(root, capsys), "INT-0001", "unserved-root")


def test_one_live_item_still_serves_the_root(tmp_path, capsys):
    """SR-0344: only a root that nothing live grounds into is unserved."""
    root = _demo(tmp_path)
    assert _cli(["-C", root, "status", "NFR-0001", "rejected"]) == 0

    assert not _named(_findings(root, capsys), "INT-0001", "unserved-root")


def test_an_item_grounded_only_through_a_rejected_item_is_an_orphan(tmp_path, capsys):
    """SR-0344."""
    root = _demo(tmp_path)
    assert not _named(_findings(root, capsys), "TEST-0001", "orphan")
    assert _cli(["-C", root, "status", "REQ-0001", "rejected"]) == 0

    found = _findings(root, capsys)

    assert _named(found, "TEST-0001", "orphan")
    assert not _named(found, "NFR-0001", "orphan")      # its own chain is live


def test_a_second_chain_that_is_live_keeps_the_item_grounded(tmp_path, capsys):
    """SR-0344: every chain must pass through a dead item for it to be an orphan."""
    root = _demo(tmp_path)
    assert _cli(["-C", root, "link", "TEST-0001", "NFR-0001", "--type", "verifies"]) == 0
    assert _cli(["-C", root, "status", "REQ-0001", "rejected"]) == 0

    assert not _named(_findings(root, capsys), "TEST-0001", "orphan")


def test_a_tombstone_that_stands_grounds_nothing_and_a_proposed_one_still_does(
        tmp_path, capsys):
    """SR-0344, SR-0267: an unsigned deletion changes nothing downstream."""
    from throughline.grounding import retired
    root = _demo(tmp_path)
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    assert _cli(["-C", root, "delete", "REQ-0001", "--reason", "gone"]) == 0
    project = load_project(root)
    assert not retired(project.get("REQ-0001"), project.schema)
    assert not _named(_findings(root, capsys), "TEST-0001", "orphan")

    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    project = load_project(root)
    assert retired(project.get("REQ-0001"), project.schema)
    from throughline.graph import Index
    from throughline.grounding import grounding_gap
    assert grounding_gap(project.schema, Index.build(project),
                         project.get("TEST-0001")) is not None
