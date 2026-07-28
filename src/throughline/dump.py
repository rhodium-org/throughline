# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Canonical whole-project JSON dump (SR-0055).

throughline does not generate presentation or exchange formats (NG-0005); this
single documented JSON structure is the sanctioned interchange surface for
third-party tooling. It is a faithful projection of what the loader holds — the
schema (config), every register manifest, and every item (live and tombstoned),
with each item's typed links embedded. It is deterministic: no wall-clock field,
so two dumps of the same graph are byte-identical and diff cleanly.
"""
from __future__ import annotations

from .model import Project
from .storage import FORMAT_VERSION

DUMP_SCHEMA_VERSION = 1


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

    return {
        "throughline_dump": {
            "dump_schema_version": DUMP_SCHEMA_VERSION,
            "format_version": FORMAT_VERSION,
            "tool_version": tool_version,
        },
        "config": project.config,
        "registers": registers,
        "items": [it.to_dict() for it in items],
    }
