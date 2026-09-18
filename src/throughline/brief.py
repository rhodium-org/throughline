# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""The agent brief: what an agent joining a project must know, generated from the
project's own configuration (UR-0022, SR-0088, SR-0129, SR-0161).

It is built here, not in the command line that prints it, so a browser or any other
front end can obtain it as text without importing a command line (SR-0224,
SR-0225). The command surface it describes is read from `throughline.parser`, which
is why the brief cannot fall behind the tool: a command the tool gains is described
without anyone remembering to describe it.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .graph import Index
from .parser import build_parser
from .schema import WARNING
from .views import _by_count, render_subgraph


def _fmt_set(items) -> str:
    """`` `a`, `b` `` from any iterable; ``(none)`` when empty."""
    xs = sorted(items)
    return ", ".join(f"`{x}`" for x in xs) if xs else "_(none)_"


def _ctx_idd(schema) -> str:
    """The fixed Intent-Driven Development contract, with this project's own
    root types and grounding links named so it is accurate for the project."""
    return (
        "## The contract: Intent-Driven Development\n\n"
        "This project uses **throughline**, a Git-native requirements tool. Each item "
        "is one small YAML file with a permanent UID; the files are the product "
        "and live in the repo. You work under a discipline called **Intent-Driven "
        "Development (IDD)** — the *why* axis to BDD/TDD's *what*:\n\n"
        f"- **Roots justify themselves.** The root types — {_fmt_set(schema.root_types)} "
        "— are the *why*; they may exist ungrounded.\n"
        "- **Everything else must justify itself** by reaching a root through a "
        f"**grounding link** ({_fmt_set(schema.ground_link_types)}). Those links "
        "form a DAG — circular justification is rejected.\n"
        "- **Author the why first.** Create the grounded requirement as a "
        "`draft` — throughline's version of a *red test*: specified and justified, "
        "not yet built. Implement it, then flip it to `approved`/`implemented` "
        "when the thing exists. Ground at birth with "
        "`tl new <PREFIX> --type <T> --ground <PARENT_UID>`.\n"
        f"- **AI-origin items are provisional.** An item whose `origin` is one of "
        f"{_fmt_set(schema.ai_origins)} enters `proposed` and must be **ratified** "
        "by a human (`tl ratify <UID> --by <who>`) before it counts as "
        "agreed. If you are that agent, you propose; a human accepts.\n"
        "- **`tl check` is the gate.** It validates the whole graph and "
        "returns a stable exit code (0 ok · 1 error-severity findings · 2 usage "
        "error). An ungrounded, unserved, or otherwise invalid graph **fails the "
        "build**. Keep it green; run it before you commit."
    )


def _ctx_working(schema) -> str:
    """The working discipline an agent must follow in the project (SR-0129): stay
    inside the graph, change it only through the CLI, write items that stay readable
    (SR-0163), and leave a reusable rule for the next agent. Fixed contract text —
    it holds for every project — with the root types named so the scope rule reads
    accurately."""
    return (
        "## How to work here\n\n"
        "This is a discipline, not just a data model. Five rules govern how you "
        "work, whatever the task:\n\n"
        f"- **Do only work the graph justifies.** throughline exists to keep scope "
        f"honest: every change should trace to a root ({_fmt_set(schema.root_types)}). "
        "Before you write code, docs, or config, check that a ratified root actually "
        "calls for it. If nothing in the graph justifies the work, author the intent "
        "and have a human ratify it **first** — or stop and ask — rather than doing "
        "ungrounded work. \"It seemed useful\" is not grounding.\n"
        "- **Change the graph only through the CLI — never hand-edit its structure.** "
        "Create, link, ratify, review, and delete items with the commands below; "
        "never open a `<UID>.yml` item or a `.register.yml` manifest to add, rename, "
        "relink, restatus, or renumber by hand, and never allocate a UID yourself. "
        "The CLI is what keeps UIDs unique and immutable, links well-typed, statuses "
        "on legal transitions, and `check` green — a hand-edit silently breaks those "
        "invariants and the files stop being a product you can trust. Run `tl check` "
        "after any change.\n"
        "- **If it binds someone outside the project, say who pays.** Before you "
        "propose a requirement that obliges anyone who is not you — a contributor, "
        "a consumer, an end user — name in the `rationale` who bears the cost and "
        "what it costs them, and weigh that against what the requirement prevents. "
        "A clean grounding chain is not an answer to that question: grounding shows "
        "the requirement is wanted by someone above it, and says nothing about who "
        "pays below it. The people it binds usually have no item in the graph and "
        "nobody arguing for them, and `check` cannot help you — a graph containing "
        "the requirement is clean by construction.\n"
        "- **Write an item short, and rewrite rather than append.** State "
        "the obligation in the fewest words that still bind — a requirement nobody "
        "finishes reading does not bind. A `rationale` records the decision taken "
        "and the alternatives rejected, not the history of your own drafting. When "
        "you revise an item you wrote earlier, **rewrite it**; appending is the "
        "cheaper edit and it is how a clear requirement becomes an unread one.\n"
        "- **Leave a reusable rule for the next agent (idempotent).** If your AI "
        "coding framework has a mechanism for durable, auto-loaded project "
        "instructions — a skill, a rules file, an `AGENTS.md`, or the equivalent — "
        "and this project has no throughline rule yet, add one that captures the "
        "essentials: run this brief (`tl context`) first, drive every graph change "
        "through the CLI, keep `tl check` green. Do it once; if it is already there, "
        "leave it. That way the discipline is inherited, not re-derived each time."
    )


def _ctx_types(schema) -> str:
    out = ["## Item types and their attributes\n"]
    if not schema.types:
        out.append("_This project declares no per-type attribute schema; item "
                    "types are free-form._")
        return "\n".join(out)
    for tname in sorted(schema.types):
        tags = []
        if tname in schema.root_types:
            tags.append("root")
        if tname in schema.delivery_roots:
            tags.append("delivery-root")
        if not schema.is_normative(tname):
            tags.append("non-normative")
        tag = f" _({', '.join(tags)})_" if tags else ""
        out.append(f"### `{tname}`{tag}\n")
        specs = schema.attrs_for(tname)
        if not specs:
            out.append("_(no declared attributes)_\n")
            continue
        out.append("| attribute | kind | required | normative | values |")
        out.append("|---|---|---|---|---|")
        for aname in sorted(specs):
            s = specs[aname]
            vals = ", ".join(f"`{v}`" for v in s.values) if s.values else "—"
            out.append(
                f"| `{aname}` | {s.kind or 'free'} | "
                f"{'yes' if s.required else 'no'} | "
                f"{'yes' if s.normative else 'no'} | {vals} |")
        out.append("")
    return "\n".join(out).rstrip()


def _ctx_links(schema) -> str:
    out = ["## Links\n"]
    if schema.link_types is None:
        out.append("- **Link vocabulary:** unconstrained (any link type is legal).")
    else:
        out.append(f"- **Link vocabulary:** {_fmt_set(schema.link_types)}.")
    out.append(f"- **Grounding links** (these carry justification): "
               f"{_fmt_set(schema.ground_link_types)}.")
    if schema.link_rules:
        out.append("- **Endpoint rules** — a link of a constrained type may only "
                   "join the item types shown (unlisted link types are "
                   "unconstrained):\n")
        out.append("| link | from | to |")
        out.append("|---|---|---|")
        for lt in sorted(schema.link_rules):
            rule = schema.link_rules[lt]
            frm = _fmt_set(rule.frm) if rule.frm is not None else "_any_"
            to = _fmt_set(rule.to) if rule.to is not None else "_any_"
            out.append(f"| `{lt}` | {frm} | {to} |")
    else:
        out.append("- **Endpoint rules:** none declared — any type may sit at "
                   "either end of any link.")
    return "\n".join(out)


def _ctx_status(schema) -> str:
    out = ["## Status and lifecycle\n"]
    if schema.statuses is None:
        out.append("- **Statuses:** unconstrained (any status string is legal).")
    else:
        out.append(f"- **Statuses:** {_fmt_set(schema.statuses)}.")
    if schema.transitions:
        out.append("- **Allowed transitions** (a status may only move along "
                   "these edges; staying put is always allowed):\n")
        for frm in sorted(schema.transitions):
            tos = schema.transitions[frm]
            out.append(f"  - `{frm}` → {_fmt_set(tos)}")
        out.append("\n  Render this as a diagram with "
                   "`tl diagram transitions`.")
    else:
        out.append("- **Transitions:** none declared — any status may change to "
                   "any other.")
    return "\n".join(out)


def _ctx_grounding(schema) -> str:
    out = [
        "## Grounding configuration\n",
        f"- **Root types** (may be ungrounded): {_fmt_set(schema.root_types)}",
        f"- **Delivery roots** (must be *served* — something must derive from / "
        f"mitigate them): {_fmt_set(schema.delivery_roots)}",
        f"- **Grounding link types:** {_fmt_set(schema.ground_link_types)}",
    ]
    # The cascade is the one thing `tl` does that restatuses items the caller did
    # not name, so an agent that had read only the rest of this brief would be
    # surprised by it (SR-0161). Both branches are stated: a project that
    # declared nothing here has not switched the mechanic off, it has narrowed
    # it, and "nothing extra" is itself the fact worth knowing.
    if schema.suspect_link_types:
        out.append(
            f"- **Withdrawing link types:** {_fmt_set(schema.suspect_link_types)} "
            f"— these confer no grounding, but `tl invalidate` marks an item "
            f"suspect when a link of this type points at what was invalidated, "
            f"just as it does along a grounding link.")
    else:
        out.append(
            "- **Withdrawing link types:** none declared — `tl invalidate` "
            "spreads suspicion along the grounding links above and nothing else.")
    out.append(
        f"- **AI origins** (items with these origins enter `proposed` and need "
        f"human ratification): {_fmt_set(schema.ai_origins)}")
    return "\n".join(out)


def _ctx_coverage(schema) -> str:
    out = ["## Coverage rules\n"]
    if not schema.coverage:
        out.append("_No `[[rules.coverage]]` declared._")
        return "\n".join(out)
    out.append("Each rule requires the matching items to have the stated link "
               "(unmet → a `coverage` finding). They are numbered as "
               "`tl schema rule remove` counts them:\n")
    for pos, rule in enumerate(schema.coverage, start=1):
        filt = rule.get("filter", "*")
        needs = rule.get("needs", "?")
        sev = rule.get("severity", WARNING)
        out.append(f"{pos}. items where `{filt}` **need** `{needs}` "
                   f"(severity: {sev})")
    return "\n".join(out)


_CTX_FORMAT = (
    "## The on-disk format\n\n"
    "A project is a directory: `throughline.toml` (config) + one folder per register, "
    "each with a `.register.yml` manifest and one `<UID>.yml` per item. **An item "
    "looks like this** (attributes under `attrs:` are the project-defined ones "
    "listed above for that type):\n\n"
    "```yaml\n"
    "uid: FR-0022                 # permanent, immutable, never reused\n"
    "type: requirement            # one of the item types above\n"
    "status: approved             # one of the declared statuses\n"
    "title: Guided setup wizard\n"
    "text: The system shall walk a new user through setup in 3 steps.\n"
    "normative: true              # set by the type; feeds the fingerprint, and a\n"
    "                             # normative item must reach a published document\n"
    "links:\n"
    "  - target: BN-0003          # a grounding link up to a root\n"
    "    type: derives_from\n"
    "  - target: ASM-0002\n"
    "    type: assumes\n"
    "    stamp: sha256:…          # target fingerprint when last confirmed\n"
    "attrs:\n"
    "  priority: must             # project-defined attributes (see the type above)\n"
    "```\n\n"
    "Never invent a UID or edit a manifest by hand — let the CLI allocate."
)

# Usage lines worth spelling out beyond what the parser's own help says — the
# arguments an agent will otherwise have to discover. Commands absent from this
# table are still listed; they are rendered from the parser alone. This table may
# never *decide* which commands appear (SR-0161): the command surface is the
# parser's, so a capability the tool gains is described without anyone remembering
# to describe it, and _ctx_commands_uncovered() fails the build if one slips past.
_CTX_COMMAND_USAGE = {
    "new": "tl new <PREFIX> --type <T> [--title …] [--text …] --ground <PARENT_UID>",
    "amend": "tl amend <UID> [--title …] [--text …] [--rationale …] [--attr K=V] "
             "[--unset K]",
    "link": "tl link <SRC> <DST> --type <kind>",
    "unlink": "tl unlink <SRC> <DST> [--type <kind>]",
    "check": "tl check [--strict] [--format json] [--base REF | --base-dir DIR]",
    "ratify": "tl ratify <UID> --by <who> [--by-id <scheme:value>]",
    "trace": "tl trace <UID> [--direction in|out]",
    "blast": "tl blast <UID>",
    "subgraph": "tl subgraph <UID> [--depth N] [--link-type KIND] [--format json]",
    "shape": "tl shape [--format json]",
    "dump": "tl dump [-o FILE]",
    "diagram": "tl diagram [types|transitions|both]",
    "docs": "tl docs [FILE ...] [--at REF] [--check]",
    "status": "tl status <UID> <STATUS>",
    "invalidate": "tl invalidate <UID> [--reason …]",
    "withdraw": "tl withdraw <UID> [<UID> …] --reason <why> --by <who>",
    "clarify": "tl clarify <UID> --reason <why> --by <who>",
    "flag": "tl flag <UID> --reason <why> --by <who>",
    "delete": "tl delete <UID>",
    "query": "tl query [--type T] [--status S] [--format json]",
    "register": "tl register new <PREFIX> <FOLDER> --title <…>",
    "schema": "tl schema <noun> <verb> … --because <why>",
    "migrate": "tl migrate",
    "review": "tl review",
    "init": "tl init [--demo]",
    "context": "tl context [<UID>]",
}

# Commands whose importance is not evident from a one-line help string, and which
# an agent that had read only the rest of the brief would be surprised by.
_CTX_COMMAND_EMPHASIS = {
    "check": "THE GATE — run before committing",
    "ratify": "a human accepts a proposed item; never run this for a human",
    "migrate": "idempotent repairs; extend this, never a script beside it",
    "invalidate": "retires an item and cascades suspicion — see grounding, below",
    "withdraw": "removes a signature that should not stand (wrong name, signed in "
                "error) without touching content or dependents; the item awaits a "
                "human again — not `invalidate`, which says the item is false",
    "flag": "records that an item's wording is ambiguous, with who flagged it and "
            "why; check reports it and ratify refuses it until `tl clarify` "
            "removes the flag",
    "clarify": "removes an item's ambiguity flag once someone judges the ambiguity "
               "resolved, recording who and why; it accepts nothing — a proposed "
               "item still needs a human to ratify it",
    "delete": "tombstones an item; the file stays, the item stops counting",
    "amend": "change content through the tool, never by opening the YAML",
    "schema": "change the schema itself — nouns: status, transition, type, attr, "
              "linktype, linkrule, grounding, rule; refuses a change that would "
              "invalidate existing items and says what to fix",
}


def _subcommands() -> list[tuple[str, tuple[str, ...], str]]:
    """(name, aliases, help) for every subcommand the CLI exposes, read off the
    parser itself so the brief cannot fall behind the tool (SR-0161).

    Aliases are folded into the command they alias rather than listed as commands
    of their own — ``tl ls`` is a second spelling of ``tl query``, not a second
    capability, and presenting it as one would overstate the surface."""
    actions = [a for a in build_parser()._actions
               if isinstance(a, argparse._SubParsersAction)]
    if not actions:                                  # pragma: no cover — defensive
        return []
    sub = actions[0]
    primary = {c.dest for c in sub._choices_actions}
    helps = {c.dest: (c.help or "").strip() for c in sub._choices_actions}
    aliases: dict[str, list[str]] = {}
    for name, parser in sub.choices.items():
        if name in primary:
            continue
        owner = next((n for n in primary if sub.choices[n] is parser), None)
        if owner:
            aliases.setdefault(owner, []).append(name)
    return sorted(
        (name, tuple(sorted(aliases.get(name, ()))), helps.get(name, ""))
        for name in primary
    )


def _ctx_commands_uncovered() -> list[str]:
    """Subcommands the brief would describe from the parser alone, with no usage
    line of their own. Returned rather than raised so a caller — the test that
    gates this — decides how loudly to fail (SR-0161)."""
    return [name for name, _, _ in _subcommands() if name not in _CTX_COMMAND_USAGE]


def _ctx_commands() -> str:
    """The command section, derived from the live parser rather than a hand-kept
    list. A hand-kept list drifts precisely when someone is moving fast, and the
    commands that went missing from this brief for months — delete, invalidate,
    migrate, query, register, review, status, unlink — were exactly the ones an
    agent most needed to be told about."""
    rows = []
    for name, aliases, help_text in _subcommands():
        usage = _CTX_COMMAND_USAGE.get(name, f"tl {name}")
        note = _CTX_COMMAND_EMPHASIS.get(name) or help_text.split(" — ")[0]
        if aliases:
            note = f"{note} (also: {', '.join(aliases)})" if note else \
                   f"also: {', '.join(aliases)}"
        rows.append((usage, note))
    width = min(max((len(u) for u, _ in rows), default=0) + 2, 58)
    # a usage line wider than the column still keeps a gap before its note
    body = "\n".join(
        f"{u.ljust(max(width, len(u) + 2))}# {n}".rstrip() if n else u
        for u, n in rows)
    return "## Commands (every command this tool exposes)\n\n```\n" + body + "\n```"


def _ctx_snapshot(project) -> str:
    live = [it for it in project.items() if not it.is_deleted]
    out = ["## Live snapshot of this graph\n"]
    out.append(f"- **Items:** {len(live)} live — {_by_count(it.type for it in live)}")
    out.append(f"- **Status:** {_by_count(it.status for it in live)}")
    shape = Index.build(project).link_shape()
    if shape:
        out.append("- **Link shape** (source → link → target · count):")
        for (s, lt, t), n in sorted(shape.items(), key=lambda kv: (-kv[1], kv[0])):
            out.append(f"  - `{s}` -[{lt}]-> `{t or '<external>'}` × {n}")
    else:
        out.append("- **Link shape:** no links yet.")
    return "\n".join(out)


def _ctx_non_goals(project) -> str | None:
    """List every live `non_goal` item so deliberately-excluded scope is visible
    to an agent reading the brief (SR-0097). Returns None when the project
    declares none, so projects not using non-goals see no extra section."""
    goals = [it for it in project.items()
             if it.type == "non_goal" and not it.is_deleted]
    if not goals:
        return None
    out = ["## Non-goals (deliberately out of scope)\n",
           "These are recorded, out-of-scope statements. Do **not** propose work "
           "that pursues them; if one looks wrong, raise it with a human rather "
           "than working around it.\n"]
    for it in sorted(goals, key=lambda i: i.uid):
        line = f"- **{it.uid}** {it.title}".rstrip()
        if it.text:
            line += f" — {it.text}"
        out.append(line)
    return "\n".join(out)


def context_markdown(project) -> str:
    schema = project.schema
    name = schema.name or Path(project.path).name
    header = (
        f"# Working in the throughline project: {name}\n\n"
        "You are an AI agent working in a project managed by **throughline**. This "
        "brief is **generated from the project's live `throughline.toml`** — it "
        "reflects the rules the validator actually enforces. If the "
        "configuration changes, regenerate it with `tl context`. "
        "If a capability isn't described here, it isn't in the tool."
    )
    sections = [
        header,
        _ctx_idd(schema),
        _ctx_working(schema),
        _ctx_types(schema),
        _ctx_links(schema),
        _ctx_status(schema),
        _ctx_grounding(schema),
        _ctx_coverage(schema),
        _CTX_FORMAT,
        _ctx_commands(),
        _ctx_snapshot(project),
    ]
    non_goals = _ctx_non_goals(project)
    if non_goals is not None:
        sections.insert(-1, non_goals)  # before the live snapshot
    return "\n\n".join(sections) + "\n"


def context_item_section(project, view, *, uid_display=None) -> str:
    """The `<UID>` section of the brief, as markdown (SR-0190).

    Both `tl context <UID>` and tl-compose's union-aware version render through
    here, so the wording an agent is trained on cannot differ between the two
    tools — only the graph the neighbourhood was computed over does.
    """
    show = uid_display or (lambda u: u)
    lines: list[str] = []
    render_subgraph(project, view, uid_display=uid_display, emit=lines.append)
    body = "\n".join(lines)
    return (
        f"## The item you were given: {show(view.start)}\n\n"
        "Its neighbourhood — what it rests on, what rests on it, and the links "
        "joining them. Anything listed under *depended on by* may be affected by "
        "your change.\n\n"
        f"```\n{body}\n```\n")

