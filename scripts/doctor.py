#!/usr/bin/env python3
# Copyright (c) 2026 Time Back Solutions Limited
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
    * pytest test runner available
    * local grounding gate wired (pre-commit hook installed)
    * grounding gate passes (self-hosted requirements + demo graph)
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MIN_PYTHON = (3, 11)

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
    return _grounding_gate("throughline's own requirements", "requirements")


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
    args = parser.parse_args(argv)

    results = [
        check_python(),
        check_throughline_import(),
        check_cli(),
        check_pytest(),
        check_precommit_hook(fix=args.fix),
        check_grounding_selfhost(),
        check_grounding_demo(),
    ]
    return render(results)


if __name__ == "__main__":
    raise SystemExit(main())
