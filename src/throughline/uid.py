# Copyright (c) 2026 Time Back Solutions Limited
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


def used_numbers(document) -> set[int]:
    """Every number that has ever been consumed for this document's prefix:
    live items, tombstones (still on disk), and reserved (SR-0003)."""
    nums = set(document.reserved)
    for item in document.items.values():
        try:
            pfx, n = parse_uid(item.uid)
        except UidError:
            continue
        if pfx == document.prefix:
            nums.add(n)
    return nums


def next_uid(document) -> str:
    """Allocate the next unused number for the document's prefix (SR-0005)."""
    used = used_numbers(document)
    n = (max(used) + 1) if used else 1
    return format_uid(document.prefix, n, document.digits)


def collisions(project) -> list[str]:
    """UIDs that appear in more than one place (parallel-branch merge, SR-0006)."""
    seen: dict[str, int] = {}
    for item in project.items():
        seen[item.uid] = seen.get(item.uid, 0) + 1
    return sorted(u for u, c in seen.items() if c > 1)
