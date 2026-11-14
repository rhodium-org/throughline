#!/usr/bin/env python3
# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Compare this working tree with a released throughline over existing graphs
(SR-0334 / UR-0048).

A release must leave the graphs that already exist answering as they did, or say
what it changes. This finds every graph beneath the directories it is given, runs
the released version and the working tree over each, and reports every graph that
answers differently: its check findings, plain and strict, its documents gate, its
dump, or the exit code of any of them. It exits non-zero when any graph differs.

It only reads. The commands it runs are the ones that write nothing to a graph, and
it names no directory of its own: the graphs are wherever the caller says.

This is *developer tooling* kept in the repository, like the doctor. It is not part
of the shipped ``throughline`` package: it needs a second installed version, which a
user of the Tool has no reason to have.

Usage:
    python scripts/compare_releases.py --against 5.4.0 ~/work ~/standards
    python scripts/compare_releases.py --against 5.4.0 --format json ~/work

The released version is installed from the package index into a virtual environment
in the per-user cache the first time it is asked for. ``--released-command`` names a
command to run in its place, for a host with no network or a version built by hand.

Exit codes: 0 every graph answers alike · 1 at least one differs · 2 usage.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shlex
import subprocess
import sys
import venv
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_NAME = "throughline.toml"

# What is asked of each build about each graph, in the order reported. None of
# them writes to the graph: the documents gate renders and compares, it does not
# inject.
COMMANDS: tuple[tuple[str, ...], ...] = (
    ("check", "--format", "json"),
    ("check", "--strict", "--format", "json"),
    ("docs", "--check"),
    ("dump",),
)

# A refusal: the Tool's own exit code for a command it would not carry out.
REFUSED = 2

# The lines a build writes on the way to its answer that depend on what its cache
# held, not on the graph: one build fetches what the other already had.
_NOTICE = re.compile(r"^tl: (resolved|resolving|fetch\w*) source |"
                     r"^tl: source '[^']*' ref \S+ moved .* refetching", re.M)
# A build naming itself: its version, and where it is installed.
_SELF = re.compile(r"\(throughline [^\s)]+ at [^)]*\)")
_TOOL_VERSION = re.compile(r'("tool_version":\s*)"[^"]*"')
# The directory a fetch is made into before it is kept, named afresh each time.
_FETCH_DIR = re.compile(r"\.fetch-[A-Za-z0-9_]+")


@dataclass
class Answer:
    """What one build said to one command about one graph."""
    code: int
    out: str
    err: str

    @property
    def crashed(self) -> bool:
        return self.code not in (0, 1, REFUSED) or "Traceback (most recent" in self.err

    @property
    def said(self) -> str:
        """The answer as it is compared: what was printed, and for a refusal or a
        stop the reason given, since that is then the whole answer."""
        if self.code == REFUSED or self.crashed:
            return self.out + self.err
        return self.out


@dataclass
class Difference:
    command: str
    released: Answer
    candidate: Answer

    def lines(self) -> list[str]:
        out = [f"  {self.command}: exit {self.released.code} released, "
               f"exit {self.candidate.code} candidate"]
        diff = difflib.unified_diff(
            self.released.said.splitlines(), self.candidate.said.splitlines(),
            "released", "candidate", lineterm="", n=1)
        shown = list(diff)
        out += [f"    {line}" for line in shown[:40]]
        if len(shown) > 40:
            out.append(f"    … {len(shown) - 40} more line(s)")
        return out


@dataclass
class Compared:
    graph: Path
    differences: list[Difference] = field(default_factory=list)
    refused_alike: bool = False
    unread: bool = False
    # Commands whose JSON holds the same values with the keys of some object in
    # another order. The bytes differ, so it fails the comparison like any other
    # difference (NFR-0012): an output that is not the same bytes cannot be
    # diffed, and a change hidden inside a tolerated reordering is not found. It
    # is named for what it is, in one line, so it is not mistaken for a changed
    # answer nor buried under a diff of the whole dump.
    reordered: list[str] = field(default_factory=list)


def _same_but_for_key_order(was: Answer, now: Answer) -> bool:
    """Whether two answers are JSON holding the same values, differing only in the
    order of an object's keys. The order of a list is part of what it says."""
    if was.code != now.code or was.code == REFUSED or was.crashed:
        return False
    try:
        return json.loads(was.out) == json.loads(now.out)
    except ValueError:
        return False


def working_tree_command() -> list[str]:
    """The command that runs this checkout's source, whatever is installed."""
    src = REPO_ROOT / "src"
    return [sys.executable, "-c",
            f"import sys; sys.path.insert(0, {str(src)!r}); "
            "from throughline.cli import main; sys.exit(main())"]


def released_command(version: str, cache: Path) -> list[str]:
    """The command that runs release ``version``, installed into a virtual
    environment under ``cache`` the first time it is asked for."""
    home = cache / version
    bindir = home / ("Scripts" if os.name == "nt" else "bin")
    python = bindir / ("python.exe" if os.name == "nt" else "python")
    marker = home / ".installed"
    if not marker.exists():
        venv.EnvBuilder(with_pip=True, clear=True).create(home)
        done = subprocess.run(
            [str(python), "-m", "pip", "install", "--quiet",
             f"throughline=={version}"], capture_output=True, text=True)
        if done.returncode != 0:
            raise SystemExit(f"compare_releases: cannot install throughline=={version}:"
                             f"\n{done.stderr.strip()}")
        marker.write_text(version, encoding="utf-8")
    return [str(python), "-c",
            "import sys; from throughline.cli import main; sys.exit(main())"]


def default_cache() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "throughline-compare"


def graphs_beneath(directories) -> list[Path]:
    """Every graph beneath ``directories``: each directory holding a
    ``throughline.toml``, in path order. Hidden directories, ``node_modules`` and
    virtual environments are not looked in; a graph inside one is a copy a tool
    keeps, not one somebody works."""
    found: set[Path] = set()
    for top in directories:
        for here, dirs, files in os.walk(top):
            dirs[:] = sorted(
                d for d in dirs
                if not d.startswith(".") and d not in ("node_modules", "site-packages")
                and not (Path(here) / d / "pyvenv.cfg").exists())
            if CONFIG_NAME in files:
                found.add(Path(here))
    return sorted(found)


def _ask(build: list[str], graph: Path, command: tuple[str, ...],
         timeout: float) -> Answer:
    try:
        done = subprocess.run([*build, "-C", str(graph), *command],
                              capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return Answer(-1, "", f"no answer within {timeout:g} seconds")
    except OSError as e:
        return Answer(-1, "", f"could not be run: {e}")
    return Answer(done.returncode, _settled(done.stdout), _settled(done.stderr))


def _settled(text: str) -> str:
    """``text`` without what two builds are expected to say differently: the
    notices about fetching, and each build's own version and place."""
    text = "\n".join(line for line in text.splitlines() if not _NOTICE.match(line))
    text = _SELF.sub("(throughline)", text)
    text = _FETCH_DIR.sub(".fetch-", text)
    return _TOOL_VERSION.sub(r'\1"-"', text)


def compare_graph(graph: Path, released: list[str], candidate: list[str],
                  timeout: float) -> Compared:
    result = Compared(graph)
    refusals = stops = 0
    for command in COMMANDS:
        was = _ask(released, graph, command, timeout)
        now = _ask(candidate, graph, command, timeout)
        name = " ".join(c for c in command if c not in ("--format", "json"))
        if (was.code, was.said) == (now.code, now.said):
            pass
        elif _same_but_for_key_order(was, now):
            result.reordered.append(name)
            continue
        else:
            result.differences.append(Difference(name, was, now))
            continue
        if was.crashed:
            stops += 1
        elif was.code == REFUSED:
            refusals += 1
    if not result.differences and not result.reordered:
        result.unread = stops > 0
        # The check is what loads the graph: where both refuse it, the graph was
        # refused, whatever the other commands went on to say.
        result.refused_alike = not result.unread and refusals > 0
    return result


def report(results: list[Compared], against: str) -> dict:
    differ = [r for r in results if r.differences]
    return {
        "against": against,
        "compared": len(results),
        "refused_alike": sum(r.refused_alike for r in results),
        "unread": sum(r.unread for r in results),
        "differ": [{
            "graph": str(r.graph),
            "commands": [{"command": d.command,
                          "released": {"exit": d.released.code, "said": d.released.said},
                          "candidate": {"exit": d.candidate.code, "said": d.candidate.said}}
                         for d in r.differences]} for r in differ],
        "unread_graphs": [str(r.graph) for r in results if r.unread],
        "reordered": [{"graph": str(r.graph), "commands": r.reordered}
                      for r in results if r.reordered],
    }


def render(results: list[Compared], against: str) -> str:
    lines: list[str] = []
    for r in results:
        if r.differences:
            lines.append(str(r.graph))
            for d in r.differences:
                lines += d.lines()
            lines.append("")
    unread = [r for r in results if r.unread]
    for r in unread:
        lines.append(f"could not be read by either build: {r.graph}")
    if unread:
        lines.append("")
    reordered = [r for r in results if r.reordered]
    for r in reordered:
        lines.append(f"not the same bytes — same values, keys in another order "
                     f"({', '.join(r.reordered)}): {r.graph}")
    if reordered:
        lines.append("")
    differ = sum(bool(r.differences or r.reordered) for r in results)
    lines.append(
        f"{len(results)} graph(s) compared with throughline {against}: "
        f"{differ} differ"
        + (f" ({len(reordered)} only in the order of keys)" if reordered else "")
        + f" · {sum(r.refused_alike for r in results)} refused alike by "
        f"both · {len(unread)} could not be read")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="compare_releases.py",
        description="Compare this working tree with a released throughline over "
                    "every graph beneath the directories given (SR-0334).")
    parser.add_argument("directories", nargs="+", metavar="DIR",
                        help="a directory to look for graphs beneath")
    parser.add_argument("--against", required=True, metavar="VERSION",
                        help="the released version to compare with, e.g. 5.4.0")
    parser.add_argument("--released-command", metavar="CMD",
                        help="a command that runs the released version, used in "
                             "place of installing it")
    parser.add_argument("--candidate-command", metavar="CMD",
                        help="a command that runs the candidate (default: this "
                             "working tree)")
    parser.add_argument("--cache", type=Path, default=None, metavar="DIR",
                        help="where released versions are installed (default: the "
                             "per-user cache)")
    parser.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1))
    parser.add_argument("--timeout", type=float, default=180.0,
                        help="seconds allowed for one command on one graph")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)

    missing = [d for d in args.directories if not Path(d).is_dir()]
    if missing:
        print(f"compare_releases: not a directory: {', '.join(missing)}",
              file=sys.stderr)
        return 2
    graphs = graphs_beneath([Path(d).resolve() for d in args.directories])
    if not graphs:
        print("compare_releases: no graph (no throughline.toml) beneath "
              + ", ".join(args.directories), file=sys.stderr)
        return 2

    released = (shlex.split(args.released_command) if args.released_command
                else released_command(args.against, args.cache or default_cache()))
    candidate = (shlex.split(args.candidate_command) if args.candidate_command
                 else working_tree_command())

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        results = list(pool.map(
            lambda g: compare_graph(g, released, candidate, args.timeout), graphs))

    if args.format == "json":
        print(json.dumps(report(results, args.against), indent=2))
    else:
        print(render(results, args.against))
    return 1 if any(r.differences or r.reordered for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
