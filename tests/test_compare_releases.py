# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""A candidate build is compared with a released one over existing graphs
(SR-0334 / UR-0048).

5.4.0 was measured against 5.2.0 over 480 graphs by hand, once. The command these
tests hold makes that a step anyone can repeat before a release: two builds, every
graph beneath the directories given, and a failure wherever a graph answers
differently.

Most tests drive it with two stand-in builds, small programs that answer as a
graph's own `answers.json` tells them to, so a difference can be staged exactly.
One runs the working tree against itself over a real graph.
"""
from __future__ import annotations

import importlib.util
import json
import shlex
import sys
from pathlib import Path

import pytest

from throughline.cli import main as cli_main

REPO_ROOT = Path(__file__).resolve().parent.parent

# A stand-in build. It answers `-C DIR <command...>` from DIR/answers.json, under
# the name it was started with, falling back to the answer both builds share.
STAND_IN = r'''
import json, sys
from pathlib import Path
who, args = sys.argv[1], sys.argv[2:]
root = Path(args[args.index("-C") + 1])
command = " ".join(a for a in args if a not in ("-C", str(root)))
answers = json.loads((root / "answers.json").read_text())
answer = answers.get(who, {}).get(command) or answers.get("both", {}).get(command) \
    or {"out": "", "err": "", "code": 0}
sys.stdout.write(answer.get("out", ""))
sys.stderr.write(answer.get("err", ""))
sys.exit(answer.get("code", 0))
'''


def _load():
    path = REPO_ROOT / "scripts" / "compare_releases.py"
    spec = importlib.util.spec_from_file_location("_compare_releases", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


compare = _load()


def _graph(root: Path, answers: dict | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "throughline.toml").write_text('[project]\nname = "g"\n', encoding="utf-8")
    (root / "answers.json").write_text(json.dumps(answers or {}), encoding="utf-8")
    return root


def _run(tmp_path: Path, *dirs: Path, extra=(), capsys=None) -> tuple[int, str]:
    stand_in = tmp_path / "stand_in.py"
    stand_in.write_text(STAND_IN, encoding="utf-8")
    code = compare.main([
        "--against", "9.9.9",
        "--released-command", f"{sys.executable} {stand_in} released",
        "--candidate-command", f"{sys.executable} {stand_in} candidate",
        *extra, *[str(d) for d in dirs]])
    said = capsys.readouterr() if capsys else None
    return code, (said.out + said.err) if said else ""


def test_two_builds_that_answer_alike_pass(tmp_path, capsys):
    _graph(tmp_path / "work" / "a")
    _graph(tmp_path / "work" / "deep" / "b" / "idd")
    code, said = _run(tmp_path, tmp_path / "work", capsys=capsys)
    assert code == 0
    assert "2 graph(s) compared" in said and "0 differ" in said


@pytest.mark.parametrize("command, shown", [
    ("check --format json", "check"),
    ("check --strict --format json", "check --strict"),
    ("docs --check", "docs --check"),
    ("dump", "dump"),
])
def test_a_graph_that_answers_differently_fails_and_is_named(
        tmp_path, capsys, command, shown):
    """SR-0334: check findings plain and strict, the documents gate and the dump."""
    _graph(tmp_path / "work" / "same")
    odd = _graph(tmp_path / "work" / "odd", {
        "released": {command: {"out": '[{"rule": "orphan", "uid": "A-1"}]\n', "code": 1}},
        "candidate": {command: {"out": '[{"rule": "orphan", "uid": "A-2"}]\n', "code": 1}}})
    code, said = _run(tmp_path, tmp_path / "work", capsys=capsys)
    assert code == 1
    assert str(odd) in said and shown in said
    assert "A-1" in said and "A-2" in said, "the difference itself is shown"
    assert "1 differ" in said and str(tmp_path / "work" / "same") not in said


def test_an_exit_code_that_differs_is_a_difference(tmp_path, capsys):
    """SR-0334."""
    odd = _graph(tmp_path / "work", {
        "released": {"check --format json": {"out": "[]\n", "code": 0}},
        "candidate": {"check --format json": {"out": "[]\n", "code": 1}}})
    code, said = _run(tmp_path, odd, capsys=capsys)
    assert code == 1 and "exit 0" in said and "exit 1" in said


def test_a_build_naming_itself_is_not_a_difference(tmp_path, capsys):
    """Each build says which version it is and where it is installed. That is the
    one thing two builds are expected to say differently."""
    _graph(tmp_path / "work", {
        "released": {
            "dump": {"out": '{"throughline_dump": {"tool_version": "9.9.9"}, "items": []}\n'},
            "check --format json": {"code": 2, "err":
                "tl: x is at format version 3; this tl (throughline 9.9.9 at "
                "/venvs/old/site-packages/throughline) uses 4\n"}},
        "candidate": {
            "dump": {"out": '{"throughline_dump": {"tool_version": "10.0.0+editable"}, "items": []}\n'},
            "check --format json": {"code": 2, "err":
                "tl: x is at format version 3; this tl (throughline 10.0.0+editable "
                "at /home/someone/src/throughline) uses 4\n"}}})
    code, said = _run(tmp_path, tmp_path / "work", capsys=capsys)
    assert code == 0, said


def test_a_graph_both_builds_refuse_alike_is_counted_and_passes(tmp_path, capsys):
    """SR-0334: how many were refused alike by both."""
    refusal = {"code": 2, "err": "tl: invalid configuration in throughline.toml\n"}
    _graph(tmp_path / "work" / "refused", {"both": {
        "check --format json": refusal, "check --strict --format json": refusal,
        "docs --check": refusal, "dump": refusal}})
    _graph(tmp_path / "work" / "fine")
    code, said = _run(tmp_path, tmp_path / "work", capsys=capsys)
    assert code == 0
    assert "2 graph(s) compared" in said and "1 refused alike by both" in said


def test_a_graph_refused_by_one_build_alone_is_a_difference(tmp_path, capsys):
    """The case the comparison exists for: a graph that loaded and now does not."""
    _graph(tmp_path / "work", {"candidate": {"check --format json": {
        "code": 2, "err": "tl: [rules] orphan must be one of error, warning or off\n"}}})
    code, said = _run(tmp_path, tmp_path / "work", capsys=capsys)
    assert code == 1 and "must be one of error, warning or off" in said


def test_a_graph_neither_build_could_read_is_counted_and_passes(tmp_path, capsys):
    """SR-0334: how many it could not read. Both builds stopping alike is no
    difference between them, and is said, not passed over."""
    crash = {"code": 1, "err": "Traceback (most recent call last):\nKeyError: 'uid'\n"}
    _graph(tmp_path / "work", {"both": {"check --format json": crash}})
    code, said = _run(tmp_path, tmp_path / "work", capsys=capsys)
    assert code == 0 and "1 could not be read" in said


def test_notices_about_fetching_are_not_a_difference(tmp_path, capsys):
    """What a build says on the way to its answer depends on what its cache held."""
    _graph(tmp_path / "work", {
        "released": {"check --format json": {
            "out": "[]\n", "err": "tl: resolved source 'std' from https://x@v1\n"}},
        "candidate": {"check --format json": {
            "out": "[]\n", "err": "tl: source 'std' ref main moved abc -> def — refetching\n"}}})
    code, _said = _run(tmp_path, tmp_path / "work", capsys=capsys)
    assert code == 0


def test_graphs_are_found_beneath_every_directory_given_and_nowhere_else(tmp_path):
    """SR-0334: the graphs are wherever the caller says."""
    one = _graph(tmp_path / "one" / "idd")
    two = _graph(tmp_path / "two" / "a" / "b")
    for hidden in (tmp_path / "one" / ".cache" / "g",
                   tmp_path / "one" / "node_modules" / "pkg" / "g",
                   tmp_path / "one" / "env" / "lib" / "g"):
        _graph(hidden)
    (tmp_path / "one" / "env" / "pyvenv.cfg").write_text("", encoding="utf-8")
    _graph(tmp_path / "elsewhere")

    assert compare.graphs_beneath([tmp_path / "one", tmp_path / "two"]) == [one, two]


def test_it_writes_to_no_graph_and_the_working_tree_agrees_with_itself(tmp_path, capsys):
    """SR-0334: read-only. The real Tool, both sides, over a real graph."""
    root = tmp_path / "demo"
    assert cli_main(["-C", str(root), "init", "--name", "t"]) == 0
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    capsys.readouterr()

    code = compare.main(["--against", "0.0.0",
                         "--released-command", shlex.join(compare.working_tree_command()),
                         str(tmp_path)])

    said = capsys.readouterr()
    assert code == 0, said.out + said.err
    assert "1 graph(s) compared" in said.out and "0 differ" in said.out
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_the_report_can_be_read_by_a_program(tmp_path, capsys):
    _graph(tmp_path / "work" / "same")
    odd = _graph(tmp_path / "work" / "odd", {
        "candidate": {"dump": {"out": '{"items": [1]}\n'}},
        "released": {"dump": {"out": '{"items": []}\n'}}})
    code, said = _run(tmp_path, tmp_path / "work", extra=["--format", "json"],
                      capsys=capsys)
    report = json.loads(said)
    assert code == 1
    assert report["against"] == "9.9.9"
    assert report["compared"] == 2 and report["refused_alike"] == 0
    assert report["unread"] == 0
    assert [d["graph"] for d in report["differ"]] == [str(odd)]
    assert report["differ"][0]["commands"][0]["command"] == "dump"


def test_no_directory_or_one_that_is_not_there_is_a_usage_error(tmp_path, capsys):
    """It names no directory of its own."""
    with pytest.raises(SystemExit) as e:
        compare.main(["--against", "9.9.9"])
    assert e.value.code == 2
    assert compare.main(["--against", "9.9.9", "--released-command", "true",
                         str(tmp_path / "nowhere")]) == 2
    assert "nowhere" in capsys.readouterr().err


def test_no_graph_beneath_what_was_given_is_said_and_fails(tmp_path, capsys):
    """Comparing nothing must not read as every graph agreeing."""
    code, said = _run(tmp_path, tmp_path, capsys=capsys)
    assert code == 2 and "no graph" in said


def test_the_same_values_in_another_order_are_not_the_same_bytes_and_fail(
        tmp_path, capsys):
    """NFR-0012, SR-0334: the comparison is of bytes. Found by the first run over
    real graphs: a dump whose `grounding` keys came out in a different order each
    time. A tolerated reordering is where a real change hides, so it fails; it is
    said in one line for what it is, not shown as a diff of the whole dump."""
    keys = _graph(tmp_path / "work" / "keys", {
        "released": {"dump": {"out": '{"a": 1, "b": [1, 2]}\n'}},
        "candidate": {"dump": {"out": '{"b": [1, 2], "a": 1}\n'}}})
    _graph(tmp_path / "work" / "same")
    code, said = _run(tmp_path, tmp_path / "work", capsys=capsys)
    assert code == 1
    assert "1 differ (1 only in the order of keys)" in said
    assert f"keys in another order (dump): {keys}" in said
    assert "+++ candidate" not in said

    code, said = _run(tmp_path, tmp_path / "work", extra=["--format", "json"],
                      capsys=capsys)
    assert code == 1
    assert json.loads(said)["reordered"] == [{"graph": str(keys), "commands": ["dump"]}]


def test_the_name_of_a_fetch_s_working_directory_is_not_a_difference(tmp_path, capsys):
    """A refusal that quotes git names the directory the fetch was made into,
    which is named afresh each time."""
    def refusal(name):
        return {"code": 2, "err": f"tl: could not clone x: Cloning into "
                                  f"'/cache/sources/.fetch-{name}'...\n"}
    _graph(tmp_path / "work", {
        "released": {"check --format json": refusal("3yu4jifv")},
        "candidate": {"check --format json": refusal("pslr80jp")}})
    code, said = _run(tmp_path, tmp_path / "work", capsys=capsys)
    assert code == 0 and "1 refused alike by both" in said
