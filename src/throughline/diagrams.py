# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Mermaid pictures of a project's model and lifecycle (SR-0086).

Text a caller renders where it likes — a terminal, a document, a browser — so they
live here rather than in the command line that happens to print them (SR-0224).
"""
from __future__ import annotations


def diagram_types(index) -> str | None:
    """A flowchart of the type model: item types as nodes, one labelled edge per
    (source type, link type, target type) the graph actually shows. Edges to
    external or unknown targets are left out. ``None`` when the graph has none."""
    edges = sorted({(s, lt, t) for (s, lt, t) in index.link_shape() if t is not None})
    if not edges:
        return None
    return "\n".join(["flowchart LR"]
                      + [f"    {s} -->|{lt}| {t}" for s, lt, t in edges])


def diagram_transitions(schema) -> str | None:
    """A state diagram of the declared status lifecycle. ``None`` when the project
    declares no transitions."""
    if not schema.transitions:
        return None
    lines = ["stateDiagram-v2"]
    for frm in sorted(schema.transitions):
        for to in sorted(schema.transitions[frm]):
            lines.append(f"    {frm} --> {to}")
    return "\n".join(lines)
