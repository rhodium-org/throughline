# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""SR-0224 to SR-0228 — the names a program embedding the Tool may rely on.

The browser editor drives the command line in process and reads what it prints, and
imports private names where printed text will not do. These tests hold the interface
that replaces that: the operations are reachable by name, the command line is one of
their callers rather than their home, the work only a host can do is a parameter,
the whole surface runs with no subprocess, no socket and no terminal, and the list
of names is documented and cannot drift.
"""
from __future__ import annotations

import ast
import re
import socket
import subprocess
import sys
from pathlib import Path

import pytest

import throughline
from throughline import (
    Index,
    amend_item,
    birth_item,
    build_dump,
    clarify,
    default_ratifier,
    flag,
    init_project,
    inject_text,
    load_project,
    migrate_project,
    parse_attrs,
    ratify,
    validate,
    withdraw,
    write_item,
)

SRC = Path(throughline.__file__).parent
DOC = SRC.parent.parent / "docs" / "referenced-resource" / "10_library_api.md"


def _documented_names() -> set[str]:
    """The names the published list offers, read from the document itself."""
    body = DOC.read_text(encoding="utf-8")
    names = set()
    for line in body.splitlines():
        if line.startswith("`") or " · " in line:
            names.update(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", line))
    return names



def _rooted(root: Path):
    """A scaffolded project holding an intent for other items to ground against."""
    init_project(root, demo=False)
    project = load_project(root)
    intent = birth_item(project.schema, project.registers["INT"], "INT-0001",
                        item_type="intent", title="Why", text="The why.")
    project.registers["INT"].items[intent.uid] = intent
    write_item(intent, project.registers["INT"])
    return load_project(root)


# ------------------------------------------------- SR-0228: the list is the surface

def test_the_exported_names_are_the_documented_ones():
    exported = set(throughline.__all__)
    documented = _documented_names()
    assert exported - documented == set(), "exported but undocumented"
    assert documented - exported == set(), "documented but not exported"


def test_every_exported_name_exists():
    assert [n for n in throughline.__all__ if not hasattr(throughline, n)] == []


# ------------------------------- SR-0225: the command line is a caller, not a home

def test_no_module_of_the_package_imports_the_command_line():
    """An operation living in the command-line module is one no other program can
    reach without importing a command line (SR-0225)."""
    offenders = []
    for path in sorted(SRC.glob("*.py")):
        if path.name == "cli.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("cli"):
                offenders.append(f"{path.name}:{node.lineno}")
            if isinstance(node, ast.Import):
                offenders += [f"{path.name}:{node.lineno}" for a in node.names
                              if a.name.endswith("throughline.cli")]
    assert offenders == []


def test_the_operations_are_reachable_without_the_command_line(tmp_path, monkeypatch):
    """The whole round trip — create, amend, flag, clarify, ratify — with the
    command-line module never imported."""
    monkeypatch.delitem(sys.modules, "throughline.cli", raising=False)
    root = tmp_path / "proj"
    project = _rooted(root)
    reg = project.registers["REQ"]
    schema = project.schema
    item = birth_item(schema, reg, "REQ-0001", item_type="requirement",
                      title="Fast", text="The Tool shall be fast.", origin="ai",
                      attrs=parse_attrs(schema, "requirement", ["priority=must"],
                                        command="new"))
    item.links.append(throughline.Link(target="INT-0001", type="derives_from"))
    reg.items[item.uid] = item
    write_item(item, reg)

    project = load_project(root)
    amend_item(project, "REQ-0001", text="The Tool shall answer within 200 ms.")
    write_item(project.get("REQ-0001"), project.register_of("REQ-0001"))

    project = load_project(root)
    flag(project, "REQ-0001", by="Scout", reason="'fast' is not measurable")
    write_item(project.get("REQ-0001"), project.register_of("REQ-0001"))
    assert any(f.rule == "ambiguous" for f in validate(load_project(root)))

    project = load_project(root)
    clarify(project, "REQ-0001", by="Ada", reason="reworded to 200 ms")
    write_item(project.get("REQ-0001"), project.register_of("REQ-0001"))

    project = load_project(root)
    ratify(project, "REQ-0001", by="Ada", index=Index.build(project))
    write_item(project.get("REQ-0001"), project.register_of("REQ-0001"))
    assert load_project(root).get("REQ-0001").attrs["ratified_by"] == "Ada"
    assert "throughline.cli" not in sys.modules


# --------------------------------------- SR-0226: the host's work is a parameter

def test_the_signing_identity_can_be_supplied_by_the_caller(tmp_path):
    assert default_ratifier(tmp_path, identity=lambda _p: ("Ada Lovelace", None)) \
        == "Ada Lovelace"


# ------------------- SR-0227: no subprocess, no socket, no terminal is needed

@pytest.fixture
def no_host(monkeypatch):
    """A caller with none of the three — what a browser runtime has."""
    def refuse(*_a, **_k):
        # What a browser host does: the call is simply not available. Code that
        # copes with its absence passes; code that needs it does not.
        raise OSError("this host has no subprocess or socket")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(sys.stderr, "isatty", lambda: False, raising=False)


def test_the_surface_works_with_no_subprocess_socket_or_terminal(tmp_path, no_host):
    root = tmp_path / "proj"
    project = _rooted(root)
    reg = project.registers["REQ"]
    item = birth_item(project.schema, reg, "REQ-0001", item_type="requirement",
                      title="R", text="The Tool shall do R.", origin="ai")
    item.links.append(throughline.Link(target="INT-0001", type="derives_from"))
    reg.items[item.uid] = item
    write_item(item, reg)

    project = load_project(root)
    assert isinstance(validate(project), list)
    assert build_dump(project, throughline.__version__)["items"]
    assert inject_text(project, "nothing to inject") == "nothing to inject"
    migrate_project(root)

    project = load_project(root)
    ratify(project, "REQ-0001", by="Ada", index=Index.build(project))
    write_item(project.get("REQ-0001"), project.register_of("REQ-0001"))
    project = load_project(root)
    withdraw(project, ["REQ-0001"], by="Ada", reason="signed in error")
    write_item(project.get("REQ-0001"), project.register_of("REQ-0001"))
    assert load_project(root).get("REQ-0001").attrs["withdrawn_by"] == "Ada"


def test_the_default_identity_survives_a_host_with_no_subprocess(tmp_path, no_host):
    """`git_identity` is the default of SR-0226's parameter, so it must fail soft
    rather than raise where there is no subprocess to run git in."""
    assert isinstance(default_ratifier(tmp_path), str)
