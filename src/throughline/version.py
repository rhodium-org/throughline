# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""How this toolchain answers "what am I running?" (SR-0164).

A version is consulted precisely when someone is trying to establish what they
have, so the answer is read from the installed distribution's own metadata and
never restated in source. Where the running code is *not* that release — a source
tree with nothing installed, or an install that resolves back to a working tree —
the answer says so, rather than quietly naming the release it has departed from.

The marker is a PEP 440 *local version segment*, because the packaging rules forbid
one on a published artifact: ``1.9.0+editable`` can never be something fetched from
an index, so it cannot be mistaken for a real release.

This lives in one place on purpose. The rule was previously written out separately
in the library, in the CLI, and again in throughline-compose — the exact shape of
duplication SR-0164 exists to delete.
"""

from __future__ import annotations

import json
from importlib import metadata

UNKNOWN = "0.0.0+unknown"
EDITABLE = "editable"


def is_editable(dist_name: str) -> bool:
    """Whether ``dist_name`` is installed as an editable/working-tree install.

    Read from the install's own PEP 610 ``direct_url.json`` — the fact pip recorded
    at install time — rather than guessed by comparing paths, so it holds however
    the environment was built (venv, pipx, uv).
    """
    try:
        dist = metadata.distribution(dist_name)
    except metadata.PackageNotFoundError:
        return False
    return _editable_from_direct_url(dist)


def _editable_from_direct_url(dist: metadata.Distribution) -> bool:
    try:
        raw = dist.read_text("direct_url.json")
    except Exception:  # pragma: no cover - unreadable metadata is simply not proof
        return False
    if not raw:
        return False
    try:
        info = json.loads(raw)
    except ValueError:
        return False
    return bool(info.get("dir_info", {}).get("editable"))


def _mark_editable(version: str) -> str:
    """Append the editable marker, respecting PEP 440's single local segment.

    A version may already carry a local segment (``1.9.0+dirty``); a second ``+``
    would be invalid, so the marker joins the existing segment with a dot instead.
    """
    if "+" in version:
        return f"{version}.{EDITABLE}"
    return f"{version}+{EDITABLE}"


def distribution_version(dist_name: str) -> str:
    """Report the version of ``dist_name`` as actually installed.

    Returns the distribution's own metadata version, suffixed ``+editable`` when the
    running code is a working tree, or ``0.0.0+unknown`` when the package is not
    installed at all. Never a literal restated in source.
    """
    try:
        dist = metadata.distribution(dist_name)
    except metadata.PackageNotFoundError:
        # A source tree that was never installed: there is no release to name.
        return UNKNOWN
    version = dist.version
    if _editable_from_direct_url(dist):
        return _mark_editable(version)
    return version
