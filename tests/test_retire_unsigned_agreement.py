# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""SR-0313, SR-0314 — `tl migrate` retires the statuses that stood between
proposal and signature, and check does not read their retirement as a move.

Every item is born proposed and counts only once a person signs it (UR-0046), so
a status meaning "agreed" with no signature has nothing left to mean. The repair
finds such statuses by their place in the lifecycle, never by name (SR-0131),
moves what sits in them to proposed (or to ratified where the signatures already
stand), and signs nothing.
"""
from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

from throughline.cli import main as cli_main
from throughline.signatures import signatures
from throughline.storage import load_project, migrate_project, read_baseline
from throughline.validate import validate

# The lifecycle throughline shipped before 5.0.
_OLD = """[project]
name = "old"
format_version = 4

[status]
values = ["proposed", "draft", "deferred", "{agreed}", "ratified", "implemented",
          "verified", "suspect", "rejected", "deleted"]

[status.roles]
initial = "draft"
proposed = "proposed"
ratified = "ratified"
invalidated = "rejected"
suspect = "suspect"
tombstone = "deleted"

[transitions]
proposed    = ["draft", "{agreed}", "ratified", "deferred", "suspect", "rejected", "deleted"]
draft       = ["proposed", "{agreed}", "deferred", "suspect", "rejected", "deleted"]
deferred    = ["proposed", "draft", "{agreed}", "suspect", "rejected", "deleted"]
{agreed}    = ["ratified", "implemented", "deferred", "suspect", "rejected", "deleted"]
ratified    = ["implemented", "suspect", "rejected", "deleted"]
implemented = ["verified", "suspect", "rejected", "deleted"]
verified    = ["implemented", "suspect", "deleted"]
suspect     = ["{agreed}", "ratified", "implemented", "verified", "rejected", "deleted"]
rejected    = ["draft", "deleted"]
"""


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _item(root: Path, uid: str, status: str, extra: str = "") -> None:
    folder = root / ("intents" if uid.startswith("INT") else "requirements")
    (folder / f"{uid}.yml").write_text(
        f"uid: {uid}\ntype: {'intent' if uid.startswith('INT') else 'requirement'}\n"
        f"status: {status}\ntitle: {uid}\ntext: Body of {uid}.\n{extra}",
        encoding="utf-8")


def _old_graph(tmp_path: Path, agreed: str = "approved") -> Path:
    root = tmp_path / "old"
    for folder, prefix in (("intents", "INT"), ("requirements", "FR")):
        (root / folder).mkdir(parents=True)
        (root / folder / ".register.yml").write_text(
            f"prefix: {prefix}\ndigits: 4\n", encoding="utf-8")
    (root / "throughline.toml").write_text(_OLD.format(agreed=agreed), encoding="utf-8")
    grounded = "links:\n- target: INT-0001\n  type: derives_from\n"
    _item(root, "INT-0001", agreed)
    _item(root, "FR-0001", agreed, grounded)
    _item(root, "FR-0002", "draft", grounded)
    _item(root, "FR-0003", "implemented", grounded)
    _item(root, "FR-0004", agreed, grounded + "attrs:\n  ratified_by: alice\n")
    return root


def _statuses(root: Path) -> dict[str, str]:
    return {i.uid: i.status for i in load_project(root).items()}


def test_approved_and_draft_items_become_proposed_and_nothing_is_signed(tmp_path):
    root = _old_graph(tmp_path)
    # Who signed, compared by name: the record backfill completes alice's legacy
    # record with its stamp, as it always has.
    before = {i.uid: [e.get("by") for e in signatures(i)]
              for i in load_project(root).items()}
    result = migrate_project(root)
    assert set(result.retired_statuses) == {"draft", "approved"}
    assert result.moved == {"INT-0001": "approved -> proposed",
                            "FR-0001": "approved -> proposed",
                            "FR-0002": "draft -> proposed",
                            "FR-0004": "approved -> ratified"}
    assert _statuses(root) == {"INT-0001": "proposed", "FR-0001": "proposed",
                               "FR-0002": "proposed", "FR-0003": "implemented",
                               "FR-0004": "ratified"}
    after = {i.uid: [e.get("by") for e in signatures(i)]
             for i in load_project(root).items()}
    assert after == before                       # no signature added or removed


def test_the_configuration_loses_the_retired_statuses(tmp_path):
    root = _old_graph(tmp_path)
    migrate_project(root)
    config = tomllib.loads((root / "throughline.toml").read_text(encoding="utf-8"))
    assert config["status"]["values"] == ["proposed", "deferred", "ratified",
                                          "implemented", "verified", "suspect",
                                          "rejected", "deleted"]
    assert config["status"]["roles"]["initial"] == "proposed"
    transitions = config["transitions"]
    assert "approved" not in transitions and "draft" not in transitions
    assert transitions["proposed"] == ["ratified", "deferred", "suspect",
                                       "rejected", "deleted"]
    # A move into a retired status became a move into proposal.
    assert transitions["deferred"][0] == "proposed"
    assert transitions["rejected"] == ["proposed", "deleted"]
    assert "proposed" in transitions["suspect"]
    load_project(root)                           # and the schema still builds


def test_the_repair_is_idempotent(tmp_path, capsys):
    root = _old_graph(tmp_path)
    assert _cli(["-C", root, "migrate"]) == 0
    out = capsys.readouterr().out
    assert "retired draft, approved" in out and "FR-0002: draft -> proposed" in out
    text = (root / "throughline.toml").read_text(encoding="utf-8")
    assert _cli(["-C", root, "migrate"]) == 0
    assert "nothing to migrate" in capsys.readouterr().out
    assert (root / "throughline.toml").read_text(encoding="utf-8") == text


def test_the_status_is_found_by_its_place_not_its_name(tmp_path):
    """A project that called its unsigned agreement 'accepted' has the same limbo."""
    root = _old_graph(tmp_path, agreed="accepted")
    result = migrate_project(root)
    assert set(result.retired_statuses) == {"draft", "accepted"}
    assert _statuses(root)["FR-0001"] == "proposed"


def test_a_lifecycle_without_such_a_status_is_left_alone(tmp_path):
    root = tmp_path / "new"
    assert _cli(["-C", root, "init", "--bare"]) == 0
    text = (root / "throughline.toml").read_text(encoding="utf-8")
    result = migrate_project(root)
    assert result.retired_statuses == () and result.moved == {}
    assert (root / "throughline.toml").read_text(encoding="utf-8") == text


def test_check_does_not_read_a_retired_status_as_a_move(tmp_path):
    """SR-0314 — the commit that retires a status passes its own check."""
    root = _old_graph(tmp_path)
    git = lambda *a: subprocess.run(["git", "-C", str(root), *a],
                                    capture_output=True, check=True)
    git("init", "-q"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
    git("add", "-A"); git("commit", "-qm", "old lifecycle")
    migrate_project(root)
    project = load_project(root)
    baseline = read_baseline(project, ref="HEAD").statuses
    assert baseline["FR-0001"] == "approved"
    rules = {(f.uid, f.rule) for f in validate(project, baseline=baseline)}
    assert not any(rule == "bad-transition" for _, rule in rules)
    # A status still declared is judged as before.
    item = project.get("FR-0003")
    item.status = "proposed"                     # implemented -> proposed: illegal
    rules = {(f.uid, f.rule) for f in validate(project, baseline=baseline)}
    assert ("FR-0003", "bad-transition") in rules
