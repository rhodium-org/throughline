# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Report against a borrowed item only what the consumer can act on (SR-0026).

The union is validated by throughline's own unmodified validator, which knows one
graph and judges every item in it alike. That is right for the core and wrong for a
consumer: a finding about a borrowed item's internals names no act its reader can
take, because the remedy is a commit in the graph that owns the item. This module is
composition's own work over that unchanged result — it decides which findings survive
into a consumer's report, and re-puts the one grounding question that composition
changes the answer to.

Two rules, both stated in SR-0026:

  * A finding on a borrowed item survives only if its rule is a *seam* rule. The
    allowlist is deliberately the small set rather than its complement, so a rule
    added to core later is suppressed on borrowed items until someone decides it is
    actionable in a consumer. Defaulting the other way would let the next core rule
    reintroduce exactly the noise this closes.
  * An ``orphan`` on a *local* item is re-asked of core's own walk with the terminus
    widened to "a root, or anything borrowed", so a grounding chain that leaves the
    consumer and enters a source counts as grounded. No second grounding engine —
    ``Index.reaches`` already takes the predicate and the link types as arguments
    (SR-0004, NG-0001).

A consumer may *widen* the first of those, and only widen it (SR-0035). The
allowlist keys on the rule name, and a rule name does not say where the remedy for
a finding lies. For most rules the remedy is the owning graph's, which is what
SR-0026 suppresses — but a coverage rule the consumer itself declared is answered
by authoring an item *in the consumer*. Only the project that wrote the rule knows
which of the two it is, so only that project can say so, in its own configuration::

    [seam]
    report_on_borrowed = ["coverage"]

Widening only. There is deliberately no syntax for switching a built-in seam rule
off, because those are what keep the assembled union coherent — a dangling
cross-source reference, a uid collision, a stamp that no longer matches its target.
Silencing one would hide exactly the unresolved reference UR-0002 forbids.
"""
from __future__ import annotations

# Core's rule vocabulary, used to refuse a misspelled declaration when the config is
# read rather than accept it and leave it silently never firing — which is the very
# failure SR-0035 exists to close. Imported rather than restated so the two cannot
# drift.
from .validate import _DEFAULT_SEVERITY as _CORE_RULES

# Rules whose remedy lies at the seam or in the union itself, and which therefore
# name an act the consumer can take — resolve the reference, drop the citation,
# declare the namespace, move the pin. These are reported on borrowed items too.
SEAM_RULES = frozenset({
    # The consumer's reference into a source must land on something real.
    "dangling-link",
    "deleted-link-target",
    "malformed-link",
    "namespace-unresolved",
    # The union the consumer assembled must be coherent.
    "prefix-collision",
    "uid-collision",
    # A signature on a borrowed clause no longer covering its wording is the
    # consumer's business, not only the owner's — the edition they pinned has moved
    # away from what was accepted, and the remedy (move the pin, or tell the owner)
    # is theirs to take. SR-0026 names it in the seam for that reason.
    "ratified-stale",
    # Whole-graph verdicts that belong to nobody's single item.
    "empty-graph",
    "no-status-roles",
})

# Everything else — statuses and transitions, attributes, link vocabulary and
# endpoint rules, grounding, ratification, content quality — is the owning graph's
# business and is suppressed on a borrowed item, unless the consumer widens the seam
# for that rule (SR-0035, `parse_seam` below).


class SeamError(ValueError):
    """A malformed or unrecognised ``[seam]`` declaration — fail fast (SR-0005)."""


def parse_seam(project) -> frozenset[str]:
    """Read the consumer's ``[seam] report_on_borrowed`` declaration (SR-0035).

    Returns the extra rule names to report on borrowed items, empty when the table
    is absent — which is the default and leaves behaviour exactly as SR-0026 alone
    defines it.

    A name that core cannot emit is refused here rather than accepted and left
    silently inert, because a rule that never fires is indistinguishable from a rule
    that passes, and that indistinguishability is the defect this closes.
    """
    seam = project.config.get("seam", {})
    if not isinstance(seam, dict):
        raise SeamError("[seam] must be a table")

    unknown_keys = set(seam) - {"report_on_borrowed"}
    if unknown_keys:
        raise SeamError(
            f"[seam] has unknown key(s) {sorted(unknown_keys)} — the only key is "
            "'report_on_borrowed'")

    raw = seam.get("report_on_borrowed")
    if raw is None:
        return frozenset()
    if not isinstance(raw, list):
        raise SeamError(
            "[seam] report_on_borrowed must be an array of rule names, e.g. "
            '["coverage"]')

    out: set[str] = set()
    for name in raw:
        if not isinstance(name, str) or not name.strip():
            raise SeamError(
                f"[seam] report_on_borrowed contains a non-string entry {name!r}")
        name = name.strip()
        if name not in _CORE_RULES:
            raise SeamError(
                f"[seam] report_on_borrowed names '{name}', which is not a rule "
                "this validator can emit — it would never fire. Known rules are "
                + ", ".join(sorted(_CORE_RULES)))
        out.add(name)
    return frozenset(out)


def is_borrowed(union, uid: str) -> bool:
    """Whether ``uid`` names an item this project borrowed through a source.

    A union item is local exactly when its UID is not a mangled, source-owned one,
    which is the same test ``check``'s own summary uses to split the two.
    """
    return union.qualified(uid) != uid


def apply_seam(findings, union, schema, index, extra_rules=frozenset()):
    """Filter ``findings`` to what the consumer can act on, and rescue local items
    grounded through a source. Returns ``(kept, suppressed, rescued)`` so the caller
    can report the verdict and a test can audit the reasoning rather than trust it.

    ``extra_rules`` is the consumer's ``[seam] report_on_borrowed`` set (SR-0035),
    reported on borrowed items in addition to :data:`SEAM_RULES`. It can only widen
    the seam — a rule already built in stays in regardless of what is passed.
    """
    reported = SEAM_RULES | frozenset(extra_rules)
    kept, suppressed, rescued = [], [], []
    for f in findings:
        if is_borrowed(union, f.uid) and f.rule not in reported:
            suppressed.append(f)
            continue
        if f.rule == "orphan" and not is_borrowed(union, f.uid):
            if index.reaches(
                f.uid,
                lambda it: schema.is_root(it) or is_borrowed(union, it.uid),
                schema.ground_link_types,
            ):
                rescued.append(f)
                continue
        kept.append(f)
    return kept, suppressed, rescued
