# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""SR-0193: one safe loader for every YAML read, the C one where PyYAML has it."""
from __future__ import annotations

import yaml
import pytest

from throughline import storage
from throughline.cli import main as cli


def _dump(root) -> str:
    """The project as `tl dump` prints it — the whole parsed graph as one string."""
    import io
    from contextlib import redirect_stdout

    out = io.StringIO()
    with redirect_stdout(out):
        code = cli(["-C", str(root), "dump"])
    assert code == 0
    return out.getvalue()


def test_the_c_loader_is_used_when_libyaml_is_present():
    if yaml.__with_libyaml__:
        assert storage._YAML_LOADER is yaml.CSafeLoader
    else:  # pragma: no cover - depends on how PyYAML was built
        assert storage._YAML_LOADER is yaml.SafeLoader


def test_both_loaders_read_a_project_identically(tmp_path, monkeypatch):
    root = tmp_path / "p"
    root.mkdir()
    assert cli(["-C", str(root), "init", "--name", "loader", "--no-demo"]) == 0
    assert cli(["-C", str(root), "new", "INT", "--type", "intent", "--origin", "human",
                "--title", "Why", "--no-interactive"]) == 0
    assert cli(["-C", str(root), "new", "REQ", "--type", "requirement", "--origin", "human",
                "--ground", "INT-0001", "--ground-type", "derives_from",
                "--title", "A title with: a colon, \"quotes\" and — a dash",
                "--text", "Two\nlines", "--no-interactive"]) == 0
    fast = _dump(root)
    monkeypatch.setattr(storage, "_YAML_LOADER", yaml.SafeLoader)
    slow = _dump(root)
    assert fast == slow and '"REQ-0001"' in fast


@pytest.mark.parametrize("loader", [yaml.SafeLoader] + ([yaml.CSafeLoader] if yaml.__with_libyaml__ else []))
def test_a_python_object_tag_is_refused_by_either_loader(loader, monkeypatch):
    monkeypatch.setattr(storage, "_YAML_LOADER", loader)
    with pytest.raises(yaml.YAMLError):
        storage._load_yaml("uid: !!python/object/apply:os.system ['echo owned']\n")
