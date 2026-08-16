"""throughline M0 test suite — model, UID allocation, fingerprint, storage
round-trip, the validation pipeline, and the grounding operations.
"""
import shutil
import sys
import tomllib
from importlib import metadata
from pathlib import Path

import pytest

from throughline import (
    Register,
    Index,
    Item,
    Link,
    Project,
    collisions,
    fingerprint,
    init_project,
    invalidate,
    load_project,
    next_uid,
    parse_uid,
    ProjectError,
    ratify,
    validate,
    write_item,
    write_manifest,
)
import throughline as throughline_pkg
from throughline import identity
from throughline.cli import main as _cli
from throughline.identity import IdentityError
from throughline.grounding import GroundingError, scout_ingest
from throughline.schema import Schema, SchemaError
from throughline.storage import (
    CONFIG_NAME,
    FORMAT_VERSION,
    ProjectError,
    baseline_statuses,
    migrate_project,
    read_project,
)
from throughline.tomledit import TomlDocument
from throughline.uid import UidError

REPO = Path(__file__).resolve().parent.parent
DEMO = REPO / "examples" / "grounding-demo"
SELFHOST = REPO / "requirements"


# --------------------------------------------------------------------- helpers

def _doc(prefix: str, *items: Item, digits: int = 4, reserved=None) -> Register:
    d = Register(prefix=prefix, digits=digits, reserved=reserved or [])
    for it in items:
        it._register_prefix = prefix
        d.items[it.uid] = it
    return d

# The standard role -> status binding a v3 project declares (SR-0131). Mirrors
# the init template + the v2->v3 migration backfill so in-memory test projects
# carry the same status vocabulary the tool writes to disk.
_STATUS_ROLES = {
    "initial": "draft", "proposed": "proposed", "ratified": "ratified",
    "invalidated": "rejected", "suspect": "suspect", "tombstone": "deleted",
}


def _ensure_roles(config: dict) -> dict:
    """Give an in-memory test config the [status.roles] a real v3 project always
    has, unless it already declares them. Only roles whose target status is among
    the config's declared [status] values are added (an unconstrained config takes
    the whole map), so the injected table never trips the schema consistency check
    — exactly the rule the on-disk migration follows."""
    status = config.get("status") or {}
    if status.get("roles"):
        return config
    declared = status.get("values")
    roles = {r: s for r, s in _STATUS_ROLES.items()
             if not declared or s in declared}
    if not roles:
        return config
    return {**config, "status": {**status, "roles": roles}}


def _project(*docs: Register, config: dict | None = None) -> Project:
    p = Project(path=Path("/tmp/none"), config=_ensure_roles(config or {}))
    for d in docs:
        p.registers[d.prefix] = d
    return p

def _rules(findings) -> set[tuple[str, str]]:
    return {(f.uid, f.rule) for f in findings}

def _errors(findings):
    return [f for f in findings if f.severity == "error"]


# ------------------------------------------------------------------------- UID

def test_uid_grammar_accepts_and_rejects():
    assert parse_uid("SR-0001") == ("SR", 1)
    assert parse_uid("NFR-00042") == ("NFR", 42)
    with pytest.raises(UidError):
        parse_uid("sr-1")
    with pytest.raises(UidError):
        parse_uid("SR_0001")

def test_next_uid_skips_reserved_and_tombstones():
    live = Item(uid="SR-0001", type="requirement")
    tomb = Item(uid="SR-0003", type="requirement", status="deleted")
    d = _doc("SR", live, tomb, reserved=[5])
    # max ever-used is 5 (reserved); tombstone 3 still counts, so next is 6.
    assert next_uid(d) == "SR-0006"

def test_collisions_detected_across_documents():
    a = _doc("SR", Item(uid="SR-0001", type="requirement"))
    b = _doc("XR", Item(uid="SR-0001", type="requirement"))
    # same uid in two docs -> a merge collision
    p = _project(a, b)
    assert "SR-0001" in collisions(p)


def test_collisions_detected_within_a_folder_from_disk(tmp_path):
    # Two files in the SAME register folder declaring the same UID — the exact
    # shape a real merge clash takes (both branches ran `tl new SR` and allocated
    # SR-0001). Must be caught on a disk load, not only when the collision is
    # constructed in memory: the loader used to fold them into one dict entry and
    # silently drop the loser (SR-0006).
    init_project(tmp_path, name="MC")
    doc_dir = tmp_path / "sr"
    doc_dir.mkdir()
    write_manifest(Register(prefix="SR", path=doc_dir))
    (doc_dir / "SR-0001.yml").write_text(
        "uid: SR-0001\ntype: requirement\ntitle: original\n"
        "text: The system shall foo.\n", encoding="utf-8")
    (doc_dir / "SR-0001-copy.yml").write_text(
        "uid: SR-0001\ntype: requirement\ntitle: EVIL TWIN\n"
        "text: Something else entirely.\n", encoding="utf-8")
    p = load_project(tmp_path)
    assert "SR-0001" in collisions(p)
    assert ("SR-0001", "uid-collision") in _rules(validate(p))


def test_malformed_link_surfaces_as_finding_not_crash(tmp_path):
    # A hand-edited link using `to:` instead of `target:` (the exact shape a
    # human typo takes) once crashed the loader with a raw KeyError before schema
    # validation could run. It must instead load and produce a named `check`
    # finding that identifies the item and reason, with the graph still usable
    # (SR-0134).
    init_project(tmp_path, name="ML")
    doc_dir = tmp_path / "sr"
    doc_dir.mkdir()
    write_manifest(Register(prefix="SR", path=doc_dir))
    (doc_dir / "SR-0001.yml").write_text(
        "uid: SR-0001\ntype: requirement\ntitle: typo\n"
        "text: The system shall foo.\n"
        "links:\n- to: INT-0001\n  type: implements\n", encoding="utf-8")
    p = load_project(tmp_path)  # no traceback
    assert p.get("SR-0001") is not None
    assert p.get("SR-0001").links == []  # the malformed entry is dropped, not kept
    assert ("SR-0001", "malformed-link") in _rules(validate(p))


def test_malformed_link_non_mapping_entry_is_a_finding(tmp_path):
    # A links list holding a bare scalar (not a mapping) is also structurally
    # malformed and must be reported, never crash the loader (SR-0134).
    init_project(tmp_path, name="ML2")
    doc_dir = tmp_path / "sr"
    doc_dir.mkdir()
    write_manifest(Register(prefix="SR", path=doc_dir))
    (doc_dir / "SR-0001.yml").write_text(
        "uid: SR-0001\ntype: requirement\ntitle: bad list\n"
        "text: The system shall foo.\n"
        "links:\n- INT-0001\n", encoding="utf-8")
    p = load_project(tmp_path)
    assert ("SR-0001", "malformed-link") in _rules(validate(p))


# ----------------------------------------------------------------- fingerprint

def test_fingerprint_ignores_status_title_order_links():
    base = Item(uid="SR-1", type="requirement", text="The system shall foo.")
    fp = fingerprint(base)
    noisy = Item(uid="SR-1", type="requirement", text="The system shall foo.",
                 status="ratified", title="changed", order=9.0,
                 links=[Link(target="X", type="relates")])
    assert fingerprint(noisy) == fp

def test_fingerprint_tracks_text_and_normative_attrs():
    schema = Schema.from_config(
        {"types": {"requirement": {"attrs": {"priority": {"normative": True}}}}})
    a = Item(uid="SR-1", type="requirement", text="foo", attrs={"priority": "must"})
    b = Item(uid="SR-1", type="requirement", text="foo", attrs={"priority": "should"})
    assert fingerprint(a, schema) != fingerprint(b, schema)
    # text change moves the fingerprint too
    c = Item(uid="SR-1", type="requirement", text="bar", attrs={"priority": "must"})
    assert fingerprint(c, schema) != fingerprint(a, schema)

def test_fingerprint_follows_the_uid_the_item_was_authored_under():
    """A composing tool must re-label a borrowed item to keep identity unique in
    the merged graph. That label is the consumer's own, and a stamp written where
    the item was authored has to survive it (SR-0154)."""
    authored = Item(uid="SR-1", type="requirement", text="The system shall foo.")
    relabelled = Item(uid="BASESR-1", type="requirement",
                      text="The system shall foo.", _authored_uid="SR-1")
    assert fingerprint(relabelled) == fingerprint(authored)

def test_fingerprint_still_moves_when_a_relabelled_item_changes():
    """The seam must not blind the drift check — only the label is excused."""
    original = Item(uid="BASESR-1", type="requirement", text="foo",
                    _authored_uid="SR-1")
    edited = Item(uid="BASESR-1", type="requirement", text="bar",
                  _authored_uid="SR-1")
    assert fingerprint(edited) != fingerprint(original)

def test_authored_uid_defaults_to_the_items_own_uid():
    """Nothing changes for an item nobody re-labelled."""
    assert Item(uid="SR-1", type="requirement").authored_uid == "SR-1"

def test_fingerprint_follows_the_normative_attrs_of_the_authoring_graph():
    """A union is validated under the *consumer's* schema, so the set of attributes
    hashed would otherwise change the moment an item is borrowed — and every stamp
    written in the source would read as drifted on content nobody touched
    (SR-0162)."""
    source_schema = Schema.from_config(
        {"types": {"requirement": {"attrs": {"priority": {"normative": True}}}}})
    consumer_schema = Schema.from_config({"types": {"requirement": {}}})
    authored = Item(uid="SR-1", type="requirement", text="foo",
                    attrs={"priority": "must"})
    borrowed = Item(uid="BASESR-1", type="requirement", text="foo",
                    attrs={"priority": "must"}, _authored_uid="SR-1",
                    _authored_normative_attrs=("priority",))
    # read under the consumer's schema, which marks nothing normative
    assert fingerprint(borrowed, consumer_schema) == fingerprint(authored,
                                                                source_schema)

def test_a_consumer_need_not_mirror_a_sources_normative_flags():
    """The reverse direction of the same defect. A consumer that marks *more*
    normative than the source must not stale the source's stamps either, so no
    consumer is forced to adopt a source's opinion about what counts as a change
    in order to compose it (SR-0162)."""
    source_schema = Schema.from_config({"types": {"requirement": {}}})
    consumer_schema = Schema.from_config(
        {"types": {"requirement": {"attrs": {"priority": {"normative": True}}}}})
    authored = Item(uid="SR-1", type="requirement", text="foo",
                    attrs={"priority": "must"})
    borrowed = Item(uid="BASESR-1", type="requirement", text="foo",
                    attrs={"priority": "must"}, _authored_uid="SR-1",
                    _authored_normative_attrs=())
    assert fingerprint(borrowed, consumer_schema) == fingerprint(authored,
                                                                source_schema)

def test_a_borrowed_items_own_normative_attr_still_moves_its_fingerprint():
    """The seam must not blind the drift check: what the *authoring* graph called
    normative is still watched, so a genuine rewrite of borrowed content is still
    reported."""
    consumer_schema = Schema.from_config({"types": {"requirement": {}}})
    before = Item(uid="BASESR-1", type="requirement", text="foo",
                  attrs={"priority": "must"}, _authored_uid="SR-1",
                  _authored_normative_attrs=("priority",))
    after = Item(uid="BASESR-1", type="requirement", text="foo",
                 attrs={"priority": "should"}, _authored_uid="SR-1",
                 _authored_normative_attrs=("priority",))
    assert fingerprint(after, consumer_schema) != fingerprint(before,
                                                             consumer_schema)


# --------------------------------------------------------------------- storage

def test_storage_roundtrip_preserves_unknown_keys(tmp_path):
    proj = init_project(tmp_path, name="RT")
    doc = Register(prefix="SR", path=tmp_path / "sr")
    doc.path.mkdir()
    it = Item.from_dict({"uid": "SR-0001", "type": "requirement",
                         "text": "hi", "x_custom": {"keep": 1}})
    it._register_prefix = "SR"
    doc.items[it.uid] = it
    write_manifest(doc)
    write_item(it, doc)
    proj.registers["SR"] = doc
    reloaded = load_project(tmp_path).get("SR-0001")
    assert reloaded is not None
    assert reloaded.extra.get("x_custom") == {"keep": 1}


# ------------------------------------------------- safe data handling (NFR-0022)

def test_load_rejects_python_object_yaml_tags(tmp_path):
    """A malicious project file using a !!python/... tag must not construct an
    arbitrary object or execute code — the classic pyyaml deserialization RCE.
    safe_load raises rather than honouring the tag (NFR-0022)."""
    init_project(tmp_path, name="SEC")
    doc_dir = tmp_path / "sr"
    doc_dir.mkdir()
    (doc_dir / ".register.yml").write_text(
        "prefix: SR\ndigits: 4\ntitle: S\n", encoding="utf-8")
    # apply os.system would run a command under an unsafe loader.
    (doc_dir / "SR-0001.yml").write_text(
        "uid: SR-0001\ntype: requirement\n"
        "title: !!python/object/apply:os.system ['echo pwned']\n",
        encoding="utf-8")
    with pytest.raises(Exception):  # noqa: B017 - safe_load's ConstructorError
        load_project(tmp_path)


def test_hostile_field_value_cannot_break_yaml_structure(tmp_path):
    """A field value crafted to look like YAML (newlines, colons, a forged
    sibling key) is emitted through the SafeDumper as data and round-trips as a
    single string — it cannot inject sibling keys or alter structure (NFR-0022)."""
    init_project(tmp_path, name="SEC")
    doc = Register(prefix="SR", path=tmp_path / "sr")
    doc.path.mkdir()
    hostile = "Bob\ninjected_admin: true\n- not: a list item\ntype: intent"
    it = Item.from_dict({"uid": "SR-0001", "type": "requirement", "title": "t",
                         "attrs": {"ratified_by": hostile}})
    it._register_prefix = "SR"
    doc.items[it.uid] = it
    write_manifest(doc)
    write_item(it, doc)
    reloaded = load_project(tmp_path).get("SR-0001")
    assert reloaded is not None
    # The whole hostile string survives as one value; no forged siblings appear.
    assert reloaded.attrs["ratified_by"] == hostile
    assert reloaded.type == "requirement"          # not flipped to 'intent'
    assert "injected_admin" not in reloaded.attrs
    assert "injected_admin" not in reloaded.extra


def test_malformed_yaml_fails_fast(tmp_path):
    """Broken YAML raises on load rather than being silently coerced or partially
    applied (NFR-0022)."""
    init_project(tmp_path, name="SEC")
    doc_dir = tmp_path / "sr"
    doc_dir.mkdir()
    (doc_dir / ".register.yml").write_text(
        "prefix: SR\ndigits: 4\ntitle: S\n", encoding="utf-8")
    (doc_dir / "SR-0001.yml").write_text(
        "uid: SR-0001\ntype: requirement\ntitle: \"unterminated\n", encoding="utf-8")
    with pytest.raises(Exception):  # noqa: B017 - yaml.YAMLError
        load_project(tmp_path)


# -------------------------------------------------------------------- validate

def _grounded_project(config=None):
    intent = Item(uid="INT-1", type="intent", status="ratified")
    fr = Item(uid="FR-1", type="requirement", status="approved",
              links=[Link(target="INT-1", type="derives_from")])
    return _project(_doc("INT", intent), _doc("FR", fr), config=config)

def test_empty_graph_fails_rather_than_passing_vacuously(tmp_path):
    """A run that discovered nothing validated nothing — it must not report the
    graph sound (SR-0146). Items load only from a folder holding a manifest, so a
    project whose manifests are gone loads zero items and every other rule passes
    vacuously."""
    root = _scaffold(tmp_path)
    # finish the walk before removing anything — deleting a directory out from
    # under a live rglob raises FileNotFoundError on Python 3.11
    for manifest in list(root.rglob(".register.yml")):
        shutil.rmtree(manifest.parent)
    p = load_project(str(root))
    assert next(p.items(), None) is None
    findings = validate(p, strict=True)
    assert "empty-graph" in {f.rule for f in findings}
    assert _errors(findings)
    assert "register" in next(f.message for f in findings if f.rule == "empty-graph")

def test_unknown_top_level_key_is_reported(tmp_path):
    """A key that is neither a core field nor `attrs` is read by nothing, so a
    misplaced one fails silently — most damagingly `origin`, which at the top level
    exempts a machine-authored item from the unratified gate (SR-0147)."""
    intent = Item(uid="INT-1", type="intent", status="ratified")
    fr = Item(uid="FR-1", type="requirement", status="proposed",
              links=[Link(target="INT-1", type="derives_from")])
    fr.extra["origin"] = "ai"          # written at the top level, not under attrs
    p = _project(_doc("INT", intent), _doc("FR", fr))
    findings = validate(p)
    assert ("FR-1", "unknown-key") in _rules(findings)
    assert "origin" in next(f.message for f in findings if f.rule == "unknown-key")

def test_orphan_flagged_when_no_grounding_link():
    fr = Item(uid="FR-1", type="requirement")
    p = _project(_doc("FR", fr))
    assert ("FR-1", "orphan") in _rules(validate(p))

def test_grounded_requirement_is_not_orphan():
    p = _grounded_project()
    assert ("FR-1", "orphan") not in _rules(validate(p))

def test_unserved_delivery_root_flagged():
    con = Item(uid="CON-1", type="constraint", status="ratified")
    p = _project(_doc("CON", con))
    assert ("CON-1", "unserved-root") in _rules(validate(p))

def test_dangling_link_flagged():
    fr = Item(uid="FR-1", type="requirement",
              links=[Link(target="NOPE", type="derives_from")])
    assert ("FR-1", "dangling-link") in _rules(validate(_project(_doc("FR", fr))))

def test_namespace_qualified_reference_flagged_not_dangling():
    # A `<namespace>:<UID>` target (SR-0107) is the composition syntax the core cannot
    # resolve — it must fire namespace-unresolved, and NOT be mistaken for a dangling
    # local link.
    fr = Item(uid="FR-1", type="requirement",
              links=[Link(target="gds:SR-0001", type="derives_from")])
    rules = _rules(validate(_project(_doc("FR", fr))))
    assert ("FR-1", "namespace-unresolved") in rules
    assert ("FR-1", "dangling-link") not in rules

def test_free_external_references_stay_opaque():
    # URLs, repo paths, and anchors (SR-0031) are unresolvable by design and must not
    # trigger either the dangling or the namespace-unresolved rule.
    fr = Item(uid="FR-1", type="requirement", links=[
        Link(target="https://example.com/spec", type="relates"),
        Link(target="docs/spec.md#L5", type="relates"),
    ])
    rules = _rules(validate(_project(_doc("FR", fr))))
    assert not any(r in ("dangling-link", "namespace-unresolved") for _, r in rules)

def test_reference_classifiers_are_public(): # SR-0108
    # The predicates a composer needs are on the public surface, under stable names,
    # and are the same functions the validator itself runs — so a library consumer and
    # the core can never disagree about how a target is classified.
    import importlib
    import throughline
    from throughline import is_external, is_namespace_qualified
    _v = importlib.import_module("throughline.validate")

    assert "is_external" in throughline.__all__
    assert "is_namespace_qualified" in throughline.__all__
    assert is_external is _v.is_external
    assert is_namespace_qualified is _v.is_namespace_qualified

    # A namespace-qualified ref is recognised; is_external runs first so a URL scheme
    # (tail begins ``//``) is never mistaken for one.
    assert is_namespace_qualified("gds:SR-0001")
    assert not is_namespace_qualified("https://example.com/x")
    assert not is_namespace_qualified("SR-0001")

    # External pointers — URL, path, anchor (SR-0031) — classify as external.
    assert is_external("https://example.com/x")
    assert is_external("docs/spec.md#L5")
    assert is_external("path/to/thing")
    assert not is_external("SR-0001")
    assert not is_external("gds:SR-0001")

def test_grounding_cycle_flagged():
    a = Item(uid="A-1", type="requirement",
             links=[Link(target="B-1", type="derives_from")])
    b = Item(uid="B-1", type="requirement",
             links=[Link(target="A-1", type="derives_from")])
    findings = validate(_project(_doc("A", a), _doc("B", b)))
    assert any(r == "grounding-cycle" for _, r in _rules(findings))

def test_suspect_link_when_stamp_stale():
    intent = Item(uid="INT-1", type="intent", status="ratified")
    fr = Item(uid="FR-1", type="requirement", status="approved",
              links=[Link(target="INT-1", type="derives_from", stamp="sha256:stale")])
    assert ("FR-1", "suspect-link") in _rules(validate(_project(_doc("INT", intent),
                                                                _doc("FR", fr))))

def test_matching_stamp_is_not_suspect():
    intent = Item(uid="INT-1", type="intent", status="ratified")
    fr = Item(uid="FR-1", type="requirement", status="approved")
    p = _project(_doc("INT", intent), _doc("FR", fr))
    fr.links.append(Link(target="INT-1", type="derives_from",
                         stamp=fingerprint(intent, p.schema)))
    assert ("FR-1", "suspect-link") not in _rules(validate(p))

def test_unratified_ai_origin_proposed_item():
    intent = Item(uid="INT-1", type="intent", status="ratified")
    fr = Item(uid="FR-1", type="requirement", status="proposed",
              attrs={"origin": "ai"},
              links=[Link(target="INT-1", type="derives_from")])
    assert ("FR-1", "unratified") in _rules(validate(_project(_doc("INT", intent),
                                                              _doc("FR", fr))))

_ROLES = {"status": {"roles": {"initial": "draft", "proposed": "proposed",
                               "ratified": "ratified",
                               "invalidated": "rejected", "tombstone": "deleted"}}}

def _ai_findings(status, **attrs):
    intent = Item(uid="INT-1", type="intent", status="ratified")
    fr = Item(uid="FR-1", type="requirement", status=status,
              attrs={"origin": "ai", **attrs},
              links=[Link(target="INT-1", type="derives_from")])
    return validate(_project(_doc("INT", intent), _doc("FR", fr), config=_ROLES))

def _ai_item(status, **attrs):
    return _rules(_ai_findings(status, **attrs))

def test_ai_origin_item_that_left_proposed_is_still_unratified():
    """SR-0149 — moving out of `proposed` by any route other than ratification
    does not make machine-authored scope accepted."""
    assert ("FR-1", "unratified") in _ai_item("approved")

def test_ratified_status_without_a_named_ratifier_is_unratified():
    """SR-0149 — `tl status <uid> ratified` sets the status while naming nobody,
    so the status alone is not evidence a person took accountability."""
    assert ("FR-1", "unratified") in _ai_item("ratified")

def test_ai_origin_item_with_a_ratifier_is_accepted_in_any_status():
    for status in ("proposed", "approved", "implemented"):
        assert ("FR-1", "unratified") not in _ai_item(
            status, ratified_by="A Human"), status

def test_initial_status_item_is_not_accused_of_leaving_proposed():
    """SR-0149 — an item still in the initial status was never proposed, so the
    finding must say that rather than claim it escaped the proposed status."""
    msg = next(f.message for f in _ai_findings("draft") if f.rule == "unratified")
    assert "never proposed" in msg and "left the proposed status" not in msg

def test_terminal_status_ai_item_needs_no_ratifier():
    """Dead scope never reaches a reader, so it needs no accountability record."""
    for status in ("rejected", "deleted"):
        assert ("FR-1", "unratified") not in _ai_item(status), status

def test_human_origin_item_is_never_chased_for_ratification():
    intent = Item(uid="INT-1", type="intent", status="ratified")
    fr = Item(uid="FR-1", type="requirement", status="approved",
              attrs={"origin": "human"},
              links=[Link(target="INT-1", type="derives_from")])
    assert ("FR-1", "unratified") not in _rules(validate(
        _project(_doc("INT", intent), _doc("FR", fr), config=_ROLES)))

def test_bad_status_flagged_against_declared_vocabulary():
    cfg = {"status": {"values": ["draft", "approved"]}}
    fr = Item(uid="FR-1", type="requirement", status="wip",
              links=[Link(target="INT-1", type="derives_from")])
    intent = Item(uid="INT-1", type="intent", status="approved")
    p = _project(_doc("INT", intent), _doc("FR", fr), config=cfg)
    assert ("FR-1", "bad-status") in _rules(validate(p))

def test_declared_status_is_accepted_and_rule_inert_without_vocabulary():
    fr = Item(uid="FR-1", type="requirement", status="approved",
              links=[Link(target="INT-1", type="derives_from")])
    intent = Item(uid="INT-1", type="intent", status="approved")
    # declared + valid -> no finding
    cfg = {"status": {"values": ["draft", "approved"]}}
    p = _project(_doc("INT", intent), _doc("FR", fr), config=cfg)
    assert ("FR-1", "bad-status") not in _rules(validate(p))
    # no vocabulary declared -> rule is inert even for an odd status
    fr.status = "whatever"
    p2 = _project(_doc("INT", intent), _doc("FR", fr), config={})
    assert not any(r == "bad-status" for _, r in _rules(validate(p2)))

def test_strict_promotes_warnings_to_errors():
    intent = Item(uid="INT-1", type="intent", status="ratified")
    fr = Item(uid="FR-1", type="requirement", status="proposed",
              attrs={"origin": "ai"},
              links=[Link(target="INT-1", type="derives_from")])
    # Vocabularies declared, because leaving one out is an error in its own right
    # (SR-0185) and this is about the promotion of everything that is not.
    p = _project(_doc("INT", intent), _doc("FR", fr),
                 config={"status": {"values": ["proposed", "ratified"]},
                         "links": {"types": ["derives_from", "implements",
                                             "mitigates", "verifies"]}})
    assert _errors(validate(p, strict=False)) == []
    assert _errors(validate(p, strict=True)) != []

def test_coverage_rule_needs_incoming_link():
    config = {"rules": {"coverage": [
        {"filter": "type == 'requirement'", "needs": "incoming:verifies",
         "severity": "error"}]}}
    p = _grounded_project(config=config)
    assert ("FR-1", "coverage") in _rules(validate(p))


# ---------------------------------------------------------------------- schema

def test_schema_helpers_are_the_single_source(tmp_path):
    """The Schema exposes the typed lookups every component reuses (SR-0082)."""
    cfg = {
        "project": {"name": "Demo"},
        "types": {"requirement": {"attrs": {
            "priority": {"type": "enum", "values": ["must", "should"],
                         "required": True, "normative": True},
            "note": {"type": "string"}}}},
        "links": {"types": ["derives_from", "verifies"]},
        "status": {"values": ["draft", "approved"]},
        "grounding": {"root_types": ["intent"],
                      "ground_link_types": ["derives_from"]},
        "rules": {"orphan": "warning"},
    }
    s = Schema.from_config(cfg)
    assert s.name == "Demo"
    assert set(s.attrs_for("requirement")) == {"priority", "note"}
    assert s.normative_attrs("requirement") == ["priority"]
    assert s.attr("requirement", "priority").values == ("must", "should")
    assert s.is_link_type("verifies") and not s.is_link_type("bogus")
    assert s.is_status("draft") and not s.is_status("wip")
    assert s.is_root(Item(uid="I-1", type="intent"))
    assert not s.is_root(Item(uid="R-1", type="requirement"))
    # rule override beats default; strict promotes a warning to an error
    assert s.rule_severity("orphan", "error", strict=False) == "warning"
    assert s.rule_severity("orphan", "error", strict=True) == "error"

def test_schema_unconstrained_when_vocabularies_absent():
    s = Schema.from_config({})
    assert s.is_link_type("anything")   # no [links] -> everything legal
    assert s.is_status("anything")      # no [status] -> everything legal

def test_schema_rejects_grounding_link_not_in_links():
    with pytest.raises(SchemaError):
        Schema.from_config({
            "links": {"types": ["derives_from"]},
            "grounding": {"ground_link_types": ["derives_from", "mitigates"]}})

def test_schema_rejects_coverage_needing_unknown_link():
    with pytest.raises(SchemaError):
        Schema.from_config({
            "links": {"types": ["derives_from"]},
            "rules": {"coverage": [{"filter": "true", "needs": "incoming:verifies"}]}})

def test_schema_rejects_enum_without_values_and_unknown_kind():
    with pytest.raises(SchemaError):
        Schema.from_config({"types": {"r": {"attrs": {"p": {"type": "enum"}}}}})
    with pytest.raises(SchemaError):
        Schema.from_config({"types": {"r": {"attrs": {"p": {"type": "wat"}}}}})

def test_load_project_fails_fast_on_bad_config(tmp_path):
    (tmp_path / "throughline.toml").write_text(
        '[links]\ntypes = ["derives_from"]\n'
        '[grounding]\nground_link_types = ["mitigates"]\n', encoding="utf-8")
    with pytest.raises(ProjectError):
        load_project(tmp_path)


# --------------------------------------------------------------- transitions

def test_allows_transition_semantics():
    """Declared moves gate; a no-op stays; an unlisted source is stuck (SR-0083)."""
    s = Schema.from_config({
        "status": {"values": ["draft", "approved", "rejected"]},
        "transitions": {"draft": ["approved", "rejected"]}})
    assert s.allows_transition("draft", "approved")
    assert not s.allows_transition("draft", "verified")
    assert s.allows_transition("approved", "approved")   # no-op always fine
    assert not s.allows_transition("approved", "draft")  # source not listed

def test_transitions_unconstrained_when_absent():
    s = Schema.from_config({"status": {"values": ["draft", "approved"]}})
    assert s.transitions is None
    assert s.allows_transition("draft", "anything")      # inert without a table

def test_schema_rejects_transition_endpoint_not_a_status():
    with pytest.raises(SchemaError):
        Schema.from_config({
            "status": {"values": ["draft", "approved"]},
            "transitions": {"draft": ["shipped"]}})   # 'shipped' undeclared

def test_bad_transition_flagged_against_baseline():
    cfg = {"status": {"values": ["draft", "approved", "verified"]},
           "transitions": {"draft": ["approved"], "approved": ["verified"]}}
    fr = Item(uid="FR-1", type="requirement", status="verified",
              links=[Link(target="INT-1", type="derives_from")])
    intent = Item(uid="INT-1", type="intent", status="approved")
    p = _project(_doc("INT", intent), _doc("FR", fr), config=cfg)
    # draft -> verified skips approved: illegal
    baseline = {"FR-1": "draft", "INT-1": "approved"}
    assert ("FR-1", "bad-transition") in _rules(validate(p, baseline=baseline))
    # a legal single step is clean; so is no baseline at all
    fr.status = "approved"
    assert ("FR-1", "bad-transition") not in _rules(validate(p, baseline=baseline))
    assert ("FR-1", "bad-transition") not in _rules(validate(p, baseline=None))

def test_new_item_is_not_a_transition():
    cfg = {"status": {"values": ["draft", "approved"]},
           "transitions": {"draft": ["approved"]}}
    fr = Item(uid="FR-1", type="requirement", status="approved",
              links=[Link(target="INT-1", type="derives_from")])
    intent = Item(uid="INT-1", type="intent", status="approved")
    p = _project(_doc("INT", intent), _doc("FR", fr), config=cfg)
    # FR-1 absent from the baseline -> creation, not a move
    assert ("FR-1", "bad-transition") not in _rules(
        validate(p, baseline={"INT-1": "approved"}))

def test_baseline_statuses_reads_git_and_gates_check(tmp_path):
    """End-to-end: the committed status is the baseline the working tree is
    measured against (SR-0083)."""
    import subprocess
    # the scaffold ships a [transitions] table where draft may only go to
    # approved/deferred/rejected/deleted, so draft -> verified is illegal.
    init_project(tmp_path, name="TX")
    doc = Register(prefix="SR", path=tmp_path / "sr")
    doc.path.mkdir()
    write_manifest(doc)
    it = Item(uid="SR-0001", type="requirement", status="draft", text="x")
    it._register_prefix = "SR"
    it._path = doc.path / "SR-0001.yml"
    write_item(it, doc)

    git = lambda *a: subprocess.run(["git", "-C", str(tmp_path), *a],
                                    capture_output=True, check=True)
    git("init")
    git("config", "user.email", "t@t"); git("config", "user.name", "t")
    git("add", "-A"); git("commit", "-m", "seed draft")

    project = load_project(tmp_path)
    base = baseline_statuses(project, "HEAD")
    assert base["SR-0001"] == "draft"

    # jump draft -> verified in the working tree: illegal
    it.status = "verified"
    write_item(it, doc)
    p2 = load_project(tmp_path)
    findings = validate(p2, baseline=baseline_statuses(p2, "HEAD"))
    assert ("SR-0001", "bad-transition") in _rules(findings)

def test_baseline_statuses_none_outside_git(tmp_path):
    proj = init_project(tmp_path, name="NG")   # not a git repo
    assert baseline_statuses(proj, "HEAD") is None


def test_deleted_tombstone_flagged_against_baseline(tmp_path):
    """A tombstone is the permanent record that a UID was retired (SR-0093). If a
    bad merge or a stray `git rm` erases the file, the committed status was
    `deleted` but the item is gone from the working tree — the never-reused
    guarantee (SR-0001) silently breaks. The gate must catch the vanished record."""
    import subprocess
    init_project(tmp_path, name="TS")
    doc = Register(prefix="SR", path=tmp_path / "sr")
    doc.path.mkdir()
    write_manifest(doc)
    it = Item(uid="SR-0001", type="requirement", status="deleted", text="x")
    it._register_prefix = "SR"
    it._path = doc.path / "SR-0001.yml"
    write_item(it, doc)

    git = lambda *a: subprocess.run(["git", "-C", str(tmp_path), *a],
                                    capture_output=True, check=True)
    git("init")
    git("config", "user.email", "t@t"); git("config", "user.name", "t")
    git("add", "-A"); git("commit", "-m", "seed tombstone")

    # tombstone present in the working tree: no finding
    p1 = load_project(tmp_path)
    assert ("SR-0001", "tombstone-deleted") not in _rules(
        validate(p1, baseline=baseline_statuses(p1, "HEAD")))

    # erase the tombstone file behind the tool's back
    (doc.path / "SR-0001.yml").unlink()
    p2 = load_project(tmp_path)
    findings = validate(p2, baseline=baseline_statuses(p2, "HEAD"))
    assert ("SR-0001", "tombstone-deleted") in _rules(findings)
    # with no git baseline there is nothing to measure against — no false positive
    assert ("SR-0001", "tombstone-deleted") not in _rules(validate(p2, baseline=None))


def test_deleted_tombstone_scoped_to_own_project(tmp_path):
    """A project may sit in a subdirectory of a larger repo (the self-host graph
    ships next to example projects). A deleted tombstone in a *sibling* project
    must not be read as this project's erased record (SR-0093) — the baseline
    scan is scoped to the project's own subtree, not the whole git tree."""
    import subprocess
    main = tmp_path / "main"; other = tmp_path / "other"
    for root, name in ((main, "MAIN"), (other, "OTHER")):
        init_project(root, name=name)
        doc = Register(prefix="SR", path=root / "sr")
        doc.path.mkdir()
        write_manifest(doc)
        it = Item(uid="SR-0001", type="requirement", status="deleted", text="x")
        it._register_prefix = "SR"; it._path = doc.path / "SR-0001.yml"
        write_item(it, doc)

    git = lambda *a: subprocess.run(["git", "-C", str(tmp_path), *a],
                                    capture_output=True, check=True)
    git("init")
    git("config", "user.email", "t@t"); git("config", "user.name", "t")
    git("add", "-A"); git("commit", "-m", "two sibling projects")

    # erase OTHER's tombstone, then check MAIN: MAIN must stay clean
    (other / "sr" / "SR-0001.yml").unlink()
    pm = load_project(main)
    assert ("SR-0001", "tombstone-deleted") not in _rules(
        validate(pm, baseline=baseline_statuses(pm, "HEAD")))
    # OTHER itself still catches its own erased tombstone
    po = load_project(other)
    assert ("SR-0001", "tombstone-deleted") in _rules(
        validate(po, baseline=baseline_statuses(po, "HEAD")))


# --------------------------------------------------------------- link_rules

def test_link_allowed_semantics():
    """A rule constrains one or both endpoints; unruled links are free (SR-0084)."""
    s = Schema.from_config({
        "links": {"types": ["mitigates", "relates"]},
        "grounding": {"ground_link_types": ["mitigates"]},
        "link_rules": {"mitigates": {"from": ["requirement"], "to": ["risk"]}}})
    assert s.link_allowed("mitigates", "requirement", "risk") is None
    assert s.link_allowed("mitigates", "nfr", "risk")            # bad source
    assert s.link_allowed("mitigates", "requirement", "intent")  # bad target
    assert s.link_allowed("relates", "anything", "whatever") is None  # no rule
    # external / unknown target skips the target side, keeps the source check
    assert s.link_allowed("mitigates", "requirement", None) is None
    assert s.link_allowed("mitigates", "nfr", None)

def test_link_rule_one_sided():
    s = Schema.from_config({
        "links": {"types": ["verifies"]},
        "grounding": {"ground_link_types": ["verifies"]},
        "link_rules": {"verifies": {"to": ["requirement"]}}})  # source unconstrained
    assert s.link_allowed("verifies", "anything", "requirement") is None
    assert s.link_allowed("verifies", "anything", "nfr")

def test_schema_rejects_link_rule_for_unknown_link_type():
    with pytest.raises(SchemaError):
        Schema.from_config({
            "links": {"types": ["mitigates"]},
            "link_rules": {"grounds": {"from": ["risk"]}}})   # 'grounds' undeclared

def test_bad_link_shape_flagged():
    cfg = {"links": {"types": ["derives_from", "mitigates"]},
           "grounding": {"root_types": ["intent", "risk"],
                         "ground_link_types": ["derives_from", "mitigates"]},
           "link_rules": {"mitigates": {"from": ["requirement"], "to": ["risk"]}}}
    intent = Item(uid="INT-1", type="intent", status="approved")
    risk = Item(uid="RSK-1", type="risk", status="approved")
    # legal: requirement mitigates risk
    good = Item(uid="FR-1", type="requirement", status="approved",
                links=[Link(target="RSK-1", type="mitigates")])
    # illegal: an nfr mitigating a risk (bad source)
    bad = Item(uid="NF-1", type="nfr", status="approved",
               links=[Link(target="RSK-1", type="mitigates"),
                      Link(target="INT-1", type="derives_from")])
    p = _project(_doc("INT", intent), _doc("RSK", risk),
                 _doc("FR", good), _doc("NF", bad), config=cfg)
    rules = _rules(validate(p))
    assert ("NF-1", "bad-link-shape") in rules
    assert ("FR-1", "bad-link-shape") not in rules

def test_link_shape_reports_triples():
    intent = Item(uid="INT-1", type="intent")
    fr = Item(uid="FR-1", type="requirement",
              links=[Link(target="INT-1", type="derives_from"),
                     Link(target="https://x/y", type="relates")])
    idx = Index.build(_project(_doc("INT", intent), _doc("FR", fr)))
    shape = idx.link_shape()
    assert shape[("requirement", "derives_from", "intent")] == 1
    assert shape[("requirement", "relates", None)] == 1   # external target


# -------------------------------------------------------------------- diagrams

def test_mermaid_types_renders_observed_edges():
    from throughline.cli import _mermaid_types
    intent = Item(uid="INT-1", type="intent")
    fr = Item(uid="FR-1", type="requirement",
              links=[Link(target="INT-1", type="derives_from"),
                     Link(target="https://x/y", type="relates")])
    idx = Index.build(_project(_doc("INT", intent), _doc("FR", fr)))
    src = _mermaid_types(idx)
    assert src.startswith("flowchart LR")
    assert "requirement -->|derives_from| intent" in src
    # external targets have no node type, so no edge is emitted for them
    assert "relates" not in src

def test_mermaid_types_none_when_no_internal_edges():
    from throughline.cli import _mermaid_types
    fr = Item(uid="FR-1", type="requirement",
              links=[Link(target="https://x/y", type="relates")])
    idx = Index.build(_project(_doc("FR", fr)))
    assert _mermaid_types(idx) is None

def test_mermaid_transitions_renders_declared_moves():
    from throughline.cli import _mermaid_transitions
    cfg = {"status": {"values": ["draft", "approved", "deleted"]},
           "transitions": {"draft": ["approved", "deleted"],
                           "approved": ["deleted"]}}
    schema = Schema.from_config(cfg)
    src = _mermaid_transitions(schema)
    assert src.startswith("stateDiagram-v2")
    assert "draft --> approved" in src
    assert "approved --> deleted" in src

def test_mermaid_transitions_none_when_absent():
    from throughline.cli import _mermaid_transitions
    schema = Schema.from_config({"status": {"values": ["draft", "approved"]}})
    assert _mermaid_transitions(schema) is None


# ------------------------------------------------------------------ agent context

_CTX_CFG = {
    "types": {
        "requirement": {"attrs": {
            "priority": {"type": "enum", "values": ["must", "should"],
                         "normative": True},
            "verification": {"type": "enum", "values": ["test", "analysis"]}}},
        "risk": {},
    },
    "links": {"types": ["derives_from", "mitigates", "verifies", "relates"]},
    "status": {"values": ["draft", "approved", "deleted"]},
    "transitions": {"draft": ["approved", "deleted"], "approved": ["deleted"]},
    "link_rules": {"mitigates": {"from": ["requirement"], "to": ["risk"]}},
    "grounding": {"root_types": ["intent", "risk"],
                  "delivery_roots": ["intent"],
                  "ground_link_types": ["derives_from", "mitigates", "verifies"],
                  "ai_origins": ["ai"]},
    "rules": {"coverage": [{"filter": "type == 'requirement'",
                            "needs": "incoming:verifies", "severity": "warning"}]},
}

def _ctx(config=_CTX_CFG):
    from throughline.cli import _context_markdown
    intent = Item(uid="INT-1", type="intent")
    fr = Item(uid="FR-1", type="requirement", attrs={"priority": "must"},
              links=[Link(target="INT-1", type="derives_from")])
    return _context_markdown(_project(_doc("INT", intent), _doc("FR", fr),
                                      config=config))

def test_context_states_the_idd_contract():
    doc = _ctx()
    # the fixed conceptual contract is present, in agent-directed language
    assert "Intent-Driven Development" in doc
    assert "red test" in doc
    assert "ratif" in doc.lower()          # AI-origin items need human ratification
    assert "tl check" in doc          # the gate

def test_context_renders_types_and_normative_attrs_from_schema():
    doc = _ctx()
    assert "`requirement`" in doc and "`risk`" in doc
    # the enum values and the normative flag come straight from the schema
    assert "`must`, `should`" in doc
    # risk is declared a root, so it is tagged as such
    assert "risk" in doc and "root" in doc

def test_context_renders_link_rules_and_transitions_from_schema():
    doc = _ctx()
    # link endpoint rule surfaces both ends
    assert "mitigates" in doc and "`risk`" in doc
    # declared transitions are listed
    assert "`draft`" in doc and "approved" in doc
    # grounding + coverage sections reflect config
    assert "incoming:verifies" in doc
    assert "`ai`" in doc                   # ai origins

def test_context_is_dynamic_absent_optional_tables():
    # a minimal project: no attrs, no transitions, no link_rules, no coverage
    doc = _ctx(config={"links": {"types": ["derives_from"]},
                       "grounding": {"ground_link_types": ["derives_from"]}})
    assert "none declared" in doc.lower() or "unconstrained" in doc.lower()
    assert "No `[[rules.coverage]]` declared." in doc
    # still carries the fixed IDD contract regardless of config
    assert "Intent-Driven Development" in doc

def test_context_includes_live_snapshot():
    doc = _ctx()
    assert "Live snapshot" in doc
    assert "-[derives_from]->" in doc      # observed link shape line


def test_context_states_the_working_discipline():
    """SR-0129: the brief states the working discipline, not just the model —
    do only justified work, mutate the graph only through the CLI, and leave a
    reusable rule for the next agent."""
    doc = _ctx()
    assert "How to work here" in doc
    # (a) scope discipline — no ungrounded work
    assert "Do only work the graph justifies" in doc
    # (b) CLI-only — never hand-edit the structure / manifests
    assert "only through the CLI" in doc
    assert ".register.yml" in doc
    # (c) idempotent reusable rule / skill for the next agent
    assert "idempotent" in doc.lower()
    assert "skill" in doc.lower()


def test_context_asks_who_bears_the_cost_of_a_requirement(tmp_path):
    """SR-0171: the brief tells an agent that a requirement binding someone outside
    the project must name who pays, and that a clean grounding chain is not an
    answer to that question.

    The failure this closes had every automated check passing at every step
    (throughline-ratify SR-0032), so the brief is the only place the tool can raise
    it — before the item is written, not after it has been built and copied."""
    doc = _ctx()
    assert "say who pays" in doc.lower()
    # the people it binds, named as the ones without representation in the graph
    assert "contributor" in doc.lower()
    # and the specific false comfort it has to defeat
    assert "grounding chain is not an answer" in doc


def test_context_surfaces_non_goals(tmp_path):
    """SR-0097: deliberately-excluded scope must be visible to agents, so every
    live `non_goal` item is listed in the context brief by title and text."""
    from throughline.cli import _context_markdown
    ng = Item(uid="NG-1", type="non_goal", title="No editing surface",
              text="throughline shall not become a document editor.")
    doc = _context_markdown(_project(_doc("NG", ng),
                                     config={"links": {"types": ["derives_from"]},
                                             "grounding": {
                                                 "root_types": ["non_goal"],
                                                 "ground_link_types": ["derives_from"]}}))
    assert "Non-goal" in doc
    assert "No editing surface" in doc
    assert "not become a document editor" in doc


def test_context_omits_non_goal_section_when_none(tmp_path):
    """The non-goals section is absent when a project declares none, so projects
    not using them see no change (parallels the passive framing of SR-0097)."""
    doc = _ctx()  # fixture has no non_goal items
    assert "## Non-goals" not in doc


def test_every_subcommand_reaches_the_brief(tmp_path):
    """SR-0161: the brief describes the whole command surface, and a command added
    without one is made to fail here rather than pass silently.

    This is the check the requirement asks for. The commands are read off the
    parser, so a new subcommand appears in the brief automatically; what cannot be
    derived is the worked usage line, and *that* omission is what this gates. If
    it fails, add an entry to ``_CTX_COMMAND_USAGE`` for the name it prints."""
    from throughline.cli import _ctx_commands, _ctx_commands_uncovered, _subcommands
    missing = _ctx_commands_uncovered()
    assert missing == [], (
        f"subcommands with no usage line in the context brief: {missing}")
    rendered = _ctx_commands()
    for name, aliases, _ in _subcommands():
        assert f"tl {name}" in rendered, f"{name} is absent from the brief"
        for alias in aliases:
            # An alias is folded into the command it aliases, never presented as
            # a capability of its own.
            assert f"tl {alias} " not in rendered
            assert alias in rendered


def test_context_states_how_suspicion_spreads(tmp_path):
    """SR-0161: `tl invalidate` restatuses items the caller never named, so the
    vocabulary governing that cascade is stated either way — a project that
    declared none has narrowed the mechanic, not switched it off."""
    from throughline.cli import _context_markdown
    base = {"links": {"types": ["derives_from", "assumes"]},
            "grounding": {"root_types": ["intent"],
                          "ground_link_types": ["derives_from"]}}
    quiet = _context_markdown(_project(_doc("INT", Item(uid="INT-1", type="intent")),
                                       config=base))
    assert "Withdrawing link types:** none declared" in quiet
    assert "grounding links above and nothing else" in quiet

    loud_cfg = {**base, "grounding": {**base["grounding"],
                                      "suspect_link_types": ["assumes"]}}
    loud = _context_markdown(_project(_doc("INT", Item(uid="INT-1", type="intent")),
                                      config=loud_cfg))
    assert "Withdrawing link types:** `assumes`" in loud
    assert "confer no grounding" in loud
    assert "tl invalidate" in loud


# ------------------------------------------------------- docs at-ref (SR-0090)

def test_load_project_at_ref_reproduces_committed_state(tmp_path):
    """`--at REF` renders the graph as committed, ignoring later working-tree
    edits (SR-0090)."""
    import subprocess
    from throughline.storage import load_project_at_ref
    init_project(tmp_path, name="TX")
    doc = Register(prefix="SR", path=tmp_path / "sr")
    doc.path.mkdir()
    write_manifest(doc)
    it = Item(uid="SR-0001", type="requirement", status="draft",
              title="Original", text="x")
    it._register_prefix = "SR"; it._path = doc.path / "SR-0001.yml"
    write_item(it, doc)

    git = lambda *a: subprocess.run(["git", "-C", str(tmp_path), *a],
                                    capture_output=True, check=True)
    git("init"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
    git("add", "-A"); git("commit", "-m", "seed")

    # edit the working tree after committing
    it.title = "Changed in working tree"
    write_item(it, doc)

    project, sha = load_project_at_ref(tmp_path, "HEAD")
    assert len(sha) == 40
    assert project.get("SR-0001").title == "Original"   # committed, not the edit

def test_load_project_at_ref_outside_git_raises(tmp_path):
    from throughline.storage import ProjectError, load_project_at_ref
    init_project(tmp_path, name="NG")   # not a git repo
    with pytest.raises(ProjectError):
        load_project_at_ref(tmp_path, "HEAD")


# ------------------------------------------ publication coverage (SR-0096)

def _pub_project():
    intent = Item(uid="INT-1", type="intent", status="approved", title="Vision",
                  normative=True)
    fr = Item(uid="FR-1", type="requirement", status="approved", title="Wizard",
              normative=True, links=[Link(target="INT-1", type="derives_from")])
    note = Item(uid="FR-2", type="requirement", status="approved", title="Aside",
                normative=False, links=[Link(target="INT-1", type="derives_from")])
    # A normative but rejected item — dead scope that need never reach a reader,
    # so it must not be reported as unpublished (SR-0096 liveness clause).
    dead = Item(uid="FR-3", type="requirement", status="rejected", title="Dropped",
                normative=True, links=[Link(target="INT-1", type="derives_from")])
    return _project(_doc("INT", intent), _doc("FR", fr, note, dead),
                    config={"grounding": {"root_types": ["intent"],
                                          "ground_link_types": ["derives_from"]}})

def test_unpublished_rule_inert_without_published_set():
    """With no [docs] paths configured the published set is None and the rule does
    not fire — projects that do not publish see no change (SR-0096)."""
    findings = validate(_pub_project(), published=None)
    assert not [f for f in findings if f.rule == "unpublished"]

def test_unpublished_flags_normative_item_in_no_document():
    """A normative item referenced by no published document is reported; an item
    that IS referenced is not (SR-0096)."""
    findings = validate(_pub_project(), published={"INT-1"})
    flagged = {f.uid for f in findings if f.rule == "unpublished"}
    assert "FR-1" in flagged        # normative, unreferenced
    assert "INT-1" not in flagged   # referenced by a document

def test_unpublished_ignores_non_normative_items():
    """Only normative items must reach the reader; a non-normative note is not
    flagged even when unreferenced (SR-0096)."""
    findings = validate(_pub_project(), published=set())
    flagged = {f.uid for f in findings if f.rule == "unpublished"}
    assert "FR-2" not in flagged     # non-normative
    assert {"INT-1", "FR-1"} <= flagged

def test_unpublished_excludes_terminal_status_items():
    """A rejected normative item is dead scope — it need never reach a reader —
    so it is not reported as unpublished even when referenced nowhere (SR-0096)."""
    findings = validate(_pub_project(), published=set())
    flagged = {f.uid for f in findings if f.rule == "unpublished"}
    assert "FR-3" not in flagged     # rejected → terminal status, excluded
    assert "FR-1" in flagged         # a live normative item still is

def test_unpublished_default_severity_is_warning():
    findings = validate(_pub_project(), published=set())
    unpub = [f for f in findings if f.rule == "unpublished"]
    assert unpub and all(f.severity == "warning" for f in unpub)

def test_referenced_uids_none_without_docs_paths(tmp_path):
    """referenced_uids returns None when no [docs] paths are configured."""
    from throughline.inject import referenced_uids
    root = _scaffold_pub(tmp_path, docs_paths=None)
    assert referenced_uids(load_project(root)) is None

def test_referenced_uids_collects_item_and_filter_targets(tmp_path):
    """A tl:item names one UID; a tl:table/matrix publishes every matching item
    (SR-0096)."""
    from throughline.inject import referenced_uids
    root = _scaffold_pub(tmp_path, docs_paths=["*.md"])
    (root / "spec.md").write_text(
        "<!-- tl:item INT-0001 -->\n<!-- tl:end -->\n"
        "<!-- tl:table type == 'requirement' -->\n<!-- tl:end -->\n",
        encoding="utf-8")
    refs = referenced_uids(load_project(root))
    assert "INT-0001" in refs        # named directly
    assert "FR-0001" in refs         # matched by the table filter


def _scaffold_pub(tmp_path, docs_paths):
    """A scaffold with an intent (INT-0001) and one requirement (FR-0001), and an
    optional [docs] paths setting, for publication-coverage tests."""
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "feat", "--ground", "INT-0001",
                 "--no-interactive"]) == 0
    if docs_paths is not None:
        cfg = root / "throughline.toml"
        joined = ", ".join(f'"{p}"' for p in docs_paths)
        cfg.write_text(cfg.read_text(encoding="utf-8") +
                       f"\n[docs]\npaths = [{joined}]\n", encoding="utf-8")
    return root


# ------------------------------------------------ marker injection (SR-0094)

def _inject_project():
    intent = Item(uid="INT-1", type="intent", status="approved",
                  title="Ship value", text="The vision.",
                  attrs={"source_ref": "V1.2.3"})
    fr = Item(uid="FR-1", type="requirement", status="approved",
              title="Wizard", text="The system shall guide setup.\nIn three steps.",
              rationale="Newcomers stall without guidance.",
              attrs={"priority": "must"},
              links=[Link(target="INT-1", type="derives_from")])
    tc = Item(uid="TC-1", type="requirement", status="approved", title="Setup test",
              links=[Link(target="FR-1", type="verifies")])
    gone = Item(uid="FR-9", type="requirement", status="deleted", title="Dropped")
    # A rejected item that still carries a derives_from link — it must not count
    # as a live realizer in a directional matrix (SR-0099).
    dead = Item(uid="FR-2", type="requirement", status="rejected", title="Abandoned",
                links=[Link(target="INT-1", type="derives_from")])
    return _project(_doc("INT", intent), _doc("FR", fr, gone, dead), _doc("TC", tc),
                    config={"grounding": {"ground_link_types": ["derives_from"]}})

def test_inject_item_fills_only_the_marked_region():
    from throughline.inject import inject_text
    src = ("# My document\n\nSome prose I own.\n\n"
           "<!-- tl:item FR-1 -->\n<!-- tl:end -->\n\nMore of my prose.\n")
    out = inject_text(_inject_project(), src)
    # my prose is untouched, top and bottom
    assert out.startswith("# My document\n\nSome prose I own.")
    assert out.rstrip().endswith("More of my prose.")
    # the item content landed between the markers
    assert "**FR-1 — Wizard** — `requirement`, status `approved`" in out
    assert "> The system shall guide setup." in out
    assert "*Rationale:* Newcomers stall without guidance." in out
    assert "**priority**: must" in out

def test_inject_is_idempotent():
    from throughline.inject import inject_text
    src = "<!-- tl:item FR-1 -->\n<!-- tl:end -->\n"
    once = inject_text(_inject_project(), src)
    twice = inject_text(_inject_project(), once)
    assert once == twice

def test_inject_overwrites_stale_region_content():
    from throughline.inject import inject_text
    stale = ("<!-- tl:item FR-1 -->\n**FR-1 — OLD TITLE** — outdated junk\n"
             "<!-- tl:end -->\n")
    out = inject_text(_inject_project(), stale)
    assert "OLD TITLE" not in out and "outdated junk" not in out
    assert "**FR-1 — Wizard**" in out

def test_inject_table_selects_by_filter():
    from throughline.inject import inject_text
    src = "<!-- tl:table type == 'requirement' and status == 'approved' -->\n<!-- tl:end -->\n"
    out = inject_text(_inject_project(), src)
    assert "| UID | Type | Status | Title |" in out
    assert "| FR-1 | requirement | approved | Wizard |" in out
    assert "FR-9" not in out            # tombstoned item excluded

def test_inject_matrix_shows_trace_and_verification():
    from throughline.inject import inject_text
    src = "<!-- tl:matrix uid == 'FR-1' -->\n<!-- tl:end -->\n"
    out = inject_text(_inject_project(), src)
    assert "| UID | Title | Traces to | Verified by |" in out
    assert "| FR-1 | Wizard | INT-1 | TC-1 |" in out

def test_inject_matrix_incoming_lists_realizers():
    """SR-0099: incoming:<link_type> renders each match and the items that link
    TO it in that direction — here each intent and what derives_from it."""
    from throughline.inject import inject_text
    src = "<!-- tl:matrix incoming:derives_from type == 'intent' -->\n<!-- tl:end -->\n"
    out = inject_text(_inject_project(), src)
    assert "| UID | Title | Derives_from (incoming) |" in out
    assert "| INT-1 | Ship value | FR-1 |" in out

def test_inject_matrix_incoming_omits_rejected_realizers():
    """SR-0099: a rejected item (FR-2) that still links derives_from INT-1 must
    not appear as a realizer — only live items count."""
    from throughline.inject import inject_text
    src = "<!-- tl:matrix incoming:derives_from type == 'intent' -->\n<!-- tl:end -->\n"
    out = inject_text(_inject_project(), src)
    assert "| INT-1 | Ship value | FR-1 |" in out   # FR-1 live
    assert "FR-2" not in out                          # FR-2 rejected, omitted

def test_inject_matrix_outgoing_selector_lists_targets():
    """SR-0099: outgoing:<link_type> lists what each match links out to."""
    from throughline.inject import inject_text
    src = "<!-- tl:matrix outgoing:derives_from uid == 'FR-1' -->\n<!-- tl:end -->\n"
    out = inject_text(_inject_project(), src)
    assert "| UID | Title | Derives_from (outgoing) |" in out
    assert "| FR-1 | Wizard | INT-1 |" in out

def test_inject_matrix_incoming_empty_relationship_shows_dash():
    """A match with no incoming link of that type renders an em dash, not blank."""
    from throughline.inject import inject_text
    src = "<!-- tl:matrix incoming:verifies type == 'intent' -->\n<!-- tl:end -->\n"
    out = inject_text(_inject_project(), src)
    assert "| INT-1 | Ship value | — |" in out

def test_inject_matrix_target_display_attribute_only():
    """SR-0110: @<attr> renders the target's attribute instead of its UID —
    here FR-1's derives_from target INT-1 shows its source_ref, not 'INT-1'."""
    from throughline.inject import inject_text
    src = "<!-- tl:matrix outgoing:derives_from@source_ref uid == 'FR-1' -->\n<!-- tl:end -->\n"
    out = inject_text(_inject_project(), src)
    assert "| FR-1 | Wizard | V1.2.3 |" in out

def test_inject_matrix_target_display_uid_and_attribute():
    """SR-0110: @uid(<attr>) renders 'UID (attr)'."""
    from throughline.inject import inject_text
    src = "<!-- tl:matrix outgoing:derives_from@uid(source_ref) uid == 'FR-1' -->\n<!-- tl:end -->\n"
    out = inject_text(_inject_project(), src)
    assert "| FR-1 | Wizard | INT-1 (V1.2.3) |" in out

def test_inject_matrix_target_display_missing_attr_drops_brackets():
    """SR-0110: a missing secondary attribute renders no bare brackets — TC-1's
    verifies target FR-1 has no source_ref, so @uid(source_ref) shows just the UID."""
    from throughline.inject import inject_text
    src = "<!-- tl:matrix incoming:verifies@uid(source_ref) uid == 'FR-1' -->\n<!-- tl:end -->\n"
    out = inject_text(_inject_project(), src)
    assert "| FR-1 | Wizard | TC-1 |" in out

def test_inject_matrix_default_target_is_uid_unchanged():
    """SR-0110: with no @ suffix the cell is the UID, exactly as before the seam."""
    from throughline.inject import inject_text
    src = "<!-- tl:matrix outgoing:derives_from uid == 'FR-1' -->\n<!-- tl:end -->\n"
    out = inject_text(_inject_project(), src)
    assert "| FR-1 | Wizard | INT-1 |" in out

def test_inject_matrix_custom_resolver_resolves_absent_target():
    """SR-0110: a resolver supplied by a composing caller resolves a target that
    is absent from the local project — a namespace-qualified borrowed clause —
    for both liveness and attributes."""
    from throughline.inject import inject_text, TargetResolver
    fr = Item(uid="FR-1", type="requirement", status="approved", title="Wizard",
              links=[Link(target="ext:SR-9", type="satisfies")])
    proj = _project(_doc("INT", Item(uid="INT-1", type="intent", status="approved",
                                     title="Ship value")),
                    _doc("FR", fr),
                    config={"grounding": {"ground_link_types": ["derives_from"]},
                            "links": {"types": ["derives_from", "satisfies"]}})

    class _Res(TargetResolver):
        def present(self, uid):
            return uid == "ext:SR-9" or super().present(uid)
        def attr(self, uid, name):
            if uid == "ext:SR-9" and name == "source_ref":
                return "V9.9.9"
            return super().attr(uid, name)

    src = "<!-- tl:matrix outgoing:satisfies@uid(source_ref) uid == 'FR-1' -->\n<!-- tl:end -->\n"
    out = inject_text(proj, src, resolver=_Res(proj))
    assert "| FR-1 | Wizard | ext:SR-9 (V9.9.9) |" in out

def test_inject_catalog_renders_full_blocks():
    """SR-0111: tl:catalog renders every matching item as the same full block a
    tl:item marker produces, in UID order, separated by a blank line."""
    from throughline.inject import inject_text
    src = "<!-- tl:catalog type == 'intent' or uid == 'FR-1' -->\n<!-- tl:end -->\n"
    out = inject_text(_inject_project(), src)
    # both live matches appear in full — head, text, and (for FR-1) rationale
    assert "**FR-1 — Wizard** — `requirement`, status `approved`" in out
    assert "**INT-1 — Ship value** — `intent`, status `approved`" in out
    assert "> The vision." in out
    assert "*Rationale:* Newcomers stall without guidance." in out
    # UID order: INT-1 sorts before FR-1... actually 'F' < 'I', so FR-1 first
    assert out.index("**FR-1") < out.index("**INT-1")

def test_inject_catalog_empty_placeholder():
    """A filter matching nothing renders a clear placeholder, never an error."""
    from throughline.inject import inject_text
    src = "<!-- tl:catalog uid == 'NOPE-1' -->\n<!-- tl:end -->\n"
    out = inject_text(_inject_project(), src)
    assert "_(no matching items)_" in out

def test_inject_catalog_bad_filter_is_fatal():
    from throughline.inject import InjectError, inject_text
    with pytest.raises(InjectError):
        inject_text(_inject_project(),
                    "<!-- tl:catalog nonsense syntax ( -->\n<!-- tl:end -->\n")

def test_inject_item_renders_outgoing_links():
    """SR-0113: an item block lists its outgoing links grouped by type — FR-1
    derives_from INT-1, so its block carries a link line naming that target."""
    from throughline.inject import inject_text
    src = "<!-- tl:item FR-1 -->\n<!-- tl:end -->\n"
    out = inject_text(_inject_project(), src)
    assert "*Derives from:* INT-1" in out

def test_inject_item_without_links_renders_no_link_section():
    """SR-0113: an item with no outgoing links renders no link section, so an
    unlinked item's block is unchanged — INT-1 has no outgoing links."""
    from throughline.inject import inject_text
    src = "<!-- tl:item INT-1 -->\n<!-- tl:end -->\n"
    out = inject_text(_inject_project(), src)
    assert "*Derives from:*" not in out
    assert "**INT-1 — Ship value**" in out

def test_inject_item_link_display_via_resolver():
    """SR-0113: a link target is rendered through the resolver — a composing caller
    enriches a borrowed clause (here appending its reference number)."""
    from throughline.inject import inject_text, TargetResolver
    fr = Item(uid="FR-1", type="requirement", status="approved", title="Wizard",
              links=[Link(target="ext:SR-9", type="satisfies")])
    proj = _project(_doc("INT", Item(uid="INT-1", type="intent", status="approved",
                                     title="Ship value")),
                    _doc("FR", fr),
                    config={"grounding": {"ground_link_types": ["derives_from"]},
                            "links": {"types": ["derives_from", "satisfies"]}})

    class _Res(TargetResolver):
        def link_display(self, uid):
            return f"{uid} (V9.9.9)" if uid == "ext:SR-9" else super().link_display(uid)

    src = "<!-- tl:item FR-1 -->\n<!-- tl:end -->\n"
    out = inject_text(proj, src, resolver=_Res(proj))
    assert "*Satisfies:* ext:SR-9 (V9.9.9)" in out

def test_inject_catalog_blocks_render_links():
    """SR-0113: every block a tl:catalog renders lists its links too — FR-1's
    catalogue block carries the same derives_from line its tl:item block does."""
    from throughline.inject import inject_text
    src = "<!-- tl:catalog uid == 'FR-1' -->\n<!-- tl:end -->\n"
    out = inject_text(_inject_project(), src)
    assert "*Derives from:* INT-1" in out

def test_item_block_states_identity_through_resolver():
    """SR-0187: the block heading resolves identity through the target resolver, so a
    front end can state an item drawn from another graph under the identity the
    citing document uses instead of that graph's own local UID."""
    from throughline.inject import inject_text, TargetResolver
    proj = _inject_project()

    class _Res(TargetResolver):
        def display(self, uid):
            return f"ext:{uid} (V1.2.1)" if uid == "FR-1" else uid

    out = inject_text(proj, "<!-- tl:item FR-1 -->\n<!-- tl:end -->\n",
                      resolver=_Res(proj))
    assert "**ext:FR-1 (V1.2.1) — Wizard**" in out

def test_item_block_identity_unchanged_without_a_resolver():
    """SR-0187: the default resolver returns the UID unchanged, so a project that
    does not compose sees byte-identical output."""
    from throughline.inject import inject_text
    out = inject_text(_inject_project(), "<!-- tl:item FR-1 -->\n<!-- tl:end -->\n")
    assert "**FR-1 — Wizard**" in out

def test_core_does_not_provide_sourced():
    """SR-0186/NG-0007: tl:sourced needs sources core does not hold, so core does
    not provide it — and does not stub it. It is reported as unprovided."""
    from throughline.inject import InjectError, directive_names, inject_text
    assert "sourced" not in directive_names()
    with pytest.raises(InjectError) as ei:
        inject_text(_inject_project(),
                    "<!-- tl:sourced type == 'requirement' -->\n<!-- tl:end -->\n")
    assert "tl:sourced" in str(ei.value)

def test_unprovided_directive_fails_by_name_not_as_unbalanced():
    """SR-0186: a kind no registered directive provides is recognised by its general
    form and reported by name, so the author learns what is missing instead of being
    told the document has unbalanced markers."""
    from throughline.inject import InjectError, inject_text
    with pytest.raises(InjectError) as ei:
        inject_text(_inject_project(),
                    "<!-- tl:nosuchthing type == 'requirement' -->\n<!-- tl:end -->\n")
    msg = str(ei.value)
    assert "tl:nosuchthing" in msg
    assert "unbalanced" not in msg
    # The marker's tl:end is its partner, not an orphan — the old enumeration in
    # the pattern made this the confusing failure it reported instead.
    assert "front ends" in msg

def test_unprovided_directive_message_names_no_front_end():
    """SR-0186/NG-0007: core holds no mapping from a directive to the front end that
    provides it, so the message says a front end registers it without naming one."""
    from throughline.inject import InjectError, inject_text
    with pytest.raises(InjectError) as ei:
        inject_text(_inject_project(),
                    "<!-- tl:sourced type == 'requirement' -->\n<!-- tl:end -->\n")
    assert "compose" not in str(ei.value).lower()

def test_unprovided_directive_writes_nothing(tmp_path, capsys):
    """SR-0186: injection fails and no file is written — not even the documents that
    rendered cleanly before the failing one."""
    root = _scaffold_pub(tmp_path, docs_paths=["*.md"])
    good = root / "a_good.md"
    good.write_text("<!-- tl:count type == 'requirement' -->\nstale\n<!-- tl:end -->\n",
                    encoding="utf-8")
    before = good.read_text(encoding="utf-8")
    (root / "b_bad.md").write_text(
        "<!-- tl:sourced type == 'requirement' -->\n<!-- tl:end -->\n",
        encoding="utf-8")
    assert _cli(["-C", str(root), "docs"]) == 2
    assert good.read_text(encoding="utf-8") == before
    assert "tl:sourced" in capsys.readouterr().err

def test_register_directive_adds_a_kind():
    """SR-0186: a front end registers a directive of its own and injection renders
    it, so a capability depending on state core does not hold is provided by the
    layer that holds it (NG-0007)."""
    from throughline.inject import (Directive, _REGISTRY, inject_text,
                                    register_directive)
    register_directive("borrowed", lambda project, arg, resolver: f"rendered {arg}")
    try:
        out = inject_text(_inject_project(),
                          "<!-- tl:borrowed some arg -->\n<!-- tl:end -->\n")
        assert "rendered some arg" in out
    finally:
        del _REGISTRY["borrowed"]

def test_registered_directive_declares_publishing_once(tmp_path):
    """SR-0186/SR-0096: one registry entry is the only place a directive is declared,
    so the coverage question reads the same entry that rendered it — a front end's
    non-publishing directive selects items without publishing them."""
    from throughline.inject import _REGISTRY, referenced_uids, register_directive
    register_directive(
        "mirror",
        lambda project, arg, resolver: "…",
        publishes=False,
        selects=lambda project, arg: [it.uid for it in _matching_uids(project, arg)],
    )
    try:
        root = _scaffold_pub(tmp_path, docs_paths=["*.md"])
        (root / "reference.md").write_text(
            "<!-- tl:mirror type == 'requirement' -->\n<!-- tl:end -->\n",
            encoding="utf-8")
        assert "FR-0001" not in referenced_uids(load_project(root))
    finally:
        del _REGISTRY["mirror"]

def _matching_uids(project, expr):
    from throughline.inject import matching
    return matching(project, expr)

def test_unknown_modifier_is_reported():
    """SR-0186/SR-0119: the modifier is lexed generically, so an unrecognised one is
    reported rather than silently doing nothing."""
    from throughline.inject import InjectError, inject_text
    with pytest.raises(InjectError) as ei:
        inject_text(_inject_project(),
                    "<!-- tl:count.blockish type == 'requirement' -->\n<!-- tl:end -->\n")
    assert ".blockish" in str(ei.value)

def test_end_marker_is_never_read_as_a_directive():
    """SR-0186: `end` closes a region, so the generic name pattern must not treat
    `tl:end` as a directive named 'end'."""
    from throughline.inject import inject_text
    out = inject_text(_inject_project(),
                      "<!-- tl:count type == 'requirement' -->\n0\n<!-- tl:end -->\n")
    assert "tl:end" in out

def test_inject_graph_renders_colour_coded_flowchart():
    """SR-0115: tl:graph renders a Mermaid flowchart of the matching items and their
    link targets, edges labelled by link type, nodes classed by item type."""
    from throughline.inject import inject_text
    src = "<!-- tl:graph type == 'intent' or uid == 'FR-1' -->\n<!-- tl:end -->\n"
    out = inject_text(_inject_project(), src)
    assert "```mermaid" in out
    # Top-down is the only layout GitHub's Mermaid build renders reliably; LR and
    # the ELK engine both fail to draw there — SR-0115.
    assert "flowchart TD" in out
    assert 'FR_1["FR-1 — Wizard"]:::requirement' in out
    assert "FR_1 -->|derives_from| INT_1" in out
    assert "classDef intent " in out
    assert "classDef requirement " in out

def test_inject_graph_sets_external_targets_apart():
    """SR-0115: a link target that is not a matched item — a namespace-qualified
    borrowed clause — becomes an external node with the external class, and its id
    is sanitised so the colon cannot break the diagram."""
    from throughline.inject import inject_text
    fr = Item(uid="FR-1", type="requirement", status="approved", title="Wizard",
              links=[Link(target="asvs:SR-9", type="satisfies")])
    proj = _project(_doc("FR", fr),
                    config={"links": {"types": ["satisfies"]}})
    src = "<!-- tl:graph uid == 'FR-1' -->\n<!-- tl:end -->\n"
    out = inject_text(proj, src)
    assert 'asvs_SR_9["asvs:SR-9"]:::external' in out
    assert "FR_1 -->|satisfies| asvs_SR_9" in out
    assert "classDef external " in out

def test_inject_graph_collapse_external_folds_targets_by_namespace():
    """SR-0118: with the collapse-external flag, borrowed clauses fold into one node
    per source namespace, and a source draws a single de-duplicated edge to it."""
    from throughline.inject import inject_text
    fr = Item(uid="FR-1", type="requirement", status="approved", title="Wizard",
              links=[Link(target="asvs:SR-9", type="satisfies"),
                     Link(target="asvs:SR-8", type="satisfies")])
    proj = _project(_doc("FR", fr),
                    config={"links": {"types": ["satisfies"]}})
    out = inject_text(proj,
                      "<!-- tl:graph collapse-external uid == 'FR-1' -->\n"
                      "<!-- tl:end -->\n")
    # Both asvs clauses collapse to one ASVS node with a single edge into it.
    assert '_ns_asvs["ASVS"]:::external' in out
    assert "FR_1 -->|satisfies| _ns_asvs" in out
    assert "asvs_SR_9" not in out and "asvs_SR_8" not in out
    assert out.count("FR_1 -->|satisfies| _ns_asvs") == 1

def test_inject_graph_empty_and_bad_filter():
    from throughline.inject import InjectError, inject_text
    out = inject_text(_inject_project(),
                      "<!-- tl:graph uid == 'NOPE-1' -->\n<!-- tl:end -->\n")
    assert "_(no matching items to graph)_" in out
    with pytest.raises(InjectError):
        inject_text(_inject_project(),
                    "<!-- tl:graph nonsense syntax ( -->\n<!-- tl:end -->\n")

def test_inject_chart_groups_by_field():
    """SR-0116: tl:chart <key> renders a Mermaid bar chart of the live-item count
    grouped by the key — here item type."""
    from throughline.inject import inject_text
    out = inject_text(_inject_project(),
                      "<!-- tl:chart type -->\n<!-- tl:end -->\n")
    assert "xychart-beta" in out
    assert '"Items by type"' in out
    # FR-9 (deleted) and FR-2 (rejected) are not live; only FR-1, TC-1, INT-1 count.
    assert 'x-axis ["intent", "requirement"]' in out
    assert "bar [1, 2]" in out

def test_inject_chart_degree_distribution():
    """SR-0116: the reserved key 'degree' buckets nodes by total link count — a
    node-complexity distribution."""
    from throughline.inject import inject_text
    out = inject_text(_inject_project(),
                      "<!-- tl:chart degree type == 'intent' or uid == 'FR-1' -->\n"
                      "<!-- tl:end -->\n")
    assert '"Nodes by degree"' in out
    assert "xychart-beta" in out

def test_inject_chart_unknown_key_placeholder():
    """SR-0116: a key no item exhibits renders a placeholder, not an empty chart."""
    from throughline.inject import inject_text
    out = inject_text(_inject_project(),
                      "<!-- tl:chart nonesuch -->\n<!-- tl:end -->\n")
    assert "_(no data to chart for 'nonesuch')_" in out

def test_inject_chart_bad_filter_is_fatal():
    from throughline.inject import InjectError, inject_text
    with pytest.raises(InjectError):
        inject_text(_inject_project(),
                    "<!-- tl:chart type nonsense syntax ( -->\n<!-- tl:end -->\n")

def test_inject_stats_summarises_graph_complexity():
    """SR-0117: tl:stats renders item and link totals, grounding depth, the
    most-connected items, and the degree distribution."""
    from throughline.inject import inject_text
    out = inject_text(_inject_project(),
                      "<!-- tl:stats true -->\n<!-- tl:end -->\n")
    assert "**Items:**" in out and "intent 1" in out
    # Live-only: the rejected FR-2 is excluded from the live graph, so the item
    # total counts the three live items (INT-1, FR-1, TC-1), not the tombstone
    # (SR-0117 — "the live item total").
    assert "**Items:** 3 —" in out
    assert "**Links:**" in out and "derives_from" in out
    assert "**Grounding depth:**" in out
    assert "**Most connected:**" in out
    assert "**Degree distribution:**" in out

def test_inject_stats_empty_and_bad_filter():
    from throughline.inject import InjectError, inject_text
    out = inject_text(_inject_project(),
                      "<!-- tl:stats uid == 'NOPE-1' -->\n<!-- tl:end -->\n")
    assert "_(no matching items to summarise)_" in out
    with pytest.raises(InjectError):
        inject_text(_inject_project(),
                    "<!-- tl:stats nonsense syntax ( -->\n<!-- tl:end -->\n")

def test_inject_graph_chart_stats_do_not_publish(tmp_path):
    """SR-0115/SR-0116/SR-0117/SR-0096: a diagram or a statistic is not the item's
    content appearing in a document, so these directives publish nothing."""
    from throughline.inject import referenced_uids
    root = _scaffold_pub(tmp_path, docs_paths=["*.md"])
    (root / "overview.md").write_text(
        "<!-- tl:graph true -->\n<!-- tl:end -->\n"
        "<!-- tl:chart type -->\n<!-- tl:end -->\n"
        "<!-- tl:stats true -->\n<!-- tl:end -->\n", encoding="utf-8")
    refs = referenced_uids(load_project(root))
    assert refs == set()

def test_inject_unused_lists_items_no_narrative_cites(tmp_path):
    """SR-0112: tl:unused lists matching items that no narrative directive
    references. A tl:item pins INT-0001, so only FR-0001 is unreferenced."""
    from throughline import load_project
    from throughline.inject import _render_unused
    root = _scaffold_pub(tmp_path, docs_paths=["*.md"])
    (root / "spec.md").write_text(
        "<!-- tl:item INT-0001 -->\n<!-- tl:end -->\n"
        "<!-- tl:unused true -->\n<!-- tl:end -->\n", encoding="utf-8")
    out = _render_unused(load_project(root), "true")
    assert "| FR-0001 |" in out
    assert "| INT-0001 |" not in out

def test_inject_unused_catalog_is_not_narrative_use(tmp_path):
    """SR-0112: a tl:catalog mirror does not count as narrative use, so a
    full-catalogue document does not mask every item as referenced."""
    from throughline import load_project
    from throughline.inject import _render_unused
    root = _scaffold_pub(tmp_path, docs_paths=["*.md"])
    (root / "master.md").write_text(
        "<!-- tl:catalog true -->\n<!-- tl:end -->\n"
        "<!-- tl:unused true -->\n<!-- tl:end -->\n", encoding="utf-8")
    out = _render_unused(load_project(root), "true")
    assert "| INT-0001 |" in out
    assert "| FR-0001 |" in out

def test_inject_unused_needs_docs_paths(tmp_path):
    """Without configured [docs] paths the report cannot be computed and renders a
    clear note rather than treating all items as unused."""
    from throughline import load_project
    from throughline.inject import _render_unused
    root = _scaffold_pub(tmp_path, docs_paths=None)
    out = _render_unused(load_project(root), "true")
    assert "no [docs] paths configured" in out

def test_inject_count_renders_live_cardinality():
    """SR-0109: tl:count renders a bare integer — the number of live matches. The
    fixture has three requirements but FR-9 is deleted and FR-2 rejected, so only
    FR-1 and TC-1 are live."""
    from throughline.inject import inject_text
    src = "requirements: <!-- tl:count type == 'requirement' -->\n<!-- tl:end -->\n"
    out = inject_text(_inject_project(), src)
    assert "<!-- tl:count type == 'requirement' -->\n2\n<!-- tl:end -->" in out
    assert "requirements: <!-- tl:count" in out   # surrounding prose untouched

def test_inject_count_empty_match_renders_zero():
    """A filter that matches nothing renders 0 — an honest count, never an error."""
    from throughline.inject import inject_text
    src = "<!-- tl:count uid == 'NOPE-1' -->\n<!-- tl:end -->\n"
    out = inject_text(_inject_project(), src)
    assert "<!-- tl:count uid == 'NOPE-1' -->\n0\n<!-- tl:end -->" in out

def test_inject_count_bad_filter_is_fatal():
    from throughline.inject import InjectError, inject_text
    with pytest.raises(InjectError):
        inject_text(_inject_project(),
                    "<!-- tl:count nonsense syntax ( -->\n<!-- tl:end -->\n")

def test_inject_count_publishes_its_filter_matches(tmp_path):
    """SR-0109/SR-0096: the items a tl:count filter selects are published
    references, exactly as a tl:table filter's are."""
    from throughline.inject import referenced_uids
    root = _scaffold_pub(tmp_path, docs_paths=["*.md"])
    (root / "spec.md").write_text(
        "<!-- tl:count type == 'requirement' -->\n<!-- tl:end -->\n",
        encoding="utf-8")
    refs = referenced_uids(load_project(root))
    assert "FR-0001" in refs

def test_inject_count_inline_omits_wrapping_newlines():
    """SR-0119: tl:<kind>.inline renders the body with no surrounding newlines, so a
    count sits inside a sentence rather than a line-leading marker starting an HTML
    block that splits the Markdown paragraph. The fixture has two live matches."""
    from throughline.inject import inject_text
    src = ("The graph holds "
           "<!-- tl:count.inline type == 'requirement' -->x<!-- tl:end -->"
           " requirements.\n")
    out = inject_text(_inject_project(), src)
    assert ("holds <!-- tl:count.inline type == 'requirement' -->2<!-- tl:end --> "
            "requirements.") in out
    assert "\n2\n" not in out   # the inline modifier introduced no block wrapping

def test_inject_count_inline_value_matches_block_form():
    """The modifier governs only surrounding whitespace, never the body: the inline
    integer equals the one the block form renders."""
    from throughline.inject import inject_text
    inline = inject_text(_inject_project(),
        "<!-- tl:count.inline type == 'requirement' -->x<!-- tl:end -->")
    block = inject_text(_inject_project(),
        "<!-- tl:count type == 'requirement' -->\nx\n<!-- tl:end -->")
    assert "-->2<!-- tl:end -->" in inline
    assert "-->\n2\n<!-- tl:end -->" in block

def test_inject_count_inline_is_idempotent():
    """SR-0094: re-injecting already-inline output yields identical text."""
    from throughline.inject import inject_text
    src = "holds <!-- tl:count.inline type == 'requirement' -->0<!-- tl:end --> now.\n"
    once = inject_text(_inject_project(), src)
    twice = inject_text(_inject_project(), once)
    assert once == twice
    assert "-->2<!-- tl:end -->" in once

def test_inject_unknown_item_is_fatal():
    from throughline.inject import InjectError, inject_text
    with pytest.raises(InjectError):
        inject_text(_inject_project(), "<!-- tl:item NOPE-1 -->\n<!-- tl:end -->\n")

def test_inject_deleted_item_is_fatal():
    from throughline.inject import InjectError, inject_text
    with pytest.raises(InjectError):
        inject_text(_inject_project(), "<!-- tl:item FR-9 -->\n<!-- tl:end -->\n")

def test_inject_unbalanced_marker_is_fatal():
    from throughline.inject import InjectError, inject_text
    with pytest.raises(InjectError):
        inject_text(_inject_project(), "<!-- tl:item FR-1 -->\nno end marker\n")

def test_inject_bad_filter_is_fatal():
    from throughline.inject import InjectError, inject_text
    with pytest.raises(InjectError):
        inject_text(_inject_project(),
                    "<!-- tl:table nonsense syntax ( -->\n<!-- tl:end -->\n")

def test_has_markers_detects_document_files():
    from throughline.inject import has_markers
    assert has_markers("prose <!-- tl:item FR-1 --> x <!-- tl:end -->")
    assert not has_markers("# Just a normal markdown file\n\nNo markers here.\n")


# ------------------------------------------------------------------- grounding

def test_ratify_refuses_ungrounded():
    p = _project(_doc("FR", Item(uid="FR-1", type="requirement")))
    with pytest.raises(GroundingError):
        ratify(p, "FR-1", by="j.doe")

def test_ratify_refuses_ambiguous():
    intent = Item(uid="INT-1", type="intent", status="ratified")
    fr = Item(uid="FR-1", type="requirement",
              attrs={"ambiguous": True},
              links=[Link(target="INT-1", type="derives_from")])
    with pytest.raises(GroundingError):
        ratify(_project(_doc("INT", intent), _doc("FR", fr)), "FR-1", by="j.doe")

def test_ratify_succeeds_and_records_accountability():
    p = _grounded_project()
    item = ratify(p, "FR-1", by="j.doe")
    assert item.status == "ratified"
    assert item.attrs["ratified_by"] == "j.doe"

def test_ratify_refuses_an_unchanged_already_ratified_item():
    """A second ratify of unchanged content accepts nothing and would overwrite the
    record of who accepted it, leaving no trace that it changed (SR-0148)."""
    p = _grounded_project()
    ratify(p, "FR-1", by="alice")
    with pytest.raises(GroundingError, match="already ratified by alice"):
        ratify(p, "FR-1", by="bob")
    assert p.get("FR-1").attrs["ratified_by"] == "alice"

def test_ratify_after_content_changed_is_allowed_and_restamps():
    """Re-ratifying content that HAS moved is the legitimate case — a human accepts
    the new wording, and the stamp follows it (SR-0148)."""
    p = _grounded_project()
    item = ratify(p, "FR-1", by="alice")
    first = item.attrs["ratified_fingerprint"]
    item.text = "a materially different requirement"
    again = ratify(p, "FR-1", by="bob")
    assert again.attrs["ratified_by"] == "bob"
    assert again.attrs["ratified_fingerprint"] != first

# -- the composing seam (SR-0151) ------------------------------------------- #

def _consumer_and_union():
    """A consumer whose only grounding link points at a clause it does not hold —
    the shape of a composed project, where the parent is borrowed from a source.
    Returns (consumer, union); the consumer alone cannot see FR-1 reach a root."""
    borrowed = Item(uid="INT-1", type="intent", status="ratified")
    fr = Item(uid="FR-1", type="requirement", status="approved",
              links=[Link(target="INT-1", type="derives_from")])
    consumer = _project(_doc("FR", fr))
    union = _project(_doc("INT", borrowed), _doc("FR", fr))
    return consumer, union

def test_ratify_over_a_consumer_alone_cannot_see_a_borrowed_parent():
    """The premise of the seam: grounding judged over the writable graph alone
    refuses an item whose chain leaves it (SR-0151)."""
    consumer, _union = _consumer_and_union()
    with pytest.raises(GroundingError, match="not grounded to a root"):
        ratify(consumer, "FR-1", by="j.doe")

def test_a_supplied_index_grounds_over_the_wider_graph():
    """A composing caller hands in the union's index and writes to its own graph —
    the case that previously forced it to copy this function's body (SR-0151)."""
    consumer, union = _consumer_and_union()
    item = ratify(consumer, "FR-1", by="j.doe", index=Index.build(union))
    assert item.status == "ratified"
    assert item.attrs["ratified_by"] == "j.doe"
    # the whole point: the caller gets the full record, fingerprint included
    assert item.attrs["ratified_fingerprint"].startswith("sha256:")

def test_omitting_the_index_leaves_behaviour_exactly_as_before():
    p = _grounded_project()
    item = ratify(p, "FR-1", by="j.doe")
    assert item.status == "ratified" and item.attrs["ratified_fingerprint"]

def test_a_supplied_index_is_only_a_grounding_view_not_a_way_past_the_gates():
    """The index varies *where grounding is judged* and nothing else — a caller
    must not be able to reach a state the operation itself would refuse."""
    consumer, union = _consumer_and_union()
    idx = Index.build(union)

    consumer.get("FR-1").attrs["ambiguous"] = True
    with pytest.raises(GroundingError, match="ambiguous"):
        ratify(consumer, "FR-1", by="j.doe", index=idx)
    del consumer.get("FR-1").attrs["ambiguous"]

    ratify(consumer, "FR-1", by="alice", index=idx)
    with pytest.raises(GroundingError, match="already ratified by alice"):
        ratify(consumer, "FR-1", by="bob", index=idx)

def test_a_supplied_index_still_obeys_the_projects_transitions():
    """Grounding is the only thing handed in; the status move remains the config's
    to permit or refuse (SR-0130)."""
    borrowed = Item(uid="INT-1", type="intent", status="ratified")
    fr = Item(uid="FR-1", type="requirement", status="draft",
              links=[Link(target="INT-1", type="derives_from")])
    cfg = {"transitions": {"draft": ["proposed"], "proposed": ["ratified"]}}
    consumer = _project(_doc("FR", fr), config=cfg)
    union = _project(_doc("INT", borrowed), _doc("FR", fr), config=cfg)
    with pytest.raises(GroundingError, match="not an allowed transition"):
        ratify(consumer, "FR-1", by="j.doe", index=Index.build(union))


def test_rewritten_content_after_ratification_is_reported():
    """ratified_by must not vouch for words nobody agreed to: rewriting normative
    text after ratification is a named finding (SR-0148)."""
    p = _grounded_project()
    ratify(p, "FR-1", by="alice")
    p.get("FR-1").text = "something else entirely"
    assert ("FR-1", "ratified-stale") in _rules(validate(p))

def test_ratified_before_the_stamp_existed_is_not_accused():
    """An item ratified before the fingerprint stamp existed carries none and cannot
    be judged, so the rule stays silent rather than accusing the back catalogue
    (SR-0148)."""
    p = _grounded_project()
    p.get("FR-1").status = "ratified"
    p.get("FR-1").attrs["ratified_by"] = "alice"
    assert ("FR-1", "ratified-stale") not in _rules(validate(p))

# --------------------------------------------------------------------------- #
# SR-0159 / SR-0160 — suspicion travels only along links that carry justification
# --------------------------------------------------------------------------- #

def _assumption_graph(config: dict | None = None) -> Project:
    """INT-1 <- FR-1 <- NFR-1 by derives_from; FR-1 also assumes ASM-1, and NFR-1
    merely relates to a note. Invalidating ASM-1 is the interesting case."""
    intent = Item(uid="INT-1", type="intent", status="ratified")
    asm = Item(uid="ASM-1", type="assumption", status="ratified")
    fr = Item(uid="FR-1", type="requirement", status="ratified",
              links=[Link(target="INT-1", type="derives_from"),
                     Link(target="ASM-1", type="assumes")])
    nfr = Item(uid="NFR-1", type="nfr", status="ratified",
               links=[Link(target="FR-1", type="derives_from")])
    return _project(_doc("INT", intent), _doc("ASM", asm),
                    _doc("FR", fr), _doc("NFR", nfr), config=config)


_ASSUMES_WITHDRAWS = {"grounding": {"suspect_link_types": ["assumes"]}}


def test_invalidate_cascades_suspect_along_a_declared_assumption():
    """An item resting on a falsified assumption has genuinely lost its footing, so a
    project that says so gets the cascade — transitively, through the grounding links
    beyond it (SR-0159)."""
    p = _assumption_graph(_ASSUMES_WITHDRAWS)
    affected = invalidate(p, "ASM-1", reason="measured false")
    assert p.get("ASM-1").status == "rejected"
    assert set(affected) == {"FR-1", "NFR-1"}
    assert p.get("FR-1").status == "suspect"
    assert p.get("NFR-1").status == "suspect"


def test_an_undeclared_assumption_link_does_not_cascade():
    """The tool holds no link type in that set by name. 'assumes' is one string in a
    vocabulary any project may redefine, so a project that declares nothing cascades
    over its grounding links alone — the narrow default (SR-0160)."""
    p = _assumption_graph()
    assert invalidate(p, "ASM-1", reason="measured false") == []
    assert p.get("ASM-1").status == "rejected", "the item itself is still retired"
    assert p.get("FR-1").status == "ratified", "untouched"


def test_grounding_links_always_cascade_without_being_declared():
    """Grounding is justification by definition; it needs no opting in."""
    p = _assumption_graph()
    affected = invalidate(p, "INT-1", reason="withdrawn")
    assert set(affected) == {"FR-1", "NFR-1"}
    assert p.get("FR-1").status == p.get("NFR-1").status == "suspect"


def test_a_cross_reference_does_not_withdraw_footing():
    """A 'see also' is not a justification. An item that points at another for a
    reader's benefit has lost no ground to stand on when that other is withdrawn —
    and suspicion at that reach is noise that trains a reader to clear the flag
    without looking, which costs the mechanism the one thing it has (SR-0159)."""
    intent = Item(uid="INT-1", type="intent", status="ratified")
    note = Item(uid="FR-2", type="requirement", status="ratified",
                links=[Link(target="INT-1", type="derives_from")])
    fr = Item(uid="FR-1", type="requirement", status="ratified",
              links=[Link(target="INT-1", type="derives_from"),
                     Link(target="FR-2", type="relates")])
    p = _project(_doc("INT", intent), _doc("FR", note, fr),
                 config=_ASSUMES_WITHDRAWS)
    assert invalidate(p, "FR-2", reason="superseded") == []
    assert p.get("FR-1").status == "ratified"


def test_the_blast_radius_report_still_follows_every_link():
    """The two uses of the reachable set stay distinct. A reader asking what touches
    an item is asking a wider question than the tool asking whose justification has
    just been withdrawn, and only the second may restatus items on its own."""
    intent = Item(uid="INT-1", type="intent", status="ratified")
    note = Item(uid="FR-2", type="requirement", status="ratified",
                links=[Link(target="INT-1", type="derives_from")])
    fr = Item(uid="FR-1", type="requirement", status="ratified",
              links=[Link(target="INT-1", type="derives_from"),
                     Link(target="FR-2", type="relates")])
    p = _project(_doc("INT", intent), _doc("FR", note, fr))
    assert "FR-1" in Index.build(p).impact("FR-2"), "the report is unchanged"


_NO_SUSPICION_FROM_PROPOSED = {
    "status": {"values": ["proposed", "ratified", "suspect", "rejected",
                          "deleted"]},
    "transitions": {"proposed": ["ratified", "rejected", "deleted"],
                    "ratified": ["suspect", "rejected", "deleted"],
                    "suspect": ["ratified", "rejected", "deleted"],
                    "rejected": ["deleted"]},
}


def _mixed_reachability_graph() -> Project:
    """Two dependents of one root: FR-1 is proposed, which this lifecycle gives no
    route to suspect, and FR-2 is ratified, which it does."""
    intent = Item(uid="INT-1", type="intent", status="ratified")
    fr1 = Item(uid="FR-1", type="requirement", status="proposed",
               links=[Link(target="INT-1", type="derives_from")])
    fr2 = Item(uid="FR-2", type="requirement", status="ratified",
               links=[Link(target="INT-1", type="derives_from")])
    return _project(_doc("INT", intent), _doc("FR", fr1, fr2),
                    config=_NO_SUSPICION_FROM_PROPOSED)


def test_invalidate_separates_the_dependents_it_marked_from_those_it_refused():
    """Reaching a dependent and restatusing it are different events. A lifecycle with
    no route from the dependent's status refuses the move (SR-0130), and the run must
    say so rather than count the reached item as flagged (SR-0173)."""
    p = _mixed_reachability_graph()
    result = invalidate(p, "INT-1", reason="withdrawn")
    assert set(result) == {"FR-1", "FR-2"}, "the blast radius is unchanged"
    assert result.marked == ["FR-2"]
    assert result.refused == [("FR-1", "proposed", "suspect")]
    assert p.get("FR-1").status == "proposed", "refused, so genuinely untouched"
    assert "suspect_reasons" not in p.get("FR-1").attrs
    assert p.get("FR-2").status == "suspect"


def test_an_already_dead_dependent_is_neither_marked_nor_refused():
    """Nothing was withheld from an item that has already been retired, so it is not
    a gap in the lifecycle and must not be reported as one (SR-0173)."""
    intent = Item(uid="INT-1", type="intent", status="ratified")
    gone = Item(uid="FR-1", type="requirement", status="rejected",
                links=[Link(target="INT-1", type="derives_from")])
    p = _project(_doc("INT", intent), _doc("FR", gone),
                 config=_NO_SUSPICION_FROM_PROPOSED)
    result = invalidate(p, "INT-1", reason="withdrawn")
    assert result.marked == [] and result.refused == []
    assert p.get("FR-1").status == "rejected"


def test_a_refused_cascade_does_not_exit_clean(tmp_path, capsys):
    """The reader's whole basis for not going to look is the command's summary, so a
    cascade that did not fully happen must not read as one that did (SR-0173)."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--name", "t", "--bare"]) == 0
    (root / CONFIG_NAME).write_text(
        '[project]\nname = "t"\nformat_version = 3\n\n'
        '[grounding]\nroot_types = ["intent"]\n'
        'ground_link_types = ["derives_from"]\n\n'
        '[links]\ntypes = ["derives_from", "relates"]\n\n'
        '[status]\nvalues = ["proposed", "ratified", "suspect", "rejected",'
        ' "deleted"]\n\n[status.roles]\ninitial = "proposed"\n'
        'proposed = "proposed"\nratified = "ratified"\n'
        'invalidated = "rejected"\nsuspect = "suspect"\n'
        'tombstone = "deleted"\n\n[transitions]\n'
        'proposed = ["ratified", "rejected", "deleted"]\n'
        'ratified = ["suspect", "rejected", "deleted"]\n'
        'rejected = ["deleted"]\n', encoding="utf-8")
    assert _cli(["-C", str(root), "register", "new", "INT", "vision"]) == 0
    assert _cli(["-C", str(root), "register", "new", "FR", "features"]) == 0
    assert _cli(["-C", str(root), "new", "INT", "--type", "intent",
                 "--title", "why"]) == 0
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "dependent", "--ground", "INT-0001",
                 "--no-interactive"]) == 0
    capsys.readouterr()

    assert _cli(["-C", str(root), "invalidate", "INT-0001"]) == 1
    captured = capsys.readouterr()
    assert "0 dependent(s) marked suspect" in captured.out
    assert "FR-0001" not in captured.out, "not claimed as marked"
    assert "FR-0001: proposed -> suspect is not a declared transition" \
        in captured.err
    assert load_project(root).get("FR-0001").status == "proposed"


def test_a_status_with_no_route_to_suspicion_is_reported_at_the_gate():
    """The gap is in the configuration, is detectable statically, and has one settled
    remedy — so it belongs at the gate, not at the invalidation that discovers it too
    late to help (SR-0174)."""
    findings = validate(_mixed_reachability_graph())
    assert ("", "suspect-unreachable") in _rules(findings)
    assert [f for f in findings if f.rule == "suspect-unreachable"
            and "'proposed'" in f.message], "names the stranded status"
    assert not [f for f in findings if f.rule == "suspect-unreachable"
                and "'ratified'" in f.message], "a status with a route is silent"


def test_suspicion_reachability_is_unreported_without_a_lifecycle_to_judge():
    """A project that constrains nothing has nothing to answer for; the rule reads a
    declared table, it does not invent one (SR-0174)."""
    intent = Item(uid="INT-1", type="intent", status="ratified")
    p = _project(_doc("INT", intent))
    assert "suspect-unreachable" not in {f.rule for f in validate(p)}


# 'verified' is declared so borrowed items carry a valid status, and has no row in
# the table and appears as no transition's target — nothing local can ever enter it.
_BORROWED_STATUS_VOCABULARY = {
    "status": {"values": ["proposed", "ratified", "suspect", "rejected", "deleted",
                          "verified"],
               "roles": {"initial": "proposed", "proposed": "proposed",
                         "ratified": "ratified", "invalidated": "rejected",
                         "suspect": "suspect", "tombstone": "deleted"}},
    "transitions": {"proposed": ["ratified", "rejected", "deleted"],
                    "ratified": ["suspect", "rejected", "deleted"],
                    "suspect": ["ratified", "rejected", "deleted"],
                    "rejected": ["deleted"]},
}


def test_a_status_the_project_cannot_enter_is_not_a_suspicion_gap():
    """A composing project declares the statuses its borrowed items carry so the union
    validates. No local item can reach one, so reporting it as a gap is noise the
    project cannot act on — and noise is what teaches a reader to silence the rule
    that also carries the real findings (SR-0177)."""
    intent = Item(uid="INT-1", type="intent", status="ratified")
    p = _project(_doc("INT", intent), config=_BORROWED_STATUS_VOCABULARY)
    stranded = [f for f in validate(p) if f.rule == "suspect-unreachable"]
    assert not [f for f in stranded if "'verified'" in f.message]
    assert [f for f in stranded if "'proposed'" in f.message], \
        "the reachable gap is still reported"


def test_a_status_an_item_actually_sits_in_is_judged_however_it_got_there():
    """Reachability is not the whole answer: a status set by hand, or left behind by a
    lifecycle that has since changed, holds real items whose suspicion is really
    disabled. Occupancy is judged alongside reachability so those are not excused
    (SR-0177)."""
    intent = Item(uid="INT-1", type="intent", status="ratified")
    stuck = Item(uid="FR-1", type="requirement", status="verified",
                 links=[Link(target="INT-1", type="derives_from")])
    p = _project(_doc("INT", intent), _doc("FR", stuck),
                 config=_BORROWED_STATUS_VOCABULARY)
    assert [f for f in validate(p) if f.rule == "suspect-unreachable"
            and "'verified'" in f.message]


def test_a_status_only_a_borrowed_item_occupies_is_not_a_suspicion_gap():
    """A tool that composes sources merges their items into the graph, so the statuses
    those items carry arrive as occupancy — reinstating by that route exactly the
    vocabulary reachability excluded. Occupancy therefore counts locally-authored
    items only, and the borrowed status is left to the table that governs it
    (SR-0178)."""
    intent = Item(uid="INT-1", type="intent", status="ratified")
    borrowed = Item(uid="SRCFR-1", type="requirement", status="verified",
                    links=[Link(target="INT-1", type="derives_from")],
                    _authored_uid="FR-1")
    p = _project(_doc("INT", intent), _doc("SRCFR", borrowed),
                 config=_BORROWED_STATUS_VOCABULARY)
    stranded = [f for f in validate(p) if f.rule == "suspect-unreachable"]
    assert not [f for f in stranded if "'verified'" in f.message]
    assert [f for f in stranded if "'proposed'" in f.message], \
        "the reachable gap is still reported"


def test_reachability_declines_to_answer_when_there_is_nothing_to_walk():
    """None, not an empty set — a caller must be able to tell "no status is reachable"
    from "reachability cannot speak here", because reading the second as the first
    would judge every status against a lifecycle that was never declared (SR-0177)."""
    assert Schema.from_config({}).reachable_statuses() is None, "no transitions"
    no_entry = Schema.from_config({
        "status": {"values": ["a", "b"], "roles": {"ratified": "b"}},
        "transitions": {"a": ["b"]}})
    assert no_entry.reachable_statuses() is None, "no birth status to walk from"


def test_the_shipped_lifecycle_leaves_every_live_status_a_route_to_suspicion(
        tmp_path):
    """A default that disables the mechanism is not a neutral starting point, and the
    template must not trip the gate shipped beside it on the first run (SR-0175)."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--name", "t"]) == 0
    schema = load_project(root).schema
    suspect = schema.status_role("suspect")
    stranded = [s for s in schema.statuses
                if s != suspect and s not in schema.dead_statuses()
                and not schema.allows_transition(s, suspect)]
    assert stranded == []


def test_suspect_link_types_must_be_declared_link_types():
    """A typo in the declaration is caught at load, not discovered as a cascade that
    quietly never fires."""
    with pytest.raises(SchemaError, match="suspect_link_types"):
        Schema.from_config({
            "links": {"types": ["derives_from", "assumes"]},
            "grounding": {
                "ground_link_types": ["derives_from"],
                "suspect_link_types": ["assums"],
            },
        })

def test_scout_ingest_proposes_roots_and_flags_ambiguity():
    intent = Item(uid="INT-1", type="intent", status="ratified")
    fr = Item(uid="FR-1", type="requirement", status="ratified",
              attrs={"origin": "ai"},
              links=[Link(target="INT-1", type="derives_from")])
    p = _project(_doc("INT", intent, fr))
    report = {
        "proposed_roots": [{"id": "BN-9", "type": "business_need",
                            "title": "recover access", "rationale": "cluster"}],
        "ambiguities": [{"id": "FR-1", "reason": "'fast' unquantified"}],
        "coverage_gaps": [{"root": "CON-2", "detail": "unimplemented"}],
    }
    summary = scout_ingest(p, report)
    assert "BN-9" in summary["roots_proposed"]
    assert p.get("BN-9").status == "proposed"
    assert p.get("BN-9").attrs["origin"] == "ai"
    assert p.get("FR-1").attrs["ambiguous"] is True
    assert p.get("FR-1").status == "suspect"
    assert ("CON-2", "unimplemented") in summary["gaps"]


# --------------------------------------------- config-driven status ops (SR-0130/0131/0132)

def test_shipped_lifecycle_leaves_every_live_status_a_route_to_ratified(tmp_path):
    """SR-0150 — a newly initialised project must not be able to strand an item in
    a status from which no human can accept it. Walking the shipped [transitions]
    table, every live status reaches the ratified role; a dead status is exempt."""
    init_project(tmp_path, name="Example")
    project = load_project(tmp_path)
    schema = Schema.from_config(project.config)
    moves = schema.transitions
    ratified, dead = schema.status_role("ratified"), schema.dead_statuses()

    def reaches_ratified(start: str) -> bool:
        seen, queue = {start}, [start]
        while queue:
            here = queue.pop()
            if here == ratified:
                return True
            for nxt in moves.get(here, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        return False

    stranded = [s for s in sorted(schema.statuses or ())
                if s not in dead and not reaches_ratified(s)]
    assert not stranded, f"no route to '{ratified}' from: {stranded}"


def test_shipped_lifecycle_can_put_a_below_proposed_item_forward(tmp_path):
    """SR-0150 — the binding clause, and the one the old table broke. A status
    below `proposed` (one `proposed` can drop into, which cannot itself reach
    `ratified` in a single move) must be able to go back to `proposed`. Otherwise
    the only way on is via a status that reaches `ratified` without the gate —
    exactly the escape SR-0149 reports. The set is derived from the table, not
    named here, so the property survives a project renaming its statuses."""
    init_project(tmp_path, name="Example")
    schema = Schema.from_config(load_project(tmp_path).config)
    moves, dead = schema.transitions, schema.dead_statuses()
    proposed, ratified = (schema.status_role("proposed"),
                          schema.status_role("ratified"))

    below = {s for s in moves.get(proposed, ())
             if s not in dead and s not in (proposed, ratified)
             and ratified not in moves.get(s, ())}
    assert below, "expected the shipped table to have statuses below proposed"
    stranded = sorted(s for s in below if proposed not in moves.get(s, ()))
    assert not stranded, f"cannot be put forward for ratification: {stranded}"


def test_status_roles_resolve_and_report_dead(tmp_path):
    """A project binds each semantic role to one of its statuses; the tool reads
    status VALUES through the role, never a literal (SR-0131). The 'dead' set is
    the invalidated + tombstone roles."""
    s = Schema.from_config({
        "status": {"values": ["draft", "ratified", "rejected", "deleted"],
                   "roles": {"initial": "draft", "ratified": "ratified",
                             "invalidated": "rejected", "tombstone": "deleted"}}})
    assert s.status_role("ratified") == "ratified"
    assert s.status_role("initial") == "draft"
    assert s.dead_statuses() == frozenset({"rejected", "deleted"})


def test_status_role_unknown_key_and_unbound_role_fail_fast():
    """An operation asking for a role the project has not bound, or a role name the
    tool does not define, fails fast with a clear error rather than inventing a
    status literal (SR-0131, fail-fast)."""
    s = Schema.from_config({"status": {"values": ["draft"],
                                       "roles": {"initial": "draft"}}})
    with pytest.raises(SchemaError, match="unknown status role"):
        s.status_role("bogus")
    with pytest.raises(SchemaError, match="no status is bound to the 'ratified'"):
        s.status_role("ratified")


def test_status_roles_absent_leaves_vocabulary_inert():
    """A project that declares no roles keeps the role vocabulary inert, like the
    tool's other optional vocabularies — dead_statuses is empty and no literal is
    assumed."""
    s = Schema.from_config({"status": {"values": ["draft", "approved"]}})
    assert s.status_roles is None
    assert s.dead_statuses() == frozenset()


def test_status_roles_must_map_to_declared_statuses():
    """A role bound to a status the project never declared is a config that can
    never be satisfied, so it is refused at load (SR-0082)."""
    with pytest.raises(SchemaError, match=r"\[status.roles\] maps to status"):
        Schema.from_config({"status": {"values": ["draft"],
                                       "roles": {"ratified": "signed-off"}}})


def test_status_roles_reject_unknown_role_name():
    """[status.roles] may only bind roles the tool's operations act on (SR-0131)."""
    with pytest.raises(SchemaError, match=r"unknown role"):
        Schema.from_config({"status": {"values": ["draft"],
                                       "roles": {"whenever": "draft"}}})


def test_set_status_refuses_illegal_transition():
    """The single choke point every status change flows through refuses a move the
    [transitions] table forbids, at the source — the exact bug where `tl ratify`
    wrote 'ratified' over a 'draft' the config never permitted (SR-0130)."""
    from throughline.grounding import set_status
    s = Schema.from_config({
        "status": {"values": ["draft", "approved", "ratified"]},
        "transitions": {"draft": ["approved"], "approved": ["ratified"]}})
    item = Item(uid="SR-1", type="requirement", status="draft")
    with pytest.raises(GroundingError, match="not an allowed transition"):
        set_status(s, item, "ratified")
    assert item.status == "draft"            # refused at source, not written
    set_status(s, item, "approved")          # a permitted move goes through
    assert item.status == "approved"


def test_ratify_refuses_draft_when_transitions_forbid_it():
    """The regression: ratify must not emit a state `check --strict` would reject.
    With a transitions table that forbids draft -> ratified, ratify is refused
    rather than writing an illegal status (SR-0130)."""
    intent = Item(uid="INT-1", type="intent", status="ratified")
    fr = Item(uid="FR-1", type="requirement", status="draft",
              links=[Link(target="INT-1", type="derives_from")])
    cfg = {"status": {"values": ["draft", "approved", "ratified"],
                      "roles": {"initial": "draft", "ratified": "ratified"}},
           "transitions": {"draft": ["approved"], "approved": ["ratified"]}}
    p = _project(_doc("INT", intent), _doc("FR", fr), config=cfg)
    with pytest.raises(GroundingError, match="not an allowed transition"):
        ratify(p, "FR-1", by="j.doe")
    assert fr.status == "draft"


def test_operation_without_bound_role_fails_fast():
    """A status operation on a project that has not bound the role it needs fails
    fast, naming the missing role — never silently falling back to a literal
    (SR-0131)."""
    intent = Item(uid="INT-1", type="intent", status="approved")
    fr = Item(uid="FR-1", type="requirement", status="approved",
              links=[Link(target="INT-1", type="derives_from")])
    # config declares statuses + a role table WITHOUT 'ratified'
    cfg = {"status": {"values": ["draft", "approved", "ratified"],
                      "roles": {"initial": "draft"}}}
    p = _project(_doc("INT", intent), _doc("FR", fr), config=cfg)
    with pytest.raises(SchemaError, match="no status is bound to the 'ratified'"):
        ratify(p, "FR-1", by="j.doe")


def test_tl_status_verb_moves_through_permitted_transition(tmp_path):
    """`tl status <uid> <status>` is the generic verb that reaches every permitted
    move, so no state change needs a hand-edited YAML file (SR-0132). It fills the
    draft -> approved gap that had no dedicated command."""
    root = _scaffold(tmp_path)  # ships intent INT-0001 at the 'initial' role (draft)
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "feature", "--ground", "INT-0001"]) == 0
    assert load_project(root).get("FR-0001").status == "draft"
    assert _cli(["-C", str(root), "status", "FR-0001", "approved"]) == 0
    assert load_project(root).get("FR-0001").status == "approved"


def test_tl_status_verb_refuses_illegal_transition(tmp_path):
    """The generic verb is transition-validated: an illegal move is refused with a
    non-zero exit and the on-disk status is untouched (SR-0130/0132)."""
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "feature", "--ground", "INT-0001"]) == 0
    # draft -> verified is not a permitted first hop in the seeded transitions
    assert _cli(["-C", str(root), "status", "FR-0001", "verified"]) != 0
    assert load_project(root).get("FR-0001").status == "draft"


def test_tl_status_verb_rejects_undeclared_status(tmp_path):
    """A target that is not in the declared [status] values is refused (SR-0132)."""
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "feature", "--ground", "INT-0001"]) == 0
    assert _cli(["-C", str(root), "status", "FR-0001", "nonsense"]) in (1, 2)
    assert load_project(root).get("FR-0001").status == "draft"


def test_new_item_is_born_at_the_initial_role(tmp_path):
    """`tl new` gives a fresh item the project's 'initial' status, resolved from the
    role table rather than a literal 'draft' baked in code (SR-0131)."""
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "feature", "--ground", "INT-0001"]) == 0
    born = load_project(root).get("FR-0001")
    initial = load_project(root).schema.status_role("initial")
    assert born.status == initial == "draft"


def test_delete_writes_the_tombstone_role(tmp_path):
    """`tl delete` retires a UID by moving it to the 'tombstone' status resolved
    from the role table (SR-0131)."""
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "feature", "--ground", "INT-0001"]) == 0
    assert _cli(["-C", str(root), "delete", "FR-0001", "--reason", "dropped"]) == 0
    gone = load_project(root).get("FR-0001")
    assert gone.status == load_project(root).schema.status_role("tombstone") == "deleted"


def test_migrate_v2_to_v3_backfills_status_roles(tmp_path):
    """A v2 project has no [status.roles]; migrating to v3 backfills the role table
    using the status literals the pre-v3 operations had hardcoded, preserving
    behaviour, and the upgraded project then loads and operates (SR-0131, NFR-0010)."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--bare"]) == 0
    # strip the roles the v3 template ships, and drop the version to v2, to build a
    # genuine pre-roles project.
    cfg_file = root / "throughline.toml"
    text = cfg_file.read_text(encoding="utf-8")
    text = text.split("[status.roles]")[0].rstrip() + "\n"
    text = text.replace(f"format_version = {FORMAT_VERSION}", "format_version = 2")
    cfg_file.write_text(text, encoding="utf-8")
    with pytest.raises(ProjectError, match="tl migrate"):
        load_project(root)
    assert migrate_project(root)[:2] == (2, FORMAT_VERSION)
    schema = load_project(root).schema
    assert schema.status_role("ratified") == "ratified"
    assert schema.status_role("initial") == "draft"
    assert schema.dead_statuses() == frozenset({"rejected", "deleted"})


def test_migrate_v2_to_v3_only_binds_declared_statuses(tmp_path):
    """The backfill binds a role only when its default target status is among the
    project's declared [status] values, so the injected table never references a
    status the project does not know (would fail the consistency check)."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--bare"]) == 0
    cfg_file = root / "throughline.toml"
    # a project whose vocabulary lacks 'rejected'/'deleted'/'proposed'/'suspect'
    text = '''[project]
name = "narrow"
format_version = 2

[status]
values = ["draft", "ratified"]
'''
    cfg_file.write_text(text, encoding="utf-8")
    assert migrate_project(root)[:2] == (2, FORMAT_VERSION)
    schema = load_project(root).schema
    assert schema.status_role("initial") == "draft"
    assert schema.status_role("ratified") == "ratified"
    # roles whose default status is undeclared are simply not bound
    assert "invalidated" not in (schema.status_roles or {})
    assert schema.dead_statuses() == frozenset()


def test_read_project_tolerates_older_major_without_touching_disk(tmp_path):
    """A read-only consumer of a source (compose) reads it through read_project, which
    tolerates an on-disk major older than this build by upgrading the format *in
    memory* — where strict load_project refuses it — and never rewrites the tree, so
    consuming a source never forces a migrate first (SR-0017, NFR-0010)."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--bare"]) == 0
    cfg = root / "throughline.toml"
    text = cfg.read_text(encoding="utf-8")
    text = text.split("[status.roles]")[0].rstrip() + "\n"  # a genuine pre-roles v2
    text = text.replace(f"format_version = {FORMAT_VERSION}", "format_version = 2")
    cfg.write_text(text, encoding="utf-8")
    before = cfg.read_text(encoding="utf-8")
    with pytest.raises(ProjectError, match="tl migrate"):
        load_project(root)
    schema = read_project(root).schema  # tolerant read: roles backfilled in memory
    assert schema.status_role("initial") == "draft"
    assert schema.status_role("ratified") == "ratified"
    assert cfg.read_text(encoding="utf-8") == before  # disk untouched — still v2
    assert "format_version = 2" in before and "[status.roles]" not in before


def test_read_project_reads_legacy_v1_manifest_layout(tmp_path):
    """read_project discovers registers across every manifest name the tooling knows,
    so a v1 source whose register manifest is still `.document.yml` loads read-only
    without the on-disk rename `tl migrate` would perform (SR-0017)."""
    root = tmp_path / "proj"
    reg = _make_legacy_v1_project(root)
    assert (reg / ".document.yml").exists()
    with pytest.raises(ProjectError, match="tl migrate"):
        load_project(root)
    project = read_project(root)
    assert [it.title for it in project.items()] == ["legacy item"]
    assert (reg / ".document.yml").exists()  # nothing renamed on disk
    assert not (reg / ".register.yml").exists()


def test_read_project_still_refuses_a_future_major(tmp_path):
    """Tolerance runs one way: read_project upgrades an older format in memory but
    cannot parse a major newer than this build, so it refuses exactly as load_project
    does — the future is never mis-parsed (SR-0017, NFR-0010)."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--bare"]) == 0
    _set_format_version(root, FORMAT_VERSION + 1)
    with pytest.raises(ProjectError, match="upgrade tl"):
        read_project(root)


# -------------------------------------------------- self-hosting / demo is green

# These proofs resolve repo-only fixture directories (the self-hosted spine and
# the grounding demo) that are intentionally not packaged in the sdist, to keep
# the tarball small. Off the repo (e.g. running the shipped suite from a pip
# sdist) they must skip cleanly rather than error, so the published suite passes
# out of the box; in CI, where the directories are present, they run and gate
# (SR-0135, mirrors SR-0061 self-hosting).
_needs_demo = pytest.mark.skipif(
    not DEMO.exists(), reason="grounding demo not packaged in the sdist (SR-0135)")
_needs_selfhost = pytest.mark.skipif(
    not SELFHOST.exists(),
    reason="self-hosted requirements spine not packaged in the sdist (SR-0135)")


@_needs_demo
def test_demo_project_passes_strict():
    """The committed demo must stay green under --strict — the CI-gate contract
    (mirrors SR-0061 self-hosting)."""
    p = load_project(DEMO)
    assert _errors(validate(p, strict=True)) == []

@_needs_demo
def test_demo_has_no_uid_collisions():
    assert collisions(load_project(DEMO)) == []

@_needs_selfhost
def test_selfhost_project_passes_strict():
    """throughline's own spec, seeded as a throughline project, must stay grounded
    under --strict — the tool dogfoods its own scope-discipline gate (SR-0061)."""
    p = load_project(SELFHOST)
    assert _errors(validate(p, strict=True)) == []

@_needs_selfhost
def test_selfhost_has_no_uid_collisions():
    assert collisions(load_project(SELFHOST)) == []


# ---------------------------------------------- grounding-assisted authoring (SR-0073)

from throughline.cli import main as _cli  # noqa: E402


def _scaffold(tmp_path) -> Path:
    """A minimal project with a root (INT) and a home for requirements (FR). Uses
    --bare so the test controls the whole graph rather than inheriting the seeded
    starter (SR-0100)."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--name", "t", "--bare"]) == 0
    assert _cli(["-C", str(root), "register", "new", "INT", "vision"]) == 0
    assert _cli(["-C", str(root), "register", "new", "FR", "features"]) == 0
    assert _cli(["-C", str(root), "new", "INT", "--type", "intent",
                 "--title", "why"]) == 0
    return root


def test_docs_injects_markers_into_a_named_file(tmp_path):
    """`tl docs FILE` fills marked regions from the graph and leaves the rest of
    the human-owned file byte-for-byte intact (SR-0094)."""
    root = _scaffold(tmp_path)  # ships an intent INT-0001 titled "why"
    doc = root / "overview.md"
    doc.write_text("# Overview\n\nMy prose.\n\n"
                   "<!-- tl:item INT-0001 -->\n<!-- tl:end -->\n", encoding="utf-8")
    assert _cli(["-C", str(root), "docs", str(doc)]) == 0
    out = doc.read_text(encoding="utf-8")
    assert out.startswith("# Overview\n\nMy prose.")
    assert "**INT-0001 — why**" in out


def test_docs_uses_config_paths_and_skips_marker_free_files(tmp_path):
    """With no file arguments, `tl docs` injects the [docs] paths from config and
    never touches a file that has no tl: markers (SR-0094/0095)."""
    root = _scaffold(tmp_path)
    cfg = root / "throughline.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + '\n[docs]\npaths = ["*.md"]\n',
                   encoding="utf-8")
    spec = root / "spec.md"
    spec.write_text("<!-- tl:item INT-0001 -->\n<!-- tl:end -->\n", encoding="utf-8")
    prose = root / "notes.md"
    prose.write_text("just prose, no markers\n", encoding="utf-8")
    assert _cli(["-C", str(root), "docs"]) == 0
    assert "INT-0001 — why" in spec.read_text(encoding="utf-8")
    assert prose.read_text(encoding="utf-8") == "just prose, no markers\n"


def test_docs_treats_marker_free_configured_doc_as_clean_noop(tmp_path):
    """A configured document that currently holds no tl: markers is treated no
    differently from one full of them: `tl docs` is a no-op that succeeds (exit 0)
    and leaves the file byte-for-byte unchanged, rather than erroring "no
    documents to inject". Nothing to inject shouldn't matter (SR-0094)."""
    root = _scaffold(tmp_path)
    cfg = root / "throughline.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + '\n[docs]\npaths = ["*.md"]\n',
                   encoding="utf-8")
    prose = root / "notes.md"
    original = "# Notes\n\nAll prose, no markers yet.\n"
    prose.write_text(original, encoding="utf-8")
    assert _cli(["-C", str(root), "docs"]) == 0            # not an error
    assert prose.read_text(encoding="utf-8") == original   # untouched
    assert _cli(["-C", str(root), "docs", "--check"]) == 0  # gate inert, passes


def test_docs_check_passes_when_up_to_date(tmp_path):
    """`tl docs --check` is the separate CI gate (SR-0095): a document whose marked
    regions already match the graph passes with exit 0."""
    root = _scaffold(tmp_path)
    doc = root / "overview.md"
    doc.write_text("# Overview\n\n<!-- tl:item INT-0001 -->\n<!-- tl:end -->\n",
                   encoding="utf-8")
    assert _cli(["-C", str(root), "docs", str(doc)]) == 0        # inject once
    assert _cli(["-C", str(root), "docs", str(doc), "--check"]) == 0  # now clean


def test_docs_check_fails_and_does_not_write_when_stale(tmp_path):
    """A drifted document fails the gate with a non-zero exit and is left on disk
    unchanged — --check reports drift, it never fixes it (SR-0095)."""
    root = _scaffold(tmp_path)
    doc = root / "overview.md"
    stale = ("# Overview\n\n<!-- tl:item INT-0001 -->\nSTALE CONTENT\n"
             "<!-- tl:end -->\n")
    doc.write_text(stale, encoding="utf-8")
    rc = _cli(["-C", str(root), "docs", str(doc), "--check"])
    assert rc == 1                                   # FINDINGS exit code
    assert doc.read_text(encoding="utf-8") == stale  # untouched by --check


def test_docs_check_inert_without_markers(tmp_path):
    """A file with no tl: markers is not a throughline document, so the gate is
    inert and passes (SR-0095)."""
    root = _scaffold(tmp_path)
    doc = root / "plain.md"
    doc.write_text("# Just prose\n", encoding="utf-8")
    assert _cli(["-C", str(root), "docs", str(doc), "--check"]) == 0


def test_new_ground_flag_grounds_at_birth(tmp_path):
    """--ground attaches a parent when the item is created, so it is justified
    the moment it exists (SR-0073) rather than being caught later by check."""
    root = _scaffold(tmp_path)
    rc = _cli(["-C", str(root), "new", "FR", "--type", "requirement",
               "--title", "feat", "--ground", "INT-0001",
               "--ground-type", "derives_from", "--no-interactive"])
    assert rc == 0
    item = load_project(root).get("FR-0001")
    assert [(l.target, l.type) for l in item.links] == [("INT-0001", "derives_from")]


def test_new_ground_defaults_to_derives_from(tmp_path):
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "feat", "--ground", "INT-0001",
                 "--no-interactive"]) == 0
    item = load_project(root).get("FR-0001")
    assert item.links[0].type == "derives_from"


def test_new_ground_rejects_missing_target(tmp_path):
    root = _scaffold(tmp_path)
    rc = _cli(["-C", str(root), "new", "FR", "--type", "requirement",
               "--title", "feat", "--ground", "NOPE-0001", "--no-interactive"])
    assert rc == 2
    assert load_project(root).get("FR-0001") is None


def test_new_non_interactive_leaves_ungrounded(tmp_path):
    """Backward compat: without --ground and non-interactive, creation is
    unchanged — the item is born orphaned and check will flag it."""
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "feat", "--no-interactive"]) == 0
    assert load_project(root).get("FR-0001").links == []


def test_new_root_honors_explicit_grounding(tmp_path):
    """An EXPLICIT --ground link on a root type must be attached, never silently
    dropped (SR-0091). Roots are exempt from the interactive prompt, but a link
    the author asked for is authoring intent — a root may legitimately ground to
    another root (e.g. a business_need that derives_from the vision)."""
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "new", "INT", "--type", "intent",
                 "--title", "another", "--ground", "INT-0001",
                 "--ground-type", "derives_from", "--no-interactive"]) == 0
    item = load_project(root).get("INT-0002")
    assert [(l.target, l.type) for l in item.links] == [("INT-0001", "derives_from")]


def test_init_reports_absolute_path(tmp_path, capsys):
    """init echoes where the project actually landed, not a bare '.' (SR-0077)."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--name", "t"]) == 0
    out = capsys.readouterr().out
    assert str(root.resolve()) in out


def test_scaffold_ships_non_goal_root_type(tmp_path):
    """SR-0097: the default scaffold declares a `non_goal` type that is a root
    (self-justifying, may be ungrounded) but NOT a delivery root — a non-goal is
    negative space, so nothing needs to derive from it and check must not flag it
    unserved."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--name", "t"]) == 0
    schema = load_project(root).schema
    assert "non_goal" in schema.types            # a first-class declared type
    assert "non_goal" in schema.root_types       # may exist ungrounded
    assert "non_goal" not in schema.delivery_roots  # passive: never 'unserved'


def test_init_seeds_a_check_clean_starter_graph(tmp_path):
    """By default init seeds a small grounded example so a fresh project passes
    `tl check` and renders content immediately, instead of leaving the newcomer an
    empty project to reverse-engineer from the schema (SR-0100)."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--name", "Widget"]) == 0
    proj = load_project(root)
    assert {it.uid for it in proj.items()} == {
        "INT-0001", "REQ-0001", "NFR-0001", "TEST-0001", "NG-0001"}
    # sound under the strictest gate, with no hand-fixing required
    assert _cli(["-C", str(root), "check", "--strict", "--quiet"]) == 0
    # and the published document ships already rendered, not as empty markers
    overview = (root / "docs" / "overview.md").read_text(encoding="utf-8")
    assert "**INT-0001 — Deliver Widget**" in overview
    assert "| REQ-0001 | requirement |" in overview


def test_init_seeded_docs_are_fresh(tmp_path):
    """The seeded document is injected at init time, so `tl docs --check` — the CI
    freshness gate — is green on a fresh project from minute one (SR-0100)."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init"]) == 0
    assert _cli(["-C", str(root), "docs", "--check"]) == 0


def test_init_bare_writes_only_the_config(tmp_path):
    """--bare suppresses the starter entirely: only throughline.toml is written,
    with publication left off, for users who want an empty project (SR-0100)."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--bare"]) == 0
    assert (root / "throughline.toml").exists()
    assert list(load_project(root).items()) == []
    assert not (root / "docs").exists()
    assert "[docs]" not in (root / "throughline.toml").read_text(encoding="utf-8")


def test_init_no_demo_keeps_registers_omits_items(tmp_path):
    """--no-demo suppresses the seeded example items and rendered document but still
    creates the default registers, so the user gets an empty-but-scaffolded project
    (SR-0100)."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--no-demo"]) == 0
    proj = load_project(root)
    assert list(proj.items()) == []                       # no seeded items
    assert set(proj.registers) == {"INT", "REQ", "NFR", "NG", "TEST"}  # registers present
    assert not (root / "docs").exists()                   # no rendered document
    assert "[docs]" not in (root / "throughline.toml").read_text(encoding="utf-8")


def test_init_no_defaults_omits_registers_and_demo(tmp_path):
    """--no-defaults omits the default registers, and since seeded items have nowhere
    to live it takes the demo with it — leaving only throughline.toml (SR-0100)."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--no-defaults"]) == 0
    proj = load_project(root)
    assert list(proj.registers) == []
    assert list(proj.items()) == []
    assert not (root / "docs").exists()


def _make_tty(monkeypatch, answers):
    """Simulate an interactive terminal for the wizard (SR-0120/0121): mark the CLI
    interactive and feed the picker's ``input`` calls from ``answers`` in order."""
    import throughline.cli as _climod
    monkeypatch.setattr(_climod, "_interactive", lambda: True)
    supply = iter(answers)
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(supply))


def _two_type_project(tmp_path) -> Path:
    """A scaffold with two item types present (intent INT-0001 + requirement FR-0001)
    so the type-then-item picker exercises both stages."""
    root = _scaffold(tmp_path)  # ships intent INT-0001 "why"
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "a feature", "--ground", "INT-0001",
                 "--no-interactive"]) == 0
    return root


def test_item_command_fails_fast_when_non_interactive(tmp_path):
    """On a non-interactive session an omitted item UID does not prompt (which would
    hang CI): it fails fast with a usage error naming the missing detail (SR-0120)."""
    root = _two_type_project(tmp_path)
    # pytest's stdin is not a TTY, so _interactive() is False here by default.
    assert _cli(["-C", str(root), "delete"]) == 2               # USAGE
    assert load_project(root).get("FR-0001").status != "deleted"  # nothing changed


def test_supplied_uid_bypasses_the_picker(tmp_path, monkeypatch):
    """A UID given as an argument runs the command directly, never prompting, so
    scripts are unaffected (SR-0121) — even on an interactive terminal. The picker's
    ``input`` is wired to raise, proving it is never consulted."""
    root = _two_type_project(tmp_path)
    import throughline.cli as _climod
    monkeypatch.setattr(_climod, "_interactive", lambda: True)
    def _boom(*a, **k):
        raise AssertionError("picker consulted despite a supplied UID")
    monkeypatch.setattr("builtins.input", _boom)
    assert _cli(["-C", str(root), "delete", "FR-0001"]) == 0
    assert load_project(root).get("FR-0001").status == "deleted"


def test_picker_selects_type_then_item(tmp_path, monkeypatch):
    """With the UID omitted on a terminal, the wizard offers a type then an item and
    acts on the chosen one (SR-0121). Types sort as [intent, requirement]; choosing
    '2' then '1' tombstones FR-0001."""
    root = _two_type_project(tmp_path)
    _make_tty(monkeypatch, ["2", "1"])   # type #2 (requirement), item #1 (FR-0001)
    assert _cli(["-C", str(root), "delete"]) == 0
    assert load_project(root).get("FR-0001").status == "deleted"


def test_picker_cancel_returns_usage_and_writes_nothing(tmp_path, monkeypatch):
    """An empty answer cancels the picker cleanly (the grounding convention): the
    command makes no change and returns a usage code (SR-0121)."""
    root = _two_type_project(tmp_path)
    _make_tty(monkeypatch, [""])         # cancel at the type prompt
    assert _cli(["-C", str(root), "delete"]) == 2
    assert load_project(root).get("FR-0001").status != "deleted"


def test_link_wizard_selects_both_endpoints_and_type(tmp_path, monkeypatch):
    """A two-endpoint command selects each endpoint in turn and then the link type
    (SR-0120/0121). Both items are intents/requirements; feed source, destination,
    and a free-text link type."""
    root = _two_type_project(tmp_path)
    # source: type intent(#1)->INT-0001 ; dest: type requirement(#2)->FR-0001 ; type
    _make_tty(monkeypatch, ["1", "1", "2", "1", "relates_to"])
    assert _cli(["-C", str(root), "link"]) == 0
    links = load_project(root).get("INT-0001").links
    assert ("FR-0001", "relates_to") in [(l.target, l.type) for l in links]


def test_ratify_wizard_prompts_for_ratifier(tmp_path, monkeypatch):
    """ratify with no --by prompts for the ratifier identity on a terminal, keeping
    the value flag-settable (SR-0120)."""
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "feat", "--status", "proposed", "--ground", "INT-0001",
                 "--no-interactive"]) == 0
    # single type after picking is 'requirement'; but INT-0001 is also live, so the
    # type stage runs: [intent, requirement] -> pick requirement(#2), item #1, then by.
    _make_tty(monkeypatch, ["2", "1", "henry"])
    assert _cli(["-C", str(root), "ratify"]) == 0
    assert load_project(root).get("FR-0001").attrs["ratified_by"] == "henry"


def test_register_new_refuses_duplicate_prefix(tmp_path):
    """A prefix owns a UID namespace and must be unique (SR-0101). `tl register
    new` refuses a prefix another register already declares, exits with a usage
    error, and writes nothing — rather than letting the loader silently drop one
    register's items."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--bare"]) == 0
    assert _cli(["-C", str(root), "register", "new", "SR", "system"]) == 0
    # a second register reusing prefix SR into a different folder is refused
    assert _cli(["-C", str(root), "register", "new", "SR", "system2"]) == 2  # USAGE
    assert not (root / "system2" / ".register.yml").exists()   # nothing written


def test_check_reports_prefix_collision_from_disk(tmp_path):
    """The validation backstop for a clash introduced by a merge or hand edit
    (SR-0101): two register folders on disk declaring the same prefix make the
    loader keep only one, so `check` reports a prefix-collision error rather than
    silently loading a graph missing items."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--bare"]) == 0
    for sub in ("a", "b"):
        d = root / sub
        d.mkdir()
        write_manifest(Register(prefix="SR", path=d))
    rules = {f.rule for f in validate(load_project(root))}
    assert "prefix-collision" in rules
    assert _cli(["-C", str(root), "check", "--quiet"]) == 1  # FINDINGS


def _set_format_version(root, value):
    """Rewrite the format_version line of a project's config for the tests below."""
    cfg = root / "throughline.toml"
    text = cfg.read_text(encoding="utf-8")
    if value is None:  # simulate a hand-authored / pre-versioning config
        lines = [ln for ln in text.splitlines() if not ln.strip().startswith("format_version")]
        cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return
    import re
    cfg.write_text(re.sub(r"(?m)^format_version\s*=.*$",
                          f"format_version = {value}", text), encoding="utf-8")


def _make_legacy_v1_project(root):
    """Build a project in the pre-register (v1) on-disk layout: a `.document.yml`
    manifest holding one item, and format_version = 1 — exactly what any released
    tl (0.1.0-0.1.4) wrote before the register rename (SR-0102)."""
    assert _cli(["-C", str(root), "init", "--bare"]) == 0
    assert _cli(["-C", str(root), "register", "new", "SR", "system"]) == 0
    assert _cli(["-C", str(root), "new", "SR", "--type", "system_requirement",
                 "--no-interactive", "--title", "legacy item"]) == 0
    reg = root / "system"
    (reg / ".register.yml").rename(reg / ".document.yml")  # the v1 manifest name
    _set_format_version(root, 1)
    return reg


def test_load_refuses_newer_format_version(tmp_path):
    """A project whose format major is newer than this tl must refuse to load and
    tell the user to upgrade tl, never silently mis-parse a future format
    (NFR-0010)."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--bare"]) == 0
    _set_format_version(root, FORMAT_VERSION + 1)
    with pytest.raises(ProjectError, match="upgrade that installation"):
        load_project(root)
    assert _cli(["-C", str(root), "check"]) == 2  # USAGE — refuses to run


def test_version_refusal_names_the_tl_that_refused(tmp_path):
    """Both version refusals identify the running copy by version and path, so a
    reader with a current tl on their own machine can tell that the refusal came
    from somewhere else — a build runner, say — rather than conclude the Tool is
    wrong about itself (SR-0168)."""
    import throughline

    here = str(Path(throughline.__file__).resolve().parent)

    newer = tmp_path / "newer"
    assert _cli(["-C", str(newer), "init", "--bare"]) == 0
    _set_format_version(newer, FORMAT_VERSION + 1)
    with pytest.raises(ProjectError) as caught:
        load_project(newer)
    message = str(caught.value)
    assert throughline.__version__ in message and here in message
    # both majors and the project it read stay in the message
    assert str(FORMAT_VERSION + 1) in message and str(FORMAT_VERSION) in message
    assert str(newer / CONFIG_NAME) in message

    older = tmp_path / "older"
    _make_legacy_v1_project(older)
    with pytest.raises(ProjectError) as caught:
        load_project(older)
    older_message = str(caught.value)
    assert throughline.__version__ in older_message and here in older_message
    # the remedy here is the project's, not the Tool's — so no upgrade command
    assert "tl migrate" in older_message
    assert "pip install" not in older_message and "pipx" not in older_message


def test_upgrade_command_matches_how_the_tool_was_installed(monkeypatch, tmp_path):
    """The remedy is derived from where the running interpreter lives, never
    assumed. Telling someone who installed through pipx to run `pip install`
    sends them to change an environment other than the one that refused
    (SR-0168)."""
    from throughline import storage

    monkeypatch.setattr(sys, "prefix", "/home/u/.local/share/pipx/venvs/throughline")
    assert storage._upgrade_command() == "pipx upgrade throughline"

    # injected into a sibling's pipx venv — name the venv that actually owns it
    monkeypatch.setattr(sys, "prefix",
                        "/home/u/.local/share/pipx/venvs/throughline-compose")
    assert storage._upgrade_command() == "pipx upgrade throughline-compose"

    # anything else is upgraded through the interpreter that is running, so the
    # command cannot land in a different environment than the one that refused
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "venv"))
    monkeypatch.setattr(sys, "executable", "/opt/venv/bin/python")
    assert storage._upgrade_command() == (
        "/opt/venv/bin/python -m pip install --upgrade throughline")


def test_load_points_older_format_at_migrate(tmp_path):
    """A v1 project (declared) refuses to load under this v2 tl and points at
    `tl migrate`, rather than silently loading its `.document.yml` registers as an
    empty graph — the data-loss trap the gate exists to prevent (NFR-0010)."""
    root = tmp_path / "proj"
    reg = _make_legacy_v1_project(root)
    assert (reg / ".document.yml").exists()
    with pytest.raises(ProjectError, match="tl migrate"):
        load_project(root)
    assert _cli(["-C", str(root), "check"]) == 2  # USAGE — refuses to run


def test_load_missing_field_empty_project_assumes_current(tmp_path):
    """An empty config with no format_version and no registers on disk is assumed
    current and loads, honouring no-lock-in rather than rejecting it (UR-0015)."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--bare"]) == 0
    _set_format_version(root, None)
    assert list(load_project(root).items()) == []  # loads without error


def test_infer_absent_field_from_v1_layout_routes_to_migrate(tmp_path):
    """The content-inference case: a config that omits format_version but has the
    v1 `.document.yml` layout is inferred as v1 and routed to migrate, not
    mis-read as an empty current-format graph (NFR-0010, UR-0015)."""
    root = tmp_path / "proj"
    _make_legacy_v1_project(root)
    _set_format_version(root, None)  # drop the version line entirely
    with pytest.raises(ProjectError, match="tl migrate"):
        load_project(root)
    assert migrate_project(root)[:2] == (1, FORMAT_VERSION)  # migrate off the inferred major


def test_infer_absent_field_from_v2_layout_routes_to_migrate(tmp_path):
    """A config that omits format_version but has the `.register.yml` layout is
    inferred as v2 — the major that introduced that layout — and routed to migrate,
    since the v2->v3 change is a config addition the layout cannot reveal (UR-0015,
    NFR-0010). `tl migrate` then upgrades it to the current major."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--bare"]) == 0
    assert _cli(["-C", str(root), "register", "new", "SR", "system"]) == 0
    _set_format_version(root, None)
    with pytest.raises(ProjectError, match="tl migrate"):
        load_project(root)
    assert migrate_project(root)[:2] == (2, FORMAT_VERSION)
    assert list(load_project(root).items()) == []  # loads after upgrade


def test_migrate_current_project_is_noop(tmp_path):
    """`tl migrate` on an already-current, sound project changes nothing (no repair
    fires) and exits 0."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--bare"]) == 0
    result = migrate_project(root)
    assert (result.start, result.end) == (FORMAT_VERSION, FORMAT_VERSION)
    assert result.repaired is None  # sound project: the current-major repair no-ops
    assert _cli(["-C", str(root), "migrate"]) == 0


def _strip_status_roles(root: Path) -> None:
    """Drop the [status.roles] table the template ships while leaving the project at
    the current major — i.e. a project hand-authored at this major that never met
    the upgrade which introduces the table."""
    cfg = root / "throughline.toml"
    text = cfg.read_text(encoding="utf-8")
    cfg.write_text(text.split("[status.roles]")[0].rstrip() + "\n", encoding="utf-8")


def test_migrate_repairs_current_major_missing_status_roles(tmp_path):
    """A project already at the current major but missing the [status.roles] table
    that major requires is repaired in place by the same `tl migrate` — not left for
    a hand-edit (SR-0137). The repair reports the bindings it wrote, and the project
    then loads and resolves roles."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--bare"]) == 0
    _strip_status_roles(root)
    with pytest.raises(SchemaError):  # roles inert -> role lookups cannot resolve
        load_project(root).schema.status_role("ratified")

    result = migrate_project(root)
    assert (result.start, result.end) == (FORMAT_VERSION, FORMAT_VERSION)
    assert result.repaired and result.repaired["ratified"] == "ratified"

    schema = load_project(root).schema  # now loads and operates
    assert schema.status_role("initial") == "draft"
    # idempotent: a second migrate finds the table present and writes nothing
    assert migrate_project(root).repaired is None


def test_migrate_current_major_repair_reported_on_cli(tmp_path, capsys):
    """`tl migrate` names each binding it backfilled so an in-place repair is never
    silent (SR-0137)."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--bare"]) == 0
    _strip_status_roles(root)
    assert _cli(["-C", str(root), "migrate"]) == 0
    out = capsys.readouterr().out
    assert "backfilled [status.roles]" in out
    assert 'ratified = "ratified"' in out


def test_migrate_leaves_declared_empty_status_roles_untouched(tmp_path):
    """A project that declares [status.roles] and deliberately binds nothing has made
    a choice; the current-major repair tests for the table's presence, not its
    contents, so it is preserved — never rewritten or duplicated (SR-0137)."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--bare"]) == 0
    cfg = root / "throughline.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8").split("[status.roles]")[0].rstrip()
                   + "\n[status.roles]\n", encoding="utf-8")
    before = cfg.read_text(encoding="utf-8")
    result = migrate_project(root)
    assert result.repaired is None          # declared (empty) table => left alone
    assert cfg.read_text(encoding="utf-8") == before  # not rewritten or duplicated


# -- migration binds an unstamped ratification record (SR-0152) -------------- #

def _legacy_ratified(root: Path, uid: str, by: str, *, status: str | None = None,
                     **attrs) -> None:
    """Put an item into the shape a graph ratified before the fingerprint existed
    carries on disk (SR-0148 arrived after the signature): a status and a named
    ratifier, but no stamp binding that name to what was signed."""
    project = load_project(root)
    item = project.get(uid)
    item.status = status or project.schema.status_role("ratified")
    item.attrs["ratified_by"] = by
    item.attrs.update(attrs)
    item.attrs.pop("ratified_fingerprint", None)
    write_item(item)


def _unstamped_project(tmp_path, by: str = "alice") -> Path:
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init"]) == 0
    _legacy_ratified(root, "REQ-0001", by)
    return root


def test_migrate_binds_a_ratification_record_that_has_no_fingerprint(tmp_path):
    """A record naming a ratifier but carrying no stamp proves who accepted the item
    and not what they accepted. `tl migrate` — the command that already repairs the
    rest of the major — completes it, and marks it as bound retrospectively so it
    stays distinguishable from a stamp written at sign-off (SR-0152)."""
    root = _unstamped_project(tmp_path)
    result = migrate_project(root)

    assert list(result.bound) == ["REQ-0001"]
    item = load_project(root).get("REQ-0001")
    assert item.attrs["ratified_fingerprint"] == result.bound["REQ-0001"]
    assert item.attrs["ratified_fingerprint"].startswith("sha256:")
    assert item.attrs["ratified_backfilled"] is True


def test_the_bound_stamp_is_the_fingerprint_of_the_content_on_disk(tmp_path):
    """It is a real fingerprint of the item, not a placeholder — so the drift rule
    that was silent over the whole back catalogue starts working on it (SR-0152)."""
    root = _unstamped_project(tmp_path)
    migrate_project(root)
    project = load_project(root)
    item = project.get("REQ-0001")
    assert item.attrs["ratified_fingerprint"] == fingerprint(item, project.schema)

    findings = validate(project, strict=True)
    assert not [f for f in findings if f.rule == "ratified-stale"]
    item.text = "materially different wording nobody signed off"
    write_item(item)
    stale = [f for f in validate(load_project(root), strict=True)
             if f.rule == "ratified-stale"]
    assert [f.uid for f in stale] == ["REQ-0001"]


def test_migrate_reuses_the_recorded_ratifier_and_never_reattributes(tmp_path):
    """The repair completes an accountability record; it must not author one. The
    ratifier already on the item is reused verbatim and there is no way to pass a
    substitute — a sweep that stamped everything with one name would silently
    reattribute hundreds of sign-offs (SR-0152)."""
    root = _unstamped_project(tmp_path, by="j.doe@example.org")
    migrate_project(root)
    assert load_project(root).get("REQ-0001").attrs["ratified_by"] == "j.doe@example.org"
    with pytest.raises(SystemExit):     # no seam through which to name anyone else
        _cli(["-C", str(root), "migrate", "--by", "someone.else"])


def test_migrate_leaves_an_already_bound_record_untouched(tmp_path):
    """Idempotent, like every repair the chain runs (SR-0137): a bound record carries
    a fingerprint, so it never matches again — and a second pass cannot restamp a
    record whose content moved after sign-off, which would bless the drift."""
    root = _unstamped_project(tmp_path)
    first = migrate_project(root).bound["REQ-0001"]
    assert migrate_project(root).bound == {}

    project = load_project(root)
    item = project.get("REQ-0001")
    item.text = "changed after the human signed it off"
    write_item(item)
    assert migrate_project(root).bound == {}
    assert load_project(root).get("REQ-0001").attrs["ratified_fingerprint"] == first


def test_migrate_does_not_bind_an_item_it_could_not_legitimately_ratify(tmp_path):
    """Legitimacy is decided by the same predicate `ratify` refuses on, so the repair
    cannot complete a record the Tool would not have written in the first place — an
    ambiguous item is passed over, stamp and all (SR-0152)."""
    root = _unstamped_project(tmp_path)
    _legacy_ratified(root, "REQ-0001", "alice", ambiguous=True)
    assert migrate_project(root).bound == {}
    item = load_project(root).get("REQ-0001")
    assert "ratified_fingerprint" not in item.attrs
    assert item.attrs["ratified_by"] == "alice"   # the record is left exactly as found


def test_migrate_binds_a_record_whose_item_has_moved_past_ratification(tmp_path):
    """An item that went on to `implemented` still carries the signature it was given,
    and that signature is just as unbound. The repair writes no status, so the record
    is completed without moving the item — the fingerprint covers normative content,
    which the workflow move did not touch (SR-0152)."""
    root = _unstamped_project(tmp_path)
    _legacy_ratified(root, "REQ-0001", "alice", status="implemented")
    assert list(migrate_project(root).bound) == ["REQ-0001"]
    assert load_project(root).get("REQ-0001").status == "implemented"


def test_migrate_ignores_an_item_that_names_no_ratifier(tmp_path):
    """Nothing is inferred about who signed off. An item with no ratifier has no
    record to complete, so the repair invents neither a name nor a stamp (SR-0152)."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init"]) == 0
    assert migrate_project(root).bound == {}
    assert "ratified_fingerprint" not in load_project(root).get("REQ-0001").attrs


def test_migrate_names_every_record_it_bound_on_the_cli(tmp_path, capsys):
    """The change is never silent: it wrote to an accountability record, so the
    operator must be able to see which item now carries a backfilled stamp and say
    so if they disagree (SR-0152, mirroring SR-0137)."""
    root = _unstamped_project(tmp_path)
    assert _cli(["-C", str(root), "migrate"]) == 0
    out = capsys.readouterr().out
    assert "REQ-0001 = sha256:" in out
    assert "ratified_backfilled" in out
    assert "nothing to migrate" not in out   # something *was* done


def test_migrate_binds_records_on_a_project_that_also_needs_upgrading(tmp_path):
    """The two halves of the repair run in order on one command: a project still on
    an older major is upgraded, its [status.roles] backfilled, and its unbound
    records completed in the same pass — which is the whole estate's case (SR-0152)."""
    root = _unstamped_project(tmp_path)
    _strip_status_roles(root)
    cfg = root / "throughline.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace(
        f"format_version = {FORMAT_VERSION}", "format_version = 2"), encoding="utf-8")

    result = migrate_project(root)
    assert (result.start, result.end) == (2, FORMAT_VERSION)
    assert list(result.bound) == ["REQ-0001"]
    assert load_project(root).schema.status_role("ratified") == "ratified"


# -- the repair accepts a supplied grounding index (SR-0153) ----------------- #

def _grounded_through_a_source(root: Path, by: str = "alice", **attrs) -> Item:
    """An item whose only path to a root leaves this graph — a consumer item
    grounded on a clause of a standard it composes. On disk it is indistinguishable
    from an orphan, which is why the bare Tool declines to complete its record."""
    project = load_project(root)
    reg = next(r for r in project.registers.values() if "REQ-0001" in r.items)
    item = Item(uid="REQ-0002", type="requirement",
                status=project.schema.status_role("ratified"),
                title="Grounded on a borrowed clause",
                text="Something this system shall do to satisfy a composed standard.",
                links=[Link(target="base:RISK-0001", type="implements")],
                attrs={"ratified_by": by, **attrs})
    write_item(item, reg)
    return item


def _union_index(root: Path) -> Index:
    """The grounding view a composing tool holds: this project's own items plus the
    source items they borrow, indexed as one graph (SR-0153)."""
    project = load_project(root)
    reg = next(iter(project.registers.values()))
    reg.items["base:RISK-0001"] = Item(
        uid="base:RISK-0001", type="risk", status="approved",
        title="A risk carried by the composed source")
    return Index.build(project)


def test_migrate_declines_a_record_it_cannot_justify_from_this_graph_alone(tmp_path):
    """The bare Tool cannot see the borrowed parent, so the item reads as orphaned
    and its record is passed over. This is the refusal working, not failing — and it
    is the reason a composing caller needs a seam rather than a copy (SR-0153)."""
    root = _unstamped_project(tmp_path)
    _grounded_through_a_source(root)

    bound = migrate_project(root).bound
    assert "REQ-0002" not in bound          # declined
    assert "REQ-0001" in bound              # everything justifiable still bound
    assert "ratified_fingerprint" not in load_project(root).get("REQ-0002").attrs


def test_migrate_binds_that_record_when_given_the_union_it_grounds_over(tmp_path):
    """Supplied a grounding index that reaches the borrowed parent, the repair
    completes the very record it declined without one — the composing tool reuses
    the repair instead of reimplementing it (SR-0153)."""
    root = _unstamped_project(tmp_path)
    _grounded_through_a_source(root)

    result = migrate_project(root, index=_union_index(root))
    assert "REQ-0002" in result.bound
    item = load_project(root).get("REQ-0002")
    assert item.attrs["ratified_fingerprint"] == result.bound["REQ-0002"]


def test_omitting_the_index_is_exactly_the_behaviour_without_the_argument(tmp_path):
    """The seam is additive: with no index supplied the repair builds one from the
    project handed to it, and binds precisely what it bound before (SR-0153)."""
    plain = migrate_project(_unstamped_project(tmp_path / "a")).bound
    explicit = migrate_project(_unstamped_project(tmp_path / "b"), index=None).bound
    assert list(plain) == list(explicit) == ["REQ-0001"]


def test_a_supplied_index_buys_the_whole_record_and_nothing_partial(tmp_path):
    """The grounding view is all a caller may vary. Every other decision stays inside
    the repair, so the record it gains is complete on the repair's terms: the recorded
    ratifier verbatim, marked as backfilled, and no status written (SR-0153)."""
    root = _unstamped_project(tmp_path)
    _grounded_through_a_source(root, by="j.doe@example.org")

    migrate_project(root, index=_union_index(root))
    project = load_project(root)
    item = project.get("REQ-0002")
    assert item.attrs["ratified_by"] == "j.doe@example.org"
    assert item.attrs["ratified_backfilled"] is True
    assert item.status == project.schema.status_role("ratified")


def test_a_supplied_index_cannot_make_the_repair_sign_the_unsignable(tmp_path):
    """A wider grounding view answers only the grounding question. An item flagged
    ambiguous is refused on the same predicate as before, so a composing caller
    cannot widen its way past a refusal that was never about grounding (SR-0153)."""
    root = _unstamped_project(tmp_path)
    _grounded_through_a_source(root, ambiguous=True)

    assert "REQ-0002" not in migrate_project(root, index=_union_index(root)).bound
    assert "ratified_fingerprint" not in load_project(root).get("REQ-0002").attrs


def test_migration_writes_only_to_the_project_it_was_given(tmp_path):
    """Grounding over the union, writing to the consumer: the borrowed item is in the
    index the repair judges by, and gains no file and no stamp of its own — the same
    division of labour SR-0151 gave ratify (SR-0153)."""
    root = _unstamped_project(tmp_path)
    _grounded_through_a_source(root)

    result = migrate_project(root, index=_union_index(root))
    assert "base:RISK-0001" not in result.bound
    assert not list(root.rglob("*RISK-0001*"))


def _v3_project(config_extra: dict | None = None) -> Project:
    """An empty in-memory project pinned to the current major, with no [status.roles]
    unless the caller adds one — the shape the gate must flag (SR-0136)."""
    config = {"project": {"format_version": FORMAT_VERSION},
              "status": {"values": ["draft", "ratified"]}}
    if config_extra:
        for k, v in config_extra.items():
            config[k] = {**config.get(k, {}), **v} if isinstance(v, dict) else v
    return Project(path=Path("/tmp/none"), config=config)


def test_gate_flags_v3_project_with_no_status_roles():
    """At the current major, a project that declares no [status.roles] at all is
    reported at warning severity — the status-writing operations are inert and
    nothing else would notice (SR-0136)."""
    findings = validate(_v3_project())
    hits = [f for f in findings if f.rule == "no-status-roles"]
    assert len(hits) == 1
    assert hits[0].severity == "warning"


def test_gate_silent_when_status_roles_declared_but_empty():
    """A declared-but-empty [status.roles] is a deliberate choice (drive every status
    by hand); the gate tests only for the wholesale absence of the table, so this
    passes silently (SR-0136)."""
    findings = validate(_v3_project({"status": {"roles": {}}}))
    assert not [f for f in findings if f.rule == "no-status-roles"]


def test_gate_no_status_roles_is_error_under_strict():
    """The warning is promoted to an error under --strict like any other, so a
    graph-gated CI catches the inert configuration (SR-0136)."""
    findings = validate(_v3_project(), strict=True)
    hits = [f for f in findings if f.rule == "no-status-roles"]
    assert hits and hits[0].severity == "error"


def test_gate_no_status_roles_is_suppressible():
    """A project that intentionally drives every status move by hand can turn the
    finding off through the standard rule-severity configuration (SR-0136)."""
    findings = validate(_v3_project({"rules": {"no-status-roles": "off"}}))
    assert not [f for f in findings if f.rule == "no-status-roles"]


# ------------------------------ undeclared vocabularies at the gate (SR-0185)

def _open_vocab_project(config_extra: dict | None = None,
                        *docs: Register) -> Project:
    """A project declaring neither vocabulary — the shape SR-0185 turns red. The
    roles are bound because the check is about the vocabularies alone."""
    config = {"project": {"format_version": FORMAT_VERSION},
              "status": {"roles": {"initial": "draft", "ratified": "ratified"}}}
    for k, v in (config_extra or {}).items():
        config[k] = {**config.get(k, {}), **v} if isinstance(v, dict) else v
    p = Project(path=Path("/tmp/none"), config=config)
    for d in docs:
        p.registers[d.prefix] = d
    return p


def test_gate_flags_each_undeclared_vocabulary_as_an_error():
    """SR-0185. An absent [status] values or [links] types admits everything, which
    makes SR-0081's membership rule inert — every typo enters the graph unremarked
    while check pronounces it sound. Error by default, because a warning is how
    'anything goes' survives years of green builds."""
    hits = [f for f in validate(_open_vocab_project())
            if f.rule == "undeclared-vocabulary"]

    assert len(hits) == 2
    assert {f.severity for f in hits} == {"error"}
    assert any("[status] values" in f.message for f in hits)
    assert any("[links] types" in f.message for f in hits)


def test_the_undeclared_finding_names_what_the_graph_relies_on():
    """SR-0185. The finding has to leave the reader able to act, and the one list
    guaranteed to invalidate nothing is what the project already relies on: the
    values its items hold, plus whatever the rest of its configuration names — the
    grounding link types among them, which the schema will not build without."""
    intent = Item(uid="INT-1", type="intent", status="agreed")
    fr = Item(uid="FR-1", type="requirement", status="draft",
              links=[Link(target="INT-1", type="derives_from")])
    p = _open_vocab_project(None, _doc("INT", intent), _doc("FR", fr))
    said = {f.message.split("relies on ")[1].split(" —")[0]
            for f in validate(p) if f.rule == "undeclared-vocabulary"}

    assert "agreed, draft, ratified" in said        # held, and named by a role
    assert "derives_from, implements, mitigates, verifies" in said


def test_gate_silent_when_a_vocabulary_is_declared_however_it_is_declared():
    """SR-0185. Presence is the whole test. A project that deliberately declares an
    open vocabulary has said so where the choice can be read as a choice, which is
    the entire difference between that and an omission."""
    declared = {"status": {"values": ["draft", "ratified"]}, "links": {"types": []}}

    assert not [f for f in validate(_open_vocab_project(declared))
                if f.rule == "undeclared-vocabulary"]


def test_gate_undeclared_vocabulary_is_suppressible():
    """SR-0185. Configurable per SR-0041 like every other rule — a project that
    genuinely wants an open vocabulary says so once, in configuration."""
    findings = validate(_open_vocab_project({"rules": {"undeclared-vocabulary": "off"}}))

    assert not [f for f in findings if f.rule == "undeclared-vocabulary"]


def test_migrate_declares_the_vocabularies_a_project_left_open(tmp_path):
    """SR-0185. The rule turns an existing project red through an upgrade it did not
    ask for, so the same single command has to correct it — and what it writes is
    the project's own reliance, which cannot invalidate an item or leave the schema
    unbuildable, so the declaration changes nothing about what the graph admits."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--name", "t", "--bare"]) == 0
    assert _cli(["-C", str(root), "register", "new", "INT", "vision"]) == 0
    assert _cli(["-C", str(root), "new", "INT", "--type", "intent",
                 "--title", "why"]) == 0
    cfg = root / "throughline.toml"
    doc = TomlDocument(cfg.read_text(encoding="utf-8"))
    doc.remove_key("status", "values")
    doc.remove_key("links", "types")
    cfg.write_text(doc.text(), encoding="utf-8")
    assert len([f for f in validate(load_project(root))
                if f.rule == "undeclared-vocabulary"]) == 2

    result = migrate_project(root)

    assert set(result.declared) == {"[status] values", "[links] types"}
    assert "draft" in result.declared["[status] values"]
    assert not [f for f in validate(load_project(root))
                if f.rule == "undeclared-vocabulary"]
    # idempotent, and a declared vocabulary is never rewritten
    before = cfg.read_text(encoding="utf-8")
    assert migrate_project(root).declared == {}
    assert cfg.read_text(encoding="utf-8") == before


def test_the_backfilled_status_vocabulary_covers_the_roles_bound_beside_it(tmp_path):
    """SR-0185. Order is load-bearing: the roles are backfilled first, so the
    vocabulary written after them includes the statuses they name. Written the other
    way round the two halves of one repair would leave the schema refusing to
    build — [status.roles] mapping to statuses the values do not declare."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--name", "t", "--bare"]) == 0
    cfg = root / "throughline.toml"
    doc = TomlDocument(cfg.read_text(encoding="utf-8"))
    doc.remove_key("status", "values")
    cfg.write_text(doc.text().split("[status.roles]")[0].rstrip() + "\n",
                   encoding="utf-8")

    result = migrate_project(root)

    schema = load_project(root).schema           # builds, so the two halves agree
    assert set(result.repaired.values()) <= set(result.declared["[status] values"])
    assert schema.status_role("initial") in schema.statuses


def test_migrate_declaring_a_vocabulary_leaves_the_rest_of_the_file_alone(tmp_path):
    """SR-0185 through NFR-0009. The repair reaches for the same surgical editor the
    schema commands use, so a config an author has commented survives being brought
    up to what the major requires."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--name", "t", "--bare"]) == 0
    cfg = root / "throughline.toml"
    doc = TomlDocument(cfg.read_text(encoding="utf-8"))
    doc.remove_key("links", "types")
    cfg.write_text("# Why this project is shaped as it is.\n" + doc.text(),
                   encoding="utf-8")

    migrate_project(root)
    out = cfg.read_text(encoding="utf-8")

    assert out.startswith("# Why this project is shaped as it is.\n")
    assert "implements" in tomllib.loads(out)["links"]["types"]


def test_migrate_names_the_vocabularies_it_declared_on_the_cli(tmp_path, capsys):
    """SR-0185 with SR-0137. This decides what every future item is validated
    against, so it is named in full rather than counted — it is the part an author
    is most likely to want to narrow."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--name", "t", "--bare"]) == 0
    cfg = root / "throughline.toml"
    doc = TomlDocument(cfg.read_text(encoding="utf-8"))
    doc.remove_key("links", "types")
    cfg.write_text(doc.text(), encoding="utf-8")
    capsys.readouterr()

    assert _cli(["-C", str(root), "migrate"]) == 0
    out = capsys.readouterr().out

    assert "[links] types = derives_from, implements, mitigates, verifies" in out
    assert "nothing to migrate" not in out


def test_migrate_refuses_newer_project(tmp_path):
    """`tl migrate` refuses a project newer than this tl — there is nothing to
    migrate down to; the user must upgrade tl (NFR-0010)."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--bare"]) == 0
    _set_format_version(root, FORMAT_VERSION + 1)
    with pytest.raises(ProjectError, match="upgrade tl"):
        migrate_project(root)
    assert _cli(["-C", str(root), "migrate"]) == 2  # USAGE


def test_migrate_no_registered_path_errors_cleanly(tmp_path):
    """An older major with no registered migration step (a hypothetical v0) fails
    with a clear error rather than a half-applied upgrade (NFR-0010)."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--bare"]) == 0
    _set_format_version(root, 0)
    with pytest.raises(ProjectError, match="no migration path"):
        migrate_project(root)


def test_migrate_v1_document_manifests_to_v2_registers(tmp_path):
    """The real 1->2 migration: `tl migrate` renames every `.document.yml` to
    `.register.yml`, bumps the recorded version, and the project then loads with
    its item intact — a genuine upgrade that preserves data (NFR-0010, SR-0102)."""
    root = tmp_path / "proj"
    reg = _make_legacy_v1_project(root)
    assert migrate_project(root)[:2] == (1, FORMAT_VERSION)  # walks the whole chain
    assert (reg / ".register.yml").exists()
    assert not (reg / ".document.yml").exists()
    assert f"format_version = {FORMAT_VERSION}" in (
        root / "throughline.toml").read_text(encoding="utf-8")
    assert [i.uid for i in load_project(root).items()] == ["SR-0001"]  # item survived


def test_init_refuses_to_nest_inside_existing_project(tmp_path):
    """init inside a tree already holding an throughline.toml is refused and writes
    nothing, so a newcomer cannot silently create a broken nested layout."""
    outer = tmp_path / "outer"
    assert _cli(["-C", str(outer), "init", "--name", "outer"]) == 0
    inner = outer / "sub" / "inner"
    assert _cli(["-C", str(inner), "init", "--name", "inner"]) == 2
    assert not (inner / "throughline.toml").exists()


def test_init_force_allows_nesting(tmp_path):
    """--force overrides the nesting guard for the rare intentional case."""
    outer = tmp_path / "outer"
    assert _cli(["-C", str(outer), "init", "--name", "outer"]) == 0
    inner = outer / "sub" / "inner"
    assert _cli(["-C", str(inner), "init", "--name", "inner", "--force"]) == 0
    assert (inner / "throughline.toml").exists()


def test_init_refuses_to_wrap_existing_child_project(tmp_path):
    """The mirror case: init in a dir that already has a descendant project is
    refused too — wrapping is the same broken nested layout (SR-0077)."""
    child = tmp_path / "outer" / "sub" / "inner"
    assert _cli(["-C", str(child), "init", "--name", "inner"]) == 0
    outer = tmp_path / "outer"
    assert _cli(["-C", str(outer), "init", "--name", "outer"]) == 2
    assert not (outer / "throughline.toml").exists()


def test_init_force_allows_wrapping_child(tmp_path):
    """--force overrides the wrap guard as well."""
    child = tmp_path / "outer" / "sub" / "inner"
    assert _cli(["-C", str(child), "init", "--name", "inner"]) == 0
    outer = tmp_path / "outer"
    assert _cli(["-C", str(outer), "init", "--name", "outer", "--force"]) == 0
    assert (outer / "throughline.toml").exists()


def test_default_status_set_includes_deferred(tmp_path):
    """A fresh project ships a 'deferred' status so a parked backlog item is
    distinct from an active 'draft' (SR-0080). Ordered right after 'draft'."""
    init_project(tmp_path, name="DS")
    cfg = load_project(tmp_path).config
    values = cfg["status"]["values"]
    assert "deferred" in values
    assert values.index("deferred") == values.index("draft") + 1


def test_find_nested_project_reports_progress(tmp_path):
    """The descendant scan reports a monotonic directory count so the CLI can
    show live progress on a slow scan, and still returns the right answer."""
    from throughline.storage import find_nested_project, init_project

    base = tmp_path / "tree"
    (base / "a" / "b" / "c").mkdir(parents=True)
    counts: list[int] = []

    # No descendant project -> walks the whole tree, calling back per directory.
    assert find_nested_project(base, on_progress=counts.append) is None
    assert counts == sorted(counts) and counts[-1] >= 4  # base + a + b + c

    # A descendant project is found and still reported through the callback.
    init_project(base / "a" / "proj", name="child")
    hits: list[int] = []
    found = find_nested_project(base, on_progress=hits.append)
    assert found is not None and found.name == "proj"
    assert hits  # callback fired at least once


def test_check_summary_default_and_quiet(tmp_path, capsys):
    """check prints a graph summary by default (SR-0078) and --quiet suppresses
    it, leaving only findings + the tally."""
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "feat", "--ground", "INT-0001",
                 "--no-interactive"]) == 0
    capsys.readouterr()  # discard setup output

    assert _cli(["-C", str(root), "check"]) == 0
    err = capsys.readouterr().err
    assert "tl check ·" in err
    assert "trace to a root" in err
    assert "error(s)" in err  # tally still present

    assert _cli(["-C", str(root), "check", "--quiet"]) == 0
    err_q = capsys.readouterr().err
    assert "trace to a root" not in err_q
    assert "error(s)" in err_q  # tally survives --quiet


def test_check_json_unaffected_by_summary(tmp_path, capsys):
    """--format json stays a clean machine contract: no summary prose on stdout."""
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "feat", "--ground", "INT-0001",
                 "--no-interactive"]) == 0
    capsys.readouterr()
    assert _cli(["-C", str(root), "check", "--format", "json"]) == 0
    out = capsys.readouterr().out
    import json as _json
    _json.loads(out)  # parses cleanly
    assert "tl check ·" not in out


def _seed_query_project(tmp_path) -> Path:
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "alpha", "--ground", "INT-0001",
                 "--no-interactive"]) == 0            # FR-0001, status draft
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "beta", "--status", "approved", "--ground",
                 "INT-0001", "--no-interactive"]) == 0  # FR-0002, approved
    return root


def test_query_filters_by_status(tmp_path, capsys):
    """query lists items matching an SR-0045 expression (SR-0079)."""
    root = _seed_query_project(tmp_path)
    capsys.readouterr()
    assert _cli(["-C", str(root), "query", "status == 'draft'"]) == 0
    out = capsys.readouterr().out
    assert "FR-0001" in out and "alpha" in out
    assert "FR-0002" not in out  # approved item excluded


def test_query_no_expr_lists_all_live_items(tmp_path, capsys):
    root = _seed_query_project(tmp_path)
    capsys.readouterr()
    assert _cli(["-C", str(root), "query"]) == 0
    out = capsys.readouterr().out
    assert "INT-0001" in out and "FR-0001" in out and "FR-0002" in out


def test_query_ls_alias_and_json(tmp_path, capsys):
    root = _seed_query_project(tmp_path)
    capsys.readouterr()
    assert _cli(["-C", str(root), "ls", "type == 'intent'",
                 "--format", "json"]) == 0
    import json as _json
    data = _json.loads(capsys.readouterr().out)
    assert [d["uid"] for d in data] == ["INT-0001"]


def test_query_bad_expression_is_usage_error(tmp_path, capsys):
    root = _seed_query_project(tmp_path)
    assert _cli(["-C", str(root), "query", "status = 'draft'"]) == 2  # '=' typo


def test_version_flag_prints_and_exits_zero(capsys):
    """--version reports the installed version and exits 0, so users and CI can
    record which build produced a result (SR-0076)."""
    with pytest.raises(SystemExit) as exc:
        _cli(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("tl ")
    assert out.split()[1]  # a non-empty version string follows the program name


def test_trace_renders_unresolved_target_without_crashing(tmp_path, capsys):
    """`tl trace` walks outward through links; a link whose target is not a local
    item — a dangling reference, or a namespace-qualified cross-source reference
    resolved only under tl-compose — is shown as an `(unresolved)` leaf rather
    than aborting the walk (SR-0051)."""
    root = _scaffold(tmp_path)  # INT-0001 + FR register
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "cites an external clause",
                 "--ground", "INT-0001"]) == 0
    # Give it a link to a target that does not exist in this project.
    item = root / "features" / "FR-0001.yml"
    item.write_text(
        item.read_text(encoding="utf-8")
        + "- target: \"asvs:SR-0003\"\n  type: relates\n",
        encoding="utf-8",
    )
    capsys.readouterr()
    assert _cli(["-C", str(root), "trace", "FR-0001"]) == 0
    out = capsys.readouterr().out
    assert "asvs:SR-0003 (unresolved)" in out
    assert "FR-0001" in out


def test_trace_indents_nested_branches_with_continuation_guides(tmp_path, capsys):
    """The tree renderer propagates the branch prefix down every level: children
    of a non-last sibling carry a `│ ` guide, the last child of a node uses `└─`
    and its subtree indents under blank space. A prior bug reset the prefix to ""
    on recursion, collapsing all descendants below depth 1 to a flat column."""
    root = _scaffold(tmp_path)  # INT-0001 + FR register
    # A small tree: INT-0001 <- FR-0001 (two children) ; FR-0002 <- FR-0003.
    for title, ground in [("a", "INT-0001"), ("b", "INT-0001")]:
        assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                     "--title", title, "--ground", "INT-0001"]) == 0
    # Add a grandchild under FR-0001 so the walk has to indent past depth 1.
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "c", "--ground", "INT-0001"]) == 0
    assert _cli(["-C", str(root), "link", "FR-0003", "FR-0001",
                 "--type", "relates"]) == 0
    capsys.readouterr()
    # Trace incoming into INT-0001: its children are FR-0001, FR-0002, FR-0003.
    assert _cli(["-C", str(root), "trace", "INT-0001", "--direction", "in"]) == 0
    lines = capsys.readouterr().out.splitlines()
    # Root has no connector; children hang off it with ├─/└─ and the last is └─.
    assert lines[0].startswith("INT-0001")
    assert any(ln.startswith("├─(") for ln in lines[1:])
    assert lines[-1].startswith("└─(") or lines[-1].startswith("  ")
    # The grandchild (FR-0003 relates FR-0001) must be indented under its parent,
    # never at column 0 — proof the prefix propagated across the recursion.
    grandchild = next(ln for ln in lines if "FR-0003" in ln and "relates" in ln)
    assert grandchild.startswith(("│ ", "  ")) and ("└─" in grandchild or "├─" in grandchild)


def test_render_trace_expand_and_uid_display_seams(tmp_path, capsys):
    """`render_trace` is the shared walk tl-compose builds on. Its `expand`
    predicate stops recursion at a chosen boundary (proof it can render a borrowed
    node without dragging in that node's subtree), and `uid_display` rewrites the
    shown UID (proof compose can show a namespace-qualified name)."""
    from throughline.cli import render_trace
    from throughline.storage import load_project
    root = _scaffold(tmp_path)  # INT-0001 + FR register
    # Chain FR-0001 -> INT-0001 (grounding) and FR-0002 -> FR-0001 so the walk has
    # two levels; boundary-stopping at FR-0001 must hide INT-0001.
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "a", "--ground", "INT-0001"]) == 0
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "b", "--ground", "FR-0001", "--ground-type", "relates"]) == 0
    project = load_project(str(root))
    capsys.readouterr()
    # expand only FR-0002: FR-0001 is shown as a leaf, INT-0001 below it is not walked.
    render_trace(project, "FR-0002", direction="out",
                 expand=lambda u: u == "FR-0002",
                 uid_display=lambda u: f"ns:{u}")
    out = capsys.readouterr().out
    assert "ns:FR-0002" in out and "ns:FR-0001" in out  # display seam applied to both
    assert "INT-0001" not in out                          # boundary stop honored


# --------------------------------------------------------------------- SR-0139..0143
# Regressions for the CLI-ergonomics + integrity fixes (out-of-hours batch).

def test_force_utf8_io_reconfigures_streams(monkeypatch):
    """`force_utf8_io` (SR-0139) makes stdout/stderr emit UTF-8 so tl's arrow
    glyphs (U+2192) never crash a cp1252 console. We assert it calls reconfigure
    with encoding='utf-8' on both streams."""
    from throughline.cli import force_utf8_io

    class _Stream:
        def __init__(self):
            self.encoding = None
        def reconfigure(self, encoding=None, **kw):
            self.encoding = encoding

    out, err = _Stream(), _Stream()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stderr", err)
    force_utf8_io()
    assert out.encoding == "utf-8" and err.encoding == "utf-8"


def test_force_utf8_io_tolerates_unreconfigurable_stream(monkeypatch):
    """A stream without reconfigure (e.g. a plain StringIO) must not raise."""
    import io
    from throughline.cli import force_utf8_io
    monkeypatch.setattr("sys.stdout", io.StringIO())
    monkeypatch.setattr("sys.stderr", io.StringIO())
    force_utf8_io()  # no exception


def test_register_new_rejects_single_char_prefix(tmp_path, capsys):
    """A one-character prefix violates the UID grammar (doc 06 §3): the number is
    matched greedily so its items never parse, silently resetting allocation to 1.
    `register new` must reject it up front rather than accept it and corrupt
    numbering (SR-0140)."""
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "register", "new", "R", "risks"]) == 2
    err = capsys.readouterr().err
    assert "not a valid UID prefix" in err and "doc 06" in err
    # The register was not created.
    assert not (root / "risks").exists()


def test_register_new_accepts_two_char_prefix(tmp_path):
    """The shortest legal prefix (two chars) is accepted (SR-0140)."""
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "register", "new", "RK", "risks"]) == 0


def test_new_machine_origin_is_born_proposed(tmp_path):
    """A machine-origin item is born 'proposed', not 'initial', so the SR-0092
    ratification gate engages and a human must ratify before it counts (SR-0141)."""
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "m", "--ground", "INT-0001", "--origin", "ai",
                 "--no-interactive"]) == 0
    project = load_project(str(root))
    born = project.get("FR-0001")
    assert born.status == project.schema.status_role("proposed") == "proposed"


def test_new_human_origin_is_born_initial(tmp_path):
    """A human-origin item keeps the ordinary 'initial' birth status (SR-0141)."""
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "h", "--ground", "INT-0001", "--origin", "human",
                 "--no-interactive"]) == 0
    project = load_project(str(root))
    assert project.get("FR-0001").status \
        == project.schema.status_role("initial") == "draft"


def test_new_attr_sets_declared_enum_attribute(tmp_path):
    """`--attr KEY=VALUE` sets a project-declared attribute at creation so a typed
    attr no longer needs hand-editing the YAML afterwards (SR-0142)."""
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "p", "--ground", "INT-0001", "--origin", "human",
                 "--attr", "priority=must", "--no-interactive"]) == 0
    assert load_project(str(root)).get("FR-0001").attrs["priority"] == "must"


def test_new_attr_coerces_declared_int(tmp_path):
    """A declared int attribute is coerced from the CLI string so it round-trips as
    an int scalar, not a quoted string (SR-0142)."""
    root = _scaffold(tmp_path)
    cfg = root / "throughline.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace(
        "[types.requirement]\n",
        '[types.requirement]\nattrs.weight = { type = "int" }\n', 1),
        encoding="utf-8")
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "w", "--ground", "INT-0001", "--origin", "human",
                 "--attr", "weight=5", "--no-interactive"]) == 0
    weight = load_project(str(root)).get("FR-0001").attrs["weight"]
    assert weight == 5 and isinstance(weight, int)


def _with_tier_default(root):
    cfg = root / "throughline.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace(
        "[types.requirement]\n",
        '[types.requirement]\nattrs.tier = { type = "enum", '
        'values = ["a", "b", "unset"], default = "unset" }\n', 1),
        encoding="utf-8")


def test_new_applies_schema_declared_default(tmp_path):
    """A schema-declared attribute default lands at birth on an attribute the
    author did not set (SR-0138)."""
    root = _scaffold(tmp_path)
    _with_tier_default(root)
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "d", "--ground", "INT-0001", "--origin", "human",
                 "--no-interactive"]) == 0
    assert load_project(str(root)).get("FR-0001").attrs["tier"] == "unset"


def test_new_attr_overrides_schema_default(tmp_path):
    """An explicit --attr wins over the schema default (SR-0138/SR-0142)."""
    root = _scaffold(tmp_path)
    _with_tier_default(root)
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "d", "--ground", "INT-0001", "--origin", "human",
                 "--attr", "tier=a", "--no-interactive"]) == 0
    assert load_project(str(root)).get("FR-0001").attrs["tier"] == "a"


def test_new_attr_rejects_value_outside_declared_enum(tmp_path, capsys):
    """A value the enum does not declare is refused at creation rather than written
    and left for `check` to find later (SR-0142, fail-fast; SR-0023 membership)."""
    root = _scaffold(tmp_path)
    _with_tier_default(root)
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "d", "--ground", "INT-0001", "--origin", "human",
                 "--attr", "tier=nope", "--no-interactive"]) == 2
    assert "['a', 'b', 'unset']" in capsys.readouterr().err
    assert load_project(str(root)).get("FR-0001") is None


def test_new_attr_rejects_malformed_pair(tmp_path, capsys):
    """A `--attr` without '=' is a hard error at creation, not a silent skip
    (SR-0142, fail-fast)."""
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "x", "--ground", "INT-0001", "--origin", "human",
                 "--attr", "bogus", "--no-interactive"]) == 2
    assert "KEY=VALUE" in capsys.readouterr().err


def _linked_pair(tmp_path):
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "a", "--ground", "INT-0001", "--origin", "human",
                 "--no-interactive"]) == 0
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "b", "--ground", "INT-0001", "--origin", "human",
                 "--no-interactive"]) == 0
    assert _cli(["-C", str(root), "link", "FR-0002", "FR-0001",
                 "--type", "relates"]) == 0
    return root


def test_link_retype_changes_type_in_place(tmp_path):
    """`tl link --retype` changes an existing edge's type rather than adding a
    parallel one, so a semantic-link review needs no YAML hand-editing (SR-0143)."""
    root = _linked_pair(tmp_path)
    assert _cli(["-C", str(root), "link", "FR-0002", "FR-0001",
                 "--type", "refines", "--retype"]) == 0
    links = [ln for ln in load_project(str(root)).get("FR-0002").links
             if ln.target == "FR-0001"]
    assert len(links) == 1 and links[0].type == "refines"


def test_link_retype_without_existing_link_errors(tmp_path, capsys):
    """`--retype` with no existing SRC -> DST link is an error, not a silent add
    (SR-0143). FR-0001 and FR-0002 both ground to INT-0001 but are not linked to
    each other, so retyping between them has nothing to change."""
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "a", "--ground", "INT-0001", "--origin", "human",
                 "--no-interactive"]) == 0
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "b", "--ground", "INT-0001", "--origin", "human",
                 "--no-interactive"]) == 0
    assert _cli(["-C", str(root), "link", "FR-0001", "FR-0002",
                 "--type", "relates", "--retype"]) == 2
    assert "no existing link" in capsys.readouterr().err


def test_unlink_removes_link(tmp_path):
    """`tl unlink SRC DST` removes the edge without touching other links (SR-0143)."""
    root = _linked_pair(tmp_path)
    assert _cli(["-C", str(root), "unlink", "FR-0002", "FR-0001"]) == 0
    remaining = load_project(str(root)).get("FR-0002").links
    assert all(ln.target != "FR-0001" for ln in remaining)
    # The grounding link to INT-0001 survives.
    assert any(ln.target == "INT-0001" for ln in remaining)


def test_unlink_missing_link_errors(tmp_path, capsys):
    """Unlinking an edge that does not exist is an error (SR-0143)."""
    root = _linked_pair(tmp_path)
    assert _cli(["-C", str(root), "unlink", "FR-0001", "FR-0002"]) == 2
    assert "no link" in capsys.readouterr().err


# ---------------------------------------------- whole-project JSON dump (SR-0055)

def test_dump_structure_is_complete_and_deterministic():
    """build_dump projects the whole project — schema, registers, and every item
    (live and tombstoned) with links embedded — into one documented structure,
    and serializes reproducibly (SR-0055)."""
    import json
    from throughline.dump import DUMP_SCHEMA_VERSION, build_dump

    live = Item(uid="SR-0001", type="requirement", title="live",
                links=[Link(target="INT-0001", type="implements")])
    tomb = Item(uid="SR-0002", type="requirement", status="deleted",
                deleted={"date": "2026-07-28", "reason": "obsolete"})
    doc = _doc("SR", live, tomb)
    project = _project(doc)

    dump = build_dump(project, tool_version="9.9.9")

    assert list(dump) == ["throughline_dump", "config", "registers", "items"]
    meta = dump["throughline_dump"]
    assert meta["dump_schema_version"] == DUMP_SCHEMA_VERSION
    assert meta["format_version"] == FORMAT_VERSION
    assert meta["tool_version"] == "9.9.9"
    # The schema (config) travels with the dump.
    assert dump["config"] == project.config
    # Registers carry their manifest plus an item count.
    assert dump["registers"][0]["prefix"] == "SR"
    assert dump["registers"][0]["item_count"] == 2
    # Every item is present, tombstones included, links embedded.
    uids = [it["uid"] for it in dump["items"]]
    assert uids == ["SR-0001", "SR-0002"]          # sorted by uid
    assert dump["items"][0]["links"][0]["target"] == "INT-0001"
    assert dump["items"][1]["deleted"]["reason"] == "obsolete"
    # No wall-clock field: two dumps of the same graph are byte-identical.
    a = json.dumps(build_dump(project, "9.9.9"), sort_keys=False, default=str)
    b = json.dumps(build_dump(project, "9.9.9"), sort_keys=False, default=str)
    assert a == b


def test_dump_cli_emits_valid_json(tmp_path, capsys):
    """`tl dump` writes the documented structure to stdout as valid JSON."""
    import json
    root = _scaffold(tmp_path)  # ships intent INT-0001
    capsys.readouterr()         # discard scaffold output; keep only the dump
    assert _cli(["-C", str(root), "dump"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data) == {"throughline_dump", "config", "registers", "items"}
    assert any(it["uid"] == "INT-0001" for it in data["items"])


def test_dump_cli_writes_to_output_file(tmp_path, capsys):
    """`tl dump -o FILE` writes to a file instead of stdout (SR-0055)."""
    import json
    root = _scaffold(tmp_path)
    out = tmp_path / "project.json"
    assert _cli(["-C", str(root), "dump", "-o", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["throughline_dump"]["format_version"] == FORMAT_VERSION


# --------------------------------------------------------------------------- #
# SR-0156 / SR-0157 — who signed
# --------------------------------------------------------------------------- #

def test_default_ratifier_offers_the_identity_the_repo_signs_with(monkeypatch):
    """The repository already knows who is working in it. Offering the OS account
    name instead is how the same person ends up under several spellings."""
    monkeypatch.setattr(identity, "_git_config",
                        lambda key, path: "Ada Lovelace" if key == "user.name" else None)
    assert identity.default_ratifier() == "Ada Lovelace"


def test_default_ratifier_falls_back_to_the_account_name(monkeypatch):
    """Only where no signing identity is configured — the fallback is the old
    behaviour, not a guess layered on top of it."""
    monkeypatch.setattr(identity, "_git_config", lambda key, path: None)
    monkeypatch.setattr(identity.getpass, "getuser", lambda: "ada")
    assert identity.default_ratifier() == "ada"


def test_git_identity_is_silent_when_git_is_absent(monkeypatch):
    """No git, no repository, nothing configured — all the same answer, and never
    an exception in the middle of a ratification."""
    def _boom(*a, **k):
        raise OSError("no git here")
    monkeypatch.setattr(identity.subprocess, "run", _boom)
    assert identity.git_identity() == (None, None)


def test_ratify_records_an_identifier_in_its_own_field():
    p = _grounded_project()
    item = ratify(p, "FR-1", by="Ada Lovelace", by_id="github:ada")
    assert item.attrs["ratified_by"] == "Ada Lovelace"
    assert item.attrs["ratified_id"] == "github:ada", "never conflated with the name"


def test_ratify_invents_no_identifier():
    """A record given none keeps none. An invented identifier is worse than an
    absent one: it looks like evidence."""
    p = _grounded_project()
    assert "ratified_id" not in ratify(p, "FR-1", by="Ada Lovelace").attrs


def test_an_identifier_must_state_its_scheme():
    """'ada' is not stable, merely opaque — there is no way to resolve it later."""
    p = _grounded_project()
    with pytest.raises(IdentityError, match="scheme"):
        ratify(p, "FR-1", by="Ada Lovelace", by_id="ada")


@pytest.mark.parametrize("value", ["github:ada", "email:ada@example.com",
                                   "gitlab:ada", "some-forge:ada"])
def test_the_scheme_vocabulary_is_open(value):
    """A project on a forge the tool has not heard of must not have to misfile its
    people under one it has."""
    assert identity.normalise_identifier(value) == value


def test_writing_a_ratification_needs_no_network(monkeypatch):
    """A ratification must be writable on a train. Nothing here may reach out."""
    def _refuse(*a, **k):
        raise AssertionError("ratifying must not run a subprocess")
    monkeypatch.setattr(identity.subprocess, "run", _refuse)
    p = _grounded_project()
    assert ratify(p, "FR-1", by="Ada Lovelace", by_id="github:ada")


# --- the reported version (SR-0164) ------------------------------------------

def test_the_reported_version_is_the_installed_distributions():
    """1.9.0 shipped saying "1.8.0" because the release bumped the packaging
    metadata and not the literal beside it. Deriving the value is what makes that
    class of drift impossible rather than merely unlikely."""
    from importlib.metadata import version as dist_version

    # The base version is always the distribution's own; a working tree adds a
    # marker to it, and never substitutes a different number.
    assert throughline_pkg.__version__.split("+")[0] == dist_version("throughline")


def test_an_uninstalled_source_tree_declines_to_name_a_release(monkeypatch):
    """The other half of the obligation. Asked from a tree that was never
    installed, the honest answer is that this is not a release — guessing at the
    nearest one would recreate, from the other direction, the very claim the
    literal used to make."""
    from throughline import version as version_mod

    def _absent(name):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(version_mod.metadata, "distribution", _absent)
    assert version_mod.distribution_version("throughline") == "0.0.0+unknown"


def test_a_working_tree_install_is_not_reported_as_a_clean_release(monkeypatch):
    """An editable install has genuine metadata, so it answers with a release number
    for code that may be arbitrarily far from that release. A full day was lost to
    exactly that — a cockpit and a validator that were different software, with every
    version string agreeing. The marker is what makes the difference visible."""
    from throughline import version as version_mod

    monkeypatch.setattr(version_mod, "_editable_from_direct_url", lambda _d: True)
    reported = version_mod.distribution_version("throughline")

    assert reported.endswith("+editable")
    # A local version segment is forbidden on a published artifact, so the marker
    # cannot collide with anything an index could serve.
    assert reported != metadata.version("throughline")


def test_the_editable_marker_respects_a_version_that_already_has_a_local_segment(
    monkeypatch,
):
    """PEP 440 allows one local segment, so a second '+' would be invalid."""
    from throughline import version as version_mod

    assert version_mod._mark_editable("1.9.0") == "1.9.0+editable"
    assert version_mod._mark_editable("1.9.0+dirty") == "1.9.0+dirty.editable"


def test_editability_is_read_from_recorded_metadata_not_guessed(monkeypatch):
    """PEP 610 direct_url.json is the fact pip recorded at install time, so the
    answer holds however the environment was built — venv, pipx or uv."""
    from throughline import version as version_mod

    class _Dist:
        version = "1.2.3"

        def __init__(self, payload):
            self._payload = payload

        def read_text(self, name):
            return self._payload if name == "direct_url.json" else None

    editable = _Dist('{"url":"file:///w/tl","dir_info":{"editable":true}}')
    published = _Dist('{"url":"https://pypi/x.whl","archive_info":{}}')

    assert version_mod._editable_from_direct_url(editable)
    assert not version_mod._editable_from_direct_url(published)
    # Absent or unparseable metadata is not evidence of a working tree.
    assert not version_mod._editable_from_direct_url(_Dist(None))
    assert not version_mod._editable_from_direct_url(_Dist("{not json"))
