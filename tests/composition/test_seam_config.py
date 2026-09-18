# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""A consumer may widen the seam for its own rules (SR-0035).

The seam allowlist keys on the rule *name*, and a rule name does not say where the
remedy for a finding lies. A coverage rule the consumer itself declared is answered
by authoring an item in the consumer, so it is not the class SR-0026 suppresses —
but only the project that wrote the rule can say that. These tests hold both halves:
the default stays exactly as SR-0026 alone defines it, and a declaration is honoured
without ever being able to narrow the seam.
"""
from __future__ import annotations

import pytest

from throughline.cli import main as tlc_main
from throughline.seam import SEAM_RULES, SeamError, apply_seam, parse_seam


class _Cfg:
    """The one thing `parse_seam` reads off a project."""

    def __init__(self, config):
        self.config = config


# --------------------------------------------------------------------------
# Reading the declaration
# --------------------------------------------------------------------------


def test_absent_table_widens_nothing():
    """The default. A project that says nothing must behave exactly as before, so
    no existing consumer inherits findings by upgrading."""
    assert parse_seam(_Cfg({})) == frozenset()
    assert parse_seam(_Cfg({"seam": {}})) == frozenset()


def test_a_declared_rule_is_read():
    assert parse_seam(
        _Cfg({"seam": {"report_on_borrowed": ["coverage"]}})
    ) == frozenset({"coverage"})


def test_an_unknown_rule_name_is_refused_not_ignored():
    """The whole defect being closed is a rule that silently never fires. A typo
    accepted quietly would reproduce it exactly, so it fails at read time."""
    with pytest.raises(SeamError) as exc:
        parse_seam(_Cfg({"seam": {"report_on_borrowed": ["coverge"]}}))
    assert "coverge" in str(exc.value)
    assert "never fire" in str(exc.value)


def test_a_malformed_declaration_is_refused():
    with pytest.raises(SeamError):
        parse_seam(_Cfg({"seam": {"report_on_borrowed": "coverage"}}))
    with pytest.raises(SeamError):
        parse_seam(_Cfg({"seam": {"report_on_borrowed": [1]}}))
    with pytest.raises(SeamError):
        parse_seam(_Cfg({"seam": "coverage"}))


def test_an_unknown_key_is_refused():
    """A key nobody reads is indistinguishable from one that did not work."""
    with pytest.raises(SeamError) as exc:
        parse_seam(_Cfg({"seam": {"report_on_borowed": ["coverage"]}}))
    assert "report_on_borrowed" in str(exc.value)


# --------------------------------------------------------------------------
# Applying it
# --------------------------------------------------------------------------


class _Union:
    """Minimal stand-in: uids starting `B` are borrowed."""

    def qualified(self, uid):
        return f"src:{uid}" if uid.startswith("B") else uid


class _Finding:
    def __init__(self, rule, uid):
        self.rule, self.uid = rule, uid


def test_widening_cannot_narrow_the_built_in_seam():
    """There is no syntax for switching a built-in rule off, and passing an
    unrelated set must not turn one off by accident either — those rules are what
    keep the assembled union coherent."""
    findings = [_Finding("dangling-link", "B-0001")]
    kept, suppressed, _ = apply_seam(
        findings, _Union(), None, None, frozenset({"coverage"})
    )
    assert [f.rule for f in kept] == ["dangling-link"]
    assert suppressed == []


def test_a_widened_rule_survives_on_a_borrowed_item():
    findings = [_Finding("coverage", "B-0001")]
    kept, suppressed, _ = apply_seam(
        findings, _Union(), None, None, frozenset({"coverage"})
    )
    assert [f.uid for f in kept] == ["B-0001"]
    assert suppressed == []


def test_the_same_rule_is_suppressed_when_not_declared():
    """The premise of the test above — without the declaration this finding is
    dropped, which is the behaviour SR-0026 defines and this change preserves."""
    assert "coverage" not in SEAM_RULES
    findings = [_Finding("coverage", "B-0001")]
    kept, suppressed, _ = apply_seam(findings, _Union(), None, None)
    assert kept == []
    assert [f.uid for f in suppressed] == ["B-0001"]


def test_a_local_finding_is_reported_either_way():
    """Widening the seam concerns borrowed items only; a consumer's own item was
    always judged under its own model in full."""
    findings = [_Finding("coverage", "L-0001")]
    for extra in (frozenset(), frozenset({"coverage"})):
        kept, suppressed, _ = apply_seam(findings, _Union(), None, None, extra)
        assert [f.uid for f in kept] == ["L-0001"]
        assert suppressed == []


# --------------------------------------------------------------------------
# End to end, through the real CLI over a real composed graph
# --------------------------------------------------------------------------


def test_check_hides_the_borrowed_coverage_gap_by_default(
    coverage_consumer_dir, capsys
):
    """The bug this closes, stated as behaviour: the consumer declares a coverage
    rule, the borrowed intent does not satisfy it, and `check` says nothing about
    it. Only the local intent is named."""
    rc = tlc_main(["-C", str(coverage_consumer_dir), "check"])
    captured = capsys.readouterr()
    out = captured.out + captured.err
    assert "toy:INT-0001" not in out
    assert "INT-0001" in out  # the consumer's own is reported
    assert rc == 0


def test_declaring_the_rule_makes_the_borrowed_gap_visible(
    widened_consumer_dir, capsys
):
    """The same graph, one declaration different, and the borrowed gap is now
    reported — in the consumer's own vocabulary (`toy:INT-0001`, not a mangled
    union uid)."""
    tlc_main(["-C", str(widened_consumer_dir), "check"])
    captured = capsys.readouterr()
    out = captured.out + captured.err
    assert "toy:INT-0001" in out
    assert "coverage" in out


def test_a_widened_rule_can_fail_the_build_under_strict(widened_consumer_dir):
    """The point of the whole exercise. A coverage gap over borrowed items has to
    be able to gate CI, not merely print."""
    assert tlc_main(["-C", str(widened_consumer_dir), "check", "--strict"]) == 1


def test_the_default_still_passes_strict(coverage_consumer_dir, capsys):
    """And the same graph without the declaration must NOT fail, or the default
    would have changed for every existing consumer."""
    rc = tlc_main(["-C", str(coverage_consumer_dir), "check", "--strict"])
    out = capsys.readouterr().out
    assert "toy:INT-0001" not in out
    # The local intent still trips the rule under strict; the borrowed one does not.
    assert rc == 1


def test_a_bad_declaration_is_refused_by_the_cli(widened_consumer_dir, capsys):
    """Refused where the reader can see it, rather than at import time."""
    cfg = widened_consumer_dir / "throughline.toml"
    cfg.write_text(
        cfg.read_text().replace('["coverage"]', '["nonsense-rule"]'), encoding="utf-8"
    )
    rc = tlc_main(["-C", str(widened_consumer_dir), "check"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "nonsense-rule" in err
