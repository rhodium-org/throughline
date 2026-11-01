# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""A standard's shape declared as items, and a follower checked against it
(UR-0044, SR-0285..SR-0299)."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
import yaml

from throughline.cli import main
from throughline.declared import base_accepts, check_pattern, DeclarationError
from throughline.storage import ProjectError, load_project
from throughline.validate import validate

STATUS = '''
[status]
values = ["proposed", "ratified", "rejected", "deleted"]
[status.roles]
initial = "proposed"
proposed = "proposed"
ratified = "ratified"
invalidated = "rejected"
tombstone = "deleted"
'''
ORIGIN = 'attrs.origin = { type = "enum", values = ["human", "ai", "hybrid"] }'
META_TOML = f'''[project]
name = "meta"
format_version = 4
[grounding]
root_types = ["base", "link_type", "register", "type", "enum", "attribute", "link", "intent", "series", "source"]
delivery_roots = []
ground_link_types = ["derives_from"]
[links]
types = ["derives_from", "belongs_to", "has_type", "from", "to", "via", "aliases"]
{STATUS}
[types.base]
attrs.name = {{ type = "string", required = true }}
[types.link_type]
{ORIGIN}
attrs.name = {{ type = "string", required = true }}
attrs.grounds = {{ type = "bool" }}
attrs.scope = {{ type = "string" }}
[types.register]
{ORIGIN}
attrs.prefix = {{ type = "string", required = true }}
attrs.dir = {{ type = "string", required = true }}
attrs.holds = {{ type = "string", required = true }}
attrs.label = {{ type = "string" }}
attrs.required = {{ type = "bool" }}
attrs.exactly_one = {{ type = "bool" }}
attrs.scope = {{ type = "string" }}
attrs.normative = {{ type = "bool" }}
[types.type]
{ORIGIN}
attrs.pattern = {{ type = "string" }}
attrs.min = {{}}
attrs.max = {{}}
[types.enum]
{ORIGIN}
attrs.values = {{ type = "string", required = true }}
[types.attribute]
{ORIGIN}
attrs.name = {{ type = "string", required = true }}
attrs.required = {{ type = "bool" }}
attrs.required_when = {{ type = "string" }}
attrs.absent_when = {{ type = "string" }}
attrs.list = {{ type = "bool" }}
attrs.scope = {{ type = "string" }}
attrs.normative = {{ type = "bool" }}
[types.link]
{ORIGIN}
attrs.min = {{ type = "int" }}
attrs.max = {{ type = "int" }}
attrs.per_target_min = {{ type = "int" }}
attrs.when = {{ type = "string" }}
attrs.from_when = {{ type = "string" }}
attrs.scope = {{ type = "string" }}
[types.intent]
'''
BASES = ["string", "text", "int", "float", "bool", "date", "enum", "file"]
LTS = [("derives_from", True), ("belongs_to", True), ("has_type", True),
       ("from", True), ("to", True), ("via", True), ("aliases", True)]


def item(uid, type_, links=(), **attrs):
    return {"uid": uid, "type": type_, "status": "proposed", "title": uid, "text": uid,
            "normative": True,
            "links": [{"target": t, "type": lt} for lt, t in links],
            "attrs": attrs}


def write(root: Path, toml: str, registers: dict) -> Path:
    """registers: {folder: (prefix, type, [items])}"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "throughline.toml").write_text(toml, encoding="utf-8")
    for folder, (prefix, type_, items) in registers.items():
        d = root / folder
        d.mkdir(parents=True, exist_ok=True)
        (d / ".register.yml").write_text(
            yaml.safe_dump({"prefix": prefix, "digits": 4, "title": folder, "type": type_}),
            encoding="utf-8")
        for it in items:
            (d / f"{it['uid']}.yml").write_text(yaml.safe_dump(it, sort_keys=False),
                                                 encoding="utf-8")
    return root


def meta(tmp: Path) -> Path:
    return write(tmp / "meta", META_TOML, {
        "base-types": ("BASE", "base", [item(f"BASE-{i:04d}", "base", name=n)
                                        for i, n in enumerate(BASES, 1)]),
        "link-types": ("LT", "link_type", [item(f"LT-{i:04d}", "link_type", name=n, grounds=g)
                                           for i, (n, g) in enumerate(LTS, 1)]),
    })


B = {n: f"meta:BASE-{i:04d}" for i, n in enumerate(BASES, 1)}


def standard(tmp: Path, *, attrs=(), links=(), types=(), enums=(), regs=None,
             toml_extra: str = "", lts=()) -> Path:
    meta(tmp)
    regs = regs or [
        item("REG-0001", "register", prefix="SER", dir="series", holds="series", exactly_one=True),
        item("REG-0002", "register", prefix="ED", dir="editions", holds="edition"),
        item("REG-0003", "register", prefix="SLD", dir="slides", holds="slide"),
        item("REG-0004", "register", prefix="ROLE", dir="roles", holds="role", required=False),
    ]
    types = list(types) or [
        item("TYPE-0001", "type", [("has_type", B["string"])]),
        item("TYPE-0002", "type", [("has_type", B["string"])], pattern="^[0-9]{4}-(0[1-9]|1[0-2])$"),
        item("TYPE-0003", "type", [("has_type", B["int"])], min=1),
        item("TYPE-0004", "type", [("has_type", B["date"])]),
        item("TYPE-0005", "type", [("has_type", B["float"])], min=0),
    ]
    enums = list(enums) or [
        item("ENUM-0001", "enum", [("has_type", B["enum"])], values="briefing, training"),
        item("ENUM-0002", "enum", [("has_type", B["enum"])], values="cards, stats, rows"),
    ]
    attrs = list(attrs) or [
        item("ATTR-0001", "attribute", [("belongs_to", "REG-0001"), ("has_type", "ENUM-0001")],
             name="kind", required=True),
        item("ATTR-0002", "attribute", [("belongs_to", "REG-0002"), ("has_type", "TYPE-0002")],
             name="month", required_when="SER.kind == briefing", absent_when="SER.kind == training"),
        item("ATTR-0003", "attribute", [("belongs_to", "REG-0002"), ("has_type", "TYPE-0004")],
             name="presented"),
        item("ATTR-0004", "attribute", [("belongs_to", "REG-0003"), ("has_type", "TYPE-0003")],
             name="order", required=True, normative=True),
        item("ATTR-0005", "attribute", [("belongs_to", "REG-0003"), ("has_type", "ENUM-0002")],
             name="layout", required=True),
        item("ATTR-0006", "attribute", [("belongs_to", "REG-0003"), ("has_type", "TYPE-0005")],
             name="weight"),
    ]
    links = list(links) or [
        item("LINK-0001", "link", [("from", "REG-0002"), ("to", "REG-0001"), ("via", "meta:LT-0001")],
             min=1, max=1),
        item("LINK-0002", "link", [("from", "REG-0003"), ("to", "REG-0002"), ("via", "meta:LT-0001")],
             min=1, max=1),
    ]
    regs_out = {
        "registers": ("REG", "register", regs), "types": ("TYPE", "type", types),
        "enums": ("ENUM", "enum", enums), "attributes": ("ATTR", "attribute", attrs),
        "links": ("LINK", "link", links),
    }
    if lts:
        regs_out["link-types"] = ("LT", "link_type", list(lts))
    return write(tmp / "std", f'''[project]
name = "std"
format_version = 4
[[sources]]
namespace = "meta"
path = "../meta"
schema = true
{toml_extra}''', regs_out)


FOLLOWER_TOML = '''[project]
name = "fol"
format_version = 4
[[sources]]
namespace = "presentation"
path = "../std"
schema = true
'''


def follower(tmp: Path, *, ser=None, eds=None, slds=None, extra=None, toml: str = FOLLOWER_TOML) -> Path:
    regs = {
        "series": ("SER", "series", ser if ser is not None else [item("SER-0001", "series", kind="briefing")]),
        "editions": ("ED", "edition", eds if eds is not None else [
            item("ED-0001", "edition", [("derives_from", "SER-0001")], month="2026-11",
                 presented=dt.date(2026, 11, 5))]),
        "slides": ("SLD", "slide", slds if slds is not None else [
            item("SLD-0001", "slide", [("derives_from", "ED-0001")], order=1, layout="cards")]),
    }
    regs.update(extra or {})
    return write(tmp / "fol", toml, regs)


DECLARED_RULES = {"register-declared", "attribute-declared", "link-declared", "undeclared"}


def findings(root: Path) -> list:
    return [f for f in validate(load_project(root)) if f.rule in DECLARED_RULES]


# ----------------------------------------------------------------------- tests

def test_a_conforming_follower_has_no_findings(tmp_path):
    standard(tmp_path)
    assert findings(follower(tmp_path)) == []


def test_the_follower_takes_its_types_from_the_declarations_not_its_configuration(tmp_path):
    standard(tmp_path)
    project = load_project(follower(tmp_path))
    spec = project.schema.attr("slide", "layout")
    assert spec.kind == "enum" and spec.values == ("cards", "stats", "rows")
    assert "derives_from" in project.schema.link_types


def test_a_missing_required_register_is_reported(tmp_path):
    standard(tmp_path)
    root = follower(tmp_path)
    import shutil
    shutil.rmtree(root / "slides")
    rules = [(f.rule, f.uid) for f in findings(root)]
    assert ("register-declared", "presentation:REG-0003") in rules


def test_an_optional_register_may_be_absent(tmp_path):
    standard(tmp_path)
    assert not [f for f in findings(follower(tmp_path)) if "ROLE" in f.message]


def test_a_register_in_the_wrong_folder_is_reported(tmp_path):
    standard(tmp_path)
    root = follower(tmp_path)
    (root / "slides").rename(root / "pages")
    msgs = [f.message for f in findings(root) if f.rule == "register-declared"]
    assert any("pages/" in m and "slides/" in m for m in msgs)


def test_exactly_one_is_enforced(tmp_path):
    standard(tmp_path)
    root = follower(tmp_path, ser=[item("SER-0001", "series", kind="briefing"),
                                   item("SER-0002", "series", kind="briefing")])
    assert any("exactly one" in f.message for f in findings(root))


def test_a_value_failing_its_type_pattern_is_reported(tmp_path):
    standard(tmp_path)
    root = follower(tmp_path, eds=[item("ED-0001", "edition", [("derives_from", "SER-0001")],
                                        month="Nov 2026")])
    msgs = [f.message for f in findings(root) if f.rule == "attribute-declared"]
    assert any("month" in m and "^[0-9]{4}" in m for m in msgs)


def test_a_required_when_condition_on_the_one_series_binds_every_edition(tmp_path):
    standard(tmp_path)
    root = follower(tmp_path, eds=[item("ED-0001", "edition", [("derives_from", "SER-0001")])])
    msgs = [f.message for f in findings(root) if f.rule == "attribute-declared"]
    assert any("month is required when SER.kind == briefing" in m for m in msgs)


def test_absent_when_holds_reports_the_attribute(tmp_path):
    standard(tmp_path)
    root = follower(tmp_path, ser=[item("SER-0001", "series", kind="training")])
    msgs = [f.message for f in findings(root) if f.rule == "attribute-declared"]
    assert any("month must be absent when SER.kind == training" in m for m in msgs)


def test_an_undeclared_attribute_names_the_likely_one(tmp_path):
    standard(tmp_path)
    root = follower(tmp_path, eds=[item("ED-0001", "edition", [("derives_from", "SER-0001")],
                                        month="2026-11", monht="2026-11")])
    msgs = [f.message for f in findings(root) if f.rule == "undeclared"]
    assert any("monht" in m and "did you mean month" in m for m in msgs)


def test_origin_is_the_tools_own_and_needs_no_declaration(tmp_path):
    standard(tmp_path)
    root = follower(tmp_path, ser=[item("SER-0001", "series", kind="briefing", origin="ai")])
    assert findings(root) == []


def test_a_link_count_below_min_and_an_undeclared_link_are_reported(tmp_path):
    standard(tmp_path)
    root = follower(tmp_path, slds=[item("SLD-0001", "slide", [("relates", "SER-0001")],
                                         order=1, layout="cards")])
    rules = {(f.rule, f.uid) for f in findings(root)}
    assert ("link-declared", "SLD-0001") in rules
    assert ("undeclared", "SLD-0001") in rules


def test_per_target_min_counts_incoming_links_where_from_when_holds(tmp_path):
    links = [
        item("LINK-0001", "link", [("from", "REG-0002"), ("to", "REG-0001"), ("via", "meta:LT-0001")]),
        item("LINK-0002", "link", [("from", "REG-0003"), ("to", "REG-0002"), ("via", "meta:LT-0001")],
             per_target_min=1, from_when="SLD.layout in (cards, stats)"),
    ]
    standard(tmp_path, links=links)
    ok = follower(tmp_path)
    assert findings(ok) == []
    import shutil
    shutil.rmtree(tmp_path / "fol")
    root = follower(tmp_path, slds=[item("SLD-0001", "slide", [("derives_from", "ED-0001")],
                                         order=1, layout="rows")])
    assert any(f.rule == "link-declared" and f.uid == "ED-0001" for f in findings(root))


def test_conditions_compare_dates_and_numbers(tmp_path):
    attrs_extra = [
        item("ATTR-0007", "attribute", [("belongs_to", "REG-0003"), ("has_type", "TYPE-0001")],
             name="note", required_when="SLD.weight > 0.5"),
        item("ATTR-0008", "attribute", [("belongs_to", "REG-0002"), ("has_type", "TYPE-0001")],
             name="presenter", required_when="ED.presented >= 2026-01-01"),
    ]
    std = standard(tmp_path)
    for it in attrs_extra:
        (std / "attributes" / f"{it['uid']}.yml").write_text(yaml.safe_dump(it), encoding="utf-8")
    root = follower(tmp_path, slds=[item("SLD-0001", "slide", [("derives_from", "ED-0001")],
                                         order=1, layout="cards", weight=0.9)])
    msgs = [f.message for f in findings(root)]
    assert any("note is required when SLD.weight > 0.5" in m for m in msgs)
    assert any("presenter is required when ED.presented >= 2026-01-01" in m for m in msgs)


def test_a_float_value_is_checked_against_its_minimum(tmp_path):
    standard(tmp_path)
    root = follower(tmp_path, slds=[item("SLD-0001", "slide", [("derives_from", "ED-0001")],
                                         order=1, layout="cards", weight=-1.0)])
    assert any("weight" in f.message and "at least 0" in f.message for f in findings(root))


def test_a_condition_naming_a_register_with_many_items_is_refused(tmp_path):
    std = standard(tmp_path)
    bad = item("ATTR-0009", "attribute", [("belongs_to", "REG-0003"), ("has_type", "TYPE-0001")],
               name="x", required_when="ED.presented > 2026-01-01")
    (std / "attributes" / "ATTR-0009.yml").write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(ProjectError, match="neither its own register nor one declared exactly_one"):
        load_project(follower(tmp_path))


def test_an_enum_condition_with_a_value_the_set_lacks_is_refused(tmp_path):
    std = standard(tmp_path)
    bad = item("ATTR-0009", "attribute", [("belongs_to", "REG-0003"), ("has_type", "TYPE-0001")],
               name="x", required_when="SER.kind == lecture")
    (std / "attributes" / "ATTR-0009.yml").write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(ProjectError, match="'lecture'"):
        load_project(follower(tmp_path))


def test_one_attribute_declared_twice_is_refused_whatever_its_conditions(tmp_path):
    std = standard(tmp_path)
    dup = item("ATTR-0009", "attribute", [("belongs_to", "REG-0002"), ("has_type", "TYPE-0001")],
               name="month", required_when="SER.kind == training")
    (std / "attributes" / "ATTR-0009.yml").write_text(yaml.safe_dump(dup), encoding="utf-8")
    with pytest.raises(ProjectError, match="declared twice.*rename one, or alias"):
        load_project(follower(tmp_path))


def test_a_follower_declares_its_own_additions(tmp_path):
    standard(tmp_path)
    extra = {
        "registers": ("REG", "register", [
            item("REG-0001", "register", prefix="QST", dir="questions", holds="question",
                 scope="this-graph")]),
        "attributes": ("ATTR", "attribute", [
            item("ATTR-0001", "attribute", [("belongs_to", "REG-0001"), ("has_type", "presentation:TYPE-0004")],
                 name="asked", required=True, scope="this-graph"),
            item("ATTR-0002", "attribute", [("belongs_to", "presentation:REG-0003"),
                                            ("has_type", "presentation:TYPE-0001")],
                 name="draft_note", scope="this-graph")]),
        "links": ("LINK", "link", [
            item("LINK-0001", "link", [("from", "REG-0001"), ("to", "presentation:REG-0003"),
                                       ("via", "meta:LT-0001")], scope="this-graph")]),
        "questions": ("QST", "question", [
            item("QST-0001", "question", [("derives_from", "SLD-0001")], asked=dt.date(2026, 10, 30))]),
    }
    root = follower(tmp_path, slds=[item("SLD-0001", "slide", [("derives_from", "ED-0001")],
                                         order=1, layout="cards", draft_note="check it")], extra=extra)
    assert findings(root) == []


def test_a_follower_declaration_colliding_with_the_standard_is_refused(tmp_path):
    standard(tmp_path)
    extra = {"attributes": ("ATTR", "attribute", [
        item("ATTR-0001", "attribute", [("belongs_to", "presentation:REG-0003"),
                                        ("has_type", "presentation:TYPE-0001")],
             name="layout", scope="this-graph")])}
    # The follower holds the faulty declaration, so it still loads, to be put right
    # with tl, and its check reports the clash.
    root = follower(tmp_path, extra=extra)
    faults = [f for f in validate(load_project(root)) if f.rule == "declaration"]
    assert faults and "SLD.layout is declared twice" in faults[0].message


def test_a_half_written_declaration_leaves_its_graph_loadable(tmp_path):
    std = standard(tmp_path)
    half = item("ATTR-0009", "attribute", [("belongs_to", "REG-0003")], name="x")
    (std / "attributes" / "ATTR-0009.yml").write_text(yaml.safe_dump(half), encoding="utf-8")
    faults = [f for f in validate(load_project(std)) if f.rule == "declaration"]
    assert faults and "has_type" in faults[0].message
    with pytest.raises(ProjectError, match="holds a faulty declaration"):
        load_project(follower(tmp_path))


def test_an_alias_lets_a_follower_write_the_standards_attribute_under_its_own_name(tmp_path):
    standard(tmp_path)
    extra = {"attributes": ("ATTR", "attribute", [
        item("ATTR-0001", "attribute", [("belongs_to", "presentation:REG-0003"),
                                        ("has_type", "presentation:TYPE-0001"),
                                        ("aliases", "presentation:ATTR-0005")],
             name="std_layout", scope="this-graph")])}
    root = follower(tmp_path, slds=[item("SLD-0001", "slide", [("derives_from", "ED-0001")],
                                         order=1, std_layout="cards")], extra=extra)
    assert findings(root) == []
    marks = json.loads(_dump(root))["declared"]
    assert marks["SLD-0001"]["attrs"]["std_layout"] == "presentation:ATTR-0005"


def test_a_pattern_outside_the_subset_is_refused_with_its_equivalent():
    with pytest.raises(DeclarationError, match=r"write \[0-9\] instead"):
        check_pattern(r"^\d{4}$")
    for bad in (r"(?i)^abc$", r"^(?=a)", r"(a)\1"):
        with pytest.raises(DeclarationError):
            check_pattern(bad)
    check_pattern("^[0-9]{4}-(0[1-9]|1[0-2])$")


def test_a_standard_declaring_by_items_and_in_its_configuration_is_refused(tmp_path):
    standard(tmp_path)
    root = follower(tmp_path)
    std = tmp_path / "std" / "throughline.toml"
    std.write_text(std.read_text() + '\n[types.series]\nattrs.kind = { type = "string" }\n')
    with pytest.raises(ProjectError, match="declared both"):
        load_project(root)


def test_the_dump_marks_only_what_the_standard_declares(tmp_path):
    standard(tmp_path)
    data = json.loads(_dump(follower(tmp_path)))
    mark = data["declared"]["SLD-0001"]
    assert mark["register"] == "presentation:REG-0003"
    assert mark["attrs"] == {"order": "presentation:ATTR-0004", "layout": "presentation:ATTR-0005"}
    assert mark["links"][0]["declaration"] == "presentation:LINK-0002"


def test_init_from_schema_creates_every_declared_register(tmp_path, monkeypatch):
    standard(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["-C", "fresh", "init", "--from-schema", "std"]) == 0
    project = load_project(tmp_path / "fresh")
    assert set(project.registers) == {"SER", "ED", "SLD", "ROLE"}
    assert project.registers["SLD"].path.name == "slides"
    assert project.registers["SLD"].type == "slide"


def test_a_graph_with_no_declarations_is_unchanged(tmp_path):
    root = write(tmp_path / "plain", f'[project]\nname = "p"\nformat_version = 4\n{STATUS}\n'
                 '[links]\ntypes = ["derives_from"]\n[grounding]\nground_link_types = ["derives_from"]\n'
                 '[types.thing]\n',
                 {"things": ("TH", "thing", [item("TH-0001", "thing", odd="x")])})
    assert findings(root) == []


@pytest.mark.parametrize("base,good,bad", [
    ("string", ["Henry", "AI in five"], ["", "a\nb", 3]),
    ("text", ["One line", "a\nb"], [""]),
    ("int", [0, 12, -3], [True, 1.5, "12"]),
    ("float", [0, 1.5, -2.25], [True, "1.5"]),
    ("bool", [True, False], ["yes", 1]),
    ("date", ["2026-11-01", "2024-02-29", dt.date(2026, 11, 1)],
     ["2026-02-30", "2026-11-1", "2026-11-01T09:00", "01/11/2026", dt.datetime(2026, 11, 1, 9)]),
])
def test_base_types_accept_and_refuse_the_meta_standards_test_values(base, good, bad):
    for v in good:
        assert base_accepts(base, v), (base, v)
    for v in bad:
        assert not base_accepts(base, v), (base, v)


def test_a_file_must_exist_in_the_graph(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "mark.png").write_bytes(b"x")
    assert base_accepts("file", "assets/mark.png", tmp_path)
    for v in ("/etc/passwd", "../mark.png", "assets\\mark.png", "assets/missing.png"):
        assert not base_accepts("file", v, tmp_path)


def _dump(root: Path) -> str:
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert main(["-C", str(root), "dump"]) == 0
    return buf.getvalue()


def test_a_meta_standard_of_another_version_is_refused(tmp_path):
    standard(tmp_path)
    cfg = tmp_path / "meta" / "throughline.toml"
    cfg.write_text(cfg.read_text().replace('name = "meta"', 'name = "meta"\nmeta_standard = 2'))
    with pytest.raises(ProjectError, match="meta-standard version 2.*implements version 1"):
        load_project(follower(tmp_path))


def test_tl_check_over_the_union_judges_only_the_followers_own_items(tmp_path, capsys):
    standard(tmp_path)
    root = follower(tmp_path)
    main(["-C", str(root), "check", "--format", "json"])
    out = json.loads(capsys.readouterr().out)
    assert [f for f in out if f["rule"] in DECLARED_RULES] == []


def test_normative_declarations_reach_the_schema_and_so_the_fingerprint(tmp_path):
    regs = [
        item("REG-0001", "register", prefix="SER", dir="series", holds="series", exactly_one=True),
        item("REG-0002", "register", prefix="ED", dir="editions", holds="edition"),
        item("REG-0003", "register", prefix="SLD", dir="slides", holds="slide", normative=False),
    ]
    standard(tmp_path, regs=regs)
    schema = load_project(follower(tmp_path)).schema
    assert schema.attr("slide", "order").normative
    assert not schema.attr("slide", "layout").normative
    assert schema.is_normative("edition") and not schema.is_normative("slide")


def test_a_coverage_rule_may_need_a_link_type_only_an_item_declares(tmp_path):
    lts = [item("LT-0001", "link_type", name="verifies", grounds=True, scope="this-graph")]
    std = standard(tmp_path, lts=lts, toml_extra=(
        '[[rules.coverage]]\nfilter = "type == \'slide\'"\nneeds = "incoming:verifies"\n'
        'severity = "warning"\n'))
    load_project(std)
    load_project(follower(tmp_path))


def test_a_follower_redefining_a_declared_type_in_its_configuration_is_refused(tmp_path):
    standard(tmp_path)
    toml = FOLLOWER_TOML + '\n[types.slide]\nattrs.layout = { type = "enum", values = ["cards", "wide"] }\n'
    with pytest.raises(ProjectError, match=r"cannot be redefined here \(SR-0276\)"):
        load_project(follower(tmp_path, toml=toml))


def test_a_follower_may_still_say_a_declared_type_is_not_normative(tmp_path):
    standard(tmp_path)
    toml = FOLLOWER_TOML + '\n[types.slide]\nnormative = false\n'
    assert not load_project(follower(tmp_path, toml=toml)).schema.is_normative("slide")
