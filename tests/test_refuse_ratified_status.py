"""Only `tl ratify` puts an item in the status bound to the ratified role
(SR-0318): `tl new` and `tl status` refuse it, naming `tl ratify`, and write
nothing; the refusal follows the role, not the word."""

from __future__ import annotations

from pathlib import Path

from throughline.cli import main as _cli
from throughline.storage import load_project


def _init(tmp_path: Path, *extra: str) -> Path:
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--name", "t", *extra]) == 0
    return root


def test_tl_new_refuses_the_ratified_status_and_writes_nothing(tmp_path, capsys):
    root = _init(tmp_path)
    before = sorted(p.name for p in (root / "requirements").iterdir())
    assert _cli(["-C", str(root), "new", "REQ", "--title", "signed by nobody",
                 "--status", "ratified", "--ground", "INT-0001",
                 "--no-interactive"]) == 2
    assert "tl ratify" in capsys.readouterr().err
    assert sorted(p.name for p in (root / "requirements").iterdir()) == before


def test_tl_status_refuses_the_ratified_status(tmp_path, capsys):
    root = _init(tmp_path)
    assert load_project(root).get("REQ-0001").status == "proposed"
    assert _cli(["-C", str(root), "status", "REQ-0001", "ratified"]) == 2
    assert "tl ratify" in capsys.readouterr().err
    assert load_project(root).get("REQ-0001").status == "proposed"


def test_the_refusal_follows_the_role_not_the_word(tmp_path):
    """A project that binds the ratified role to its own word is refused that
    word, and may still name a status called 'ratified' that plays no role."""
    root = _init(tmp_path, "--bare")
    cfg = root / "throughline.toml"
    text = cfg.read_text(encoding="utf-8")
    assert 'ratified = "ratified"' in text and 'values = ["proposed",' in text
    cfg.write_text(text.replace('ratified = "ratified"', 'ratified = "accepted"')
                   .replace('values = ["proposed",', 'values = ["accepted", "proposed",'),
                   encoding="utf-8")
    assert _cli(["-C", str(root), "register", "new", "INT", "vision"]) == 0
    assert _cli(["-C", str(root), "new", "INT", "--type", "intent", "--title", "why",
                 "--status", "accepted"]) == 2
    assert _cli(["-C", str(root), "new", "INT", "--type", "intent", "--title", "why",
                 "--status", "ratified"]) == 0


def test_tl_ratify_still_moves_a_proposed_item_to_ratified(tmp_path):
    root = _init(tmp_path)
    assert _cli(["-C", str(root), "ratify", "REQ-0001", "--by", "Ada Lovelace"]) == 0
    item = load_project(root).get("REQ-0001")
    assert item.status == "ratified"
    assert item.ratifications[-1]["by"] == "Ada Lovelace"
