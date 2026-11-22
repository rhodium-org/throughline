# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""What Python runs tl, and how tl was installed (SR-0371).

A pipx or virtual-environment install runs on the interpreter it was made with.
When the operating system upgrades its Python and removes the old one, every
command in that environment fails before any of its code runs, so tl cannot
report the fault then. What it can do is say, beforehand, whether an install
depends on the operating system's Python, and name the one command that rebuilds
it for the installer that made it.

Everything is read from the running interpreter and the files its installer left
in the environment: pipx writes ``pipx_metadata.json`` and ``uv tool`` writes
``uv-receipt.toml`` into the environment's root. Nothing is run.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path, PurePath

UV_TOOL = "uv tool"
PIPX = "pipx"
VENV = "a virtual environment"
NONE = "none (installed into the Python itself)"

REBUILD = {
    UV_TOOL: "uv tool install --reinstall --python 3.13 --managed-python throughline",
    PIPX: "pipx reinstall-all --python python3   (if pipx is broken too: "
          "uvx pipx reinstall-all --python python3)",
    VENV: "python3 -m venv --clear {prefix} && {prefix_bin}/pip install throughline",
    NONE: "reinstall throughline into the new Python",
}


@dataclass(frozen=True)
class Environment:
    """The facts the report is made from, so a test can give its own."""

    executable: str
    version: str
    prefix: str
    base_prefix: str
    base_executable: str
    uv_python_dir: str | None = None

    @classmethod
    def running(cls) -> "Environment":
        return cls(
            executable=sys.executable,
            version=sys.version.split()[0],
            prefix=sys.prefix,
            base_prefix=sys.base_prefix,
            base_executable=getattr(sys, "_base_executable", sys.executable),
            uv_python_dir=os.environ.get("UV_PYTHON_INSTALL_DIR"),
        )


def installer(env: Environment) -> str:
    """Which installer made the environment tl runs in."""
    root = Path(env.prefix)
    if (root / "uv-receipt.toml").is_file():
        return UV_TOOL
    if (root / "pipx_metadata.json").is_file():
        return PIPX
    if env.prefix != env.base_prefix:
        return VENV
    return NONE


def uv_managed(env: Environment) -> bool:
    """Whether the interpreter the environment was made from is one uv manages.

    uv keeps its Pythons under ``<data dir>/uv/python/`` unless
    ``UV_PYTHON_INSTALL_DIR`` says otherwise; the operating system's package
    manager never touches either.
    """
    base = PurePath(env.base_prefix)
    if env.uv_python_dir and _within(base, PurePath(env.uv_python_dir)):
        return True
    parts = [p.lower() for p in base.parts]
    return any(a == "uv" and b == "python" for a, b in zip(parts, parts[1:]))


def _within(path: PurePath, root: PurePath) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def report(env: Environment, tl_version: str) -> list[tuple[str, str]]:
    """The lines ``tl doctor`` prints, as (label, value) pairs."""
    how = installer(env)
    managed = uv_managed(env)
    bin_dir = "Scripts" if os.name == "nt" else "bin"
    rebuild = REBUILD[how].format(prefix=env.prefix,
                                  prefix_bin=str(Path(env.prefix) / bin_dir))
    if managed:
        survives = "yes: uv manages this Python, so the operating system will not remove it"
    else:
        survives = ("no: if the operating system upgrades or removes this Python, "
                    "rebuild with the command below")
    return [
        ("tl", tl_version),
        ("Python", env.version),
        ("Running on", env.executable),
        ("Made from", env.base_executable),
        ("Python managed by uv", "yes" if managed else "no"),
        ("Installed with", how),
        ("Survives an OS Python upgrade", survives),
        ("Rebuild on a new Python", rebuild),
    ]


def render(lines: list[tuple[str, str]]) -> str:
    width = max(len(label) for label, _ in lines)
    return "\n".join(f"{label + ':':<{width + 1}} {value}" for label, value in lines)
