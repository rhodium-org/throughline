# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Which filter language a test's projects are in.

`tl init` writes a project whose filters are GQL conditions (SR-0337). The tests
written before that pin the earlier language (SR-0104), which a project that
declares no language is still read in, so their projects are made as `tl init`
made them then: the same configuration and seeded document, without the
`filter_language` key and with the two templates' filters in the earlier
language. The tests of the GQL language and of the move to it use what ships.
"""
from __future__ import annotations

import pytest

import throughline.storage as storage

# The test modules whose projects are what `tl init` writes today.
_GQL_MODULES = {"test_gql_filters", "test_gql_migrate", "test_gql_format_version"}


def _earlier(template: str) -> str:
    """A shipped template as it was before SR-0337."""
    start = template.find("# The language this project's filters are in")
    if start != -1:
        key = 'filter_language = "gql"\n'
        template = template[:start] + template[template.index(key, start) + len(key):]
    return (template
            .replace("item.type = 'requirement' AND item.status <> 'deleted'",
                     "type == 'requirement' and status != 'deleted'")
            .replace("item.type = '", "type == '"))


@pytest.fixture(autouse=True)
def _projects_in_the_language_the_test_was_written_for(request, monkeypatch):
    if request.module.__name__.rsplit(".", 1)[-1] in _GQL_MODULES:
        return
    for name in ("_DEFAULT_CONFIG", "_STARTER_OVERVIEW"):
        monkeypatch.setattr(storage, name, _earlier(getattr(storage, name)))
