# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the composition engine (union.py, sources.py)."""
from __future__ import annotations

from pathlib import Path

import pytest
from throughline.model import Item, Link, Project, Register
from throughline.validate import validate

from throughline.sources import SourceError, parse_sources
from throughline.union import ComposeError, build_union, translate_finding

# A permissive consumer schema so these unit tests exercise the merge, not the
# consumer's own type rules (those are covered by the on-disk fixtures). Permissive
# in what it admits, not in what it leaves unsaid: core requires a project to state
# the vocabularies its items are validated against (throughline SR-0185), and these
# tests assert a clean validate, so the schema declares exactly what the fixtures
# below use.
_SCHEMA = {
    "project": {"name": "c", "format_version": 2},
    "grounding": {"root_types": ["intent"], "delivery_roots": ["intent"],
                  "ground_link_types": ["derives_from", "implements"]},
    "status": {"values": ["approved"]},
    "links": {"types": ["derives_from", "implements", "relates"]},
}


def _project(config: dict, regs: dict[str, list[Item]]) -> Project:
    p = Project(path=Path("."), config=config)
    for prefix, items in regs.items():
        reg = Register(prefix=prefix)
        for it in items:
            it._register_prefix = prefix
            reg.items[it.uid] = it
        p.registers[prefix] = reg
    return p


def _source() -> Project:
    intent = Item(uid="INT-0001", type="intent", status="approved", normative=False)
    sr = Item(uid="SR-0001", type="system_requirement", status="approved",
              links=[Link(target="INT-0001", type="derives_from")])
    return _project({"project": {"name": "toy", "format_version": 2}},
                    {"INT": [intent], "SR": [sr]})


# --------------------------------------------------------------- sources parsing

def test_parse_sources_reads_declarations():
    p = _project({**_SCHEMA, "sources": [{"namespace": "toy", "path": "../toy"}]}, {})
    sources = parse_sources(p)
    assert [(s.namespace, s.path) for s in sources] == [("toy", "../toy")]


def test_no_sources_is_empty():
    assert parse_sources(_project(_SCHEMA, {})) == []


def test_bad_namespace_rejected():
    p = _project({**_SCHEMA, "sources": [{"namespace": "Toy", "path": "x"}]}, {})
    with pytest.raises(SourceError):
        parse_sources(p)


def test_duplicate_namespace_rejected():
    p = _project({**_SCHEMA, "sources": [
        {"namespace": "toy", "path": "a"}, {"namespace": "toy", "path": "b"}]}, {})
    with pytest.raises(SourceError):
        parse_sources(p)


def test_url_ref_source_parsed():
    p = _project({**_SCHEMA, "sources": [
        {"namespace": "asvs", "url": "https://example.com/x.git", "ref": "v4.0.3"}]}, {})
    (s,) = parse_sources(p)
    assert s.is_remote
    assert (s.url, s.ref, s.path) == ("https://example.com/x.git", "v4.0.3", None)


def test_url_without_ref_rejected():
    p = _project({**_SCHEMA, "sources": [
        {"namespace": "asvs", "url": "https://example.com/x.git"}]}, {})
    with pytest.raises(SourceError, match="no 'ref'"):
        parse_sources(p)


def test_path_and_url_mutually_exclusive():
    p = _project({**_SCHEMA, "sources": [
        {"namespace": "asvs", "path": "x", "url": "https://e/x", "ref": "v1"}]}, {})
    with pytest.raises(SourceError, match="mutually exclusive"):
        parse_sources(p)


def test_neither_path_nor_url_rejected():
    p = _project({**_SCHEMA, "sources": [{"namespace": "asvs"}]}, {})
    with pytest.raises(SourceError, match="either a 'path' or a 'url'"):
        parse_sources(p)


def test_ref_with_path_rejected():
    p = _project({**_SCHEMA, "sources": [
        {"namespace": "asvs", "path": "x", "ref": "v1"}]}, {})
    with pytest.raises(SourceError, match="ref"):
        parse_sources(p)


def test_subdir_parsed():  # SR-0008
    p = _project({**_SCHEMA, "sources": [{
        "namespace": "console", "url": "https://example.com/app.git",
        "ref": "main", "subdir": "requirements"}]}, {})
    (s,) = parse_sources(p)
    assert s.subdir == "requirements"


def test_absolute_subdir_rejected():  # SR-0008
    p = _project({**_SCHEMA, "sources": [{
        "namespace": "console", "url": "https://e/x", "ref": "v1",
        "subdir": "/etc"}]}, {})
    with pytest.raises(SourceError, match="absolute 'subdir'"):
        parse_sources(p)


def test_escaping_subdir_rejected():  # SR-0008
    p = _project({**_SCHEMA, "sources": [{
        "namespace": "console", "url": "https://e/x", "ref": "v1",
        "subdir": "../../etc"}]}, {})
    with pytest.raises(SourceError, match="escapes the source root"):
        parse_sources(p)


# ------------------------------------------------------------------ union merge

def test_qualified_reference_rewritten_and_resolves():
    consumer = _project(_SCHEMA, {"SR": [Item(
        uid="SR-0001", type="system_requirement", status="approved",
        links=[Link(target="toy:SR-0001", type="relates")])]})
    union = build_union(consumer, {"toy": _source()})

    # The borrowed item is present under a mangled UID, mapped back to qualified.
    assert union.project.get("TOYSR-0001") is not None
    assert union.qualified("TOYSR-0001") == "toy:SR-0001"
    # The consumer's cross-source link now points at the mangled UID — no colon left.
    consumer_sr = union.project.get("SR-0001")
    assert consumer_sr.links[0].target == "TOYSR-0001"
    assert not any(":" in l.target for it in union.project.items() for l in it.links)


def test_source_internal_reference_rewritten():
    union = build_union(_project(_SCHEMA, {}), {"toy": _source()})
    # SR-0001 -> INT-0001 inside the source becomes TOYSR-0001 -> TOYINT-0001.
    assert union.project.get("TOYSR-0001").links[0].target == "TOYINT-0001"


def test_unbound_namespace_fails_fast():
    consumer = _project(_SCHEMA, {"SR": [Item(
        uid="SR-0001", type="system_requirement", status="approved",
        links=[Link(target="ghost:SR-0001", type="relates")])]})
    with pytest.raises(ComposeError, match="ghost"):
        build_union(consumer, {"toy": _source()})


def test_composed_graph_validates_clean():
    consumer = _project(_SCHEMA, {
        "INT": [Item(uid="INT-0001", type="intent", status="approved",
                     normative=False)],
        "SR": [Item(uid="SR-0001", type="system_requirement", status="approved",
                    links=[Link(target="INT-0001", type="derives_from"),
                           Link(target="toy:SR-0001", type="relates")])]})
    union = build_union(consumer, {"toy": _source()})
    errors = [f for f in validate(union.project) if f.severity == "error"]
    assert errors == [], errors


def test_dangling_cross_source_reference_translated_back():
    consumer = _project(_SCHEMA, {"SR": [Item(
        uid="SR-0001", type="system_requirement", status="approved",
        links=[Link(target="toy:SR-9999", type="relates")])]})
    union = build_union(consumer, {"toy": _source()})
    pattern = union.pattern()
    dangling = [translate_finding(f, union, pattern)
                for f in validate(union.project) if f.rule == "dangling-link"]
    assert dangling, "expected a dangling-link for the missing source clause"
    # The composer sees the reference in its own vocabulary, not the mangled form.
    assert "toy:SR-9999" in dangling[0].message
    assert "TOYSR-9999" not in dangling[0].message


# ------------------------------------------------------------- prefix collisions

def test_synthetic_prefix_collision_fails_fast():
    # Two namespaces that sanitise to the same stem and share a source prefix.
    src = _source()
    consumer = _project(_SCHEMA, {})
    with pytest.raises(ComposeError, match="distinct namespace"):
        build_union(consumer, {"g-s": src, "gs": _source()})


def test_a_borrowed_stamp_survives_a_consumer_with_different_normative_flags():
    """throughline SR-0162, end to end through the merge.

    The union is validated under the consumer's schema, so before this the set of
    attributes hashed into a fingerprint changed the moment an item was borrowed.
    Composing throughline's own graph into throughline-ratify made the consequence
    plain — leave the consumer's flags alone and every ratified item in the source
    reads as drifted; mirror the source's flags and every ratified item the
    *consumer* wrote reads as drifted instead. There is no configuration that
    satisfies both, so the authoring graph's judgement travels with the item.
    """
    from throughline.fingerprint import fingerprint

    source = _project(
        {"project": {"name": "toy", "format_version": 2},
         "types": {"system_requirement": {
             "attrs": {"priority": {"normative": True}}}}},
        {"SR": [Item(uid="SR-0001", type="system_requirement", status="ratified",
                     text="The system shall foo.", attrs={"priority": "must"})]})
    # the stamp as written in the source's own graph
    authored = source.registers["SR"].items["SR-0001"]
    stamped = fingerprint(authored, source.schema)

    # a consumer that marks nothing normative — it has its own model and no
    # reason to adopt the source's
    consumer = _project(_SCHEMA, {})
    union = build_union(consumer, {"toy": source})

    borrowed = union.project.get("TOYSR-0001")
    assert borrowed is not None, "the item was merged under the mangled UID"
    assert fingerprint(borrowed, union.project.schema) == stamped
