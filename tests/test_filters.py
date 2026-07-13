# Copyright (c) 2026 Time Back Solutions Limited
# SPDX-License-Identifier: Apache-2.0
"""Grammar and safety tests for the filter language (SR-0104, SR-0103).

These pin the *whole* accepted grammar and the rejection of everything outside
it, so the filter surface is defined here rather than inferred from the few
expressions the engine tests happen to use.
"""
from __future__ import annotations

import pytest

from throughline.filters import FilterError, safe_eval

NS = {
    "uid": "SR-0045", "type": "system_requirement", "status": "approved",
    "register": "SR", "title": "Filter expression language",
    "text": "one boolean filter language", "rationale": "",
    "normative": True, "derived": False,
    "attrs": {"priority": "must", "tags": ["security", "query"], "score": 3},
    "true": True, "false": False, "none": None,
}


def ok(expr: str) -> bool:
    return bool(safe_eval(expr, NS))


# ----------------------------------------------------------------- accepted
@pytest.mark.parametrize("expr", [
    "true", "false", "not false",
    "type == 'system_requirement'", "type=='system_requirement'",
    'type == "system_requirement"',
    "status != 'draft'", "register == 'SR'", "uid == 'SR-0045'",
    "normative", "not derived", "normative and not derived",
    "status == 'draft' or status == 'approved'",
    "not (status == 'draft' and normative)",
    "status in ['approved', 'draft']", "status not in ('rejected', 'deleted')",
    "'boolean' in text", "'x' not in text",
    "attrs.get('priority') == 'must'", "attrs['priority'] == 'must'",
    "attrs.get('missing') == none", "attrs.get('missing', 'd') == 'd'",
    "'security' in attrs.get('tags')", "'security' in attrs['tags']",
    "attrs.get('score') >= 3", "attrs['score'] < 10", "attrs['score'] == 3",
    "title.lower().startswith('filter')", "title.upper().endswith('LANGUAGE')",
    "text.strip() == 'one boolean filter language'",
])
def test_accepted_grammar(expr):
    assert isinstance(safe_eval(expr, NS), (bool, str, int, list, type(None)))


def test_semantics_true():
    assert ok("type == 'system_requirement' and attrs.get('priority') == 'must'")


def test_semantics_false():
    assert not ok("status == 'draft'")


def test_empty_and_membership_forms():
    assert ok("attrs['score'] in [1, 2, 3]")
    assert not ok("attrs['score'] in [1, 2]")


# ------------------------------------------------------------------ rejected
@pytest.mark.parametrize("expr", [
    "__import__('os').system('id')",
    "().__class__.__bases__[0]",
    "attrs.__class__",
    "attrs.__class__.__mro__",
    "open('/etc/passwd').read()",
    "'{0.__class__}'.format(attrs)",
    "type.format('x')",
    "[x for x in attrs]",
    "(lambda: 1)()",
    "1 if normative else 0",
    "attrs.pop('priority')",
    "attrs.update({'x': 1})",
    "status = 'draft'",           # assignment, not comparison
    "unknownname == 'x'",         # name outside the namespace
    "1 + 1 == 2",                 # arithmetic is not part of the grammar
    "status & 1",                 # bitwise
    "attrs[0:1]",                 # slice
])
def test_rejected_outside_grammar(expr):
    with pytest.raises(FilterError):
        safe_eval(expr, NS)


def test_rejection_does_not_execute(tmp_path):
    marker = tmp_path / "pwned"
    payload = f"__import__('pathlib').Path({str(marker)!r}).write_text('x')"
    with pytest.raises(FilterError):
        safe_eval(payload, NS)
    assert not marker.exists()
