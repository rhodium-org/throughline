"""throughline M0 test suite — model, UID allocation, fingerprint, storage
round-trip, the validation pipeline, and the grounding operations.
"""
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
from throughline.grounding import GroundingError, scout_ingest
from throughline.schema import Schema, SchemaError
from throughline.storage import (
    FORMAT_VERSION,
    ProjectError,
    baseline_statuses,
    migrate_project,
)
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

def _project(*docs: Register, config: dict | None = None) -> Project:
    p = Project(path=Path("/tmp/none"), config=config or {})
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
    p = _project(_doc("INT", intent), _doc("FR", fr))
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

def test_inject_sourced_renders_borrowed_blocks():
    """SR-0114: tl:sourced renders, in full, the distinct external clauses the
    matching items reference by a namespace-qualified target, via the resolver."""
    from throughline.inject import inject_text, TargetResolver
    fr = Item(uid="FR-1", type="requirement", status="approved", title="Wizard",
              links=[Link(target="ext:SR-9", type="satisfies")])
    proj = _project(_doc("INT", Item(uid="INT-1", type="intent", status="approved",
                                     title="Ship value")),
                    _doc("FR", fr),
                    config={"grounding": {"ground_link_types": ["derives_from"]},
                            "links": {"types": ["derives_from", "satisfies"]}})

    class _Res(TargetResolver):
        def block(self, uid):
            return "**ext:SR-9 — Borrowed clause**\n\n> The upstream text." \
                if uid == "ext:SR-9" else None

    src = "<!-- tl:sourced uid == 'FR-1' -->\n<!-- tl:end -->\n"
    out = inject_text(proj, src, resolver=_Res(proj))
    assert "**ext:SR-9 — Borrowed clause**" in out
    assert "> The upstream text." in out

def test_inject_sourced_placeholder_without_resolver():
    """SR-0114: with the default resolver no external clause resolves, so the
    directive renders a clear placeholder rather than an error."""
    from throughline.inject import inject_text
    fr = Item(uid="FR-1", type="requirement", status="approved", title="Wizard",
              links=[Link(target="ext:SR-9", type="satisfies")])
    proj = _project(_doc("FR", fr),
                    config={"links": {"types": ["satisfies"]}})
    src = "<!-- tl:sourced uid == 'FR-1' -->\n<!-- tl:end -->\n"
    out = inject_text(proj, src)
    assert "no source-backed external clauses to mirror" in out

def test_inject_sourced_omits_unresolvable_targets():
    """SR-0114: a target the resolver cannot render is omitted; a resolvable one
    beside it still renders."""
    from throughline.inject import inject_text, TargetResolver
    fr = Item(uid="FR-1", type="requirement", status="approved", title="Wizard",
              links=[Link(target="ext:SR-9", type="satisfies"),
                     Link(target="ext:SR-8", type="satisfies")])
    proj = _project(_doc("FR", fr), config={"links": {"types": ["satisfies"]}})

    class _Res(TargetResolver):
        def block(self, uid):
            return "**ext:SR-9 — Resolvable**" if uid == "ext:SR-9" else None

    src = "<!-- tl:sourced uid == 'FR-1' -->\n<!-- tl:end -->\n"
    out = inject_text(proj, src, resolver=_Res(proj))
    assert "**ext:SR-9 — Resolvable**" in out
    assert "ext:SR-8" not in out

def test_inject_sourced_bad_filter_is_fatal():
    from throughline.inject import InjectError, inject_text
    with pytest.raises(InjectError):
        inject_text(_inject_project(),
                    "<!-- tl:sourced nonsense syntax ( -->\n<!-- tl:end -->\n")

def test_inject_sourced_does_not_publish_local_items(tmp_path):
    """SR-0114/SR-0096: a tl:sourced block publishes the external clauses, not the
    local items its filter selects — those items stay unpublished for coverage."""
    from throughline.inject import referenced_uids
    root = _scaffold_pub(tmp_path, docs_paths=["*.md"])
    (root / "reference.md").write_text(
        "<!-- tl:sourced type == 'requirement' -->\n<!-- tl:end -->\n",
        encoding="utf-8")
    refs = referenced_uids(load_project(root))
    assert "FR-0001" not in refs

def test_inject_graph_renders_colour_coded_flowchart():
    """SR-0115: tl:graph renders a Mermaid flowchart of the matching items and their
    link targets, edges labelled by link type, nodes classed by item type."""
    from throughline.inject import inject_text
    src = "<!-- tl:graph type == 'intent' or uid == 'FR-1' -->\n<!-- tl:end -->\n"
    out = inject_text(_inject_project(), src)
    assert "```mermaid" in out
    # Left-to-right so a dense graph grows tall-and-narrow to fit a portrait page,
    # and renders on GitHub (which cannot draw the ELK layout engine) — SR-0115.
    assert "flowchart LR" in out
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

def test_invalidate_cascades_suspect_to_blast_radius():
    intent = Item(uid="INT-1", type="intent", status="ratified")
    asm = Item(uid="ASM-1", type="assumption", status="ratified")
    fr = Item(uid="FR-1", type="requirement", status="ratified",
              links=[Link(target="INT-1", type="derives_from"),
                     Link(target="ASM-1", type="assumes")])
    nfr = Item(uid="NFR-1", type="nfr", status="ratified",
               links=[Link(target="FR-1", type="derives_from")])
    p = _project(_doc("INT", intent), _doc("ASM", asm),
                 _doc("FR", fr), _doc("NFR", nfr))
    affected = invalidate(p, "ASM-1", reason="measured false")
    assert asm.status == "rejected"
    assert set(affected) == {"FR-1", "NFR-1"}
    assert fr.status == "suspect"
    assert nfr.status == "suspect"

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


# -------------------------------------------------- self-hosting / demo is green

def test_demo_project_passes_strict():
    """The committed demo must stay green under --strict — the CI-gate contract
    (mirrors SR-0061 self-hosting)."""
    p = load_project(DEMO)
    assert _errors(validate(p, strict=True)) == []

def test_demo_has_no_uid_collisions():
    assert collisions(load_project(DEMO)) == []

def test_selfhost_project_passes_strict():
    """throughline's own spec, seeded as a throughline project, must stay grounded
    under --strict — the tool dogfoods its own scope-discipline gate (SR-0061)."""
    p = load_project(SELFHOST)
    assert _errors(validate(p, strict=True)) == []

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
    with pytest.raises(ProjectError, match="upgrade tl"):
        load_project(root)
    assert _cli(["-C", str(root), "check"]) == 2  # USAGE — refuses to run


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
    assert migrate_project(root) == (1, FORMAT_VERSION)  # migrate off the inferred major


def test_infer_absent_field_from_v2_layout_loads(tmp_path):
    """A config that omits format_version but has the v2 `.register.yml` layout is
    inferred as current and loads without migration (UR-0015)."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--bare"]) == 0
    assert _cli(["-C", str(root), "register", "new", "SR", "system"]) == 0
    _set_format_version(root, None)
    assert list(load_project(root).items()) == []  # loads (inferred current)


def test_migrate_current_project_is_noop(tmp_path):
    """`tl migrate` on an already-current project changes nothing and exits 0."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--bare"]) == 0
    assert migrate_project(root) == (FORMAT_VERSION, FORMAT_VERSION)
    assert _cli(["-C", str(root), "migrate"]) == 0


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
    assert migrate_project(root) == (1, 2)
    assert (reg / ".register.yml").exists()
    assert not (reg / ".document.yml").exists()
    assert "format_version = 2" in (root / "throughline.toml").read_text(encoding="utf-8")
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
