# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Canonical whole-project JSON dump (SR-0055).

throughline does not generate presentation or exchange formats (NG-0005); this
single documented JSON structure is the sanctioned interchange surface for
third-party tooling. It is a faithful projection of what the loader holds — the
schema (config), every register manifest, and every item (live and tombstoned),
with each item's typed links embedded. It is deterministic: no wall-clock field,
so two dumps of the same graph are byte-identical and diff cleanly.

Each item carries ``fingerprint``: the Tool's fingerprint of its normative
content as it stands (SR-0251), under this documented key, so a reader working
from the dump can quote back which version of an item it read — what a change
set hands to ``tl apply`` (SR-0252). It is computed here by the one fingerprint
rule and nowhere else, so a tool built on the dump never hashes an item itself.

Each item also carries ``revision``: a digest of its whole stored record, so it
moves on any change at all — a title, a rationale, a link, a status — where the
fingerprint, which ratification is taken over, moves only on normative content.
A change set quotes the revision (SR-0252), so an operation drafted against an
item that has since changed in any way is refused rather than written over it.
"""
from __future__ import annotations

from .fingerprint import fingerprint, revision
from .model import Project
from .storage import FORMAT_VERSION

# 2 since format 4 (SR-0254, SR-0263): an item carries its signatures as a
# top-level `ratifications` list and its withdrawals as `withdrawals`, and no
# `ratified_*` or `withdrawn_*` attribute. A reader of a dump at 1 found the
# record under `attrs`.
DUMP_SCHEMA_VERSION = 2


def build_dump(project: Project, tool_version: str) -> dict:
    """Build the canonical dump dict for ``project``.

    Keys are emitted in a stable order and every collection is sorted by a
    stable key, so serializing the result yields a reproducible document.
    """
    registers = []
    for prefix in sorted(project.registers):
        reg = project.registers[prefix]
        entry = reg.manifest_dict()
        entry["item_count"] = len(reg.items)
        registers.append(entry)

    items = sorted(project.items(), key=lambda it: it.uid)
    schema = project.schema

    def entry(it) -> dict:
        d = it.to_dict()
        d["fingerprint"] = fingerprint(it, schema)
        d["revision"] = revision(it)
        return d

    out = {
        "throughline_dump": {
            "dump_schema_version": DUMP_SCHEMA_VERSION,
            "format_version": FORMAT_VERSION,
            "tool_version": tool_version,
        },
        "config": project.config,
        "registers": registers,
        "items": [entry(it) for it in items],
    }
    # A project that takes its schema from a source (SR-0275) says so, with the
    # schema as formed and every declaration it inherited (SR-0279). `config`
    # stays the project's own file. Absent for every other project, whose dump is
    # unchanged.
    inheritance = project.inheritance
    if inheritance is not None:
        out["schema_source"] = {
            "namespace": inheritance.namespace,
            "formed": project.formed_config,
            "inherited": sorted(list(p) for p in inheritance.paths),
        }
    return out
