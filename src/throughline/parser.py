# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""The shape of the command line: every command, its arguments and its help.

Separate from the command line that runs it, so that what describes the tool can
read it without importing it (SR-0225). The agent brief is built from this tree,
which is why the brief cannot fall behind the tool (SR-0161): a command added here
is described there without anyone remembering to describe it.

``handlers`` maps each command's function name to what runs it. The command line
passes its own; a reader that only wants the names passes nothing.
"""
from __future__ import annotations

import argparse

from . import schema_ops
from .schema import ERROR, OFF, WARNING
from .version import distribution_version

def _add_schema_parser(sub, handlers) -> None:
    """`tl schema …` — the verbs that change what the validator enforces.

    A separate noun rather than `tl status add` / `tl link type add`, because
    `tl status` and `tl link` already name operations on the *graph*: overloading
    them would make `tl status add draft` ambiguous with moving an item called
    'add'. Keeping the schema verbs under one noun also draws the distinction
    that matters — these change the rules, the others work within them.
    """
    s = sub.add_parser(
        "schema",
        help="change the project's own schema (types, statuses, links, grounding)")
    nouns = s.add_subparsers(dest="schema_noun", required=True)
    noun_help = {
        "status": "the status vocabulary",
        "transition": "permitted status moves",
        "type": "item types",
        "attr": "attributes of an item type",
        "linktype": "the link vocabulary",
        "linkrule": "endpoint constraints on a link type",
        "grounding": "the grounding configuration",
        "rule": "coverage rules the gate enforces",
    }
    made: dict[str, object] = {}

    def _schema_verb(noun: str, verb: str, builder, help_text):
        """One `tl schema <noun> <verb>`, creating the noun's parser on first use."""
        if noun not in made:
            parser = nouns.add_parser(noun, help=noun_help[noun])
            made[noun] = parser.add_subparsers(dest=f"{noun}_verb", required=True)
        v = made[noun].add_parser(verb, help=help_text)
        v.add_argument("--because", required=True,
                       help="why this change is being made; recorded as a comment "
                            "beside it in throughline.toml")
        v.set_defaults(func=handlers.get("cmd_schema"), builder=builder)
        return v

    v = _schema_verb("status", "add",
                     lambda p, a: schema_ops.status_add(p, a.name),
                     "declare a new status")
    v.add_argument("name")
    v = _schema_verb("status", "declare",
                     lambda p, a: schema_ops.status_declare(p, a.names),
                     "declare the whole status vocabulary, where none is declared")
    v.add_argument("names", metavar="STATUS", nargs="+")
    v = _schema_verb("status", "remove",
                     lambda p, a: schema_ops.status_remove(p, a.name),
                     "withdraw a status")
    v.add_argument("name")

    v = _schema_verb("transition", "allow",
                     lambda p, a: schema_ops.transition_allow(p, a.frm, a.to),
                     "permit a status move")
    v.add_argument("frm", metavar="FROM")
    v.add_argument("to", metavar="TO")
    v = _schema_verb("transition", "deny",
                     lambda p, a: schema_ops.transition_deny(p, a.frm, a.to),
                     "withdraw a permitted status move")
    v.add_argument("frm", metavar="FROM")
    v.add_argument("to", metavar="TO")

    v = _schema_verb("type", "add",
                     lambda p, a: schema_ops.type_add(
                         p, a.name, normative=not a.non_normative),
                     "declare a new item type")
    v.add_argument("name")
    v.add_argument("--non-normative", action="store_true",
                   help="items of this type are not normative: they feed no "
                        "fingerprint drift and need reach no published document")
    v = _schema_verb("type", "normative",
                     lambda p, a: schema_ops.type_normative(
                         p, a.name, a.value == "true"),
                     "declare whether items of an existing type are normative")
    v.add_argument("name", metavar="TYPE")
    v.add_argument("value", choices=["true", "false"])
    v = _schema_verb("type", "remove",
                     lambda p, a: schema_ops.type_remove(p, a.name),
                     "withdraw an item type")
    v.add_argument("name")

    v = _schema_verb("attr", "add",
                     lambda p, a: schema_ops.attr_add(
                         p, a.itype, a.name, kind=a.kind, values=a.values,
                         required=a.required, normative=a.normative,
                         default=a.default),
                     "declare an attribute on an item type")
    v.add_argument("itype", metavar="TYPE")
    v.add_argument("name")
    v.add_argument("--kind", default=None,
                   help="enum|string|text|int|float|bool|date")
    v.add_argument("--values", default=None, type=lambda s: s.split(","),
                   help="comma-separated members, for --kind enum")
    v.add_argument("--required", action="store_true")
    v.add_argument("--normative", action="store_true",
                   help="the value feeds the content fingerprint")
    v.add_argument("--default", default=None)
    v = _schema_verb("attr", "remove",
                     lambda p, a: schema_ops.attr_remove(p, a.itype, a.name),
                     "withdraw an attribute from an item type")
    v.add_argument("itype", metavar="TYPE")
    v.add_argument("name")
    v.add_argument("--unset", action="store_true",
                   help="also remove the attribute from every item of the type that "
                        "carries it; finishes a withdrawal that left values behind")
    v.set_defaults(func=handlers.get("cmd_schema_attr_remove"))

    v = _schema_verb("linktype", "add",
                     lambda p, a: schema_ops.linktype_add(p, a.name),
                     "declare a new link type")
    v.add_argument("name")
    v = _schema_verb("linktype", "declare",
                     lambda p, a: schema_ops.linktype_declare(p, a.names),
                     "declare the whole link vocabulary, where none is declared")
    v.add_argument("names", metavar="LINKTYPE", nargs="+")
    v = _schema_verb("linktype", "remove",
                     lambda p, a: schema_ops.linktype_remove(p, a.name),
                     "withdraw a link type")
    v.add_argument("name")

    for verb, op, help_text in (
            ("allow", schema_ops.linkrule_allow,
             "permit an endpoint type on a link type"),
            ("deny", schema_ops.linkrule_deny,
             "withdraw an endpoint type from a link type")):
        v = _schema_verb(
            "linkrule", verb,
            lambda p, a, op=op: op(p, a.ltype,
                                   side="from" if a.frm else "to",
                                   itype=a.frm or a.to),
            help_text)
        v.add_argument("ltype", metavar="LINKTYPE")
        side = v.add_mutually_exclusive_group(required=True)
        side.add_argument("--from", dest="frm", metavar="TYPE")
        side.add_argument("--to", dest="to", metavar="TYPE")
    v = _schema_verb("linkrule", "clear",
                     lambda p, a: schema_ops.linkrule_clear(p, a.ltype),
                     "leave a link type unconstrained again")
    v.add_argument("ltype", metavar="LINKTYPE")

    v = _schema_verb("grounding", "add",
                     lambda p, a: schema_ops.grounding_add(p, a.field, a.value),
                     "add an entry to a grounding field")
    v.add_argument("field", metavar="FIELD")
    v.add_argument("value")
    v = _schema_verb("grounding", "remove",
                     lambda p, a: schema_ops.grounding_remove(p, a.field, a.value),
                     "withdraw an entry from a grounding field")
    v.add_argument("field", metavar="FIELD")
    v.add_argument("value")

    v = _schema_verb("rule", "add",
                     lambda p, a: schema_ops.rule_add(
                         p, filter=a.filter, needs=a.needs, severity=a.severity),
                     "declare a coverage rule")
    v.add_argument("--filter", required=True,
                   help="which items the rule governs, e.g. \"type == 'system_"
                        "requirement' and attrs.get('verification') == 'test'\"")
    v.add_argument("--needs", required=True,
                   help="what they must have: incoming:<link type> or "
                        "outgoing:<link type>")
    v.add_argument("--severity", default=None, choices=[ERROR, WARNING, OFF],
                   help="default: warning")
    v = _schema_verb("rule", "remove",
                     lambda p, a: schema_ops.rule_remove(p, a.index),
                     "withdraw a coverage rule, by its position in `tl context`")
    v.add_argument("index", type=int, metavar="N")


def build_parser(handlers=None) -> argparse.ArgumentParser:
    handlers = handlers or {}
    p = argparse.ArgumentParser(prog="tl", description=__doc__.splitlines()[0])
    p.add_argument("--version", action="version", version=f"tl {distribution_version('throughline')}")
    p.add_argument("-C", "--path", default=".", help="project root (default: .)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create a new project")
    s.add_argument("--name", default="Example")
    s.add_argument("--force", action="store_true",
                   help="create even if nested inside an existing project")
    s.add_argument("--no-demo", action="store_true",
                   help="skip the seeded example items and rendered doc; still "
                        "create the default registers")
    s.add_argument("--no-defaults", action="store_true",
                   help="skip the default registers (implies --no-demo)")
    s.add_argument("--bare", action="store_true",
                   help="write only throughline.toml (same as --no-defaults)")
    s.set_defaults(func=handlers.get("cmd_init"))

    s = sub.add_parser("migrate",
                       help="upgrade an older project's on-disk format to this tl")
    s.set_defaults(func=handlers.get("cmd_migrate"))

    s = sub.add_parser("register", help="register (prefix-owning collection) operations")
    dsub = s.add_subparsers(dest="register_cmd", required=True)
    d = dsub.add_parser("new", help="create a register/manifest")
    d.add_argument("prefix")
    d.add_argument("dir", help="directory (relative to project root)")
    d.add_argument("--title", default="")
    d.add_argument("--digits", type=int, default=4)
    d.add_argument("--parent", default=None)
    d.set_defaults(func=handlers.get("cmd_register_new"))

    _add_schema_parser(sub, handlers)

    s = sub.add_parser("new", help="allocate + create an item")
    s.add_argument("prefix")
    s.add_argument("--uid", default=None, help="explicit UID (must match prefix)")
    s.add_argument("--type", default="requirement")
    s.add_argument("--status", default=None,
                   help="birth status (default: the project's 'initial' role)")
    s.add_argument("--title", default="")
    s.add_argument("--text", default="")
    s.add_argument("--origin", default=None, help="human|ai|hybrid")
    s.add_argument("--attr", action="append", metavar="KEY=VALUE",
                   help="set a project-declared attribute at creation, e.g. "
                        "--attr priority=must (repeatable; coerced to the "
                        "attribute's declared type)")
    s.add_argument("--ground", action="append", metavar="UID",
                   help="parent to ground against at creation (repeatable)")
    s.add_argument("--ground-type", default=None,
                   help="grounding link type (default: derives_from)")
    s.add_argument("--no-interactive", action="store_true",
                   help="never prompt for a parent (for scripts/CI)")
    s.set_defaults(func=handlers.get("cmd_new"))

    s = sub.add_parser("link", help="add a typed link SRC -> DST")
    s.add_argument("src", nargs="?", default=None,
                   help="source UID (omit on a terminal to pick one)")
    s.add_argument("dst", nargs="?", default=None,
                   help="destination UID (omit on a terminal to pick one)")
    s.add_argument("--type", default=None,
                   help="link type (omit on a terminal to choose one)")
    s.add_argument("--stamp", action="store_true",
                   help="record the target's fingerprint, or refresh it on an edge that already exists (suspect tracking)")
    s.add_argument("--retype", action="store_true",
                   help="change the type of the existing SRC -> DST link in "
                        "place instead of adding a new one")
    s.set_defaults(func=handlers.get("cmd_link"))

    s = sub.add_parser("unlink", help="remove a typed link SRC -> DST")
    s.add_argument("src", nargs="?", default=None,
                   help="source UID (omit on a terminal to pick one)")
    s.add_argument("dst", nargs="?", default=None,
                   help="destination UID (omit on a terminal to pick one)")
    s.add_argument("--type", default=None,
                   help="only remove the link of this type (required when "
                        "several types link the same pair)")
    s.set_defaults(func=handlers.get("cmd_unlink"))

    s = sub.add_parser("delete", help="tombstone an item (never erased)")
    s.add_argument("uid", nargs="?", default=None,
                   help="UID to tombstone (omit on a terminal to pick one)")
    s.add_argument("--reason", default="")
    s.set_defaults(func=handlers.get("cmd_delete"))

    s = sub.add_parser("amend", help="change an item's title/text/rationale/attrs")
    s.add_argument("uid", nargs="?", default=None)
    # default=None distinguishes "not given" from "given as empty", which is how a
    # field is cleared without opening the YAML.
    s.add_argument("--title", default=None)
    s.add_argument("--text", default=None)
    s.add_argument("--rationale", default=None)
    s.add_argument("--attr", action="append", metavar="KEY=VALUE",
                   help="set a declared attribute, e.g. --attr priority=must "
                        "(repeatable; coerced to the attribute's declared type)")
    s.add_argument("--unset", action="append", metavar="KEY",
                   help="remove an attribute from the item, whether or not its type "
                        "still declares it (repeatable)")
    s.set_defaults(func=handlers.get("cmd_amend"))

    s = sub.add_parser("review", help="mark item(s) reviewed at current content")
    s.add_argument("uid", nargs="?", default=None)
    s.add_argument("--all-clean", action="store_true")
    s.set_defaults(func=handlers.get("cmd_review"))

    s = sub.add_parser("check", help="validate the graph (CI gate)")
    s.add_argument("--strict", action="store_true", help="warnings become errors")
    s.add_argument("--base", default="HEAD", metavar="REF",
                   help="git ref for the status-transition baseline "
                        "(default HEAD; empty to disable)")
    s.add_argument("--base-dir", default=None, metavar="DIR",
                   help="read the baseline from DIR, a copy of the project as it "
                        "stood, instead of from git (for a host without git)")
    s.add_argument("--format", choices=["text", "json"], default="text")
    s.add_argument("--quiet", "-q", action="store_true",
                   help="suppress the graph summary (findings + tally only)")
    s.set_defaults(func=handlers.get("cmd_check"))

    s = sub.add_parser("query", aliases=["ls"],
                       help="list items matching a filter expression")
    s.add_argument("expr", nargs="?", default="",
                   help="SR-0045 filter, e.g. \"status == 'draft'\" or "
                        "\"type == 'system_requirement' and "
                        "attrs.get('priority') == 'must'\"; omit to list all")
    s.add_argument("--format", choices=["text", "json"], default="text")
    s.add_argument("--all", action="store_true",
                   help="include deleted (tombstoned) items")
    s.add_argument("--local", action="store_true",
                   help="list only this project's own items, not the ones it "
                        "borrows through a source")
    s.set_defaults(func=handlers.get("cmd_query"))

    s = sub.add_parser("shape",
                       help="report the graph's (from)-[link]->(to) type shape")
    s.add_argument("--format", choices=["text", "json"], default="text")
    s.set_defaults(func=handlers.get("cmd_shape"))

    s = sub.add_parser(
        "dump",
        help="export the whole project as one documented JSON structure "
             "(SR-0055) — the sanctioned interchange surface")
    s.add_argument("-o", "--output", default=None, metavar="FILE",
                   help="write to FILE (default: stdout)")
    s.add_argument("--local", action="store_true",
                   help="export only this project's own items, not the ones it "
                        "borrows through a source")
    s.set_defaults(func=handlers.get("cmd_dump"))

    s = sub.add_parser("diagram",
                       help="emit Mermaid diagrams of the type model and status lifecycle")
    s.add_argument("kind", nargs="?", choices=["types", "transitions", "both"],
                   default="both")
    s.add_argument("--format", choices=["markdown", "mermaid"], default="markdown")
    s.set_defaults(func=handlers.get("cmd_diagram"))

    s = sub.add_parser(
        "docs",
        help="inject graph content into the marked regions of Markdown files")
    s.add_argument("file", nargs="*",
                   help="Markdown files to inject (default: [docs] paths in config)")
    s.add_argument("--check", action="store_true",
                   help="CI gate: fail (exit 1) if any document is out of date, "
                        "without rewriting it (not run by `tl check`)")
    s.add_argument("--at", default=None, metavar="REF",
                   help="inject content as the graph stood at a git revision")
    s.set_defaults(func=handlers.get("cmd_docs"))

    s = sub.add_parser(
        "context", aliases=["agentinfo"],
        help="emit an agent-facing Markdown brief (IDD + this project's model, "
             "and the composition it declares)")
    s.add_argument("uid", nargs="?", default=None,
                   help="append this item's neighbourhood to the brief")
    s.set_defaults(func=handlers.get("cmd_context"))

    s = sub.add_parser("trace", help="print the link tree from a UID")
    s.add_argument("uid", nargs="?", default=None,
                   help="UID to trace (omit on a terminal to pick one)")
    s.add_argument("--direction", choices=["in", "out"], default="out")
    s.add_argument("--depth", type=int, default=0, help="0 = unbounded")
    s.set_defaults(func=handlers.get("cmd_trace"))

    s = sub.add_parser("blast", help="show the blast radius (dependents) of a UID")
    s.add_argument("uid", nargs="?", default=None,
                   help="UID to inspect (omit on a terminal to pick one)")
    s.add_argument("--format", choices=["text", "json"], default="text")
    s.set_defaults(func=handlers.get("cmd_blast"))

    s = sub.add_parser(
        "subgraph",
        help="show a UID's neighbourhood — both directions plus the links between")
    s.add_argument("uid", nargs="?", default=None,
                   help="UID to inspect (omit on a terminal to pick one)")
    s.add_argument("--format", choices=["text", "json"], default="text")
    s.add_argument("--depth", type=int, default=0, help="0 = unbounded")
    s.add_argument("--link-type", action="append", default=None, metavar="KIND",
                   help="restrict the walk to this link type (repeatable)")
    s.set_defaults(func=handlers.get("cmd_subgraph"))

    s = sub.add_parser("ratify", help="a human takes accountability for an item")
    # A list, as withdraw's is (SR-0197): several proposed items are the usual
    # shape of a machine-authored batch, and each is still shown and confirmed on
    # its own (SR-0199). A front end that read the single `uid` by name must now
    # read `uids`.
    s.add_argument("uids", nargs="*", metavar="UID",
                   help="item(s) to ratify, in order (omit on a terminal to pick one)")
    s.add_argument("--by", default=None,
                   help="ratifier name (omit on a terminal to be prompted; "
                        "defaults to the identity this repository signs with)")
    s.add_argument("--by-id", default=None, metavar="SCHEME:VALUE",
                   help="optional stable identifier for that human, e.g. "
                        "github:octocat or email:ada@example.com")
    # Its own flag, never a general assent (SR-0167): an automated re-ratification
    # should say that a change was accepted unseen and be findable later, not
    # inherit permission granted for something else.
    s.add_argument("--accept-change", action="store_true",
                   help="accept a change made since the last ratification without "
                        "seeing it (non-interactive sessions only)")
    # Correcting the identity on a record that was never published (SR-0196). Its
    # own flag, because the act it permits — a signature over content that has not
    # moved — is the one SR-0148 otherwise refuses outright.
    s.add_argument("--replacing", action="store_true",
                   help="replace the ratifier recorded on an uncommitted "
                        "ratification, e.g. to correct a misspelled name")
    s.set_defaults(func=handlers.get("cmd_ratify"))

    s = sub.add_parser("invalidate", help="falsify an item; cascade suspect")
    s.add_argument("uid", nargs="?", default=None,
                   help="UID to invalidate (omit on a terminal to pick one)")
    s.add_argument("--reason", default="")
    s.set_defaults(func=handlers.get("cmd_invalidate"))

    # Withdrawing a signature is its own verb, not a mode of ratify (SR-0197): it
    # names no new accountable party and is the one act here anyone may perform,
    # so it must not share a verb with the act that requires a named human.
    s = sub.add_parser("withdraw",
                       help="withdraw an item's ratification — the signature no "
                            "longer stands; the item awaits a human again")
    s.add_argument("uids", nargs="*", metavar="UID",
                   help="item(s) whose ratification to withdraw (omit on a "
                        "terminal to pick one)")
    s.add_argument("--reason", default=None,
                   help="why the signature should no longer stand (required; "
                        "prompted for on a terminal)")
    s.add_argument("--by", default=None,
                   help="who is withdrawing it (omit on a terminal to be prompted; "
                        "defaults to the identity this repository signs with)")
    s.add_argument("--by-id", default=None, metavar="SCHEME:VALUE",
                   help="optional stable identifier for the withdrawer, e.g. "
                        "github:octocat or email:ada@example.com")
    s.set_defaults(func=handlers.get("cmd_withdraw"))

    # Its own verb, not a mode of amend (SR-0213): removing the ambiguity flag is a
    # judgement that the ambiguity is resolved, recorded with a name and a reason.
    # One item per run, because each flag carries its own reasons.
    s = sub.add_parser("clarify",
                       help="remove an item's ambiguity flag once the ambiguity is "
                            "resolved, recording who judged it and why")
    s.add_argument("uid", nargs="?", default=None,
                   help="the flagged item (omit on a terminal to pick one)")
    s.add_argument("--reason", default=None,
                   help="why the ambiguity is resolved: how the item was reworded, "
                        "or why the flag was wrong (required; prompted for on a "
                        "terminal)")
    s.add_argument("--by", default=None,
                   help="who judged it resolved (omit on a terminal to be prompted; "
                        "defaults to the identity this repository signs with)")
    s.set_defaults(func=handlers.get("cmd_clarify"))

    # The other half of the pair (SR-0221): raising the doubt is as much a judgement
    # as removing it, so it carries a name and a reason too. One item per run,
    # because each doubt has its own reason.
    s = sub.add_parser("flag",
                       help="record that an item's wording is ambiguous, with who "
                            "flagged it and why")
    s.add_argument("uid", nargs="?", default=None,
                   help="item to flag (omit on a terminal to pick one)")
    s.add_argument("--reason", default=None,
                   help="why the wording is ambiguous — what the author must "
                        "settle (required; prompted for on a terminal)")
    s.add_argument("--by", default=None,
                   help="who is flagging it (omit on a terminal to be prompted; "
                        "defaults to the identity this repository signs with)")
    s.set_defaults(func=handlers.get("cmd_flag"))

    s = sub.add_parser("status",
                       help="move an item to a status (transition-validated)")
    s.add_argument("uid", nargs="?", default=None,
                   help="UID to change (omit on a terminal to pick one)")
    s.add_argument("status", nargs="?", default=None,
                   help="target status (omit on a terminal to pick one)")
    s.set_defaults(func=handlers.get("cmd_status"))

    return p

