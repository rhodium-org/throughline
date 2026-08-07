# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""What ratification does to an item's status is declared per project (SR-0172).

The default advances the item to the ratified status, as it always has. A project
whose statuses track *progress* rather than *agreement* — a delivery graph of
backlog / in_progress / done — binds the ratified role to a workflow state, and
there advancing a finished item on sign-off would fabricate a history. Such a
project turns the move off; the accountability record is written identically either
way.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from throughline.grounding import GroundingError, ratify
from throughline.model import Item, Link, Project, Register
from throughline.schema import SchemaError

# A workflow vocabulary in which `ratified` is bound to a mid-pipeline state, and
# `done` cannot legally move back to it. This is the shape that could not be
# ratified at all before SR-0172.
_WORKFLOW = {
    "status": {
        "values": ["proposed", "backlog", "in_progress", "done", "cancelled",
                   "deleted"],
        "roles": {
            "initial": "proposed",
            "proposed": "proposed",
            "ratified": "backlog",
            "invalidated": "cancelled",
            "suspect": "cancelled",
            "tombstone": "deleted",
        },
    },
    "transitions": {
        "proposed": ["backlog", "cancelled", "deleted"],
        "backlog": ["in_progress", "cancelled", "deleted"],
        "in_progress": ["done", "cancelled", "deleted"],
        "done": ["deleted"],
        "cancelled": ["deleted"],
    },
}


def _project(*items: Item, config: dict | None = None) -> Project:
    p = Project(path=Path("/tmp/none"), config=config or {})
    reg = Register(prefix="X", digits=4, reserved=[])
    for it in items:
        it._register_prefix = "X"
        reg.items[it.uid] = it
    p.registers["X"] = reg
    return p


def _graph(config, status="proposed"):
    """A grounded item under `config`, sitting at `status`."""
    root = Item(uid="X-0001", type="intent", status=status)
    child = Item(uid="X-0002", type="task", status=status,
                 links=[Link(target="X-0001", type="derives_from")])
    return _project(root, child, config=config)


def _in_place(**over):
    cfg = {**_WORKFLOW, "ratify": {"moves_status": False}}
    cfg.update(over)
    return cfg


# --------------------------------------------------------------------------
# The default is unchanged
# --------------------------------------------------------------------------


def test_the_default_still_advances_the_item():
    """A project that declares nothing must behave exactly as before, or every
    existing graph's `ratified` status changes meaning on upgrade."""
    p = _graph(_WORKFLOW)
    item = ratify(p, "X-0002", by="j.doe")
    assert item.status == "backlog"
    assert item.attrs["ratified_by"] == "j.doe"


def test_declaring_the_default_explicitly_is_the_same_thing():
    p = _graph({**_WORKFLOW, "ratify": {"moves_status": True}})
    assert ratify(p, "X-0002", by="j.doe").status == "backlog"


def test_the_default_still_refuses_an_item_that_cannot_reach_ratified():
    """The behaviour SR-0172 makes optional, not the behaviour it removes. Under
    the default an illegal transition is still refused rather than forced."""
    p = _graph(_WORKFLOW, status="done")
    with pytest.raises(GroundingError):
        ratify(p, "X-0002", by="j.doe")


# --------------------------------------------------------------------------
# Recording the sign-off in place
# --------------------------------------------------------------------------


def test_in_place_records_accountability_without_moving_the_item():
    p = _graph(_in_place())
    item = ratify(p, "X-0002", by="j.doe")
    assert item.status == "proposed"          # exactly where it was
    assert item.attrs["ratified_by"] == "j.doe"
    assert item.attrs["ratified_fingerprint"].startswith("sha256:")


def test_in_place_can_ratify_an_item_that_has_overshot():
    """The defect this closes. A finished task was unratifiable, because the sign-off
    forced a move its transition table forbids — so the items whose work was
    complete were exactly the ones that could never be accepted."""
    p = _graph(_in_place(), status="done")
    item = ratify(p, "X-0002", by="j.doe")
    assert item.status == "done"
    assert item.attrs["ratified_by"] == "j.doe"


def test_in_place_still_refuses_an_ungrounded_item():
    """Turning off the status move must not turn off what may be signed off."""
    orphan = Item(uid="X-0003", type="task", status="proposed")
    p = _project(orphan, config=_in_place())
    with pytest.raises(GroundingError):
        ratify(p, "X-0003", by="j.doe")


def test_in_place_still_refuses_an_ambiguous_item():
    root = Item(uid="X-0001", type="intent", status="proposed")
    child = Item(uid="X-0002", type="task", status="proposed",
                 attrs={"ambiguous": True},
                 links=[Link(target="X-0001", type="derives_from")])
    p = _project(root, child, config=_in_place())
    with pytest.raises(GroundingError):
        ratify(p, "X-0002", by="j.doe")


# --------------------------------------------------------------------------
# "Already ratified" has to be read from the record, not the status
# --------------------------------------------------------------------------


def test_in_place_refuses_a_second_ratify_of_unchanged_content():
    """SR-0148 still holds. With the status no longer moving, the record is the only
    witness that a sign-off happened — so the guard reads that instead."""
    p = _graph(_in_place())
    ratify(p, "X-0002", by="alice")
    with pytest.raises(GroundingError, match="already ratified by alice"):
        ratify(p, "X-0002", by="bob")
    assert p.get("X-0002").attrs["ratified_by"] == "alice"


def test_in_place_allows_re_ratification_after_the_content_moves():
    p = _graph(_in_place())
    ratify(p, "X-0002", by="alice")
    p.get("X-0002").text = "reworded, so nobody has accepted these words"
    item = ratify(p, "X-0002", by="bob")
    assert item.attrs["ratified_by"] == "bob"
    assert item.status == "proposed"


# --------------------------------------------------------------------------
# The declaration itself
# --------------------------------------------------------------------------


def test_a_non_boolean_is_a_configuration_error():
    """Not coerced. "no" and "off" are both truthy, so a truthiness read would
    silently mean the opposite of what was written."""
    for bad in ("false", "no", 0, 1):
        with pytest.raises(SchemaError, match="moves_status"):
            _graph({**_WORKFLOW, "ratify": {"moves_status": bad}}).schema


def test_an_unknown_key_is_a_configuration_error():
    with pytest.raises(SchemaError, match="moves_status"):
        _graph({**_WORKFLOW, "ratify": {"move_status": False}}).schema


def test_a_non_table_ratify_section_is_a_configuration_error():
    with pytest.raises(SchemaError, match=r"\[ratify\]"):
        _graph({**_WORKFLOW, "ratify": "in_place"}).schema
