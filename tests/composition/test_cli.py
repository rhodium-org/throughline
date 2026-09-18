# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""End-to-end CLI tests over materialised fixtures: the two-tool contract
(SR-0003, SR-0005) — bare `tl` fails fast, `tl-compose` resolves and passes."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from throughline.cli import main as tl_main
from throughline.storage import load_project
import throughline
from throughline.cli import main as tlc_main
from throughline.seam import apply_seam


def test_bare_tl_check_fails_fast_on_qualified_reference(consumer_dir, capsys):
    # A project that declares no sources cannot resolve `toy:SR-0001`; the finding
    # names the namespace nothing declares (SR-0107), never a broken local link.
    toml = consumer_dir / "throughline.toml"
    toml.write_text(re.sub(r"\[\[sources\]\][^\[]*", "", toml.read_text()))
    rc = tl_main(["-C", str(consumer_dir), "check", "--base", ""])
    assert rc == 1
    assert "namespace-unresolved" in capsys.readouterr().out


def test_compose_check_resolves_and_passes(consumer_dir, capsys):
    rc = tlc_main(["-C", str(consumer_dir), "check", "--base", ""])
    assert rc == 0
    assert "composed graph is sound" in capsys.readouterr().err


def test_compose_check_prints_union_summary_split_local_borrowed(consumer_dir, capsys):
    # SR-0022: the composed check prints core's graph summary computed over the
    # union (2 consumer items + 3 borrowed toy items = 5), then a Local line that
    # splits the consumer's own items from the borrowed ones.
    rc = tlc_main(["-C", str(consumer_dir), "check", "--base", ""])
    assert rc == 0
    err = capsys.readouterr().err
    assert "tl check ·" in err            # the same header core prints
    assert "Items      5 live" in err     # union totals
    assert "Grounding  " in err           # full core summary, not just a tally
    assert "Local      2 of 5 local" in err
    assert "3 borrowed" in err


def test_grounding_figure_counts_only_what_the_consumer_can_act_on(
    lean_consumer_dir, capsys
):
    """SR-0029: the grounding headline is scoped to the consumer's own items.

    The lean consumer does not adopt `implements` as a grounding link, so the toy
    source's SR — which grounds through it — reaches no root under this schema.
    Counted over the union that reads as 2 of 3; counted over what the consumer can
    actually link, it is 1 of 1. The union figure would print a shortfall directly
    above a verdict of zero errors, with no act available to close it.
    """
    rc = tlc_main(["-C", str(lean_consumer_dir), "check", "--base", ""])
    assert rc == 0
    err = capsys.readouterr().err

    assert "Grounding  1/1 local non-root items trace to a root" in err
    assert "1/1 local delivery roots served" in err
    # The union figure is the one this requirement exists to stop printing.
    assert "2/3" not in err
    # The line says what it counts, so its scope is not inferred from its size.
    assert "local non-root items" in err
    # Union totals stay: a composer still sees the size of what was validated.
    assert "Items      5 live" in err
    assert "Local      2 of 5 local" in err
    assert "3 borrowed" in err


def test_the_borrowed_orphan_is_real_but_not_the_consumers_to_answer(
    lean_consumer_dir, capsys
):
    """The premise of the test above: the borrowed item genuinely does not reach a
    root under the consumer's schema, and the seam is what keeps it out of the
    report (SR-0026). Without this, a scoped headline of 1/1 could be passing for
    the trivial reason that nothing was ungrounded in the first place."""
    from throughline.graph import Index
    from throughline.validate import validate
    from throughline.composition import resolve_sources
    from throughline.sources import parse_sources
    from throughline.union import build_union

    consumer = load_project(str(lean_consumer_dir))
    res = resolve_sources(parse_sources(consumer), Path(lean_consumer_dir))
    union = build_union(consumer, res.projects(), res.labels)

    raw = validate(union.project, strict=False)
    assert any(f.rule == "orphan" for f in raw), (
        "expected core to report the borrowed item as an orphan under this schema"
    )
    kept, suppressed, _rescued = apply_seam(
        raw, union, union.project.schema, Index.build(union.project)
    )
    assert any(f.rule == "orphan" for f in suppressed)
    assert not any(f.rule == "orphan" for f in kept)


def test_core_still_labels_the_grounding_line(source_dir):
    """The rescope finds core's grounding line by its label rather than its
    position. If core ever renames or reformats it, that must fail here — loudly,
    at build time — rather than degrade a user's report quietly at runtime."""
    from throughline.cli import _check_summary
    from throughline.composition import _GROUNDING_LABEL

    lines = _check_summary(load_project(str(source_dir)))
    labelled = [ln for ln in lines if ln.startswith(_GROUNDING_LABEL)]
    assert len(labelled) == 1, (
        f"core's summary no longer has exactly one {_GROUNDING_LABEL!r} line: {lines}"
    )


def test_compose_check_quiet_suppresses_summary(consumer_dir, capsys):
    # SR-0022: the summary obeys the existing quiet flag, exactly as core does.
    rc = tlc_main(["-C", str(consumer_dir), "check", "--base", "", "--quiet"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "tl check ·" not in err
    assert "Local " not in err


def test_compose_check_json_output_has_no_summary(consumer_dir, capsys):
    # SR-0022: the machine-readable json output is unaffected by the summary.
    import json
    rc = tlc_main(["-C", str(consumer_dir), "check", "--base", "", "--format", "json"])
    assert rc == 0
    cap = capsys.readouterr()
    assert "Local " not in cap.out and "tl check ·" not in cap.err
    assert isinstance(json.loads(cap.out), list)  # stdout is the findings array


def test_compose_check_passthrough_without_sources(source_dir):
    # A source project has no [[sources]] — tl-compose check must behave like tl.
    rc = tlc_main(["-C", str(source_dir), "check", "--base", ""])
    assert rc == 0


def test_compose_check_resolves_url_source(consumer_dir, source_dir, tmp_path,
                                           monkeypatch, capsys):
    # Same composition, but the source is pinned by url+ref (SR-0006): tag the toy
    # source as a git origin, point the consumer at it by url, and compose.
    monkeypatch.setenv("TL_CACHE", str(tmp_path / "cache"))

    def git(*a):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a],
                       cwd=str(source_dir), check=True, capture_output=True)
    git("init", "-b", "main"); git("add", "."); git("commit", "-m", "edition")
    git("tag", "v4.0.3")

    toml = consumer_dir / "throughline.toml"
    toml.write_text(toml.read_text().replace(
        'path = "../toy-source"', f'url = "{source_dir}"\nref = "v4.0.3"'))

    rc = tlc_main(["-C", str(consumer_dir), "check", "--base", ""])
    assert rc == 0
    assert "composed graph is sound" in capsys.readouterr().err


def test_compose_check_translates_dangling_cross_source(consumer_dir, capsys):
    sr = consumer_dir / "system-requirements" / "SR-0001.yml"
    sr.write_text(sr.read_text().replace("toy:SR-0001", "toy:SR-9999"))
    rc = tlc_main(["-C", str(consumer_dir), "check", "--base", ""])
    assert rc == 1
    out = capsys.readouterr().out
    assert "toy:SR-9999" in out       # the composer's own vocabulary
    assert "TOYSR-9999" not in out    # never the mangled internal form


def test_compose_trace_resolves_cross_source(consumer_dir, capsys):
    # SR-0010: the consumer's SR-0001 relates to toy:SR-0001. Bare `tl trace` sees
    # only local items and dead-ends that edge at (unresolved); tl-compose walks the
    # union, so the borrowed clause resolves into its source and reads in namespace
    # vocabulary — never the mangled internal form, never (unresolved).
    rc = tlc_main(["-C", str(consumer_dir), "trace", "SR-0001"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "(relates) toy:SR-0001" in out
    assert "A normative clause the source offers" in out  # borrowed clause's own title
    assert "(unresolved)" not in out
    assert "TOYSR-0001" not in out  # never the mangled internal UID


def test_compose_trace_starts_from_qualified_uid(consumer_dir, capsys):
    # SR-0010: the composer may trace starting from a borrowed clause itself.
    rc = tlc_main(["-C", str(consumer_dir), "trace", "toy:SR-0001"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("toy:SR-0001  [system_requirement/approved]")
    assert "TOYSR-0001" not in out


def test_compose_trace_dangling_cross_source_stays_unresolved(consumer_dir, capsys):
    # SR-0010: a namespace-qualified target with no clause at the pinned edition is
    # still (unresolved) — but in the composer's own vocabulary, never mangled.
    sr = consumer_dir / "system-requirements" / "SR-0001.yml"
    sr.write_text(sr.read_text().replace("toy:SR-0001", "toy:SR-9999"))
    rc = tlc_main(["-C", str(consumer_dir), "trace", "SR-0001"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "toy:SR-9999 (unresolved)" in out
    assert "TOYSR-9999" not in out


def test_compose_trace_passthrough_without_sources(source_dir, capsys):
    # A source project declares no [[sources]]: tl-compose trace must behave like
    # bare tl trace over the local graph.
    rc = tlc_main(["-C", str(source_dir), "trace", "SR-0001"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("SR-0001  [system_requirement/approved]")
    assert "(implements) UR-0001" in out


def test_compose_docs_renders_borrowed_target_attribute(consumer_dir):
    # SR-0007/SR-0110: a matrix cell whose target is a namespace-qualified borrowed
    # clause renders that clause's own attribute, resolved through the union — not a
    # UID the reader cannot look up. The toy source's SR-0001 carries priority: must.
    spec = consumer_dir / "spec.md"
    spec.write_text(
        "<!-- tl:matrix outgoing:relates@uid(priority) uid == 'SR-0001' -->\n"
        "<!-- tl:end -->\n", encoding="utf-8")
    rc = tlc_main(["-C", str(consumer_dir), "docs", str(spec)])
    assert rc == 0
    out = spec.read_text(encoding="utf-8")
    assert "toy:SR-0001 (must)" in out


def test_compose_docs_default_target_is_qualified_uid(consumer_dir):
    # With no @ suffix the cell is the namespace-qualified UID, exactly as core.
    spec = consumer_dir / "spec.md"
    spec.write_text(
        "<!-- tl:matrix outgoing:relates uid == 'SR-0001' -->\n"
        "<!-- tl:end -->\n", encoding="utf-8")
    rc = tlc_main(["-C", str(consumer_dir), "docs", str(spec)])
    assert rc == 0
    assert "toy:SR-0001" in spec.read_text(encoding="utf-8")


def test_compose_docs_passthrough_without_sources(source_dir):
    # A source project declares no [[sources]]: tl-compose docs must behave like tl
    # docs — inject over the local graph with no resolver.
    spec = source_dir / "spec.md"
    spec.write_text("<!-- tl:item INT-0001 -->\n<!-- tl:end -->\n", encoding="utf-8")
    rc = tlc_main(["-C", str(source_dir), "docs", str(spec)])
    assert rc == 0
    assert "Toy source purpose" in spec.read_text(encoding="utf-8")


def test_compose_docs_item_block_shows_borrowed_link_reference(consumer_dir):
    # SR-0113: an item block lists its outgoing links resolver-enriched. The
    # consumer's SR-0001 relates to toy:SR-0001, whose source_ref is V1.1.1, so the
    # block shows the reference number a reader could not derive from a UID alone.
    spec = consumer_dir / "spec.md"
    spec.write_text("<!-- tl:item SR-0001 -->\n<!-- tl:end -->\n", encoding="utf-8")
    rc = tlc_main(["-C", str(consumer_dir), "docs", str(spec)])
    assert rc == 0
    out = spec.read_text(encoding="utf-8")
    assert "*Relates:* toy:SR-0001 (V1.1.1)" in out
    assert "*Derives from:* INT-0001" in out


def test_compose_docs_sourced_mirrors_borrowed_clause(consumer_dir):
    # SR-0039: tl:sourced mirrors, in full, the external clause the consumer's items
    # reference — toy:SR-0001's own block, drawn from its source.
    ref = consumer_dir / "reference.md"
    ref.write_text(
        "<!-- tl:sourced uid == 'SR-0001' -->\n<!-- tl:end -->\n", encoding="utf-8")
    rc = tlc_main(["-C", str(consumer_dir), "docs", str(ref)])
    assert rc == 0
    out = ref.read_text(encoding="utf-8")
    assert "A normative clause the source offers" in out
    assert "The source shall provide one concrete, testable clause." in out


def test_compose_docs_sourced_states_qualified_identity(consumer_dir):
    """SR-0039: the mirrored clause is stated under the identity the citing document
    uses for it — namespace-qualified, with its reference number — never under the
    source's own local UID, which the consumer's unrelated SR-0001 also carries."""
    ref = consumer_dir / "reference.md"
    ref.write_text(
        "<!-- tl:sourced uid == 'SR-0001' -->\n<!-- tl:end -->\n", encoding="utf-8")
    assert tlc_main(["-C", str(consumer_dir), "docs", str(ref)]) == 0
    out = ref.read_text(encoding="utf-8")
    assert "**toy:SR-0001 (V1.1.1) — A normative clause the source offers**" in out
    # The defect this fixes: the borrowed clause published under a bare SR-0001, the
    # same heading the consumer's own unrelated SR-0001 renders under.
    assert "**SR-0001 — A normative clause the source offers**" not in out


def test_compose_docs_sourced_qualifies_the_mirrored_clauses_links(consumer_dir):
    """SR-0039: a mirrored clause's own outgoing links render namespace-qualified, so
    a target internal to the source is never shown as though it named a consumer
    item."""
    ref = consumer_dir / "reference.md"
    ref.write_text(
        "<!-- tl:sourced uid == 'SR-0001' -->\n<!-- tl:end -->\n", encoding="utf-8")
    assert tlc_main(["-C", str(consumer_dir), "docs", str(ref)]) == 0
    out = ref.read_text(encoding="utf-8")
    body = out.split("A normative clause the source offers", 1)[1]
    for line in body.splitlines():
        if line.startswith("*") and ":*" in line and "Rationale" not in line:
            for target in line.split(":*", 1)[1].split(","):
                target = target.strip()
                if target:
                    assert target.startswith("toy:"), f"unqualified target: {line}"


def test_compose_docs_sourced_placeholder_when_nothing_is_referenced(source_dir):
    # SR-0039: the selected items reference no external clause, so there is nothing
    # to mirror and a clear placeholder is rendered rather than an error.
    ref = source_dir / "reference.md"
    ref.write_text(
        "<!-- tl:sourced uid == 'SR-0001' -->\n<!-- tl:end -->\n", encoding="utf-8")
    rc = tlc_main(["-C", str(source_dir), "docs", str(ref)])
    assert rc == 0
    assert "reference no external clause" in ref.read_text(encoding="utf-8")


def test_compose_docs_sourced_fails_on_an_unmirrorable_reference(consumer_dir):
    """SR-0039: a referenced clause that cannot be rendered from its declared source
    fails injection rather than being quietly dropped."""
    from throughline.composition import render_sourced
    from throughline.inject import InjectError

    class _NoSources:
        def mirror_block(self, uid):
            return None

    class _Item:
        uid = "SR-0001"
        is_deleted = False
        links = [type("L", (), {"target": "toy:SR-9999", "type": "relates"})()]

    class _Proj:
        def items(self):
            return [_Item()]

    import throughline.composition as d
    real = d.matching
    d.matching = lambda project, expr: [_Item()]
    try:
        with pytest.raises(InjectError) as ei:
            render_sourced(_Proj(), "uid == 'SR-0001'", _NoSources())
        assert "toy:SR-9999" in str(ei.value)
    finally:
        d.matching = real


def _write_sr(consumer_dir: Path, uid: str, links: list[tuple[str, str]]) -> Path:
    """Materialise a consumer system_requirement with the given grounding links,
    used to exercise the ratify gate over composition."""
    body = (f"uid: {uid}\ntype: system_requirement\nstatus: proposed\n"
            f"title: {uid} fixture clause\n"
            f"text: A consumer clause used to exercise the ratify gate.\n")
    if links:
        body += "links:\n" + "".join(
            f"- target: {t}\n  type: {k}\n" for t, k in links)
    body += "attrs:\n  priority: must\n  origin: human\n"
    p = consumer_dir / "system-requirements" / f"{uid}.yml"
    p.write_text(body, encoding="utf-8")
    return p


def test_source_ratification_is_not_stale_just_because_we_relabelled_it(
        source_dir, consumer_dir, capsys):
    # SR-0024: the source's own graph signs SR-0001 and stamps the fingerprint of
    # the item as *it* holds it. Composition mangles that UID to keep identity
    # unique in the union, and the UID is the first field the fingerprint covers —
    # so before this fix every signature in every source read as drifted in every
    # consumer of it, on content nobody had touched.
    assert tl_main(["-C", str(source_dir), "ratify", "SR-0001", "--by", "tester"]) == 0
    capsys.readouterr()
    rc = tlc_main(["-C", str(consumer_dir), "check", "--base", "", "--strict"])
    assert "ratified-stale" not in capsys.readouterr().out
    assert rc == 0


def test_drift_in_a_source_is_still_reported_through_composition(
        source_dir, consumer_dir, capsys):
    # The seam excuses the label and nothing else: rewrite the signed text in the
    # source and the consumer must still be told the accepted wording has moved.
    assert tl_main(["-C", str(source_dir), "ratify", "SR-0001", "--by", "tester"]) == 0
    capsys.readouterr()
    clause = source_dir / "system-requirements" / "SR-0001.yml"
    clause.write_text(
        clause.read_text(encoding="utf-8")
        .replace("The source shall provide one concrete, testable clause.",
                 "The source shall provide something else entirely."),
        encoding="utf-8")
    rc = tlc_main(["-C", str(consumer_dir), "check", "--base", "", "--strict"])
    out = capsys.readouterr().out
    assert rc == 1
    # ...and named in the composer's own vocabulary, not the synthetic prefix.
    assert "toy:SR-0001" in out and "ratified-stale" in out


def test_compose_ratify_resolves_cross_source_grounding(consumer_dir, capsys):
    # The fix (SR-0004): tl-compose runs the accountability gate over the union, where
    # the grounding chain into the source resolves, then persists the accepted status
    # to the consumer's own file — never the read-only source (NG-0002).
    p = _write_sr(consumer_dir, "SR-0002", [("toy:INT-0001", "derives_from")])
    rc = tlc_main(["-C", str(consumer_dir), "ratify", "SR-0002", "--by", "tester"])
    assert rc == 0
    assert "SR-0002 ratified by tester" in capsys.readouterr().out
    text = p.read_text(encoding="utf-8")
    assert "status: ratified" in text
    assert "ratified_by: tester" in text


def test_compose_ratify_binds_the_signature_to_the_content(consumer_dir, capsys):
    # SR-0004: core `grounding.ratify` writes the whole record, so a composed
    # sign-off carries the content fingerprint that binds it (core SR-0148) — the
    # thing the hand-rolled copy this replaced never wrote. Proof it is the real
    # stamp and not a plausible string: the validator recomputes it and is silent.
    p = _write_sr(consumer_dir, "SR-0002", [("toy:INT-0001", "derives_from")])
    assert tlc_main(["-C", str(consumer_dir), "ratify", "SR-0002", "--by", "tester"]) == 0
    capsys.readouterr()
    assert "ratified_fingerprint:" in p.read_text(encoding="utf-8")
    assert tlc_main(["-C", str(consumer_dir), "check", "--base", "", "--strict"]) == 0
    assert "ratified-stale" not in capsys.readouterr().out


def test_compose_ratify_reads_the_configured_ratified_status(consumer_dir, capsys):
    # The copy hardcoded `item.status = "ratified"`, which happens to be right only
    # while a project names that status "ratified". Core resolves the *role* against
    # the project's own [status.roles], so a graph that calls it something else is
    # ratified into its own vocabulary, not ours.
    cfg = consumer_dir / "throughline.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8")
                   .replace('"ratified", "rejected"', '"accepted", "rejected"')
                   .replace('ratified = "ratified"', 'ratified = "accepted"'),
                   encoding="utf-8")
    p = _write_sr(consumer_dir, "SR-0002", [("toy:INT-0001", "derives_from")])
    assert tlc_main(["-C", str(consumer_dir), "ratify", "SR-0002", "--by", "tester"]) == 0
    text = p.read_text(encoding="utf-8")
    assert "status: accepted" in text
    assert "status: ratified" not in text


def test_compose_ratify_refuses_to_resign_unchanged_item(consumer_dir, capsys):
    # Core refuses a second signature on content that has not moved (SR-0148):
    # it would accept nothing while quietly replacing the name of whoever did.
    # The copy had no such guard, so it overwrote the ratifier without trace.
    p = _write_sr(consumer_dir, "SR-0002", [("toy:INT-0001", "derives_from")])
    assert tlc_main(["-C", str(consumer_dir), "ratify", "SR-0002", "--by", "alice"]) == 0
    capsys.readouterr()
    rc = tlc_main(["-C", str(consumer_dir), "ratify", "SR-0002", "--by", "mallory"])
    assert rc == 2
    assert "nothing to accept" in capsys.readouterr().err
    text = p.read_text(encoding="utf-8")
    assert "ratified_by: alice" in text        # the real signatory survives
    assert "mallory" not in text


def test_compose_ratify_forwards_the_correction_flag(consumer_dir, capsys):
    # SR-0003: `tl-compose ratify --replacing` corrects an unpublished ratifier
    # exactly as bare `tl` does (core SR-0196). A composed path that accepted the
    # flag and dropped it would meet core's "nothing to accept" refusal while the
    # bare tool performed the correction — the same command, two behaviours.
    subprocess.run(["git", "init", "-q"], cwd=consumer_dir, check=True)
    p = _write_sr(consumer_dir, "SR-0002", [("toy:INT-0001", "derives_from")])
    assert tlc_main(["-C", str(consumer_dir), "ratify", "SR-0002", "--by", "aalice"]) == 0
    capsys.readouterr()
    rc = tlc_main(["-C", str(consumer_dir), "ratify", "SR-0002", "--by", "alice",
                   "--replacing"])
    assert rc == 0, capsys.readouterr().err
    text = p.read_text(encoding="utf-8")
    assert "ratified_by: alice" in text
    assert "ratified_supersedes: aalice" in text   # the correction names what it replaced


def test_compose_ratify_restamps_once_the_content_moves(consumer_dir, capsys):
    # The counterpart: when the words change, the old signature no longer covers
    # them, so re-ratifying is exactly what should happen — and rebinds the stamp.
    p = _write_sr(consumer_dir, "SR-0002", [("toy:INT-0001", "derives_from")])
    assert tlc_main(["-C", str(consumer_dir), "ratify", "SR-0002", "--by", "alice"]) == 0
    first = [ln for ln in p.read_text(encoding="utf-8").splitlines()
             if "ratified_fingerprint:" in ln][0]
    p.write_text(p.read_text(encoding="utf-8").replace(
        "A consumer clause used to exercise the ratify gate.",
        "The consumer shall do something materially different."), encoding="utf-8")
    capsys.readouterr()
    assert tlc_main(["-C", str(consumer_dir), "ratify", "SR-0002", "--by", "bob"]) == 0
    text = p.read_text(encoding="utf-8")
    assert "ratified_by: bob" in text
    assert first not in text                   # bound to the new wording, not the old


def test_compose_ratify_refuses_ambiguous_item(consumer_dir, capsys):
    # The other half of core's gate, reached through the composed path: an item
    # flagged ambiguous is not signable however well it grounds over the union.
    p = _write_sr(consumer_dir, "SR-0002", [("toy:INT-0001", "derives_from")])
    p.write_text(p.read_text(encoding="utf-8") + "  ambiguous: true\n", encoding="utf-8")
    rc = tlc_main(["-C", str(consumer_dir), "ratify", "SR-0002", "--by", "tester"])
    assert rc == 2
    assert "ambiguous" in capsys.readouterr().err


def test_compose_ratify_still_refuses_ungrounded_item(consumer_dir, capsys):
    # The gate is preserved, only relocated to the union: an item that reaches no root
    # anywhere is still refused — composition widens what counts as grounded, it does
    # not weaken the accountability rule.
    _write_sr(consumer_dir, "SR-0002", [])
    rc = tlc_main(["-C", str(consumer_dir), "ratify", "SR-0002", "--by", "tester"])
    assert rc == 2
    assert "not grounded to a root" in capsys.readouterr().err


def test_compose_ratify_passthrough_without_sources(source_dir, capsys):
    # No [[sources]]: tl-compose ratify behaves exactly like core tl ratify over the
    # local graph (SR-0003). The toy source's UR-0001 grounds locally (derives INT).
    rc = tlc_main(["-C", str(source_dir), "ratify", "UR-0001", "--by", "tester"])
    assert rc == 0
    assert "UR-0001 ratified by tester" in capsys.readouterr().out


# ---- migrate: unbound records judged over the union (SR-0003, SR-0004) --------

def _unbound_record(consumer_dir: Path, uid: str, links: list[tuple[str, str]],
                    by: str = "tester", **attrs) -> Path:
    """A consumer item in the shape a graph ratified before the fingerprint existed
    carries on disk: a ratified status and a named ratifier, but no stamp binding
    that name to what was signed."""
    p = _write_sr(consumer_dir, uid, links)
    body = p.read_text(encoding="utf-8").replace(
        "status: proposed", "status: ratified")
    body += f"  ratified_by: {by}\n"
    body += "".join(f"  {k}: {v}\n" for k, v in attrs.items())
    p.write_text(body, encoding="utf-8")
    return p


def test_compose_migrate_binds_a_record_grounded_through_a_source(consumer_dir, capsys):
    # The fix (SR-0004): tl-compose hands the union to the *unchanged* core repair
    # (throughline SR-0153), which completes the very record it declined without one.
    p = _unbound_record(consumer_dir, "SR-0002", [("toy:INT-0001", "derives_from")])
    assert tlc_main(["-C", str(consumer_dir), "migrate"]) == 0

    out = capsys.readouterr().out
    assert "grounded through a composed source" in out
    assert "SR-0002 = sha256:" in out
    text = p.read_text(encoding="utf-8")
    assert "ratified_fingerprint: sha256:" in text
    assert "ratified_backfilled: true" in text
    assert "ratified_by: tester" in text          # reused verbatim, never reattributed


def test_compose_migrate_still_declines_what_core_would_refuse(consumer_dir, capsys):
    # Composition widens what counts as grounded; it does not weaken the rule. An
    # ambiguous item is refused on the same predicate, union or no union.
    p = _unbound_record(consumer_dir, "SR-0002", [("toy:INT-0001", "derives_from")],
                        ambiguous="true")
    assert tlc_main(["-C", str(consumer_dir), "migrate"]) == 0
    assert "ratified_fingerprint" not in p.read_text(encoding="utf-8")
    assert "grounded through a composed source" not in capsys.readouterr().out


def test_compose_migrate_is_idempotent(consumer_dir, capsys):
    # Both passes are idempotent by requirement (throughline SR-0137), so a second
    # run neither restamps nor re-reports — it cannot bless content that drifted
    # after sign-off.
    p = _unbound_record(consumer_dir, "SR-0002", [("toy:INT-0001", "derives_from")])
    tlc_main(["-C", str(consumer_dir), "migrate"])
    first = p.read_text(encoding="utf-8")
    capsys.readouterr()

    assert tlc_main(["-C", str(consumer_dir), "migrate"]) == 0
    assert "grounded through a composed source" not in capsys.readouterr().out
    assert p.read_text(encoding="utf-8") == first


def test_compose_migrate_upgrades_before_it_can_consult_the_union(consumer_dir, capsys):
    # The order is forced, not chosen: a project below the current major cannot be
    # loaded, so its [[sources]] cannot be read and no union exists until the upgrade
    # has run. One command still does both — core upgrades and binds what it can,
    # then the union pass completes what only composition can justify.
    p = _unbound_record(consumer_dir, "SR-0002", [("toy:INT-0001", "derives_from")])
    cfg = consumer_dir / "throughline.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace(
        "format_version = 3", "format_version = 2"), encoding="utf-8")

    assert tlc_main(["-C", str(consumer_dir), "migrate"]) == 0
    out = capsys.readouterr().out
    assert "migrated project from format version 2 to 3" in out
    assert "grounded through a composed source" in out
    assert "ratified_fingerprint: sha256:" in p.read_text(encoding="utf-8")


def test_compose_migrate_passthrough_without_sources(source_dir, capsys):
    # No [[sources]]: tl-compose migrate behaves exactly like core tl migrate over
    # the local graph, and says nothing about composition (SR-0003).
    assert tlc_main(["-C", str(source_dir), "migrate"]) == 0
    out = capsys.readouterr().out
    assert "already at format version 3" in out
    assert "composed source" not in out


# ---- link/new: cross-source targets resolve over the union (SR-0004) -----------

def test_compose_link_resolves_cross_source_destination(consumer_dir, capsys):
    # The fix: tl-compose validates the destination over the union and stores the
    # link namespace-qualified on the consumer's own item — the source is untouched.
    rc = tlc_main(["-C", str(consumer_dir), "link", "SR-0001", "toy:UR-0001",
                   "--type", "relates"])
    assert rc == 0
    assert "linked SR-0001 --relates--> toy:UR-0001" in capsys.readouterr().out
    sr = (consumer_dir / "system-requirements" / "SR-0001.yml").read_text()
    assert "target: toy:UR-0001" in sr


def test_compose_link_refuses_dangling_cross_source_destination(consumer_dir, capsys):
    rc = tlc_main(["-C", str(consumer_dir), "link", "SR-0001", "toy:SR-9999",
                   "--type", "relates"])
    assert rc == 2
    assert "toy:SR-9999 does not exist" in capsys.readouterr().err


def test_compose_link_local_destination_unchanged(consumer_dir, capsys):
    # A purely local link resolves in the union verbatim — behaviour-identical to core.
    rc = tlc_main(["-C", str(consumer_dir), "link", "SR-0001", "INT-0001",
                   "--type", "relates"])
    assert rc == 0
    assert "linked SR-0001 --relates--> INT-0001" in capsys.readouterr().out


def test_compose_link_passthrough_without_sources(source_dir, capsys):
    rc = tlc_main(["-C", str(source_dir), "link", "SR-0001", "INT-0001",
                   "--type", "derives_from"])
    assert rc == 0
    assert "linked SR-0001 --derives_from--> INT-0001" in capsys.readouterr().out


def test_compose_new_grounds_into_source_and_checks(consumer_dir, capsys):
    # The fix: an item can be grounded into a borrowed clause at birth; the result
    # is a properly grounded consumer item the composed check accepts.
    rc = tlc_main(["-C", str(consumer_dir), "new", "SR", "--type", "system_requirement",
                   "--status", "proposed", "--title", "grounded into a source",
                   "--ground", "toy:INT-0001", "--no-interactive"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "grounded: SR-0002 --derives_from--> toy:INT-0001" in out
    created = (consumer_dir / "system-requirements" / "SR-0002.yml").read_text()
    assert "target: toy:INT-0001" in created
    assert tlc_main(["-C", str(consumer_dir), "check", "--base", ""]) == 0


def test_compose_new_refuses_dangling_cross_source_ground(consumer_dir, capsys):
    rc = tlc_main(["-C", str(consumer_dir), "new", "SR", "--type", "system_requirement",
                   "--ground", "toy:SR-9999", "--no-interactive"])
    assert rc == 2
    assert "grounding target toy:SR-9999 does not exist" in capsys.readouterr().err


def test_compose_new_local_ground_passthrough(consumer_dir, capsys):
    # A local-only --ground defers to core new (no cross-source target to resolve).
    rc = tlc_main(["-C", str(consumer_dir), "new", "SR", "--type", "system_requirement",
                   "--ground", "INT-0001", "--no-interactive"])
    assert rc == 0
    assert "grounded: SR-0002 --derives_from--> INT-0001" in capsys.readouterr().out


# --- context / agentinfo brief (SR-0016) -------------------------------------

def test_context_appends_composition_section_and_live_sources(consumer_dir, capsys):
    # SR-0016: over a project that declares sources, `context` emits the core brief
    # first (the IDD contract) and then the composition section plus the live listing
    # of every namespace this project's union binds (SR-0045).
    rc = tlc_main(["-C", str(consumer_dir), "context"])
    out = capsys.readouterr().out
    assert rc == 0
    # Core brief passed through unchanged.
    assert "The contract: Intent-Driven Development" in out
    # Composition section appended.
    assert "Composition: working a project that declares sources" in out
    assert "Transitive sources" in out
    # Live listing names the declared source, its location and who declared it.
    assert "## Namespaces bound in this union" in out
    assert "`toy`" in out and "path `../toy-source`" in out
    assert "declared by you" in out


def test_agentinfo_is_identical_to_context(consumer_dir, capsys):
    # SR-0016: `agentinfo` is an alias for `context` — byte-identical output.
    tlc_main(["-C", str(consumer_dir), "context"])
    ctx = capsys.readouterr().out
    tlc_main(["-C", str(consumer_dir), "agentinfo"])
    agent = capsys.readouterr().out
    assert agent == ctx
    assert agent  # non-empty


def test_context_without_sources_is_the_ordinary_brief(source_dir, capsys):
    """A project that declares no sources gets the ordinary brief, byte for byte
    (SR-0230): composition is described only where there is a composition."""
    rc = tlc_main(["-C", str(source_dir), "context"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "The contract: Intent-Driven Development" in out
    assert "declares no `[[sources]]`" not in out
    assert "Transitive sources" not in out

def test_every_union_command_is_described_in_the_brief(consumer_dir, capsys):
    """SR-0025: a command given union behaviour without being described fails here
    rather than passing silently.

    The dispatch table is the only route to union behaviour and the brief is
    rendered from it, so this should hold by construction — but 'by construction'
    is a claim about today's code, and this is the check that keeps it true. If it
    fails, the command was routed some other way; put it in `UNION_COMMANDS` with
    the sentence an agent needs, rather than describing it twice."""
    from throughline.cli import UNION_COMMANDS, composition_uncovered

    assert composition_uncovered() == []
    rc = tlc_main(["-C", str(consumer_dir), "context"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "## Union-aware commands" in out
    for name, (_, note) in UNION_COMMANDS.items():
        assert f"- **`{name}`**" in out, f"{name} is not described in the brief"
        assert note in out


def test_brief_states_a_moved_ref_is_revalidated(consumer_dir, capsys):
    """SR-0025 over SR-0043: the brief is the document an agent is told to trust
    above all others, so it must describe the cache the tool actually has. It
    previously warned that a moved ref is never refetched — the opposite of the
    behaviour — which would send an agent off clearing caches to fix a problem that
    no longer exists, and teach it to distrust a check that is now sound. Both
    halves of the rule are asserted, plus the offline switch and the live cache
    path, so the section cannot drift back to describing only the easy case."""
    from throughline.resolvers import OFFLINE_ENV, cache_root

    rc = tlc_main(["-C", str(consumer_dir), "context"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "## The source cache — a moved ref **is** picked up" in out
    assert "**A commit id** names one commit for all time" in out  # the cheap half
    assert "refetches only when it has moved" in out              # the moving half
    assert OFFLINE_ENV in out
    assert str(cache_root()) in out


def test_brief_states_what_a_consumer_may_write(consumer_dir, capsys):
    """SR-0025: composition widens the view, never the authority. An agent that has
    not been told may try to ratify a borrowed clause."""
    rc = tlc_main(["-C", str(consumer_dir), "context"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "## What you may write in a consuming project" in out
    # Whitespace-normalised: where the prose happens to wrap is cosmetic, and a test
    # that pins it would fail on a reflow that changed nothing an agent reads.
    flat = " ".join(out.split())
    assert "You write only to your own registers" in flat
    assert "never ratified by you" in flat
    assert "the link is stored on *your* item" in flat.lower()


# --- the reported version (SR-0027) ------------------------------------------

def test_compose_subgraph_resolves_cross_source(consumer_dir, capsys):
    # SR-0041: the consumer's SR-0001 relates to toy:SR-0001. The neighbourhood is
    # built over the union, so the borrowed clause is a node of it — in namespace
    # vocabulary, never mangled, never (unresolved).
    rc = tlc_main(["-C", str(consumer_dir), "subgraph", "SR-0001"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "toy:SR-0001" in out
    assert "A normative clause the source offers" in out   # the clause's own title
    assert "SR-0001  -relates->  toy:SR-0001" in out
    assert "(unresolved)" not in out
    assert "TOYSR-0001" not in out


def test_compose_subgraph_stops_at_the_source_boundary(consumer_dir, capsys):
    # SR-0041/SR-0020: toy:SR-0001 is reached during the walk, so it is *shown* but
    # not traversed. Its own grounding chain (toy:UR-0001 -> toy:INT-0001) stays
    # inside the source, or one link would drag a whole standard into the view.
    rc = tlc_main(["-C", str(consumer_dir), "subgraph", "SR-0001"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "toy:SR-0001" in out
    assert "toy:UR-0001" not in out
    assert "toy:INT-0001" not in out


def test_compose_subgraph_from_a_borrowed_clause_names_its_local_adopters(
    consumer_dir, capsys
):
    # SR-0041: the question the boundary's start-node exception exists for — which
    # of *my* items adopt this clause. Applying the boundary to the named item too
    # would answer it with silence and exit 0.
    rc = tlc_main(["-C", str(consumer_dir), "subgraph", "toy:SR-0001"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("toy:SR-0001  [system_requirement/approved]")
    assert "depended on by (1):" in out
    assert "SR-0001" in out and "Consumer clause building on the source" in out
    # The named clause is walked one step, and no further: its parent shows, its
    # grandparent does not.
    assert "toy:UR-0001" in out
    assert "toy:INT-0001" not in out
    assert "TOYSR-0001" not in out


def test_compose_subgraph_reports_edges_that_start_at_a_borrowed_clause(
    consumer_dir, capsys
):
    # SR-0041: the boundary governs what is walked *into*, not what is reported
    # between nodes already in the set. toy:SR-0001 -> toy:UR-0001 joins two shown
    # nodes and must appear, or the rendered graph would contradict the set it
    # claims to induce.
    rc = tlc_main(["-C", str(consumer_dir), "subgraph", "toy:SR-0001"])
    assert rc == 0
    edges = capsys.readouterr().out.split("links within this set")[1]
    assert "toy:SR-0001  -implements->  toy:UR-0001" in edges
    assert "SR-0001      -relates->  toy:SR-0001" in edges   # the cross-seam edge


def test_compose_subgraph_dangling_cross_source_stays_unresolved(
    consumer_dir, capsys
):
    # SR-0041: a namespace-qualified target with no clause at the pinned edition is
    # a leaf reported as unresolved — in the composer's vocabulary, never mangled.
    sr = consumer_dir / "system-requirements" / "SR-0001.yml"
    sr.write_text(sr.read_text().replace("toy:SR-0001", "toy:SR-9999"))
    rc = tlc_main(["-C", str(consumer_dir), "subgraph", "SR-0001"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "toy:SR-9999  (unresolved)" in out
    assert "TOYSR-9999" not in out


def test_compose_subgraph_json_speaks_namespace_vocabulary(consumer_dir, capsys):
    # The machine-readable form an agent consumes must use the same vocabulary as
    # the text form — a mangled UID in JSON is a UID nothing else in the toolchain
    # will accept back.
    import json
    rc = tlc_main(["-C", str(consumer_dir), "subgraph", "SR-0001",
                   "--format", "json"])
    assert rc == 0
    raw = capsys.readouterr().out
    assert "TOYSR-0001" not in raw
    doc = json.loads(raw)
    assert doc["start"] == "SR-0001"
    assert "toy:SR-0001" in doc["upstream"]
    assert {"source": "SR-0001", "type": "relates",
            "target": "toy:SR-0001"} in doc["edges"]


def test_compose_subgraph_passthrough_without_sources(source_dir, capsys):
    # A project declaring no [[sources]]: subgraph must behave like bare
    # `tl subgraph` over the local graph (SR-0003).
    rc = tlc_main(["-C", str(source_dir), "subgraph", "SR-0001"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("SR-0001  [system_requirement/approved]")
    assert "UR-0001" in out and "INT-0001" in out   # nothing bounds a local walk


def test_compose_subgraph_rejects_an_unknown_uid(consumer_dir, capsys):
    rc = tlc_main(["-C", str(consumer_dir), "subgraph", "toy:SR-9999"])
    assert rc == 2
    assert "toy:SR-9999 does not exist" in capsys.readouterr().err


# ---------------------------------------------------------------------- SR-0042
# The brief's item section is composed too, not answered over the bare local graph.

def test_context_with_a_uid_resolves_borrowed_clauses(consumer_dir, capsys):
    # SR-0042: computed over the union, so the clause SR-0001 relates to reads with
    # its own type/status/title. Left to core it would say `(unresolved)` and then,
    # a few lines below, assert that composition resolves such references — a false
    # clean result in the one document an agent is told to trust (SR-0005).
    rc = tlc_main(["-C", str(consumer_dir), "context", "SR-0001"])
    assert rc == 0
    out = capsys.readouterr().out
    # Only the fenced neighbourhood, not the composition manual below it — that
    # manual discusses `(unresolved)` in prose and would mask the regression.
    section = out.split("## The item you were given")[1]
    assert section.startswith(": SR-0001")
    graph = section.split("```")[1]
    assert "toy:SR-0001  [system_requirement/approved]" in graph
    assert "A normative clause the source offers" in graph
    assert "(unresolved)" not in graph
    assert "TOYSR-0001" not in out


def test_context_with_a_uid_keeps_the_core_brief_and_composition_sections(
    consumer_dir, capsys
):
    # SR-0042: the UID adds a section; it does not disturb the brief above it or the
    # composition manual below it. Order matters — the manual stays last.
    rc = tlc_main(["-C", str(consumer_dir), "context", "SR-0001"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "The contract: Intent-Driven Development" in out
    assert "## Namespaces bound in this union" in out
    assert out.index("## The item you were given") < out.index(
        "Composition: working a project that declares sources")


def test_context_with_a_uid_stops_at_the_source_boundary(consumer_dir, capsys):
    # SR-0042 defers to SR-0041's boundary: the borrowed clause is shown, its own
    # grounding chain is not, so a brief cannot swell to include a whole standard.
    rc = tlc_main(["-C", str(consumer_dir), "context", "SR-0001"])
    assert rc == 0
    section = capsys.readouterr().out.split("## The item you were given")[1]
    graph = section.split("```")[1]
    assert "toy:SR-0001" in graph
    assert "toy:UR-0001" not in graph
    assert "toy:INT-0001" not in graph


def test_context_rejects_an_unknown_uid_before_emitting_a_brief(
    consumer_dir, capsys
):
    # SR-0042: failing after printing a brief would bury the diagnostic under a
    # screenful of output the caller did not get an answer to.
    rc = tlc_main(["-C", str(consumer_dir), "context", "toy:SR-9999"])
    assert rc == 2
    captured = capsys.readouterr()
    assert "toy:SR-9999 does not exist" in captured.err
    assert captured.out == ""


def test_context_with_a_uid_passes_through_without_sources(source_dir, capsys):
    # SR-0230: with no sources declared this is the ordinary brief, UID and all.
    rc = tlc_main(["-C", str(source_dir), "context", "SR-0001"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "## The item you were given: SR-0001" in out
    assert "Transitive sources" not in out


# ---------------------------------------------------------------------- SR-0040
# `tl-compose dump` — the interchange surface, answered over the composed graph.

def _dump(consumer_dir, capsys, *extra) -> dict:
    import json
    rc = tlc_main(["-C", str(consumer_dir), "dump", *extra])
    assert rc == 0
    raw = capsys.readouterr().out
    assert "TOYSR-0001" not in raw   # the mangling is an internal trick (SR-0004)
    return json.loads(raw)


def test_compose_dump_carries_the_borrowed_items_it_links_to(consumer_dir, capsys):
    # SR-0040: over the consumer's graph alone the document is not merely partial,
    # it is unresolvable — it carries a link to toy:SR-0001 and does not contain it,
    # so every reader must dangle or silently drop the one record proving a local
    # requirement is grounded in a published standard.
    doc = _dump(consumer_dir, capsys)
    uids = {it["uid"] for it in doc["items"]}
    assert "SR-0001" in uids and "toy:SR-0001" in uids
    targets = {link["target"] for it in doc["items"] for link in it.get("links", [])}
    assert targets <= uids   # every reference resolves inside the document


def test_compose_dump_names_each_item_s_owning_source_as_data(consumer_dir, capsys):
    # SR-0040: provenance is a field, not something a reader parses back out of a
    # qualifier — and a local item says so explicitly rather than by omission.
    doc = _dump(consumer_dir, capsys)
    by_uid = {it["uid"]: it for it in doc["items"]}
    assert by_uid["toy:SR-0001"]["source"] == "toy"
    assert by_uid["SR-0001"]["source"] is None


def test_compose_dump_states_the_scope_it_answered_over(consumer_dir, capsys):
    # SR-0040: which sources at which pin, and how many items are the consumer's
    # own. Without it a reader cannot tell a whole export from a narrowed one.
    comp = _dump(consumer_dir, capsys)["composition"]
    assert comp["scope"] == "composed"
    assert comp["local_item_count"] == 2          # INT-0001 + SR-0001
    assert comp["borrowed_item_count"] == 3       # the toy source's three items
    (source,) = comp["sources"]
    assert source["namespace"] == "toy"
    assert source["path"] == "../toy-source"
    assert source["item_count"] == 3
    assert source["fingerprint"]                  # the edition actually read


def test_compose_dump_identifies_the_composition_layer(consumer_dir, capsys):
    # SR-0040: the header named the core alone, so nothing in the file said
    # composition was involved at all.
    doc = _dump(consumer_dir, capsys)
    # One program, one version: the dump names the Tool that composed it (SR-0236),
    # and the composition block says composition was involved.
    assert doc["throughline_dump"]["tool_version"] == throughline.__version__
    assert doc["composition"]["scope"] == "composed"


def test_compose_dump_local_records_that_it_was_narrowed(consumer_dir, capsys):
    # SR-0040: restricting an export to your own items is legitimate; a restricted
    # export that did not say so would be indistinguishable from a whole one.
    doc = _dump(consumer_dir, capsys, "--local")
    assert {it["uid"] for it in doc["items"]} == {"INT-0001", "SR-0001"}
    comp = doc["composition"]
    assert comp["scope"] == "local"
    # Counted over the union either way: what the numbers exist to say is how much
    # is *missing* from this document.
    assert comp["local_item_count"] == 2
    assert comp["borrowed_item_count"] == 3


def test_compose_dump_is_deterministic(consumer_dir, capsys):
    # Core's dump carries no wall-clock field so two dumps diff cleanly (SR-0055);
    # the composition block must not reintroduce one.
    first = _dump(consumer_dir, capsys)
    second = _dump(consumer_dir, capsys)
    assert first == second


def test_compose_dump_writes_to_a_file(consumer_dir, capsys, tmp_path):
    import json
    out = tmp_path / "export.json"
    rc = tlc_main(["-C", str(consumer_dir), "dump", "-o", str(out)])
    assert rc == 0
    assert "wrote" in capsys.readouterr().err
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["composition"]["scope"] == "composed"


def test_compose_dump_passthrough_without_sources(source_dir, capsys):
    # SR-0003: no sources declared, so this is byte-for-byte core `tl dump` — no
    # composition block, and the core's own tool version.
    import json
    rc = tlc_main(["-C", str(source_dir), "dump"])
    assert rc == 0
    doc = json.loads(capsys.readouterr().out)
    assert "composition" not in doc
    assert not doc["throughline_dump"]["tool_version"].startswith("tl-compose")


def test_compose_ratify_takes_several_items_over_one_union(consumer_dir, capsys):
    # SR-0046: several items in one run, each signed over the same union in the
    # order given, as core does from 3.3.0 (throughline SR-0199).
    a = _write_sr(consumer_dir, "SR-0002", [("toy:INT-0001", "derives_from")])
    b = _write_sr(consumer_dir, "SR-0003", [("toy:INT-0001", "derives_from")])
    rc = tlc_main(["-C", str(consumer_dir), "ratify", "SR-0003", "SR-0002", "--by", "tester"])
    assert rc == 0
    assert capsys.readouterr().out.splitlines() == [
        "SR-0003 ratified by tester", "SR-0002 ratified by tester"]
    assert "ratified_by: tester" in a.read_text(encoding="utf-8")
    assert "ratified_by: tester" in b.read_text(encoding="utf-8")


def test_compose_ratify_refuses_the_whole_run_before_writing_anything(consumer_dir, capsys):
    # SR-0046: a mistyped identifier fails before any source is resolved, and an
    # item the gate refuses stops the run with nothing written.
    a = _write_sr(consumer_dir, "SR-0002", [("toy:INT-0001", "derives_from")])
    rc = tlc_main(["-C", str(consumer_dir), "ratify", "SR-0002", "SR-0099", "--by", "tester"])
    assert rc == 2
    assert "SR-0099 does not exist" in capsys.readouterr().err
    assert "ratified_by" not in a.read_text(encoding="utf-8")
    b = _write_sr(consumer_dir, "SR-0003", [("SR-0999", "derives_from")])  # ungrounded
    rc = tlc_main(["-C", str(consumer_dir), "ratify", "SR-0002", "SR-0003", "--by", "tester"])
    assert rc == 2
    assert "nothing in this run was ratified" in capsys.readouterr().err
    assert "ratified_by" not in a.read_text(encoding="utf-8")
    assert "ratified_by" not in b.read_text(encoding="utf-8")


# --- birth through core (SR-0047, throughline SR-0205) ------------------------

def test_compose_new_into_a_source_is_born_as_bare_tl_bears_it(consumer_dir, capsys):
    """A cross-source ground used to carry its own copy of the birth, which had
    drifted from core: a machine-authored item entered the initial status and
    escaped ratification, --attr values were dropped, schema defaults were not
    applied, and the type's normative flag was not read."""
    # The fixture binds no 'proposed' role; bind one so the machine-origin rule
    # (throughline SR-0141) has a status to send the item to.
    cfg = consumer_dir / "throughline.toml"
    cfg.write_text(cfg.read_text().replace(
        'initial = "draft"', 'initial = "draft"\nproposed = "proposed"'))
    assert tlc_main(["-C", str(consumer_dir), "schema", "type", "add",
                     "system_requirement", "--non-normative",
                     "--because", "guidance here"]) == 0
    assert tlc_main(["-C", str(consumer_dir), "schema", "attr", "add",
                     "system_requirement", "owner", "--default", "nobody",
                     "--because", "a sentinel"]) == 0
    rc = tlc_main(["-C", str(consumer_dir), "new", "SR", "--type", "system_requirement",
                   "--title", "born through core", "--origin", "ai",
                   "--attr", "priority=must", "--ground", "toy:INT-0001",
                   "--no-interactive"])
    assert rc == 0, capsys.readouterr().err
    from throughline.storage import load_project
    item = load_project(consumer_dir).get("SR-0002")
    assert item.status == "proposed"
    assert item.attrs == {"priority": "must", "origin": "ai", "owner": "nobody"}
    assert item.normative is False
    assert [l.target for l in item.links] == ["toy:INT-0001"]


# --- baseline rules and link operations over the union (SR-0048, SR-0049) -----

def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, check=True).stdout


def test_composed_check_runs_the_baseline_rules_as_bare_tl_does(consumer_dir, capsys):
    """On 0.20.0 an illegal status move that `tl check` reported passed
    `tl-compose check` on the same working tree, and --base was ignored."""
    cfg = consumer_dir / "throughline.toml"
    cfg.write_text(cfg.read_text() + '\n[transitions]\napproved = ["ratified", '
                   '"suspect", "rejected", "deleted"]\n')
    _git(consumer_dir, "init", "-q")
    _git(consumer_dir, "config", "user.email", "t@t")
    _git(consumer_dir, "config", "user.name", "t")
    _git(consumer_dir, "add", "-A")
    _git(consumer_dir, "commit", "-qm", "baseline")
    item = consumer_dir / "system-requirements" / "SR-0001.yml"
    item.write_text(item.read_text().replace("status: approved", "status: draft"))
    capsys.readouterr()
    assert tlc_main(["-C", str(consumer_dir), "check"]) == 1
    out = capsys.readouterr()
    assert "SR-0001" in out.out and "bad-transition" in out.out
    assert "not checked" not in out.err


def test_composed_check_says_which_rules_did_not_run(consumer_dir, capsys):
    capsys.readouterr()
    tlc_main(["-C", str(consumer_dir), "check", "--base", ""])
    assert ("not checked: tombstone permanence — the baseline was disabled"
            in capsys.readouterr().err)


def test_composed_link_restamps_and_retypes_a_cross_source_edge_in_place(consumer_dir, capsys):
    """The copy this replaced added a second edge on --stamp and a third on --retype."""
    from throughline.storage import load_project
    for _ in range(2):
        assert tlc_main(["-C", str(consumer_dir), "link", "SR-0001", "toy:SR-0001",
                         "--type", "relates", "--stamp"]) == 0
    assert "restamped SR-0001 --relates--> toy:SR-0001" in capsys.readouterr().out
    assert tlc_main(["-C", str(consumer_dir), "link", "SR-0001", "toy:SR-0001",
                     "--type", "implements", "--retype"]) == 0
    edges = [l for l in load_project(consumer_dir).get("SR-0001").links
             if l.target == "toy:SR-0001"]
    assert [(l.type, bool(l.stamp)) for l in edges] == [("implements", True)]


def test_composed_unlink_judges_grounding_over_the_union(consumer_dir, capsys):
    """SR-0001 is grounded locally and through a borrowed intent. Removing the local
    link leaves it grounded in the union, so it is allowed."""
    assert tlc_main(["-C", str(consumer_dir), "link", "SR-0001", "toy:INT-0001",
                     "--type", "derives_from"]) == 0
    assert tlc_main(["-C", str(consumer_dir), "new", "SR", "--type", "system_requirement",
                     "--title", "keeps INT-0001 served", "--ground", "INT-0001",
                     "--no-interactive"]) == 0
    capsys.readouterr()
    assert tlc_main(["-C", str(consumer_dir), "unlink", "SR-0001", "INT-0001"]) == 0
    assert "unlinked SR-0001 --derives_from--> INT-0001" in capsys.readouterr().out


def test_composed_unlink_refuses_what_would_unground_the_union(consumer_dir, capsys):
    assert tlc_main(["-C", str(consumer_dir), "unlink", "SR-0001", "INT-0001"]) == 2
    err = capsys.readouterr().err
    assert "SR-0001 reaching no root" in err and "INT-0001 served by nothing" in err
