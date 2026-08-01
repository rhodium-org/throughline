#!/usr/bin/env python3
# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Contributor environment doctor for throughline (SR-0075 / UR-0021 / BN-0010).

Diagnoses whether a contributor's development environment is ready to work on
throughline and, per check, reports either PASS or a specific remediation. Exits
non-zero if the environment is not ready.

This is *developer tooling* maintained in the repository. It is not part of the
shipped ``throughline`` package and is therefore allowed to be environment-specific
(e.g. it knows about this repo's layout, its test runner, and its pre-commit
grounding hook).

Usage:
    python scripts/doctor.py            # diagnose only
    python scripts/doctor.py --fix      # also wire the local pre-commit hook

Checks:
    * Python version (>= 3.11, per pyproject requires-python)
    * throughline importable / installed (editable install recommended)
    * every toolchain package checked out beside this one is the copy that runs,
      not a published release standing in for it
    * pytest test runner available
    * local grounding gate wired (pre-commit hook installed)
    * grounding gate passes (self-hosted requirements + demo graph)
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MIN_PYTHON = (3, 11)

# The packages that install one another. A checkout sitting beside this repo is the
# signal that the contributor is working on it, so each of these is expected to be
# the working tree rather than a published release standing in for it (UR-0021).
TOOLCHAIN = ("throughline", "throughline-compose", "throughline-ratify")

# The console script each package puts on PATH. Under pipx these live in a venv per
# application, so the build behind a CLI need not be the one this script can see.
CLI_FOR = {
    "throughline": "tl",
    "throughline-compose": "tl-compose",
    "throughline-ratify": "tl-ratify",
}

# ANSI colour, disabled when not a TTY so logs stay clean.
_TTY = sys.stdout.isatty()
GREEN = "\033[32m" if _TTY else ""
RED = "\033[31m" if _TTY else ""
YELLOW = "\033[33m" if _TTY else ""
BOLD = "\033[1m" if _TTY else ""
RESET = "\033[0m" if _TTY else ""


@dataclass
class Result:
    """Outcome of a single check."""

    name: str
    ok: bool
    detail: str = ""
    remediation: str = ""


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    """Run a command from the repo root, capturing output, never raising."""
    return subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #
def check_python() -> Result:
    v = sys.version_info
    current = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= MIN_PYTHON:
        return Result("Python version", True, detail=f"{current}")
    return Result(
        "Python version",
        False,
        detail=f"{current}",
        remediation=(
            f"throughline needs Python >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}. "
            "Install a newer interpreter (e.g. via pyenv or your OS package "
            "manager) and recreate the virtualenv with it."
        ),
    )


def check_throughline_import() -> Result:
    try:
        import throughline  # noqa: F401
    except Exception as exc:  # pragma: no cover - environment dependent
        return Result(
            "throughline importable",
            False,
            detail=f"import failed: {exc}",
            remediation=(
                "Install throughline into the active environment, ideally editable:"
                "\n      python -m pip install -e '.[dev]'"
            ),
        )
    where = getattr(throughline, "__file__", "?")
    editable = str(REPO_ROOT / "src") in str(Path(where).resolve())
    detail = "editable install" if editable else f"installed at {where}"
    return Result("throughline importable", True, detail=detail)


def _install_kind(dist_name: str) -> tuple[str, str]:
    """Return ``(kind, where)`` for an installed distribution.

    ``kind`` is ``absent``, ``editable`` or ``published``. Editability is read from
    the install's own PEP 610 ``direct_url.json`` — the fact pip recorded at install
    time — rather than guessed by matching paths, so it stays correct however the
    environment was built (venv, pipx, uv).
    """
    try:
        dist = metadata.distribution(dist_name)
    except metadata.PackageNotFoundError:
        return ("absent", "")
    raw = dist.read_text("direct_url.json")
    if raw:
        try:
            info = json.loads(raw)
        except ValueError:
            info = {}
        if info.get("dir_info", {}).get("editable"):
            url = str(info.get("url", ""))
            path = url[len("file://"):] if url.startswith("file://") else url
            return ("editable", path)
    return ("published", dist.version)


def _checkout_for(dist_name: str) -> Path:
    """Where this contributor would have ``dist_name`` checked out, if they do."""
    return REPO_ROOT if dist_name == REPO_ROOT.name else REPO_ROOT.parent / dist_name


def _checked_out() -> list[str]:
    """Toolchain packages with a working tree beside this one — the signal that the
    contributor is working on them."""
    return [d for d in TOOLCHAIN if (_checkout_for(d) / "pyproject.toml").is_file()]


def _kinds_here() -> dict[str, list[str]]:
    """Install kind of every toolchain package, in the *current* interpreter."""
    return {d: list(_install_kind(d)) for d in TOOLCHAIN}


def _kinds_in(python: Path) -> dict[str, list[str]] | None:
    """Install kinds inside *another* interpreter, by asking this same script.

    Re-running ``doctor.py --probe`` under the target interpreter keeps one
    implementation of the rule and runs it where the answer differs, rather than
    reimplementing the PEP 610 read for each environment. The script imports only
    the standard library, so any interpreter can execute it.
    """
    try:
        proc = subprocess.run(
            [str(python), str(Path(__file__).resolve()), "--probe"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return None


def _chain_remediation(unchained: list[str]) -> str:
    # Name the siblings first and this repo last, matching the recipe in AGENTS.md so
    # a contributor reading both sees one command, not two that differ cosmetically.
    present = _checked_out()
    installs = " ".join(
        [f"-e ../{d}" for d in present if d != REPO_ROOT.name]
        + (['-e ".[dev]"'] if REPO_ROOT.name in present else [])
    )
    return (
        "You have these checked out but are running the published build, so your "
        "edits are not what executes:"
        f"\n      {', '.join(unchained)}"
        "\nChain them in a single command so the resolver never reaches PyPI:"
        f"\n      python -m pip install {installs}"
        "\nThen confirm every path is your checkout, not site-packages:"
        "\n      python -c \"import throughline as m; print(m.__file__)\""
    )


def _chain_verdict(name: str, kinds: dict[str, list[str]]) -> Result:
    """Judge one environment: is every checked-out package the copy that runs?"""
    unchained: list[str] = []
    detail: list[str] = []
    present = _checked_out()
    for dist_name in present:
        kind, where = (kinds.get(dist_name) or ["absent", ""])[:2]
        if kind == "absent":
            continue  # not in this environment at all; other checks cover throughline
        if kind == "published":
            unchained.append(dist_name)
            detail.append(f"{dist_name}: published {where}")
        else:
            same = (
                Path(where).resolve() == _checkout_for(dist_name).resolve()
                if where
                else False
            )
            detail.append(f"{dist_name}: editable" + ("" if same else f" {where} (!)"))

    if not detail:
        # Either this is the only package checked out, or none of them are installed
        # in this environment at all — which the import and CLI checks already report.
        summary = "single package" if len(present) <= 1 else "none installed here"
        return Result(name, True, detail=summary)
    if not unchained:
        return Result(name, True, detail="; ".join(detail))
    return Result(
        name,
        False,
        detail="; ".join(detail),
        remediation=_chain_remediation(unchained),
    )


def check_toolchain_chained() -> Result:
    """Every toolchain package the contributor has checked out must be the one that
    actually runs (UR-0021).

    These three repositories install one another, so installing one editable resolves
    the rest from PyPI. The result is an environment that edits one package and runs
    the published copy of the next while every version string agrees — a failure that
    reports nothing at all until two people compare output and find they were running
    different software. A checkout sitting beside this repo is taken as the signal
    that the package is being worked on; that is drawn from disk rather than from
    anything the contributor has to remember to declare.
    """
    return _chain_verdict("toolchain chained editable", _kinds_here())


def _venv_python_for(cli: str) -> Path | None:
    """The interpreter that actually runs ``cli``, following the console script."""
    exe = shutil.which(cli)
    if not exe:
        return None
    real = Path(exe).resolve()  # pipx puts symlinks on PATH pointing into its venvs
    for candidate in ("python", "python3"):
        python = real.parent / candidate
        if python.exists():
            return python
    return None


def check_cli_toolchain_chained() -> Result:
    """The CLIs on PATH must be chained too, not just the interpreter running this.

    Every other check here inspects the environment the doctor happens to run in.
    pipx — the documented way to install these CLIs, and how they are used day to
    day — gives each application its own venv, so ``tl``, ``tl-compose`` and
    ``tl-ratify`` can each resolve a different build, and none of them need be the
    one the doctor can see. That is not a corner case; it is the configuration that
    produced the failure UR-0021 was written from, where a cockpit and a validator
    disagreed because they were different software. So each CLI is asked in its own
    environment, by running this script there.
    """
    name = "CLI toolchain chained editable"
    detail: list[str] = []
    unchained: set[str] = set()
    seen: set[Path] = set()
    for dist_name in TOOLCHAIN:
        cli = CLI_FOR[dist_name]
        python = _venv_python_for(cli)
        if python is None:
            continue  # not on PATH; the 'tl CLI on PATH' check covers the one we need
        # Identify the environment by its venv root, never by resolving the
        # interpreter: a venv's bin/python is a symlink to the base interpreter, so
        # resolving it collapses every distinct venv onto the same binary and the
        # separate environments this check exists to find all look like this one.
        venv_root = python.parent.parent
        if venv_root == Path(sys.prefix) or venv_root in seen:
            continue  # already judged by the in-process check, or a shared venv
        seen.add(venv_root)
        kinds = _kinds_in(python)
        if kinds is None:
            detail.append(f"{cli}: could not inspect")
            continue
        verdict = _chain_verdict(name, kinds)
        detail.append(f"{cli} → {verdict.detail}")
        if not verdict.ok:
            unchained.update(
                d
                for d in _checked_out()
                if (kinds.get(d) or ["absent"])[0] == "published"
            )

    if not detail:
        return Result(name, True, detail="no separate CLI environments")
    if not unchained:
        return Result(name, True, detail="; ".join(detail))
    return Result(
        name,
        False,
        detail="; ".join(detail),
        remediation=(
            "The CLI you actually run is not your working tree. pipx keeps a venv per "
            "application, so each needs the editable chain of its own — and injecting "
            "the core LAST, because injecting a dependent afterwards silently pulls "
            "the published core back over it:"
            "\n      pipx uninstall throughline && pipx install --editable ./throughline"
            "\n      pipx inject --force --editable throughline-compose ./throughline"
            "\n      pipx inject --force --editable throughline-ratify ./throughline-compose"
            "\n      pipx inject --force --editable throughline-ratify ./throughline"
            "\nNote that 'pipx install --force --editable' does NOT convert an existing "
            "venv — it reports success and leaves the published copy in place."
        ),
    )


def check_cli() -> Result:
    exe = shutil.which("tl")
    if not exe:
        return Result(
            "tl CLI on PATH",
            False,
            detail="not found",
            remediation=(
                "The 'tl' console script is not on PATH. Activate the "
                "project virtualenv, or install with:"
                "\n      python -m pip install -e '.[dev]'"
            ),
        )
    return Result("tl CLI on PATH", True, detail=exe)


def check_pytest() -> Result:
    try:
        import pytest  # noqa: F401
    except Exception:
        return Result(
            "pytest test runner",
            False,
            detail="not importable",
            remediation=(
                "Install the dev dependencies (they include pytest):"
                "\n      python -m pip install -e '.[dev]'"
            ),
        )
    return Result("pytest test runner", True, detail=pytest.__version__)


def check_precommit_hook(fix: bool) -> Result:
    hook_path = REPO_ROOT / ".git" / "hooks" / "pre-commit"
    wired = hook_path.exists() and "pre-commit" in hook_path.read_text(
        errors="ignore"
    )
    if wired:
        return Result("local grounding gate wired", True, detail=str(hook_path))

    have_precommit = shutil.which("pre-commit") is not None

    if fix:
        if not have_precommit:
            return Result(
                "local grounding gate wired",
                False,
                detail="pre-commit not installed",
                remediation=(
                    "--fix could not install the hook because the 'pre-commit' "
                    "tool is missing. Install it first:"
                    "\n      python -m pip install pre-commit"
                    "\n      python scripts/doctor.py --fix"
                ),
            )
        proc = _run(["pre-commit", "install"])
        if proc.returncode == 0 and hook_path.exists():
            return Result(
                "local grounding gate wired",
                True,
                detail="installed by --fix",
            )
        return Result(
            "local grounding gate wired",
            False,
            detail="pre-commit install failed",
            remediation=(proc.stderr or proc.stdout or "unknown error").strip(),
        )

    remediation = (
        "Wire the local grounding gate so bad requirement graphs cannot be "
        "committed:"
    )
    if not have_precommit:
        remediation += "\n      python -m pip install pre-commit"
    remediation += (
        "\n      pre-commit install"
        "\n      (or re-run: python scripts/doctor.py --fix)"
    )
    return Result(
        "local grounding gate wired",
        False,
        detail="pre-commit hook not installed",
        remediation=remediation,
    )


def _grounding_gate(label: str, path: str) -> Result:
    name = f"grounding gate ({label})"
    if not shutil.which("tl"):
        return Result(
            name,
            False,
            detail="tl CLI unavailable",
            remediation="Install throughline (see the 'tl CLI on PATH' check).",
        )
    proc = _run(["tl", "-C", path, "check", "--strict"])
    summary = (proc.stdout or proc.stderr or "").strip()
    if proc.returncode == 0:
        return Result(name, True, detail=summary)
    return Result(
        name,
        False,
        detail=f"exit {proc.returncode}",
        remediation=(
            "The requirements graph is not grounded/valid. Fix the reported "
            f"items, then re-run:\n      tl -C {path} check --strict"
            f"\n{summary}"
        ),
    )


def check_grounding_selfhost() -> Result:
    # The graph lives in idd/ (the estate convention); it was at requirements/ when
    # this doctor was written, and the move left this check failing for every
    # contributor who ran it.
    return _grounding_gate("throughline's own requirements", "idd")


def check_grounding_demo() -> Result:
    return _grounding_gate("demo graph", "examples/grounding-demo")


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def render(results: list[Result]) -> int:
    print(f"\n{BOLD}throughline contributor environment doctor{RESET}\n")
    failures = 0
    for r in results:
        if r.ok:
            mark = f"{GREEN}PASS{RESET}"
        else:
            mark = f"{RED}FAIL{RESET}"
            failures += 1
        detail = f"  {r.detail}" if r.detail else ""
        print(f"  [{mark}] {r.name}{detail}")
        if not r.ok and r.remediation:
            for line in r.remediation.splitlines():
                print(f"         {YELLOW}{line}{RESET}")
    print()
    if failures:
        print(
            f"{RED}{BOLD}{failures} check(s) failed.{RESET} "
            "Follow the remediation above, then re-run this doctor.\n"
        )
    else:
        print(
            f"{GREEN}{BOLD}All checks passed.{RESET} "
            "Your environment is ready to contribute.\n"
        )
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnose (and optionally repair) an throughline contributor "
            "development environment."
        )
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="wire the local pre-commit grounding hook if it is missing",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help=argparse.SUPPRESS,  # internal: report install kinds for this interpreter
    )
    args = parser.parse_args(argv)

    if args.probe:
        # Run inside another environment by check_cli_toolchain_chained, so the rule
        # lives in one place and is merely executed where the answer differs.
        print(json.dumps(_kinds_here()))
        return 0

    results = [
        check_python(),
        check_throughline_import(),
        check_toolchain_chained(),
        check_cli_toolchain_chained(),
        check_cli(),
        check_pytest(),
        check_precommit_hook(fix=args.fix),
        check_grounding_selfhost(),
        check_grounding_demo(),
    ]
    return render(results)


if __name__ == "__main__":
    raise SystemExit(main())
