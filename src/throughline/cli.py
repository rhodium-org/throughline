# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""``tl`` — the command-line entry point (arch doc 07 §6, SR-0060).

Exit codes are a stable contract: 0 = ok, 1 = findings at error severity,
2 = usage/internal error. Everything that mutates disk routes through the
storage layer so the on-disk format stays deterministic (SR-0072).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .dump import build_dump
from .fingerprint import fingerprint
from .graph import Index
from .links import LinkError, add_link, remove_link, retype_link
from .grounding import (
    AMBIGUOUS_ATTR,
    CLARIFICATION_ATTRS,
    CLARIFIED_AMBIGUITY_ATTR,
    CLARIFIED_BY_ATTR,
    CLARIFIED_REASON_ATTR,
    GroundingError,
    ambiguity_change_refusal,
    attribute_owner,
    attribute_removal_refusal,
    clarification_refusal,
    clarify,
    flag,
    flag_refusal,
    invalidate,
    origin_change_refusal,
    ratification_obstacle,
    ratify,
    reaches_root,
    set_status,
    withdraw,
)
from .identity import (
    RATIFIED_BY_ATTR,
    RATIFIED_ID_ATTR,
    WITHDRAWN_RATIFIER_ATTR,
    IdentityError,
    default_ratifier,
)
from .diagrams import diagram_transitions, diagram_types
from .inject import (
    InjectError,
    document_paths,
    has_markers,
    inject_documents,
    inject_text,
    referenced_uids,
)
from .views import (
    check_summary,
    ground_line,
    render_for_ratification,
    render_subgraph,
    render_trace,
    subgraph_json,
)
from .brief import (
    _by_count,
    context_item_section,
    context_markdown,
)
from .parser import build_parser as _command_tree
from .items import (
    Amendment,
    amend_item,
    birth_item,
    coerce_attr,
    delete_item,
    new_item,
    newly_suspect,
    parse_attrs,
    review_items,
)
from .ratification import change_since_ratification, render_change
from .model import Link, Register
from .schema import Schema, SchemaError
from . import schema_ops
from .storage import (
    CONFIG_NAME,
    MANIFEST_NAME,
    ProjectError,
    baseline_note,
    read_baseline,
    init_project,
    load_project,
    create_register,
    load_project_at_ref,
    migrate_project,
    write_item,
    write_manifest,
)
from .uid import PREFIX_GRAMMAR, UidError, next_uid, parse_uid, valid_prefix
from .validate import (
    ERROR,
    OFF,
    WARNING,
    FilterError,
    eval_filter,
    query_items,
    validate,
)
from .version import distribution_version

OK, FINDINGS, USAGE = 0, 1, 2


def _version() -> str:
    # One implementation of "what am I running?" serves the library and the CLI
    # alike (SR-0164 / SR-0076), including the +editable marker for a working tree.
    return distribution_version("throughline")


# The spellings throughline-compose 0.21.0 imports from this module. The operations
# live in `throughline.items` now (SR-0224), and nothing new should reach for a
# command line to find them (SR-0225); these stay until the major release that
# drops the private surface.
_parse_attrs = parse_attrs
_coerce_attr = coerce_attr
_newly_suspect = newly_suspect


# The spellings throughline-compose 0.21.0 imports from this module; the renderers
# live in `throughline.views` now (SR-0224).
_check_summary = check_summary
_subgraph_json = subgraph_json
_render_for_ratification = render_for_ratification
_ground_line = ground_line


# The spelling throughline-compose 0.21.0 imports from this module; the brief lives
# in `throughline.brief` now (SR-0224).
_context_markdown = context_markdown


def _err(msg: str) -> int:
    print(f"tl: {msg}", file=sys.stderr)
    return USAGE


def force_utf8_io() -> None:
    """Emit UTF-8 regardless of the console's default codec (SR-0139).

    A Windows console commonly defaults to cp1252, which raises
    ``UnicodeEncodeError`` the instant tl prints a glyph outside Latin-1 — the
    ``->`` arrow (U+2192) in grounding output is the usual trigger. Reconfiguring
    the standard streams to UTF-8 makes tl's output portable so callers no longer
    have to set ``PYTHONIOENCODING=utf-8`` on every invocation. Shared so the
    compose / ratify front-ends can apply the same guard from their own entry
    points.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # pragma: no cover - stream is not a TextIOWrapper
            continue
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):  # pragma: no cover - stream not reconfigurable
            pass


# --------------------------------------------------------------------- commands

class _ScanProgress:
    """Live progress for the init descendant scan (SR-0077). Silent unless the
    scan runs long enough to matter, and only on an interactive terminal — so
    scripts and CI see nothing on stderr and stdout stays clean. Shows a real
    running directory count, never a fabricated percentage."""

    _FRAMES = "|/-\\"

    def __init__(self, stream=sys.stderr, delay: float = 0.5,
                 interval: float = 0.1) -> None:
        self._stream = stream
        self._enabled = stream.isatty()
        self._delay = delay
        self._interval = interval
        self._start = time.monotonic()
        self._last = 0.0
        self._frame = 0
        self._active = False

    def __call__(self, scanned: int) -> None:
        if not self._enabled:
            return
        now = time.monotonic()
        if now - self._start < self._delay or now - self._last < self._interval:
            return
        self._last = now
        spin = self._FRAMES[self._frame % len(self._FRAMES)]
        self._frame += 1
        self._active = True
        self._stream.write(
            f"\r{spin} scanning for existing projects… {scanned:,} dirs"
        )
        self._stream.flush()

    def clear(self) -> None:
        if self._active:
            self._stream.write("\r\033[K")  # carriage return + clear-to-eol
            self._stream.flush()
            self._active = False


def cmd_init(args) -> int:
    progress = _ScanProgress()
    try:
        init_project(args.path, name=args.name, force=args.force,
                     defaults=not args.no_defaults, demo=not args.no_demo,
                     bare=args.bare, on_progress=progress)
    except ProjectError as e:
        progress.clear()
        return _err(str(e))
    progress.clear()
    root = Path(args.path).resolve()
    print(f"initialised throughline project at {root}")
    # Report exactly what was seeded, mirroring the same precedence init_project
    # applies: --bare / --no-defaults win over the demo (SR-0100).
    made_defaults = not (args.bare or args.no_defaults)
    made_demo = made_defaults and not args.no_demo
    if made_demo:
        print("seeded a starter graph (INT/REQ/NFR/TEST/NG) and docs/overview.md — "
              "edit or delete freely; run `tl check` and `tl docs` to explore.")
    elif made_defaults:
        print("created the default registers (INT/REQ/NFR/NG/TEST) — add items with "
              "`tl new <PREFIX>`; pass --demo next time for a worked example.")
    else:
        print("wrote only throughline.toml — add registers with `tl register new`, "
              "or re-init with --defaults for the standard registers.")
    return OK


def cmd_migrate(args) -> int:
    try:
        result = migrate_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    if result.start != result.end:
        print(f"migrated project from format version {result.start} to {result.end}")
    elif result.repaired is None:
        print(f"already at format version {result.end}"
              + ("" if result.bound or result.declared or result.routed
                 or result.cached or result.normative or result.contents
                 or result.unprovable else " — nothing to migrate"))
    else:
        # Already at this major, but missing configuration the major requires —
        # repaired in place. Name every binding written so the change is never
        # silent and the operator can correct it (SR-0137).
        print(f"already at format version {result.end} — backfilled [status.roles]")
        for role, status in result.repaired.items():
            print(f'  {role} = "{status}"')
        if not result.repaired:
            print("  (no declared status matched a role — bind them yourself, and "
                  "leave out any role your statuses have no honest counterpart for)")
    # Vocabularies the repair declared (SR-0185). Named in full rather than
    # counted, because this is what the project's items will be validated against
    # from now on: it is the project's own current reliance, so it changes nothing
    # today, and it is exactly the thing an author may want to narrow.
    if result.declared:
        print("declared the vocabularies this project relies on but had left "
              "open — every value was legal before, and nothing checked the ones "
              "in use; narrow them deliberately with `tl schema`:")
        for key, values in result.declared.items():
            print(f"  {key} = {', '.join(values)}")
    # Routes to suspicion the repair restored (SR-0188). Named per status, and kept
    # apart from the vocabularies above, because this is the one part of the repair
    # that widens what the lifecycle permits — an operator who did not want a
    # particular status able to move must be able to see which ones changed.
    if result.routed:
        print("restored this lifecycle's route to suspicion — an item in each "
              "status below could never be marked suspect, so withdrawing what it "
              "grounds in left it unflagged; narrow it back with `tl schema "
              "transition deny`:")
        for status, suspect in result.routed.items():
            print(f"  {status} -> {suspect}")
    # Records the repair completed. Named for the same reason the config bindings
    # are, and more so: this wrote to an accountability record, so an operator who
    # disagrees with a stamp must be able to see which item carries it (SR-0152).
    if result.bound:
        print(f"bound {len(result.bound)} ratification record(s) that named a "
              "ratifier but carried no fingerprint — each marked "
              "`ratified_backfilled` because it attests to the content as it "
              "stands now, not to what the ratifier read:")
        for uid, stamp in result.bound.items():
            print(f"  {uid} = {stamp}")
    # Revisions cached against a stamp (SR-0166). Counted rather than listed: this
    # one records where an answer was already found rather than deciding anything,
    # it is re-verified against the stamp before it is ever used, and a graph of
    # any size would bury the parts above that an operator may need to correct.
    if result.cached:
        print(f"cached the ratified revision for {len(result.cached)} record(s) so "
              "that what changed since a signature can be shown without walking "
              "history each time")
    # Signed content recorded on older signatures (SR-0218). Counts, as for the
    # revision cache: the content is proved by the stamp and decides nothing on its
    # own. The unprovable are counted too, because a record that stays without its
    # content still needs history to show what changed.
    if result.contents:
        print(f"recorded the signed content on {len(result.contents)} ratification "
              "record(s), so what changed since each signature can be shown without "
              "version control")
    if result.unprovable:
        print(f"could not prove the signed content of {len(result.unprovable)} "
              "ratification record(s); what changed since those signatures still "
              "needs their history:")
        counts: dict[str, int] = {}
        for why in result.unprovable.values():
            counts[why] = counts.get(why, 0) + 1
        for why, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {n} — {why}")
    # Items whose normative flag disagreed with their type (SR-0203). Named in
    # full: the flag is a fingerprint input, so each rewrite is a content change
    # the graph will hold someone to. The ratification records are left alone —
    # only ratification writes them (SR-0170) — so the stale ones are counted here
    # and each is re-ratified by a person who sees that only the flag moved.
    if result.normative:
        print(f"rewrote the normative flag on {len(result.normative)} item(s) to "
              "what the item's type declares:")
        for uid, value in result.normative.items():
            print(f"  {uid} = {'true' if value else 'false'}")
        if result.restamped:
            print(f"  refreshed {len(result.restamped)} link stamp(s) that matched "
                  "the content before the rewrite — the wording they confirmed "
                  "has not changed")
        if result.stale:
            print(f"  {len(result.stale)} ratified item(s) now await re-ratification "
                  "— `tl ratify` shows that only the flag moved before it asks: "
                  + " ".join(result.stale))
    return OK


def cmd_register_new(args) -> int:
    """Parse, call :func:`throughline.storage.create_register`, render (SR-0225)."""
    try:
        project = load_project(args.path)
        reg = create_register(project, args.prefix, args.dir, title=args.title,
                              digits=args.digits, parent=args.parent)
    except ProjectError as e:
        return _err(str(e))
    print(f"created register {reg.prefix} at {reg.path}")
    return OK


def cmd_schema(args) -> int:
    """Change the project's own schema through the tool (SR-0181). Every verb
    routes through here: the operation builds the config it wants, the change is
    refused if it would invalidate an existing item (SR-0182), and only then is
    the file edited in place with the reason recorded beside it (SR-0183/0184)."""
    root = Path(args.path)
    try:
        project = schema_ops.load(root)
        change = args.builder(project, args)
        result = schema_ops.apply_change(root, change, args.because)
    except (schema_ops.SchemaOpError, ProjectError) as e:
        return _err(str(e))
    if isinstance(result, schema_ops.Refusal):
        print(result.render(schema_ops.is_composed(project)))
        return FINDINGS
    print(f"{result} — {root / CONFIG_NAME} updated")
    return OK


def cmd_schema_attr_remove(args) -> int:
    """Withdraw an attribute from an item type, naming the items that still carry
    it, and with --unset remove it from them as well (SR-0207).

    Without --unset no item is touched, so withdrawing an attribute and declaring
    it again, the only way to change a declaration, keeps its values. With it, the
    same command also finishes a withdrawal that an earlier release left behind.
    Every refusal is decided before the configuration or any item is written."""
    root = Path(args.path)
    try:
        project = schema_ops.load(root)
        declared = project.schema.attr(args.itype, args.name) is not None
        carriers = schema_ops.attr_carriers(project, args.itype, args.name)
        type_declared = args.itype in (project.config.get("types") or {})
        if declared:
            change = schema_ops.attr_remove(project, args.itype, args.name)
        elif args.unset and carriers and type_declared:
            change = schema_ops.attr_values_unset(project, args.itype, args.name)
        else:
            hint = ""
            if carriers and type_declared:
                hint = (f" — {len(carriers)} item(s) of that type still carry it; "
                        "pass --unset to remove it from them")
            try:
                change = schema_ops.attr_remove(project, args.itype, args.name)
            except schema_ops.SchemaOpError as e:
                return _err(f"{e}{hint}")
        if args.unset:
            after = Schema.from_config(change.config)
            for it in carriers:
                refusal = attribute_removal_refusal(after, it, args.name)
                if refusal is not None:
                    return _err(f"{refusal} — nothing was changed")
        result = schema_ops.apply_change(root, change, args.because)
    except (schema_ops.SchemaOpError, ProjectError, SchemaError) as e:
        return _err(str(e))
    if isinstance(result, schema_ops.Refusal):
        print(result.render(schema_ops.is_composed(project)))
        return FINDINGS
    print(f"{result} — {root / CONFIG_NAME} updated")
    uids = " ".join(it.uid for it in carriers)
    if args.unset and carriers:
        schema_ops.unset_from(carriers, args.name)
        print(f"  removed '{args.name}' from {len(carriers)} item(s): {uids}")
    elif carriers:
        print(f"  {len(carriers)} item(s) still carry '{args.name}': {uids}")
        print(f"  remove it from them with `tl schema attr remove {args.itype} "
              f"{args.name} --unset --because \"…\"`")
    return OK


def _ground_candidates(project, schema, new_uid: str):
    """Items a new non-root could ground against: roots, plus anything already
    grounded (SR-0073). Roots first, then by type/uid, so the closest 'why' is
    at the top of the picker."""
    idx = Index.build(project)
    cands = []
    for it in project.items():
        if it.uid == new_uid or it.is_deleted:
            continue
        is_root = schema.is_root(it)
        if is_root or reaches_root(idx, schema, it.uid):
            cands.append((0 if is_root else 1, it.type, it.uid, it))
    cands.sort(key=lambda t: (t[0], t[1], t[2]))
    return [it for _, _, _, it in cands]


def _prompt_grounding(project, schema, item, default_type: str):
    """Interactive parent picker (SR-0073). Non-blocking: skip is always
    available and EOF/empty input skips, so scripted/piped use never hangs.
    Returns a list of (target_uid, link_type)."""
    cands = _ground_candidates(project, schema, item.uid)
    if not cands:
        print(f"note: no grounded 'why' exists yet — {item.uid} will be an "
              f"ungrounded root candidate; add a link later with `tl link`.",
              file=sys.stderr)
        return []
    print(f"\n{item.uid} ({item.type}) needs a grounding link to justify it.")
    print("choose a parent to reach 'why':")
    for i, it in enumerate(cands, 1):
        tag = "root" if schema.is_root(it) else "grounded"
        print(f"  {i:>2}. {it.uid}  [{it.type}/{tag}] {it.title}".rstrip())
    try:
        raw = input(f"parent number, or [s]kip [s]: ").strip()
    except EOFError:
        return []
    if not raw or raw.lower() == "s":
        return []
    try:
        pick = cands[int(raw) - 1]
    except (ValueError, IndexError):
        print("skipped (unrecognised choice)", file=sys.stderr)
        return []
    try:
        ltype = input(f"link type [{default_type}]: ").strip() or default_type
    except EOFError:
        ltype = default_type
    return [(pick.uid, ltype)]


def _interactive() -> bool:
    """Whether the CLI may prompt (SR-0120): only when both stdin and stderr are a
    TTY. Guidance and prompts are written to stderr, so stdout stays clean for piped
    output and a redirected/CI run is treated as non-interactive."""
    try:
        return sys.stdin.isatty() and sys.stderr.isatty()
    except (ValueError, AttributeError):  # pragma: no cover - detached streams
        return False


def _pick_item(project, purpose: str, *, allow=None) -> str | None:
    """Type-then-item selector for a command invoked without a UID (SR-0121). Lists
    live items grouped by type, then the chosen type's items by UID and title, so the
    user recognises the item rather than recalling its identifier. Returns the chosen
    UID, or None when the user cancels or nothing is selectable. Non-blocking: an
    empty answer or EOF cancels cleanly (the grounding-picker convention)."""
    items = [it for it in project.items()
             if not it.is_deleted and (allow is None or allow(it))]
    if not items:
        print("no items to choose from.", file=sys.stderr)
        return None
    by_type: dict[str, list] = {}
    for it in items:
        by_type.setdefault(it.type, []).append(it)
    types = sorted(by_type)
    print(f"\nwhich item to {purpose}?", file=sys.stderr)
    if len(types) == 1:
        chosen_type = types[0]
    else:
        print("choose a type:", file=sys.stderr)
        for i, t in enumerate(types, 1):
            print(f"  {i:>2}. {t}  ({len(by_type[t])})", file=sys.stderr)
        chosen_type = _choose(types, "type")
        if chosen_type is None:
            return None
    cands = sorted(by_type[chosen_type], key=lambda it: it.uid)
    print(f"choose a {chosen_type}:", file=sys.stderr)
    for i, it in enumerate(cands, 1):
        print(f"  {i:>2}. {it.uid}  [{it.status}] {it.title}".rstrip(),
              file=sys.stderr)
    pick = _choose(cands, chosen_type)
    return pick.uid if pick is not None else None


def _choose(options: list, noun: str):
    """Read a 1-based selection from ``options`` off an interactive prompt. Empty
    input, 'c', or EOF cancels and returns None; an unrecognised entry also cancels
    rather than looping, so the picker never traps the user (SR-0121)."""
    try:
        raw = input(f"{noun} number, or [c]ancel [c]: ").strip()
    except EOFError:
        return None
    if not raw or raw.lower() == "c":
        return None
    try:
        return options[int(raw) - 1]
    except (ValueError, IndexError):
        print("cancelled (unrecognised choice)", file=sys.stderr)
        return None


def _resolve_uid(project, uid: str | None, purpose: str, flag: str,
                 *, allow=None) -> str | None:
    """Turn a possibly-omitted item argument into a UID (SR-0120, SR-0121). If ``uid``
    was supplied it is returned unchanged. Otherwise, on an interactive terminal the
    type-then-item picker guides the user; on a non-interactive session the command
    fails fast, naming the missing detail and the argument that supplies it, and this
    returns None (the caller returns a usage error). A picker cancellation also
    returns None."""
    if uid:
        return uid
    if not _interactive():
        _err(f"no item given to {purpose} — pass a {flag} "
             "(this is a non-interactive session, so there is nothing to prompt)")
        return None
    return _pick_item(project, purpose, allow=allow)


def _resolve_value(value, purpose: str, flag: str, *, options=None, default=None):
    """Resolve a required non-item detail (SR-0120): return it if supplied, else
    prompt on an interactive terminal — offering the known ``options`` and a
    ``default`` where one is safe — else fail fast naming the flag. Returns None on
    cancel or non-interactive omission (the caller returns a usage error)."""
    if value:
        return value
    if not _interactive():
        _err(f"no {purpose} given — pass {flag} "
             "(this is a non-interactive session, so there is nothing to prompt)")
        return None
    if options:
        print(f"\nchoose a {purpose}:", file=sys.stderr)
        for i, o in enumerate(options, 1):
            print(f"  {i:>2}. {o}", file=sys.stderr)
    hint = f" [{default}]" if default else ""
    try:
        raw = input(f"{purpose}{hint}: ").strip()
    except EOFError:
        return None
    if not raw:
        return default  # None when no default -> treated as a cancel by the caller
    if options and raw.isdigit():
        try:
            return options[int(raw) - 1]
        except IndexError:
            print("cancelled (unrecognised choice)", file=sys.stderr)
            return None
    return raw


def cmd_new(args) -> int:
    """Parse, ask for a parent where a terminal allows it, call
    :func:`throughline.items.new_item`, render (SR-0225)."""
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    schema = project.schema
    try:
        attrs = parse_attrs(schema, args.type, args.attr, command="new")
    except UidError as e:
        return _err(str(e))
    # A declared default naming a record a single verb owns is never written
    # (SR-0170, SR-0213, SR-0219); say so rather than drop it silently.
    for name, spec in schema.attrs_for(args.type).items():
        if spec.default is not None and attribute_owner(name) is not None:
            record, owner = attribute_owner(name)
            print(f"ignored the declared default for '{name}': it is part of the "
                  f"{record} and only `tl {owner}` writes it")

    # Grounding-assisted authoring (SR-0073): attach a parent at birth so the item
    # is justified the moment it exists, rather than being created orphaned and
    # only caught later by `check`. A parent named on the command line is always
    # honoured, including for a root type (SR-0091).
    default_type = args.ground_type or "derives_from"
    named = list(args.ground or ())
    try:
        item = new_item(project, args.prefix, item_type=args.type, uid=args.uid,
                        title=args.title or "", text=args.text or "",
                        status=args.status, origin=args.origin, attrs=attrs,
                        ground=named, ground_type=default_type)
    except (ProjectError, GroundingError, SchemaError, UidError) as e:
        return _err(str(e))
    grounds = [(target, default_type) for target in named]
    # No parent named: offer one for non-roots, where a terminal can be asked.
    if not grounds and not args.no_interactive and not schema.is_root(item) \
            and sys.stdin.isatty() and sys.stdout.isatty():
        grounds = _prompt_grounding(project, schema, item, default_type)
        for target, ltype in grounds:
            item.links.append(Link(target=target, type=ltype))

    path = write_item(item, project.register_of(item.uid))
    print(f"created {item.uid} -> {path}")
    for target, ltype in grounds:
        print(f"  grounded: {item.uid} --{ltype}--> {target}")
    return OK


def cmd_link(args) -> int:
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    # Two endpoints, each selected in turn when omitted (SR-0121).
    src_uid = _resolve_uid(project, args.src, "link from (source)", "SRC")
    if src_uid is None:
        return USAGE
    dst_uid = _resolve_uid(project, args.dst, "link to (destination)", "DST")
    if dst_uid is None:
        return USAGE
    src = project.get(src_uid)
    if src is None:
        return _err(f"source {src_uid} does not exist")
    dst = project.get(dst_uid)
    if dst is None:
        return _err(f"target {dst_uid} does not exist")
    link_types = sorted(project.schema.link_types) if project.schema.link_types else None
    ltype = _resolve_value(args.type, "link type", "--type", options=link_types)
    if ltype is None:
        return USAGE
    try:
        if getattr(args, "retype", False):
            # Retype changes an existing edge in place rather than adding a parallel
            # one (SR-0143), refused where it would leave the graph ungrounded
            # (SR-0211).
            old_type = retype_link(project, src_uid, dst_uid, ltype, stamp=args.stamp)
            print(f"retyped {src_uid} {dst_uid}: --{old_type}--> is now --{ltype}-->"
                  + (" (stamped)" if args.stamp else ""))
            return OK
        outcome = add_link(project, src_uid, dst_uid, ltype, stamp=args.stamp)
    except LinkError as e:
        return _err(str(e))
    if outcome == "restamped":
        print(f"restamped {src_uid} --{ltype}--> {dst_uid}")
    else:
        print(f"linked {src_uid} --{ltype}--> {dst_uid}"
              + (" (stamped)" if args.stamp else ""))
    return OK


def cmd_unlink(args) -> int:
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    # Removing a link is the inverse of `tl link` (SR-0143); the semantic-link
    # review needs to drop an edge (e.g. a spurious TEST -> REQ) without editing
    # YAML by hand.
    src_uid = _resolve_uid(project, args.src, "unlink from (source)", "SRC")
    if src_uid is None:
        return USAGE
    dst_uid = _resolve_uid(project, args.dst, "unlink to (destination)", "DST")
    if dst_uid is None:
        return USAGE
    # Removing a link is refused where it would leave the graph ungrounded
    # (SR-0211); the operation is shared with composing tools (SR-0212).
    try:
        removed = remove_link(project, src_uid, dst_uid, args.type)
    except LinkError as e:
        return _err(str(e))
    for ltype in removed:
        print(f"unlinked {src_uid} --{ltype}--> {dst_uid}")
    return OK


def cmd_delete(args) -> int:
    """Parse, call :func:`throughline.items.delete_item`, render (SR-0225)."""
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    uid = _resolve_uid(project, args.uid, "delete", "UID")
    if uid is None:
        return USAGE
    try:
        retired = delete_item(project, uid, reason=args.reason)
    except (ProjectError, GroundingError, SchemaError) as e:
        return _err(str(e))
    if not retired:
        print(f"{uid} is already deleted — its tombstone is permanent and was left "
              "unchanged (SR-0093)")
        return OK
    write_item(project.get(uid), project.register_of(uid))
    print(f"tombstoned {uid} (UID retired, never reused)")
    return OK


def cmd_review(args) -> int:
    """Parse, call :func:`throughline.items.review_items`, render (SR-0225)."""
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    uids = None
    if not args.all_clean:
        uid = _resolve_uid(project, args.uid, "mark reviewed", "UID")
        if uid is None:
            return USAGE
        uids = [uid]
    try:
        moved = review_items(project, uids, all_items=args.all_clean)
    except (ProjectError, GroundingError, SchemaError) as e:
        return _err(str(e))
    for item in moved:
        write_item(item, project.register_of(item.uid))
    print(f"marked {len(moved)} item(s) reviewed at current content")
    return OK


def cmd_amend(args) -> int:
    """Parse, call :func:`throughline.items.amend_item`, render (SR-0225)."""
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    uid = _resolve_uid(project, args.uid, "amend", "UID")
    if uid is None:
        return USAGE
    item = project.get(uid)
    if item is None:
        return _err(f"{uid} does not exist")
    # Amending nothing is a mistake worth naming. Succeeding silently would let a
    # typo in an option name read as a change that was made.
    if args.title is None and args.text is None and args.rationale is None \
            and not args.attr and not args.unset:
        return _err("amend needs at least one of --title, --text, --rationale, "
                    "--attr or --unset")
    try:
        attrs = parse_attrs(project.schema, item.type, args.attr,
                            command="amend", declared_only=True)
    except UidError as e:
        return _err(str(e))
    unset = list(dict.fromkeys(name.strip() for name in (args.unset or [])))
    if "" in unset:
        return _err("--unset expects an attribute name")
    both = sorted(set(unset) & set(attrs))
    if both:
        return _err(f"--attr and --unset both name {', '.join(both)} — "
                    "say which you mean")
    try:
        result = amend_item(project, uid, title=args.title, text=args.text,
                            rationale=args.rationale, attrs=attrs, unset=unset)
    except (ProjectError, GroundingError, SchemaError, UidError) as e:
        return _err(str(e))
    if not result.changed:
        print(f"{uid} already says that — nothing changed")
        return OK
    write_item(project.get(uid), project.register_of(uid))

    # What the change cost, reported and not asked about (SR-0169). The gate stays
    # where it already stands — `check`, and the re-ratification that shows what
    # moved before it asks for a signature.
    print(f"amended {uid} — {', '.join(result.changed)}")
    if not result.normative_change:
        print("  normative content unchanged — nothing was made suspect")
    else:
        if result.suspects:
            print(f"  {len(result.suspects)} dependent item(s) now suspect: "
                  f"{', '.join(result.suspects)}")
        else:
            print("  no dependent item was confirmed against the old content")
        if result.review_cleared:
            print("  review record cleared — `tl review` to confirm the new wording")
        if result.ratification_stale:
            who = result.ratifier or "a human"
            print(f"  ratification by {who} no longer matches this content — "
                  f"`tl ratify {uid}` shows what moved and asks again")
    return OK




def cmd_check(args) -> int:
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    # The baseline serves transition legality (SR-0083) and tombstone permanence
    # (SR-0093), so it is read whether or not transitions are declared. A host
    # without git supplies it as a directory (SR-0210); where it cannot be read,
    # the result says which rules did not run (SR-0209).
    try:
        baseline = read_baseline(project, ref=args.base,
                                 base_dir=getattr(args, "base_dir", None))
    except ProjectError as e:
        return _err(str(e))
    note = baseline_note(project.schema, baseline)
    published = referenced_uids(project)  # None unless [docs] paths configured
    findings = validate(project, strict=args.strict, baseline=baseline.statuses,
                        published=published)
    if args.format == "json":
        print(json.dumps([f.to_dict() for f in findings], indent=2))
        if note:
            print(note, file=sys.stderr)
        return FINDINGS if any(f.severity == ERROR for f in findings) else OK

    for f in sorted(findings, key=lambda x: (x.severity != ERROR, x.uid)):
        print(f)
    sys.stdout.flush()
    errs = sum(1 for f in findings if f.severity == ERROR)
    warns = len(findings) - errs
    if not args.quiet:
        for line in check_summary(project):
            print(line, file=sys.stderr)
    # Not a finding, so neither the findings nor the exit status move; printed
    # even when quiet, because it qualifies the result a quiet run still reports.
    if note:
        print(f"\n{note}", file=sys.stderr)
    tally = f"\n{errs} error(s), {warns} warning(s)"
    if not args.quiet and errs == 0:
        tally += "  — graph is sound" + (" (strict)" if args.strict else "")
    print(tally, file=sys.stderr)
    return FINDINGS if any(f.severity == ERROR for f in findings) else OK


def cmd_query(args) -> int:
    """Parse, call :func:`throughline.validate.query_items`, render (SR-0225)."""
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    try:
        matched = query_items(project, args.expr, include_deleted=args.all)
    except FilterError as e:
        return _err(f"bad filter expression: {e}")

    if args.format == "json":
        print(json.dumps([it.to_dict() for it in matched], indent=2, default=str))
    else:
        for it in matched:
            title = f"  {it.title}" if it.title else ""
            print(f"{it.uid}  [{it.type}/{it.status}]{title}")
        sys.stdout.flush()
        print(f"\n{len(matched)} item(s)", file=sys.stderr)
    return OK


def cmd_dump(args) -> int:
    """Export the whole project as one documented JSON structure (SR-0055).

    This is the sanctioned interchange surface for third-party tooling; the
    tool itself generates no presentation or exchange formats (NG-0005)."""
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    data = build_dump(project, _version())
    text = json.dumps(data, indent=2, default=str, sort_keys=False)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        print(text)
    return OK


def cmd_shape(args) -> int:
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    shape = Index.build(project).link_shape()
    rows = sorted(shape.items(), key=lambda kv: (-kv[1], kv[0][1], kv[0][0]))

    if args.format == "json":
        print(json.dumps(
            [{"from": s, "link": lt, "to": t, "count": n}
             for (s, lt, t), n in rows], indent=2))
        return OK
    # Align every column so the arrows and counts read as a clean table.
    labels = [(s, f"-[{lt}]->", t or "<external>", n) for (s, lt, t), n in rows]
    sw = max((len(s) for s, _, _, _ in labels), default=0)
    aw = max((len(a) for _, a, _, _ in labels), default=0)
    dw = max((len(d) for _, _, d, _ in labels), default=0)
    for src, arrow, dst, n in labels:
        print(f"  {src:<{sw}}  {arrow:<{aw}}  {dst:<{dw}}  x{n}")
    sys.stdout.flush()
    print(f"\n{len(rows)} distinct link shape(s)", file=sys.stderr)
    return OK


def cmd_diagram(args) -> int:
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    idx = Index.build(project)
    blocks = []  # (heading, mermaid-source-or-None, empty-note)
    if args.kind in ("types", "both"):
        blocks.append(("Type model", diagram_types(idx),
                       "no links in the graph yet"))
    if args.kind in ("transitions", "both"):
        blocks.append(("Status transitions", _mermaid_transitions(project.schema),
                       "no [transitions] declared"))

    emitted = 0
    for heading, src, empty in blocks:
        if args.format == "markdown":
            print(f"### {heading}\n")
            print(f"```mermaid\n{src}\n```\n" if src else f"_{empty}_\n")
        elif src is not None:
            if emitted:
                print()
            print(src)
        else:
            print(f"%% {heading}: {empty}", file=sys.stderr)
        emitted += src is not None
    sys.stdout.flush()
    return OK


# ------------------------------------------------------ document injection

def _resolve_doc_paths(project, explicit: list[str]) -> list[Path]:
    """The Markdown files `tl docs` will inject into: the paths given on the
    command line, or — when none are given — the `[docs] paths` globs from the
    project config, resolved relative to the project root (SR-0094). Every matched
    file is returned regardless of whether it currently holds tl: markers: a
    marker-free document is a no-op when injected (its bytes are left unchanged),
    so a published document with nothing to inject is treated no differently from
    one full of markers — the same uniform set `referenced_uids` reasons over for
    publication coverage (SR-0096)."""
    root = Path(project.path)
    if explicit:
        return [Path(p) for p in explicit]
    out: list[Path] = []
    for pattern in project.schema.docs_paths:
        for p in sorted(root.glob(pattern)):
            if p.is_file():
                out.append(p)
    return out


def cmd_docs(args, resolver=None) -> int:
    """Render every configured document, then write them or report them stale
    (SR-0095, SR-0225). ``resolver`` is an optional target resolver (SR-0110): a
    composing front end supplies one so tl:matrix cells resolve over its union.
    """
    try:
        if args.at:
            project, _sha = load_project_at_ref(args.path, args.at)
        else:
            project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))

    if not document_paths(project, args.file):
        # In --check mode (the CI gate, SR-0095) an unconfigured project has no
        # documents to be stale, so the gate is inert and passes. In write mode a
        # caller who ran `tl docs` with nothing to inject wants to know.
        if args.check:
            return OK
        return _err("no documents to inject — pass a Markdown file, or configure "
                    "[docs] paths in throughline.toml")
    try:
        renders = inject_documents(project, args.file, resolver=resolver)
    except InjectError as e:
        return _err(str(e))
    except OSError as e:
        return _err(f"cannot read a document: {e}")

    pending = [r for r in renders if r.changed]
    if args.check:
        # Separate gate: write-then-diff. A drifted document fails CI; nothing is
        # rewritten. This is deliberately NOT part of `tl check` so routine checks
        # stay friction-free (SR-0095).
        for render in pending:
            print(f"stale: {render.path}", file=sys.stderr)
        if pending:
            print(f"{len(pending)} document(s) out of date — run `tl docs` to "
                  "regenerate", file=sys.stderr)
            return FINDINGS
        print("documents up to date")
        return OK
    for render in pending:
        render.path.write_text(render.rendered, encoding="utf-8")
        print(f"injected {render.path}")
    if not pending:
        print("documents already up to date")
    sys.stdout.flush()
    return OK



def cmd_context(args) -> int:
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    uid = getattr(args, "uid", None)
    # With no UID the output stays byte-for-byte what it has always been (SR-0190),
    # so every existing caller and script sees no change.
    if uid is None:
        sys.stdout.write(_context_markdown(project))
        sys.stdout.flush()
        return OK
    if project.get(uid) is None:
        return _err(f"{uid} does not exist")
    view = Index.build(project).subgraph(uid)
    sys.stdout.write(f"{_context_markdown(project)}\n"
                     f"{context_item_section(project, view)}")
    sys.stdout.flush()
    return OK



def cmd_trace(args) -> int:
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    uid = _resolve_uid(project, args.uid, "trace", "UID")
    if uid is None:
        return USAGE
    if project.get(uid) is None:
        return _err(f"{uid} does not exist")
    render_trace(project, uid, direction=args.direction, max_depth=args.depth or 0)
    return OK


def cmd_blast(args) -> int:
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    uid = _resolve_uid(project, args.uid, "show the blast radius of", "UID")
    if uid is None:
        return USAGE
    if project.get(uid) is None:
        return _err(f"{uid} does not exist")
    idx = Index.build(project)
    affected = idx.impact(uid)
    if args.format == "json":
        print(json.dumps(affected, indent=2))
    else:
        print(f"{uid} — blast radius: {len(affected)} dependent item(s)")
        for uid in affected:
            it = project.get(uid)
            print(f"  {uid}  [{it.type}/{it.status}] {it.title}".rstrip())
    return OK




def cmd_subgraph(args) -> int:
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    uid = _resolve_uid(project, args.uid, "show the neighbourhood of", "UID")
    if uid is None:
        return USAGE
    if project.get(uid) is None:
        return _err(f"{uid} does not exist")
    types = set(args.link_type) if args.link_type else None
    view = Index.build(project).subgraph(uid, types, args.depth or 0)
    if args.format == "json":
        print(json.dumps(_subgraph_json(project, view), indent=2))
    else:
        render_subgraph(project, view)
    return OK




def _confirm(question: str) -> bool:
    """Ask a yes/no question on an interactive terminal, defaulting to no (SR-0195).

    A confirmation is not a prompt for a value, so it has no flag behind it and
    SR-0120's rule that a fully specified command is never prompted for one does
    not reach it: the whole point is to stop a command that already says everything
    it needs to say. Silence, EOF and anything unrecognised all decline — the
    default must never be the irreversible answer.
    """
    try:
        raw = input(f"{question} [y/N]: ").strip().lower()
    except EOFError:
        return False
    return raw in ("y", "yes")


def cmd_ratify(args) -> int:
    """Ratify one or more items (SR-0199). Every item named is checked before any
    is rendered or signed, so a run that cannot complete writes nothing; the
    ratifier is asked once; each item is then shown and confirmed on its own,
    because the signature is per item (UR-0029)."""
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    uids = list(args.uids)
    if not uids:
        # A proposed item is the usual target, but ratify tolerates any item (the
        # grounding layer decides what it means), so the picker lists all live items.
        uid = _resolve_uid(project, None, "ratify", "UID")
        if uid is None:
            return USAGE
        uids = [uid]
    # Refuse before rendering anything or asking anyone (SR-0195), and for the
    # whole run before any item in it (SR-0199). The old order asked who was taking
    # accountability and only then discovered that nothing could be signed, which
    # taught that the prompt was a formality. The index is built once and handed to
    # ratify, so the question asked here and the writes below read the same graph.
    idx = Index.build(project)
    items = []
    for uid in uids:
        item = project.get(uid)
        if item is None:
            return _err(f"{uid} does not exist")
        obstacle = ratification_obstacle(project.schema, idx, item,
                                         replacing=args.replacing)
        if obstacle is not None:
            return _err(obstacle if len(uids) == 1
                        else f"{obstacle} — nothing in this run was ratified")
        items.append(item)
    # Resolved once, before anything is shown, so the difference put in front of
    # the ratifier and the gate below are the same answer (SR-0165).
    changes = {item.uid: change_since_ratification(project, item) for item in items}
    interactive = _interactive()
    if not interactive:
        for item in items:
            if changes[item.uid].stale and not args.accept_change:
                return _err(
                    f"{item.uid} has changed since "
                    f"{item.attrs.get(RATIFIED_BY_ATTR, 'a human')} ratified it and this "
                    "session cannot show you what changed — pass --accept-change to "
                    "record a signature over a change accepted unseen, or run this on "
                    "a terminal to see it first")
    # Offer the identity this repository already signs commits with (SR-0156). It
    # is only ever a default: _resolve_value shows it and takes it on assent, and a
    # non-interactive session that names no ratifier is refused, not signed for.
    # Asked once for the run — the person does not change between items — and on a
    # terminal not until the first item has been shown, so the reader knows what
    # they are being asked about while they are being asked (UR-0029, SR-0195).
    by = None
    if not interactive:
        by = _resolve_value(args.by, "ratifier", "--by",
                            default=default_ratifier(args.path))
        if by is None:
            return USAGE
    rc = OK
    for item in items:
        uid = item.uid
        change = changes[uid]
        # Ratifying is taking accountability, so the content comes before the
        # signature (UR-0029). Non-interactive runs render nothing: there is no
        # reader to serve, and output nobody reads is noise in CI.
        if interactive:
            _render_for_ratification(
                project, item, emit=lambda line: print(line, file=sys.stderr))
            # Re-ratifying is taking accountability afresh, so what moved since the
            # last signature goes in the path of this one (SR-0167). Placed after the
            # item and immediately before the stop, because that is where a reader
            # is deciding. Painted as git would paint it: colour on a terminal, none
            # when the reader has asked for none (NO_COLOR, https://no-color.org).
            if change.stale:
                for line in render_change(
                        change, ratifier=item.attrs.get(RATIFIED_BY_ATTR),
                        columns=shutil.get_terminal_size((80, 24)).columns,
                        colour=not os.environ.get("NO_COLOR")):
                    print(line, file=sys.stderr)
                print("", file=sys.stderr)
            # The stop that makes the rendering more than decoration, asked whether
            # or not --by was supplied (SR-0195) — a fully specified command is
            # exactly how a bulk or habitual ratification is run, and display
            # without a stop is a warning that scrolled past. SR-0120 permits it:
            # confirming an act is not prompting for a value. Declining writes
            # nothing for this item and is not an error; the user was asked and
            # answered, and the run goes on to the next item (SR-0199).
            if by is None:
                by = _resolve_value(args.by, "ratifier", "--by",
                                    default=default_ratifier(args.path))
                if by is None:
                    return USAGE
            question = (f"replace the ratifier recorded on {uid} with {by}?"
                        if args.replacing else f"ratify {uid} as {by}?")
            if not _confirm(question):
                print(f"{uid} not ratified", file=sys.stderr)
                continue
        try:
            item = ratify(project, uid, by=by, index=idx,
                          by_id=getattr(args, "by_id", None),
                          replacing=args.replacing)
        except IdentityError as e:
            return _err(str(e))
        except (ProjectError, GroundingError, SchemaError) as e:
            return _err(str(e))
        write_item(item, project.register_of(item.uid))
        identifier = item.attrs.get(RATIFIED_ID_ATTR)
        print(f"{uid} ratified by {by}" + (f" ({identifier})" if identifier else ""))
    return rc


def cmd_invalidate(args) -> int:
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    target = _resolve_uid(project, args.uid, "invalidate", "UID")
    if target is None:
        return USAGE
    try:
        result = invalidate(project, target, reason=args.reason or "")
    except (ProjectError, GroundingError, SchemaError) as e:
        return _err(str(e))
    write_item(project.get(target), project.register_of(target))
    for uid in result.marked:
        write_item(project.get(uid), project.register_of(uid))
    print(f"{target} invalidated; {len(result.marked)} dependent(s) marked suspect")
    for uid in result.marked:
        print(f"  {uid}")
    # A cascade that did not fully happen must not read as one that did (SR-0173).
    # These dependents have lost the ground they stood on and carry no flag saying
    # so, which is precisely the drift the mechanism exists to surface, so the run
    # reports them and does not exit clean.
    if result.refused:
        sys.stdout.flush()
        print(f"{len(result.refused)} dependent(s) could not be marked suspect:",
              file=sys.stderr)
        for r in result.refused:
            print(f"  {r.uid}: {r.frm} -> {r.to} is not a declared transition",
                  file=sys.stderr)
        print("their grounding rests on an item that is now invalid and nothing "
              "records it; declare the move under [transitions] to close the gap",
              file=sys.stderr)
        return FINDINGS
    return OK


def cmd_withdraw(args) -> int:
    """Withdraw the ratification of one or more items (SR-0197). The signature no
    longer stands; the item goes back to awaiting a human; who withdrew it, why,
    and whose signature it was are recorded. Content and dependents are untouched —
    this is not ``invalidate``."""
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    uids = list(args.uids)
    if not uids:
        # One item through the picker on a terminal; a batch is named explicitly.
        uid = _resolve_uid(project, None, "withdraw the ratification of", "UID")
        if uid is None:
            return USAGE
        uids = [uid]
    # The reason is required, not optional: removing a signature is the quieter
    # attack on an accountability record, and a withdrawal that says why is what
    # makes it as accountable as the signature it removes. Asked once for the run.
    reason = _resolve_value(args.reason, "reason", "--reason")
    if reason is None:
        return USAGE
    by = _resolve_value(args.by, "withdrawer", "--by",
                        default=default_ratifier(args.path))
    if by is None:
        return USAGE
    # Confirmed on a terminal for the same reason ratify is (SR-0195): the act is
    # about people, and a fully specified command is how a batch is run.
    if _interactive() and not _confirm(
            f"withdraw the ratification of {', '.join(uids)} as {by}?"):
        print("not withdrawn", file=sys.stderr)
        return OK
    try:
        items = withdraw(project, uids, by=by, reason=reason,
                         by_id=getattr(args, "by_id", None))
    except IdentityError as e:
        return _err(str(e))
    except (ProjectError, GroundingError, SchemaError) as e:
        return _err(str(e))
    for item in items:
        write_item(item, project.register_of(item.uid))
        print(f"{item.uid}: ratification by {item.attrs[WITHDRAWN_RATIFIER_ATTR]} "
              f"withdrawn by {by}; now '{item.status}', awaiting ratification")
    return OK


def cmd_clarify(args) -> int:
    """Remove an item's ambiguity flag once someone judges the ambiguity resolved,
    recording who, why, and what check reported for the flag (SR-0213). Refused
    before anyone is asked for a reason or a name, as ratify refuses before it
    renders (SR-0195). It accepts nothing: a proposed item still awaits a human."""
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    uid = _resolve_uid(project, args.uid, "clarify", "UID")
    if uid is None:
        return USAGE
    refusal = clarification_refusal(project, uid)
    if refusal is not None:
        return _err(refusal)
    reason = _resolve_value(args.reason, "reason", "--reason")
    if reason is None:
        return USAGE
    by = _resolve_value(args.by, "clarifier", "--by",
                        default=default_ratifier(args.path))
    if by is None:
        return USAGE
    try:
        item = clarify(project, uid, by=by, reason=reason)
    except (ProjectError, GroundingError, SchemaError) as e:
        return _err(str(e))
    write_item(item, project.register_of(uid))
    print(f"{uid}: ambiguity flag removed by {by}")
    return OK


def cmd_flag(args) -> int:
    """Flag an item as ambiguous, recording who flagged it and why (SR-0221).
    Refused before anyone is asked for a reason or a name, as ratify refuses before
    it renders (SR-0195). It takes nothing from the item: the wording stands until
    someone rewrites it, and the flag stands until someone clarifies it."""
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    uid = _resolve_uid(project, args.uid, "flag", "UID")
    if uid is None:
        return USAGE
    refusal = flag_refusal(project, uid)
    if refusal is not None:
        return _err(refusal)
    reason = _resolve_value(args.reason, "reason", "--reason")
    if reason is None:
        return USAGE
    by = _resolve_value(args.by, "reviewer", "--by",
                        default=default_ratifier(args.path))
    if by is None:
        return USAGE
    try:
        item = flag(project, uid, by=by, reason=reason)
    except (ProjectError, GroundingError, SchemaError) as e:
        return _err(str(e))
    write_item(item, project.register_of(uid))
    print(f"{uid} flagged ambiguous by {by}")
    return OK


def cmd_status(args) -> int:
    """The generic, transition-validated status verb (SR-0132). Every status
    move a project's [transitions] table permits is reachable through this
    operation, so no state change ever needs a hand-edited YAML file. The move
    is checked against the config and refused at the source when illegal, never
    written blindly."""
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    uid = _resolve_uid(project, args.uid, "change status", "UID")
    if uid is None:
        return USAGE
    item = project.get(uid)
    if item is None:
        return _err(f"{uid} does not exist")
    statuses = sorted(project.schema.statuses) if project.schema.statuses else None
    to = _resolve_value(args.status, "status", "status", options=statuses)
    if to is None:
        return USAGE
    if not project.schema.is_status(to):
        return _err(f"'{to}' is not a declared status")
    frm = item.status
    try:
        set_status(project.schema, item, to)
    except GroundingError as e:
        return _err(str(e))
    write_item(item, project.register_of(item.uid))
    print(f"{uid}: {frm} -> {to}")
    return OK


# ------------------------------------------------------------------------ parse


def build_parser() -> argparse.ArgumentParser:
    """The command tree with this module's handlers attached. The tree itself lives
    in :mod:`throughline.parser`, which knows nothing about how a command runs
    (SR-0225); `args.func` is set here, as every front end that dispatches on it
    expects."""
    return _command_tree({name: obj for name, obj in globals().items()
                          if name.startswith("cmd_") and callable(obj)})


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:  # pragma: no cover
        return USAGE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
