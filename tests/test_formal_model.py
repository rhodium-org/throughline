# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""The proved Lean model of grounding and the link guard agrees with the Python
that ships (NFR-0023).

`formal/` proves theorems about a model of `grounding_gap`, `is_unserved`,
`Index.with_edges` and `ungrounded_by` (SR-0211) and of suspect links (SR-0034).
A proof only speaks about the model, so this test feeds the same random graphs,
cycles and links to unknown targets included, to the model's executable and to
`throughline.grounding`, and requires every verdict to match. It also requires
every headline theorem to rest on Lean's standard axioms alone, which is how a
proof left as `sorry` is caught.

The Lean toolchain is not a Python dependency. Without a built `formal/` the test
skips locally; with TL_FORMAL=1 (as the `formal` CI job sets) it fails instead.
"""
from __future__ import annotations

import os
import random
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from throughline.graph import Index
from throughline.grounding import grounding_gap, is_unserved, ungrounded_by
from throughline.model import Item, Link

FORMAL = Path(__file__).resolve().parent.parent / "formal"
EXE = FORMAL / ".lake" / "build" / "bin" / "tl-formal"
REQUIRED = os.environ.get("TL_FORMAL") == "1"

ROOTS = {"intent", "non_goal"}
DELIVERY = {"intent"}
GROUND = {"implements", "derives_from", "verifies"}
KINDS = sorted(GROUND | {"relates", "assumes"})
STANDARD_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}

SCHEMA = SimpleNamespace(is_root=lambda it: it.type in ROOTS,
                         ground_link_types=GROUND, delivery_roots=DELIVERY)


def _need(path_ok: bool, what: str) -> None:
    if path_ok:
        return
    if REQUIRED:
        pytest.fail(f"{what} (TL_FORMAL=1 requires it; run `lake build` in formal/)")
    pytest.skip(f"{what}; run `lake build` in formal/ to include the formal model")


def _kind(rng: random.Random) -> str:
    """Mostly grounding links, so most cases have chains for a change to break."""
    return rng.choice(sorted(GROUND)) if rng.random() < 0.75 else rng.choice(KINDS)


def _random_case(rng: random.Random):
    n = rng.randint(1, 9)
    uids = [f"I{k}" for k in range(n)]
    items = [Item(uid=u, type=rng.choice(["intent", "non_goal", "req", "req", "req"]),
                  status="deleted" if rng.random() < 0.1 else "draft")
             for u in uids]
    targets = uids + ["GONE-1"]          # a link to an item not in the graph
    for it in items:
        for _ in range(rng.randint(0, 3)):
            it.links.append(Link(target=rng.choice(targets), type=_kind(rng)))
    src = rng.choice(items)
    existing = [(ln.target, ln.type) for ln in src.links]
    # Half the time take all of them, the change most likely to strand something.
    keep = 0.0 if rng.random() < 0.5 else 0.4
    remove = [p for p in existing if rng.random() >= keep]
    if rng.random() < 0.2:
        remove.append((rng.choice(targets), _kind(rng)))   # not there
    add = [(rng.choice(targets), _kind(rng))
           for _ in range(rng.choice([0, 0, 1, 2]))]
    return items, src.uid, remove, add


def _python(items, src, remove, add):
    before = Index.build(SimpleNamespace(items=lambda: items))
    after = before.with_edges(src, remove=remove, add=add)
    orphaned, unserved = ungrounded_by(SCHEMA, items, before, after)
    return {
        "grounded": {i.uid for i in items if grounding_gap(SCHEMA, before, i) is None},
        "unserved": {i.uid for i in items if is_unserved(SCHEMA, before, i)},
        "orphaned": set(orphaned),
        "newunserved": set(unserved),
    }


def _lean(items, src, remove, add):
    lines = [f"ground {k}" for k in sorted(GROUND)]
    for i in items:
        lines.append(f"item {i.uid} {int(i.type in ROOTS)} {int(i.type in DELIVERY)} "
                     f"{int(i.is_deleted)}")
        lines += [f"edge {i.uid} {ln.target} {ln.type}" for ln in i.links]
    lines.append(f"change {src}")
    lines += [f"remove {t} {k}" for t, k in remove]
    lines += [f"add {t} {k}" for t, k in add]
    proc = subprocess.run([str(EXE)], input="\n".join(lines), capture_output=True,
                          text=True, check=True)
    verdicts: dict[str, set[str]] = {k: set() for k in
                                     ("grounded", "unserved", "orphaned", "newunserved")}
    for line in proc.stdout.splitlines():
        kind, uid = line.split(" ", 1)
        verdicts[kind].add(uid)
    return verdicts


def test_cycle_and_dangling_target_agree():
    """A grounding cycle with no root, a self-link and a link to a missing item —
    the shapes a naive walk gets wrong."""
    _need(EXE.exists(), "formal model not built")
    items = [Item(uid="INT-1", type="intent"),
             Item(uid="R-1", type="req", links=[Link("INT-1", "implements")]),
             Item(uid="R-2", type="req", links=[Link("R-3", "derives_from"),
                                                Link("R-2", "derives_from")]),
             Item(uid="R-3", type="req", links=[Link("R-2", "derives_from"),
                                                Link("GONE-1", "implements")])]
    case = (items, "R-1", [("INT-1", "implements")], [])
    assert _lean(*case) == _python(*case)
    assert _python(*case)["orphaned"] == {"R-1"}


CASES = 2000      # about 200 of them strand an item, about 100 unserve a root


def test_random_graphs_agree():
    _need(EXE.exists(), "formal model not built")
    for seed in range(CASES):
        case = _random_case(random.Random(seed))
        assert _lean(*case) == _python(*case), f"seed {seed}"


def test_theorems_rest_on_standard_axioms_only():
    lake = shutil.which("lake")
    _need(lake is not None and EXE.exists(), "Lean toolchain or formal model missing")
    proc = subprocess.run([lake, "env", "lean", "Audit.lean"], cwd=FORMAL,
                          capture_output=True, text=True, check=True)
    reports = [ln for ln in proc.stdout.splitlines() if "depends on axioms" in ln
               or "does not depend on any axioms" in ln]
    expected = sum(1 for ln in (FORMAL / "Audit.lean").read_text().splitlines()
                   if ln.startswith("#print axioms"))
    assert len(reports) == expected, proc.stdout
    for line in reports:
        if "depends on axioms" not in line:
            continue
        named = line.split("[", 1)[1].rstrip("]").split(",")
        extra = {a.strip() for a in named} - STANDARD_AXIOMS
        assert not extra, line
