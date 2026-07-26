# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""UID grammar and allocation (SR-0001..06, doc 06 §3).

UIDs are immutable, project-unique, position-independent, and never reused —
allocation skips every number that has ever existed, including tombstones and
the manifest ``reserved`` list.
"""
from __future__ import annotations

import re

# prefix = UPPER (UPPER|DIGIT){1,15} ; number = DIGIT{width}, grows past width.
UID_RE = re.compile(r"^([A-Z][A-Z0-9]{1,15})-([0-9]+)$")


class UidError(ValueError):
    pass


def parse_uid(uid: str) -> tuple[str, int]:
    m = UID_RE.match(uid)
    if not m:
        raise UidError(f"'{uid}' is not a valid UID (expected <PREFIX>-<NUMBER>)")
    return m.group(1), int(m.group(2))


def format_uid(prefix: str, number: int, digits: int = 4) -> str:
    return f"{prefix}-{number:0{digits}d}"


def used_numbers(register) -> set[int]:
    """Every number that has ever been consumed for this register's prefix:
    live items, tombstones (still on disk), and reserved (SR-0003)."""
    nums = set(register.reserved)
    for item in register.items.values():
        try:
            pfx, n = parse_uid(item.uid)
        except UidError:
            continue
        if pfx == register.prefix:
            nums.add(n)
    return nums


def next_uid(register) -> str:
    """Allocate the next unused number for the register's prefix (SR-0005)."""
    used = used_numbers(register)
    n = (max(used) + 1) if used else 1
    return format_uid(register.prefix, n, register.digits)


def collisions(project) -> list[str]:
    """UIDs that appear in more than one place (parallel-branch merge, SR-0006)."""
    seen: dict[str, int] = {}
    for item in project.items():
        seen[item.uid] = seen.get(item.uid, 0) + 1
    cross_doc = {u for u, c in seen.items() if c > 1}
    # Same-folder duplicates never reach ``project.items()`` — the per-register
    # dict already collapsed them — so fold in what the loader recorded.
    same_folder = getattr(project, "duplicate_uids", set())
    return sorted(cross_doc | same_folder)
