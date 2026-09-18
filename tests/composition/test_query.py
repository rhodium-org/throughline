# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""A listing answers over the composed graph and states its scope (SR-0037).

The fixture is the smallest graph that tells the two scopes apart: the consumer
holds an intent and a system requirement, and the toy source holds an intent, a
*user* requirement — a type the consumer has none of — and a system requirement.
So a filter on `user_requirement` is the reported defect in miniature: the answer
is one item, and a local-only listing reports zero without saying so.

Both halves of the requirement are held here. The capability, that a borrowed
clause can be found at all and is named as the composer names it; and the
statement of scope, which is the half that matters under either design, because a
count that does not say what it counted is a wrong answer rather than a narrow one.
"""
from __future__ import annotations

import json

from throughline.cli import main as tl_main
from throughline.cli import main as tlc_main


def test_a_source_only_type_is_found_not_reported_as_zero(consumer_dir, capsys):
    """The defect exactly as reported: `user_requirement` lives only in the source,
    and the listing used to print `0 item(s)` — indistinguishable from a graph that
    genuinely holds none."""
    rc = tlc_main(["-C", str(consumer_dir), "query", "type == 'user_requirement'"])
    assert rc == 0
    out = capsys.readouterr()
    assert "toy:UR-0001" in out.out
    assert "1 item(s)" in out.err


def test_the_count_says_which_scope_it_answered_over(consumer_dir, capsys):
    """SR-0037's other half. A reader must never infer the scope of a number from
    its size, so the split the check summary prints is printed here too."""
    rc = tlc_main(["-C", str(consumer_dir), "query", ""])
    assert rc == 0
    err = capsys.readouterr().err
    assert "5 item(s)" in err                      # 2 consumer + 3 borrowed
    assert "composed graph · 2 local · 3 borrowed from 1 source(s)" in err


def test_local_narrows_the_answer_and_names_what_it_skipped(consumer_dir, capsys):
    """`--local` is a deliberate narrowing, so it may report fewer items — but not
    silently. It names the population it did not search and how to search it."""
    rc = tlc_main(["-C", str(consumer_dir), "query", "", "--local"])
    assert rc == 0
    out = capsys.readouterr()
    assert "toy:" not in out.out
    assert "2 item(s)" in out.err
    assert "local only · 3 borrowed item(s) across 1 source(s) not searched" in out.err
    assert "--local" in out.err                    # and how to widen it again


def test_the_ls_alias_is_union_aware_too(consumer_dir, capsys):
    """argparse records the spelling that was typed, so an alias reaches the
    dispatch table under a name it does not hold unless it is mapped. The failure
    would be silent and would land on the one command whose complaint was silence."""
    rc = tlc_main(["-C", str(consumer_dir), "ls", "type == 'user_requirement'"])
    assert rc == 0
    out = capsys.readouterr()
    assert "toy:UR-0001" in out.out
    assert "composed graph" in out.err


def test_a_filter_matches_the_name_the_listing_prints(consumer_dir, capsys):
    """One vocabulary. A surface that prints `toy:SR-0001` while matching only the
    mangled `TOYSR-0001` would reintroduce the silent zero one layer down, and would
    leak an internal identity trick the tool does not promise to keep."""
    rc = tlc_main(["-C", str(consumer_dir), "query", "uid == 'toy:SR-0001'"])
    assert rc == 0
    assert "toy:SR-0001" in capsys.readouterr().out

    rc = tlc_main(["-C", str(consumer_dir), "query", "register == 'toy:UR'"])
    assert rc == 0
    assert "toy:UR-0001" in capsys.readouterr().out


def test_link_predicates_answer_across_the_seam(consumer_dir, capsys):
    """The index is the union's. The consumer's SR relates *up* into the source, so
    the borrowed clause has an incoming link that exists in neither graph alone —
    which is the whole reason to compose before answering."""
    rc = tlc_main(["-C", str(consumer_dir), "query",
                   "uid == 'toy:SR-0001' and links.incoming('relates')"])
    assert rc == 0
    assert "toy:SR-0001" in capsys.readouterr().out


def test_json_carries_the_owning_source_as_data(consumer_dir, capsys):
    """Provenance is a field, not something a downstream reader parses back out of
    a UID. Link targets are qualified too, so the document is readable in one
    vocabulary throughout."""
    rc = tlc_main(["-C", str(consumer_dir), "query", "", "--format", "json"])
    assert rc == 0
    out = capsys.readouterr()
    items = {it["uid"]: it for it in json.loads(out.out)}

    assert items["toy:SR-0001"]["source"] == "toy"
    assert items["SR-0001"]["source"] is None
    assert items["toy:SR-0001"]["links"][0]["target"] == "toy:UR-0001"
    # The consumer's own reference reads exactly as it was authored.
    assert {"target": "toy:SR-0001", "type": "relates"} in items["SR-0001"]["links"]
    # The scope is stated in this mode too, on stderr where it cannot corrupt the
    # document — a listing that states it in one mode only leaves the reader to
    # work out which mode they are in.
    assert "composed graph" in out.err


def test_with_no_sources_it_is_core_byte_for_byte(source_dir, capsys):
    """The strict-superset promise (SR-0003): an ordinary project must not pay for
    composition existing, in behaviour or in output."""
    assert tl_main(["-C", str(source_dir), "query", "type == 'system_requirement'"]) == 0
    core = capsys.readouterr()
    assert tlc_main(["-C", str(source_dir), "query", "type == 'system_requirement'"]) == 0
    composed = capsys.readouterr()

    assert composed.out == core.out
    assert composed.err == core.err


def test_a_malformed_filter_is_refused_not_evaluated(consumer_dir, capsys):
    """The grammar is core's closed one (throughline:SR-0104); composing widens the
    graph, never the language."""
    rc = tlc_main(["-C", str(consumer_dir), "query", "__import__('os')"])
    assert rc == 2
    assert "bad filter expression" in capsys.readouterr().err
