# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""No file a project holds ends a command in a traceback (SR-0354).

Three small projects are made as the Tool makes them: one alone, with a signed
item, a tombstone and a rendered document; one that borrows items from a source;
and one that follows a schema source. Each of their files is then damaged one
way at a time: a value of the wrong kind, a key missing, a list where a scalar
belongs, a date not in the calendar, bytes that are not UTF-8, text that is not
YAML or TOML. Every command is run over each damaged project, and none may
raise: a command answers, or refuses in one line with its usual exit status.

The damage is enumerated, not drawn at random, so a failure names its case and
runs again the same. A plain run takes every case that ever found a fault and a
fixed sample of the rest; ``TL_FUZZ=all`` runs every case, and ``TL_FUZZ=3/8``
the fourth of eight equal parts of them, so the whole can be run side by side.
``TL_FUZZ=@FILE`` runs the cases FILE lists, one a line, and ``TL_FUZZ_LOG=FILE``
writes each fault to FILE as it is found.
"""
from __future__ import annotations

import copy
import functools
import os
import shutil
import tomllib
import traceback
import zlib
from pathlib import Path

import pytest
import yaml

from throughline.cli import main as cli_main

# One in this many of the enumerated cases runs by default.
_SAMPLE = 500

# Values of every kind a file can hold, tried wherever one of another kind stood.
_WRONG = [None, 7, -3, 1.5, True, "x", "", [], ["x"], [7], [None], {}, {"a": 1},
          [[1]], [{"a": 1}], "2026-13-45", "REQ-0001"]

# Whole files that are not what the Tool reads there.
_RAW = {
    "empty": b"",
    "a-list": b"- a\n- b\n",
    "a-scalar": b"just text\n",
    "not-utf8": b"\xff\xfe\x00caf\xe9\n",
    "unclosed": b"a: [1, 2\n[project\n",
    "no-such-day": b"uid: REQ-0001\ntype: requirement\ncreated: 2026-13-45\n",
    "binary-tag": b"uid: !!binary |\n  not base64 at all ===\ntype: x\n",
    "alias-loop": b"a: &a [*a]\nuid: REQ-0001\ntype: requirement\n",
}


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v if x is not None) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f'"{k}" = {_toml_value(x)}' for k, x in v.items()
                               if x is not None) + "}"
    raise TypeError(type(v))


def _toml(config: dict) -> str:
    """``config`` as TOML: scalars first, then one table a top-level mapping,
    everything beneath written inline."""
    lines = [f'"{k}" = {_toml_value(v)}' for k, v in config.items()
             if not isinstance(v, dict) and v is not None]
    for k, v in config.items():
        if isinstance(v, dict):
            lines.append(f'\n["{k}"]')
            lines += [f'"{kk}" = {_toml_value(vv)}' for kk, vv in v.items()
                      if vv is not None]
    return "\n".join(lines) + "\n"


def _paths(data, prefix=(), depth=4):
    """Every place in ``data`` down to ``depth``: each key, and the first and last
    member of each list."""
    if depth == 0:
        return
    if isinstance(data, dict):
        for key, value in data.items():
            yield prefix + (key,)
            yield from _paths(value, prefix + (key,), depth - 1)
    elif isinstance(data, list) and data:
        for i in sorted({0, len(data) - 1}):
            yield prefix + (i,)
            yield from _paths(data[i], prefix + (i,), depth - 1)


def _changed(data, path, value, *, remove=False):
    out = copy.deepcopy(data)
    at = out
    for step in path[:-1]:
        at = at[step]
    if remove:
        del at[path[-1]]
    else:
        at[path[-1]] = value
    return out


def _get(data, path):
    for step in path:
        data = data[step]
    return data


def _damage(rel: str, raw: bytes):
    """``(label, make)`` for every way ``rel`` is damaged; ``make()`` gives the
    damaged file, or None where the damage cannot be written down."""
    for name, body in _RAW.items():
        yield f"{rel}::{name}", lambda body=body: body
    yield f"{rel}::trailing-latin1", lambda: raw + b"# caf\xe9\n"
    toml = rel.endswith(".toml")
    data = tomllib.loads(raw.decode()) if toml else yaml.safe_load(raw.decode())
    write = _toml if toml else (lambda d: yaml.safe_dump(d, sort_keys=False))

    def written(path, wrong, remove=False):
        try:
            return write(_changed(data, path, wrong, remove=remove)).encode()
        except TypeError:
            return None

    for path in _paths(data):
        where = ".".join(map(str, path))
        yield f"{rel}::{where}::removed", lambda path=path: written(path, None, True)
        was = _get(data, path)
        for n, wrong in enumerate(_WRONG):
            if type(wrong) is type(was) and (wrong == was or not isinstance(was, (list, dict))
                                             and not isinstance(was, str)):
                continue
            if toml and wrong is None:
                continue
            yield f"{rel}::{where}::{n}", lambda path=path, wrong=wrong: written(path, wrong)


# ---------------------------------------------------------------- the projects

def _alone(root: Path) -> list[str]:
    """A project alone: a signed item, a tombstone, a proposed deletion, a
    document. Returns the files to damage."""
    assert _cli(["-C", root, "init", "--name", "alone"]) == 0
    for title in ("Two", "Three", "Four"):
        assert _cli(["-C", root, "new", "REQ", "--title", title, "--text", "t.",
                     "--ground", "INT-0001", "--no-interactive"]) == 0
    assert _cli(["-C", root, "link", "REQ-0003", "REQ-0002", "--type", "relates"]) == 0
    assert _cli(["-C", root, "ratify", "INT-0001", "REQ-0001", "REQ-0002", "--by", "Ada",
                 "--by-id", "email:ada@example.org"]) == 0
    assert _cli(["-C", root, "delete", "REQ-0002", "--reason", "gone"]) == 0
    assert _cli(["-C", root, "delete", "REQ-0004", "--reason", "never wanted"]) == 0
    assert _cli(["-C", root, "flag", "REQ-0003", "--reason", "vague", "--by", "Ada"]) == 0
    return ["throughline.toml", "requirements/.register.yml", "requirements/REQ-0001.yml",
            "requirements/REQ-0002.yml", "requirements/REQ-0003.yml",
            "requirements/REQ-0004.yml", "vision/INT-0001.yml", "tests/TEST-0001.yml"]


def _composed(root: Path) -> list[str]:
    """``app`` borrows items from ``lib`` beside it; the files damaged are the
    source's and the consumer's."""
    lib, app = root / "lib", root / "app"
    assert _cli(["-C", lib, "init", "--name", "lib"]) == 0
    assert _cli(["-C", lib, "ratify", "INT-0001", "REQ-0001", "--by", "Ada"]) == 0
    assert _cli(["-C", app, "init", "--name", "app", "--no-demo"]) == 0
    with (app / "throughline.toml").open("a", encoding="utf-8") as f:
        f.write('\n[[sources]]\nnamespace = "lib"\npath = "../lib"\n')
    assert _cli(["-C", app, "new", "REQ", "--title", "Mine", "--text", "t.",
                 "--ground", "lib:INT-0001", "--no-interactive"]) == 0
    return ["lib/throughline.toml", "lib/requirements/.register.yml",
            "lib/requirements/REQ-0001.yml", "lib/vision/INT-0001.yml",
            "app/throughline.toml", "app/requirements/REQ-0001.yml"]


def _follower(root: Path) -> list[str]:
    """``app`` takes its schema from ``std`` beside it."""
    std, app = root / "std", root / "app"
    assert _cli(["-C", std, "init", "--name", "std", "--no-demo"]) == 0
    assert _cli(["-C", app, "init", "--from-schema", std, "--namespace", "std"]) == 0
    for prefix, folder, kind in (("INT", "vision", "intent"),
                                 ("REQ", "requirements", "requirement")):
        assert _cli(["-C", app, "register", "new", prefix, folder, "--title", folder,
                     "--type", kind]) == 0
    assert _cli(["-C", app, "new", "INT", "--title", "Why", "--no-interactive"]) == 0
    assert _cli(["-C", app, "new", "REQ", "--title", "Mine", "--text", "t.",
                 "--ground", "INT-0001", "--no-interactive"]) == 0
    return ["std/throughline.toml", "app/throughline.toml", "app/requirements/.register.yml",
            "app/requirements/REQ-0001.yml"]


_PROJECTS = {"alone": (_alone, "."), "composed": (_composed, "app"),
             "follower": (_follower, "app")}

# Reads first, then every command that writes, each over what the last one left.
_COMMANDS = [
    ["check"], ["check", "--strict", "--format", "json"], ["query"],
    ["query", "--format", "json", "--type", "requirement"], ["dump"], ["context"],
    ["context", "REQ-0001"], ["coverage"], ["shape"], ["diagram"], ["docs", "--check"],
    ["trace", "REQ-0001"], ["blast", "INT-0001"], ["subgraph", "REQ-0001"],
    ["schema", "show"], ["migrate", "--beneath", "{here}"],
    ["review", "REQ-0001"], ["amend", "REQ-0001", "--title", "Renamed"],
    ["link", "REQ-0001", "REQ-0003", "--type", "relates"],
    ["unlink", "REQ-0001", "REQ-0003"],
    ["flag", "REQ-0001", "--reason", "vague", "--by", "Ada"],
    ["clarify", "REQ-0003", "--reason", "clear", "--by", "Ada"],
    ["ratify", "REQ-0001", "REQ-0003", "--by", "Ada", "--accept-change"],
    ["withdraw", "REQ-0001", "--reason", "wrong", "--by", "Ada"],
    ["status", "REQ-0001", "proposed"], ["invalidate", "REQ-0001"],
    ["delete", "REQ-0001", "--reason", "gone"], ["restore", "REQ-0001"],
    ["new", "REQ", "--title", "New", "--text", "t.", "--ground", "INT-0001",
     "--no-interactive"],
    ["register", "type", "REQ", "requirement"],
    ["schema", "status", "add", "parked", "--because", "wanted"],
    ["migrate"], ["docs"],
]


@pytest.fixture(scope="module")
def made(tmp_path_factory, request):
    """Each project made once, with every way its files are damaged."""
    # Module-scoped, so the autouse fixture that pins the earlier filter language
    # has not run: these are the projects `tl init` writes today.
    out = {}
    for name, (make, graph) in _PROJECTS.items():
        root = tmp_path_factory.mktemp(name)
        files = make(root)
        cases = {}
        for rel in files:
            for label, make in _damage(rel, (root / rel).read_bytes()):
                cases[label] = (rel, make)
        out[name] = (root, graph, cases)
    return out


def _faults(root: Path, graph: str, tmp: Path, rel: str, body: bytes) -> list[str]:
    """The commands that raise over the project at ``root`` with ``rel`` damaged."""
    work = tmp / "w"
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(root, work)
    (work / rel).write_bytes(body)
    faults = []
    for argv in _COMMANDS:
        try:
            _cli(["-C", work / graph, *(str(work) if a == "{here}" else a for a in argv)])
        except SystemExit:
            pass                              # argparse's own usage refusal
        except Exception as e:  # noqa: BLE001 - the fault this test is looking for
            frame = traceback.extract_tb(e.__traceback__)[-1]
            faults.append(f"tl {' '.join(argv)}: {type(e).__name__}: {e} "
                          f"({Path(frame.filename).name}:{frame.lineno})")
    return faults


@functools.cache
def _listed(path: str) -> frozenset[str]:
    return frozenset(Path(path).read_text(encoding="utf-8").split("\n"))


def _chosen(label: str) -> bool:
    asked = os.environ.get("TL_FUZZ", "")
    if asked.startswith("@"):                   # the cases a file lists, one a line
        return label in _listed(asked[1:])
    if asked == "all" or label in _FOUND:
        return True
    part, _, parts = asked.partition("/")       # one part of several, e.g. 3/8
    if parts:
        return zlib.crc32(label.encode()) % int(parts) == int(part)
    return zlib.crc32(label.encode()) % _SAMPLE == 0


@pytest.mark.parametrize("name", sorted(_PROJECTS))
def test_no_damaged_file_ends_a_command_in_a_traceback(name, made, tmp_path, capsys):
    root, graph, cases = made[name]
    found: dict[str, list[str]] = {}
    for label, (rel, make) in cases.items():
        if not _chosen(label):
            continue
        body = make()
        if body is None:
            continue
        faults = _faults(root, graph, tmp_path, rel, body)
        capsys.readouterr()
        if faults:
            found[label] = faults
            if os.environ.get("TL_FUZZ_LOG"):        # said as found, in a long run
                with open(os.environ["TL_FUZZ_LOG"], "a", encoding="utf-8") as log:
                    log.write(f"{name} {label}\n    " + "\n    ".join(faults) + "\n")
    report = "\n".join(f"{label}\n    " + "\n    ".join(faults)
                       for label, faults in sorted(found.items()))
    assert not found, f"{len(found)} damaged file(s) ended a command in a traceback:\n{report}"


# One case for each place tl 6.0.0 stopped with an unhandled error, taken from a
# twenty-fifth of the cases run over it (134 of about 540 damaged files stopped
# a command, at 49 places), kept so a plain run always tries each again. A case
# that finds a fault in a later release is added here with its fix.
_FOUND: set[str] = {
    "app/requirements/.register.yml::digits::5",
    "app/requirements/REQ-0001.yml::status::11",
    "app/throughline.toml::rules.coverage::4",
    "app/throughline.toml::status::10",
    "app/throughline.toml::status::16",
    "app/throughline.toml::transitions.deferred::3",
    "app/throughline.toml::types::8",
    "lib/throughline.toml::links::1",
    "lib/throughline.toml::project::3",
    "lib/throughline.toml::status.roles::13",
    "requirements/REQ-0001.yml::links.0.type::1",
    "requirements/REQ-0002.yml::deleted::16",
    "requirements/REQ-0003.yml::attrs.suspect_reasons.0::7",
    "requirements/REQ-0003.yml::links.0.type::10",
    "requirements/REQ-0003.yml::title::2",
    "std/throughline.toml::grounding.ai_origins::1",
    "std/throughline.toml::grounding.delivery_roots.3::2",
    "std/throughline.toml::grounding.delivery_roots::2",
    "std/throughline.toml::grounding.root_types.0::13",
    "std/throughline.toml::grounding::1",
    "std/throughline.toml::rules.coverage.0.filter::13",
    "std/throughline.toml::status.roles.suspect::13",
    "std/throughline.toml::status.roles.tombstone::7",
    "std/throughline.toml::status.values.7::12",
    "std/throughline.toml::status::16",
    "std/throughline.toml::transitions.deferred.0::14",
    "std/throughline.toml::types.non_goal.attrs::3",
    "std/throughline.toml::types.requirement::4",
    "throughline.toml::docs.paths::1",
    "throughline.toml::docs::4",
    "throughline.toml::grounding.ground_link_types.0::8",
    "throughline.toml::links.types.7::13",
    "throughline.toml::rules::1",
    "throughline.toml::status.values.0::10",
    "throughline.toml::transitions::16",
    "vision/INT-0001.yml::alias-loop",
    "vision/INT-0001.yml::no-such-day",
    "vision/INT-0001.yml::text::3",
}


# ------------------------------------------- the refusals, by name (SR-0354)

def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


_NAMED = {
    "configuration-not-toml": (
        "throughline.toml", lambda raw: b"[project\nname = 1\n",
        "throughline.toml as a project's configuration: it is not valid TOML"),
    "configuration-not-utf8": (
        "throughline.toml", lambda raw: raw + b"# caf\xe9\n",
        "throughline.toml as a project's configuration: it is not UTF-8 text"),
    "configuration-table-is-a-list": (
        "throughline.toml", lambda raw: b'links = ["relates"]\n'
        + raw.replace(b"[links]\n", b"[links_was]\n"),
        "[links] must be a table, not a list"),
    "configuration-names-are-not-a-list": (
        "throughline.toml", lambda raw: raw.replace(
            b'root_types = [', b'root_types = "intent"\nwas = ['),
        "[grounding] root_types must be a list of names, not a string"),
    "item-not-utf8": (
        "requirements/REQ-0001.yml", lambda raw: raw + b"note: caf\xe9\n",
        "REQ-0001.yml as an item: it is not UTF-8 text"),
    "item-date-not-in-the-calendar": (
        "requirements/REQ-0001.yml", lambda raw: raw + b"created: 2026-13-45\n",
        "REQ-0001.yml as an item: it is not valid YAML"),
    "item-title-is-a-list": (
        "requirements/REQ-0001.yml", lambda raw: raw.replace(
            b"title: First requirement", b"title: [First, requirement]"),
        "REQ-0001.yml as an item: 'title' must be one value, and is a list"),
    "item-holds-itself": (
        "requirements/REQ-0001.yml", lambda raw: raw + b"loop: &a [*a]\n",
        "REQ-0001.yml as an item: a value in it contains itself"),
    "signature-name-is-a-mapping": (
        "requirements/REQ-0001.yml", lambda raw: raw.replace(b"- by: Ada", b"- by: {a: 1}"),
        "REQ-0001.yml as an item: ratifications entry #1: 'by' must be one value"),
    "manifest-not-utf8": (
        "requirements/.register.yml", lambda raw: raw + b"# caf\xe9\n",
        ".register.yml as a register manifest: it is not UTF-8 text"),
    "manifest-digits-in-words": (
        "requirements/.register.yml", lambda raw: raw.replace(b"digits: 4", b"digits: four"),
        ".register.yml as a register manifest: 'digits' must be a whole number"),
    "manifest-reserved-not-a-list": (
        "requirements/.register.yml", lambda raw: raw + b"reserved: 7\n",
        ".register.yml as a register manifest: 'reserved' must be a list of numbers"),
}

_EVERY = [["check"], ["query"], ["dump"], ["context"], ["coverage"], ["shape"],
          ["docs", "--check"], ["trace", "REQ-0001"], ["schema", "show"],
          ["amend", "REQ-0001", "--title", "Renamed"],
          ["ratify", "REQ-0003", "--by", "Ada"],
          ["status", "REQ-0003", "deferred"],
          ["new", "REQ", "--title", "New", "--ground", "INT-0001", "--no-interactive"],
          ["schema", "status", "add", "parked", "--because", "wanted"],
          ["migrate"]]


@pytest.mark.parametrize("case", sorted(_NAMED))
def test_a_file_that_cannot_be_read_is_refused_in_one_line_and_nothing_is_written(
        case, made, tmp_path, capsys):
    rel, damage, says = _NAMED[case]
    root, _graph, _cases = made["alone"]
    work = tmp_path / "w"
    shutil.copytree(root, work)
    was = (work / rel).read_bytes()
    damaged = damage(was)
    assert damaged != was
    (work / rel).write_bytes(damaged)
    before = _tree(work)
    for argv in _EVERY:
        capsys.readouterr()
        assert _cli(["-C", work, *argv]) == 2, argv
        got = capsys.readouterr()
        assert says in got.err, (argv, got.err)
        assert got.err.count("\n") == 1 and got.out == "", (argv, got)
        assert _tree(work) == before, argv


def test_a_document_that_is_not_utf8_is_refused_by_the_command_that_renders_it(
        made, tmp_path, capsys):
    root, _graph, _cases = made["alone"]
    work = tmp_path / "w"
    shutil.copytree(root, work)
    doc = work / "docs" / "overview.md"
    doc.write_bytes(doc.read_bytes() + b"caf\xe9\n")
    before = _tree(work)
    for argv in (["docs"], ["docs", "--check"]):
        capsys.readouterr()
        assert _cli(["-C", work, *argv]) == 2
        err = capsys.readouterr().err
        assert "overview.md as a document: it is not UTF-8 text" in err
        assert err.count("\n") == 1
    assert _cli(["-C", work, "check"]) in (0, 1)        # the gate still answers
    assert _tree(work) == before


def test_a_number_or_a_date_where_text_is_read_is_read_as_its_text(made, tmp_path, capsys):
    """SR-0354: YAML reads an unquoted 404 as a number. tl docs stopped on such
    a title; every other command read it, and the fingerprint took it as text."""
    from throughline.fingerprint import fingerprint
    from throughline.storage import load_project
    root, _graph, _cases = made["alone"]
    work = tmp_path / "w"
    shutil.copytree(root, work)
    path = work / "requirements" / "REQ-0003.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    as_text = dict(data, title="404", text="2026-01-01", rationale="12.5")
    path.write_text(yaml.safe_dump(as_text, sort_keys=False), encoding="utf-8")
    project = load_project(work)
    quoted = fingerprint(project.get("REQ-0003"), project.schema)

    path.write_text(yaml.safe_dump(as_text, sort_keys=False)
                    .replace("'404'", "404").replace("'2026-01-01'", "2026-01-01")
                    .replace("'12.5'", "12.5"), encoding="utf-8")
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["title"] == 404
    project = load_project(work)
    item = project.get("REQ-0003")
    assert (item.title, item.text, item.rationale) == ("404", "2026-01-01", "12.5")
    assert fingerprint(item, project.schema) == quoted
    for argv in (["check"], ["docs"], ["context", "REQ-0003"], ["dump"], ["query"],
                 ["trace", "REQ-0003"], ["amend", "REQ-0003", "--rationale", "why"]):
        assert _cli(["-C", work, *argv]) in (0, 1), argv
    capsys.readouterr()
