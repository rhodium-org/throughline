# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""The lists three ratified requirements publish are the lists the code enforces
(SR-0022, SR-0045, SR-0103).

Both lists drifted once already: SR-0022 named twelve reserved fields while the code
reserved fifteen (issue #39), and SR-0045 and SR-0103 named a `tags` filter name the
language never had while leaving out four it admits (issue #40). Each requirement now
writes its names in backticks, so these tests read them from the graph itself. A name
added to or removed from the code fails here until the requirement says the same.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from throughline.filters import FILTER_NAMES
from throughline.model import CORE_FIELDS
from throughline.storage import load_project

GRAPH = Path(__file__).resolve().parents[1] / "idd"

pytestmark = pytest.mark.skipif(not (GRAPH / "throughline.toml").exists(),
                                reason="throughline's own graph is not beside the tests")


def _names(uid: str) -> set[str]:
    return set(re.findall(r"`([^`]+)`", load_project(GRAPH).get(uid).text))


def test_the_reserved_fields_are_the_ones_sr_0022_lists():
    assert _names("SR-0022") == set(CORE_FIELDS)


def test_the_filter_namespace_is_the_one_sr_0045_and_sr_0103_publish():
    names, literals = _names("SR-0045"), _names("SR-0103")
    assert literals == {"true", "false", "none"}
    assert names | literals == set(FILTER_NAMES)
