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
import sys
import time
from pathlib import Path

from .dump import build_dump
from .fingerprint import fingerprint
from .graph import Index
from .grounding import (
    GroundingError,
    invalidate,
    ratify,
    reaches_root,
    set_status,
)
from .identity import (
    RATIFICATION_ATTRS,
    RATIFIED_ID_ATTR,
    IdentityError,
    default_ratifier,
)
from .inject import InjectError, has_markers, inject_text, referenced_uids
from .model import Link, Register
from .schema import SchemaError
from . import schema_ops
from .storage import (
    CONFIG_NAME,
    MANIFEST_NAME,
    ProjectError,
    baseline_statuses,
    init_project,
    load_project,
    load_project_at_ref,
    migrate_project,
    write_item,
    write_manifest,
)
from .uid import PREFIX_GRAMMAR, UidError, next_uid, parse_uid, valid_prefix
from .validate import ERROR, FilterError, eval_filter, validate
from .version import distribution_version

OK, FINDINGS, USAGE = 0, 1, 2


def _version() -> str:
    # One implementation of "what am I running?" serves the library and the CLI
    # alike (SR-0164 / SR-0076), including the +editable marker for a working tree.
    return distribution_version("throughline")


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
              + ("" if result.bound or result.declared else " — nothing to migrate"))
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
    return OK


def cmd_register_new(args) -> int:
    root = Path(args.path)
    try:
        project = load_project(root)
    except ProjectError as e:
        return _err(str(e))
    # The prefix must satisfy the UID grammar (doc 06 §3, SR-0140). A prefix that
    # does not — a single character, say — would be silently accepted here and
    # then break UID allocation for every item it owns, so we reject it up front.
    if not valid_prefix(args.prefix):
        return _err(f"prefix '{args.prefix}' is not a valid UID prefix — expected "
                    f"{PREFIX_GRAMMAR}; see doc 06 §3")
    # Prefixes own a UID namespace and must be unique across the project (SR-0101);
    # a duplicate would make the loader silently drop one register's items.
    existing = project.registers.get(args.prefix)
    if existing is not None:
        return _err(f"prefix '{args.prefix}' is already used by the register at "
                    f"{existing.path} — prefixes must be unique across the project")
    reg_dir = root / args.dir
    if (reg_dir / MANIFEST_NAME).exists():
        return _err(f"{reg_dir} already has a {MANIFEST_NAME}")
    reg_dir.mkdir(parents=True, exist_ok=True)
    reg = Register(prefix=args.prefix, title=args.title or args.prefix,
                   digits=args.digits, parent=args.parent, path=reg_dir)
    write_manifest(reg)
    print(f"created register {args.prefix} at {reg_dir}")
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


def _coerce_attr(schema, item_type: str, key: str, raw: str):
    """Coerce a ``--attr KEY=VALUE`` string to the kind the schema declares for
    the attribute (SR-0142). An undeclared attribute is stored verbatim as a
    string; a declared int/float/bool is converted so it round-trips as the right
    YAML scalar rather than a quoted string, and a declared enum is checked for
    membership (SR-0023). A value the schema cannot accept is a hard error at
    creation (fail-fast), not a surprise the loader raises later."""
    spec = schema.attr(item_type, key)
    kind = spec.kind if spec is not None else "string"
    try:
        if kind == "enum":
            if raw not in spec.values:
                raise ValueError(f"not in {list(spec.values)}")
            return raw
        if kind == "int":
            return int(raw)
        if kind == "float":
            return float(raw)
        if kind == "bool":
            low = raw.strip().lower()
            if low in ("true", "1", "yes"):
                return True
            if low in ("false", "0", "no"):
                return False
            raise ValueError(f"expected a boolean, got '{raw}'")
    except ValueError as e:
        raise UidError(f"--attr {key}={raw}: {e}") from e
    return raw


def _parse_attrs(schema, item_type: str, pairs: list[str] | None,
                 *, command: str, declared_only: bool = False) -> dict:
    """Parse repeated ``--attr KEY=VALUE`` options into a coerced attrs dict.

    ``command`` names the verb doing the setting, so a refusal can say which command
    owns an attribute it will not write. ``declared_only`` rejects an attribute the
    item's type does not declare instead of storing it verbatim — what `amend`
    requires (SR-0144) and what creation deliberately does not, since an attribute
    an evolving schema has not caught up with is a reasonable thing to author."""
    attrs: dict = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise UidError(f"--attr expects KEY=VALUE, got '{pair}'")
        key, raw = pair.split("=", 1)
        key = key.strip()
        if not key:
            raise UidError(f"--attr expects a non-empty key, got '{pair}'")
        # The ratification record is evidence that a named person took
        # accountability, and evidence is worth what it costs to forge. No verb but
        # the one that owns it may write it (SR-0170).
        owner = RATIFICATION_ATTRS.get(key)
        if owner is not None:
            raise UidError(
                f"--attr {key}: '{key}' is part of the ratification record and "
                f"cannot be set by `tl {command}` — `tl {owner}` owns it")
        if declared_only and schema.attr(item_type, key) is None:
            raise UidError(
                f"--attr {key}: '{item_type}' declares no attribute '{key}'")
        attrs[key] = _coerce_attr(schema, item_type, key, raw)
    return attrs


def cmd_new(args) -> int:
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    reg = project.registers.get(args.prefix)
    if reg is None:
        return _err(f"no register with prefix '{args.prefix}' (run `tl register new`)")
    if args.uid:
        try:
            pfx, _ = parse_uid(args.uid)
        except UidError as e:
            return _err(str(e))
        if pfx != args.prefix:
            return _err(f"--uid {args.uid} does not match prefix {args.prefix}")
        if project.get(args.uid) is not None:
            return _err(f"{args.uid} already exists")
        uid = args.uid
    else:
        uid = next_uid(reg)
    from .model import Item
    schema = project.schema
    try:
        attrs = _parse_attrs(schema, args.type, args.attr, command="new")
    except UidError as e:
        return _err(str(e))
    # --origin is the canonical way to set provenance, but honour origin given via
    # --attr too so birth status stays consistent with what actually lands on the
    # item.
    origin = args.origin or attrs.get("origin")
    # Birth status comes from the project's status roles, never a value fixed in
    # code (SR-0131); --status overrides it explicitly. A machine-origin item is
    # born 'proposed' — not 'initial' — so the ratification gate (SR-0092)
    # actually engages and a named human must ratify it before it counts; without
    # this a machine-authored item would enter the ordinary initial status and
    # silently escape the gate the tool exists to enforce (SR-0141). If the
    # project declares no 'proposed' role we fall back to 'initial'.
    has_proposed_role = bool((schema.status_roles or {}).get("proposed"))
    if args.status is not None:
        status = args.status
    elif origin in schema.ai_origins and has_proposed_role:
        status = schema.status_role("proposed")
    else:
        status = schema.status_role("initial")
    item = Item(uid=uid, type=args.type, status=status,
                title=args.title or "", text=args.text or "")
    item.attrs.update(attrs)
    if args.origin:
        item.attrs["origin"] = args.origin
    # Apply schema-declared attribute defaults (SR-0138): a default only ever
    # lands at birth on an attribute the author did not set, so a schema sentinel
    # (e.g. a priority meaning "no human has decided yet") appears automatically
    # without overwriting an explicit value.
    for name, spec in schema.attrs_for(args.type).items():
        if spec.default is not None and name not in item.attrs:
            item.attrs[name] = spec.default
    item._register_prefix = reg.prefix

    # Grounding-assisted authoring (SR-0073): attach a parent at birth so the
    # item is justified the moment it exists, rather than being created orphaned
    # and only caught later by `check`. Roots are exempt — they *are* the 'why'.
    default_type = args.ground_type or "derives_from"
    grounds: list[tuple[str, str]] = []
    if args.ground:
        # Explicit grounding is ALWAYS honored — even for root types (e.g. a
        # business_need that `derives_from` the vision). An explicitly requested
        # link is authoring intent and must never be silently discarded; if it
        # cannot be added we fail loudly, we do not drop it (SR-0091, fail-fast).
        for target in args.ground:
            dst = project.get(target)
            if dst is None:
                return _err(f"grounding target {target} does not exist")
            grounds.append((target, default_type))
    elif not schema.is_root(item) and not args.no_interactive \
            and sys.stdin.isatty() and sys.stdout.isatty():
        # No explicit parent: offer to attach one for non-roots only. Roots are
        # exempt from the prompt because they *are* the 'why'.
        grounds = _prompt_grounding(project, schema, item, default_type)

    for target, ltype in grounds:
        item.links.append(Link(target=target, type=ltype))

    reg.items[uid] = item
    path = write_item(item, reg)
    print(f"created {uid} -> {path}")
    for target, ltype in grounds:
        print(f"  grounded: {uid} --{ltype}--> {target}")
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
    stamp = fingerprint(dst, project.schema) if args.stamp else None
    if getattr(args, "retype", False):
        # Retype changes an existing edge in place rather than adding a parallel
        # one (SR-0143), so the semantic-link review the tool is built for — e.g.
        # narrowing a mitigates to a relates — needs no hand-editing.
        existing = [ln for ln in src.links if ln.target == dst_uid]
        if not existing:
            return _err(f"no existing link {src_uid} -> {dst_uid} to retype "
                        f"(drop --retype to add a new one)")
        present_types = {ln.type for ln in existing}
        if len(present_types) > 1:
            return _err(f"multiple link types {src_uid} -> {dst_uid} "
                        f"({', '.join(sorted(present_types))}); remove the "
                        f"unwanted one with `tl unlink` first")
        old_type = existing[0].type
        for ln in existing:
            ln.type = ltype
            if args.stamp:
                ln.stamp = stamp
        write_item(src, project.register_of(src.uid))
        print(f"retyped {src_uid} {dst_uid}: --{old_type}--> is now --{ltype}-->"
              + (" (stamped)" if args.stamp else ""))
        return OK
    src.links.append(Link(target=dst_uid, type=ltype, stamp=stamp))
    write_item(src, project.register_of(src.uid))
    print(f"linked {src_uid} --{ltype}--> {dst_uid}"
          + (" (stamped)" if stamp else ""))
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
    src = project.get(src_uid)
    if src is None:
        return _err(f"source {src_uid} does not exist")

    def _matches(ln) -> bool:
        return ln.target == dst_uid and (args.type is None or ln.type == args.type)

    matched = [ln for ln in src.links if _matches(ln)]
    if not matched:
        what = f" of type '{args.type}'" if args.type else ""
        return _err(f"no link {src_uid} -> {dst_uid}{what} to remove")
    present_types = {ln.type for ln in matched}
    if args.type is None and len(present_types) > 1:
        return _err(f"multiple link types {src_uid} -> {dst_uid} "
                    f"({', '.join(sorted(present_types))}); pass --type to "
                    f"choose which to remove")
    src.links = [ln for ln in src.links if not _matches(ln)]
    write_item(src, project.register_of(src.uid))
    for ltype in sorted(present_types):
        print(f"unlinked {src_uid} --{ltype}--> {dst_uid}")
    return OK


def cmd_delete(args) -> int:
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    uid = _resolve_uid(project, args.uid, "delete", "UID")
    if uid is None:
        return USAGE
    item = project.get(uid)
    if item is None:
        return _err(f"{uid} does not exist")
    try:
        set_status(project.schema, item, project.schema.status_role("tombstone"))
    except (GroundingError, SchemaError) as e:
        return _err(str(e))
    item.deleted = {"reason": args.reason or "unspecified"}
    write_item(item, project.register_of(item.uid))
    print(f"tombstoned {uid} (UID retired, never reused)")
    return OK


def cmd_review(args) -> int:
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    if args.all_clean:
        targets = list(project.items())
    else:
        uid = _resolve_uid(project, args.uid, "mark reviewed", "UID")
        if uid is None:
            return USAGE
        targets = [project.get(uid)]
        if targets[0] is None:
            return _err(f"{uid} does not exist")
    n = 0
    for item in targets:
        if item is None or item.is_deleted:
            continue
        fp = fingerprint(item, project.schema)
        if item.reviewed != fp:
            item.reviewed = fp
            write_item(item, project.register_of(item.uid))
            n += 1
    print(f"marked {n} item(s) reviewed at current content")
    return OK


def _newly_suspect(project, uid: str, was: str, now: str) -> list[str]:
    """Dependents whose confirmed link to ``uid`` this content change has just
    invalidated (SR-0034, SR-0169).

    A link carries the target's fingerprint as at the last confirmation, so what
    makes a dependent *newly* suspect is a stamp that matched the old content and
    does not match the new. A stamp that already disagreed was suspect before this
    change and is not this change's doing; an unstamped link was never confirmed
    and so has nothing to lose."""
    if was == now:
        return []
    out = []
    for it in project.items():
        if it.is_deleted:
            continue
        if any(l.target == uid and l.stamp == was for l in it.links):
            out.append(it.uid)
    return sorted(out)


def cmd_amend(args) -> int:
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
    if item.is_deleted:
        return _err(f"{uid} is deleted — a tombstone is permanent (SR-0093)")
    # Amending nothing is a mistake worth naming. Succeeding silently would let a
    # typo in an option name read as a change that was made.
    if args.title is None and args.text is None and args.rationale is None \
            and not args.attr:
        return _err("amend needs at least one of --title, --text, --rationale "
                    "or --attr")
    schema = project.schema
    try:
        attrs = _parse_attrs(schema, item.type, args.attr,
                             command="amend", declared_only=True)
    except UidError as e:
        return _err(str(e))

    before = fingerprint(item, schema)
    was_reviewed = item.reviewed is not None
    changed: list[str] = []
    # None means "option not given"; an empty string is a real value that clears the
    # field, which is the only way to withdraw a rationale without opening the YAML.
    if args.title is not None and args.title != item.title:
        item.title = args.title
        changed.append("title")
    if args.text is not None and args.text != item.text:
        item.text = args.text
        changed.append("text")
    if args.rationale is not None and args.rationale != item.rationale:
        item.rationale = args.rationale
        changed.append("rationale")
    for key, value in attrs.items():
        if item.attrs.get(key) != value:
            item.attrs[key] = value
            changed.append(key)
    if not changed:
        print(f"{uid} already says that — nothing changed")
        return OK

    now = fingerprint(item, schema)
    suspects = _newly_suspect(project, uid, before, now)
    # A review confirms content, so content that has moved is no longer confirmed
    # (SR-0038, SR-0144). Only a normative change can invalidate it — retitling
    # leaves the fingerprint alone, and clearing a review it did not disturb would
    # cost the author a re-review for nothing.
    review_cleared = was_reviewed and now != before
    if review_cleared:
        item.reviewed = None
    write_item(item, project.register_of(uid))

    # What the change cost, reported and not asked about (SR-0169). The gate stays
    # where it already stands — `check`, and the re-ratification that shows what
    # moved before it asks for a signature.
    print(f"amended {uid} — {', '.join(changed)}")
    if now == before:
        print("  normative content unchanged — nothing was made suspect")
    else:
        if suspects:
            print(f"  {len(suspects)} dependent item(s) now suspect: "
                  f"{', '.join(suspects)}")
        else:
            print("  no dependent item was confirmed against the old content")
        if review_cleared:
            print("  review record cleared — `tl review` to confirm the new wording")
        stamp = item.attrs.get("ratified_fingerprint")
        if stamp and stamp != now:
            who = item.attrs.get("ratified_by") or "a human"
            print(f"  ratification by {who} no longer matches this content — "
                  f"`tl ratify {uid}` shows what moved and asks again")
    return OK


def _by_count(pairs) -> str:
    """'a 3 · b 1' from an iterable of names, most-frequent first."""
    from collections import Counter
    c = Counter(pairs)
    return " · ".join(f"{name} {n}" for name, n in c.most_common())


def _check_summary(project) -> list[str]:
    """Human-readable picture of what check validated (SR-0078): what is in the
    graph, how it is linked, and whether every requirement traces to a root."""
    schema = project.schema
    idx = Index.build(project)
    live = [it for it in project.items() if not it.is_deleted]

    links = [l.type for it in live for l in it.links]
    non_roots = [it for it in live if not schema.is_root(it)]
    grounded = sum(1 for it in non_roots if reaches_root(idx, schema, it.uid))
    delivery = [it for it in live if it.type in schema.delivery_roots]
    served = sum(
        1 for it in delivery
        if any(lt in schema.ground_link_types for _o, lt in idx.in_links(it.uid))
    )

    name = schema.name or Path(project.path).name
    lines = [
        "",
        f"tl check · {name}",
        f"  Items      {len(live)} live   {_by_count(it.type for it in live)}",
        f"  Status     {_by_count(it.status for it in live)}",
        f"  Links      {len(links)}        {_by_count(links)}",
        f"  Grounding  {grounded}/{len(non_roots)} non-root items trace to a "
        f"root · {served}/{len(delivery)} delivery roots served",
    ]
    return lines


def cmd_check(args) -> int:
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    baseline = None
    if project.schema.transitions is not None and args.base:
        baseline = baseline_statuses(project, args.base)
    published = referenced_uids(project)  # None unless [docs] paths configured
    findings = validate(project, strict=args.strict, baseline=baseline,
                        published=published)
    if args.format == "json":
        print(json.dumps([f.to_dict() for f in findings], indent=2))
        return FINDINGS if any(f.severity == ERROR for f in findings) else OK

    for f in sorted(findings, key=lambda x: (x.severity != ERROR, x.uid)):
        print(f)
    sys.stdout.flush()
    errs = sum(1 for f in findings if f.severity == ERROR)
    warns = len(findings) - errs
    if not args.quiet:
        for line in _check_summary(project):
            print(line, file=sys.stderr)
    tally = f"\n{errs} error(s), {warns} warning(s)"
    if not args.quiet and errs == 0:
        tally += "  — graph is sound" + (" (strict)" if args.strict else "")
    print(tally, file=sys.stderr)
    return FINDINGS if any(f.severity == ERROR for f in findings) else OK


def cmd_query(args) -> int:
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    candidates = [it for it in project.items()
                  if args.all or not it.is_deleted]
    idx = Index.build(project)
    try:
        matched = [it for it in candidates if eval_filter(it, args.expr, idx)]
    except FilterError as e:
        return _err(f"bad filter expression: {e}")
    matched.sort(key=lambda it: it.uid)

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


def _mermaid_types(idx) -> str | None:
    """A Mermaid flowchart of the type model: item types as nodes, one labelled
    edge per (source type, link type, target type) observed in the graph. Edges
    to external/unknown targets are omitted (SR-0086)."""
    edges = sorted({(s, lt, t) for (s, lt, t) in idx.link_shape() if t is not None})
    if not edges:
        return None
    lines = ["flowchart LR"]
    lines += [f"    {s} -->|{lt}| {t}" for s, lt, t in edges]
    return "\n".join(lines)


def _mermaid_transitions(schema) -> str | None:
    """A Mermaid state diagram of the declared status lifecycle (SR-0086).
    ``None`` when the project declares no [transitions]."""
    if not schema.transitions:
        return None
    lines = ["stateDiagram-v2"]
    for frm in sorted(schema.transitions):
        for to in sorted(schema.transitions[frm]):
            lines.append(f"    {frm} --> {to}")
    return "\n".join(lines)


def cmd_diagram(args) -> int:
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    idx = Index.build(project)
    blocks = []  # (heading, mermaid-source-or-None, empty-note)
    if args.kind in ("types", "both"):
        blocks.append(("Type model", _mermaid_types(idx),
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
    """Inject the configured documents from the local project. ``resolver`` is an
    optional target resolver (SR-0110); when a composing front end supplies one,
    tl:matrix target cells resolve attributes and liveness through it — e.g. over
    tl-compose's union graph — instead of the local project alone."""
    try:
        if args.at:
            project, _sha = load_project_at_ref(args.path, args.at)
        else:
            project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))

    paths = _resolve_doc_paths(project, args.file)
    if not paths:
        # In --check mode (the CI gate, SR-0095) an unconfigured project has no
        # documents to be stale, so the gate is inert and passes. In write mode a
        # caller who ran `tl docs` with nothing to inject wants to know.
        if args.check:
            return OK
        return _err("no documents to inject — pass a Markdown file, or configure "
                    "[docs] paths in throughline.toml")

    stale: list[Path] = []
    changed = 0
    for path in paths:
        try:
            original = path.read_text(encoding="utf-8")
        except OSError as e:
            return _err(f"cannot read {path}: {e}")
        if not has_markers(original):
            continue  # SR-0094/0095: a file with no tl: markers is left untouched
        try:
            rendered = inject_text(project, original, resolver=resolver)
        except InjectError as e:
            return _err(f"{path}: {e}")
        if rendered == original:
            continue
        if args.check:
            stale.append(path)
            print(f"stale: {path}", file=sys.stderr)
        else:
            path.write_text(rendered, encoding="utf-8")
            changed += 1
            print(f"injected {path}")

    if args.check:
        # Separate gate: write-then-diff. A drifted document fails CI; nothing is
        # rewritten. This is deliberately NOT part of `tl check` so routine checks
        # stay friction-free (SR-0095).
        if stale:
            print(f"{len(stale)} document(s) out of date — run `tl docs` to "
                  "regenerate", file=sys.stderr)
            return FINDINGS
        print("documents up to date")
        return OK
    if changed == 0:
        print("documents already up to date")
    sys.stdout.flush()
    return OK


# --------------------------------------------------------------- agent context

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
               "(unmet → a `coverage` finding):\n")
    for rule in schema.coverage:
        filt = rule.get("filter", "*")
        needs = rule.get("needs", "?")
        sev = rule.get("severity", "error")
        out.append(f"- items where `{filt}` **need** `{needs}` "
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
    "normative: true              # content changes mark dependents suspect\n"
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
    "amend": "tl amend <UID> [--title …] [--text …] [--rationale …] [--attr K=V]",
    "link": "tl link <SRC> <DST> --type <kind>",
    "unlink": "tl unlink <SRC> <DST> [--type <kind>]",
    "check": "tl check [--strict] [--format json]",
    "ratify": "tl ratify <UID> --by <who> [--by-id <scheme:value>]",
    "trace": "tl trace <UID> [--direction in|out]",
    "blast": "tl blast <UID>",
    "shape": "tl shape [--format json]",
    "dump": "tl dump [-o FILE]",
    "diagram": "tl diagram [types|transitions|both]",
    "docs": "tl docs [FILE ...] [--at REF] [--check]",
    "status": "tl status <UID> <STATUS>",
    "invalidate": "tl invalidate <UID> [--reason …]",
    "delete": "tl delete <UID>",
    "query": "tl query [--type T] [--status S] [--format json]",
    "register": "tl register new <PREFIX> <FOLDER> --title <…>",
    "schema": "tl schema <noun> <verb> … --because <why>",
    "migrate": "tl migrate",
    "review": "tl review",
    "init": "tl init [--demo]",
    "context": "tl context",
}

# Commands whose importance is not evident from a one-line help string, and which
# an agent that had read only the rest of the brief would be surprised by.
_CTX_COMMAND_EMPHASIS = {
    "check": "THE GATE — run before committing",
    "ratify": "a human accepts a proposed item; never run this for a human",
    "migrate": "idempotent repairs; extend this, never a script beside it",
    "invalidate": "retires an item and cascades suspicion — see grounding, below",
    "delete": "tombstones an item; the file stays, the item stops counting",
    "amend": "change content through the tool, never by opening the YAML",
    "schema": "change the schema itself — nouns: status, transition, type, attr, "
              "linktype, linkrule, grounding; refuses a change that would "
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


def _context_markdown(project) -> str:
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


def cmd_context(args) -> int:
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    sys.stdout.write(_context_markdown(project))
    sys.stdout.flush()
    return OK


def render_trace(project, start: str, *, direction: str = "out", max_depth: int = 0,
                 uid_display=None, expand=None, emit=print) -> None:
    """Print the link tree rooted at ``start`` as an indented ASCII tree.

    The single walk both ``tl trace`` and ``tl-compose trace`` share, so the tree
    glyphs cannot drift between the two front ends. Two seams let a composing
    caller specialise the view without touching the walk:

    - ``uid_display(uid) -> str`` renders the label shown for a node's UID
      (default: the UID itself); tl-compose passes the namespace-qualified form so
      a borrowed clause reads ``asvs:SR-0272`` rather than its mangled union UID.
    - ``expand(uid) -> bool`` decides whether to recurse into a node's own links
      (default: always). tl-compose stops at the source boundary by expanding only
      consumer-local items, so a borrowed clause is shown but its source-internal
      graph is not dragged in.

    ``max_depth`` of 0 means unlimited. ``emit`` is the sink (default ``print``).
    """
    show = uid_display or (lambda u: u)
    recurse = expand or (lambda u: True)
    idx = Index.build(project)
    seen: set[str] = set()

    def walk(uid: str, depth: int, prefix: str, connector: str, ltype: str | None) -> None:
        line = f"{prefix}{connector}"
        edge = f"({ltype}) " if ltype else ""
        disp = show(uid)
        if uid in seen or (max_depth and depth > max_depth):
            marker = " (cycle)" if uid in seen else ""
            emit(f"{line}{edge}{disp}{marker}")
            return
        seen.add(uid)
        item = project.get(uid)
        if item is None:
            # A link target that is not a local item: a dangling reference, or a
            # namespace-qualified reference to another source (resolved only under
            # tl-compose). Show it as a leaf rather than crashing the walk.
            emit(f"{line}{edge}{disp} (unresolved)")
            return
        label = f"{disp}  [{item.type}/{item.status}] {item.title}".rstrip()
        emit(f"{line}{edge}{label}")
        # Continuation guide for this node's subtree: a vertical bar while more
        # siblings follow ("├─"), blank once this was the last child ("└─") or root.
        child_prefix = prefix + ("│ " if connector == "├─" else "  " if connector else "")
        if not recurse(uid):
            return
        edges = (idx.in_links(uid) if direction == "in" else idx.out_links(uid))
        for i, (other, lt) in enumerate(edges):
            last = i == len(edges) - 1
            walk(other, depth + 1, child_prefix, "└─" if last else "├─", lt)

    walk(start, 0, "", "", None)


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


def cmd_ratify(args) -> int:
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    # A proposed item is the usual target, but ratify tolerates any item (the
    # grounding layer decides what it means), so the picker lists all live items.
    uid = _resolve_uid(project, args.uid, "ratify", "UID")
    if uid is None:
        return USAGE
    # Offer the identity this repository already signs commits with (SR-0156). It
    # is only ever a default: _resolve_value shows it and takes it on assent, and a
    # non-interactive session that names no ratifier is refused, not signed for.
    by = _resolve_value(args.by, "ratifier", "--by",
                        default=default_ratifier(args.path))
    if by is None:
        return USAGE
    try:
        item = ratify(project, uid, by=by, by_id=getattr(args, "by_id", None))
    except IdentityError as e:
        return _err(str(e))
    except (ProjectError, GroundingError, SchemaError) as e:
        return _err(str(e))
    write_item(item, project.register_of(item.uid))
    identifier = item.attrs.get(RATIFIED_ID_ATTR)
    print(f"{uid} ratified by {by}" + (f" ({identifier})" if identifier else ""))
    return OK


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

def _add_schema_parser(sub) -> None:
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
        v.set_defaults(func=cmd_schema, builder=builder)
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
                     lambda p, a: schema_ops.type_add(p, a.name),
                     "declare a new item type")
    v.add_argument("name")
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tl", description=__doc__.splitlines()[0])
    p.add_argument("--version", action="version", version=f"tl {_version()}")
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
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("migrate",
                       help="upgrade an older project's on-disk format to this tl")
    s.set_defaults(func=cmd_migrate)

    s = sub.add_parser("register", help="register (prefix-owning collection) operations")
    dsub = s.add_subparsers(dest="register_cmd", required=True)
    d = dsub.add_parser("new", help="create a register/manifest")
    d.add_argument("prefix")
    d.add_argument("dir", help="directory (relative to project root)")
    d.add_argument("--title", default="")
    d.add_argument("--digits", type=int, default=4)
    d.add_argument("--parent", default=None)
    d.set_defaults(func=cmd_register_new)

    _add_schema_parser(sub)

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
    s.set_defaults(func=cmd_new)

    s = sub.add_parser("link", help="add a typed link SRC -> DST")
    s.add_argument("src", nargs="?", default=None,
                   help="source UID (omit on a terminal to pick one)")
    s.add_argument("dst", nargs="?", default=None,
                   help="destination UID (omit on a terminal to pick one)")
    s.add_argument("--type", default=None,
                   help="link type (omit on a terminal to choose one)")
    s.add_argument("--stamp", action="store_true",
                   help="record target fingerprint (suspect tracking)")
    s.add_argument("--retype", action="store_true",
                   help="change the type of the existing SRC -> DST link in "
                        "place instead of adding a new one")
    s.set_defaults(func=cmd_link)

    s = sub.add_parser("unlink", help="remove a typed link SRC -> DST")
    s.add_argument("src", nargs="?", default=None,
                   help="source UID (omit on a terminal to pick one)")
    s.add_argument("dst", nargs="?", default=None,
                   help="destination UID (omit on a terminal to pick one)")
    s.add_argument("--type", default=None,
                   help="only remove the link of this type (required when "
                        "several types link the same pair)")
    s.set_defaults(func=cmd_unlink)

    s = sub.add_parser("delete", help="tombstone an item (never erased)")
    s.add_argument("uid", nargs="?", default=None,
                   help="UID to tombstone (omit on a terminal to pick one)")
    s.add_argument("--reason", default="")
    s.set_defaults(func=cmd_delete)

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
    s.set_defaults(func=cmd_amend)

    s = sub.add_parser("review", help="mark item(s) reviewed at current content")
    s.add_argument("uid", nargs="?", default=None)
    s.add_argument("--all-clean", action="store_true")
    s.set_defaults(func=cmd_review)

    s = sub.add_parser("check", help="validate the graph (CI gate)")
    s.add_argument("--strict", action="store_true", help="warnings become errors")
    s.add_argument("--base", default="HEAD", metavar="REF",
                   help="git ref for the status-transition baseline "
                        "(default HEAD; empty to disable)")
    s.add_argument("--format", choices=["text", "json"], default="text")
    s.add_argument("--quiet", "-q", action="store_true",
                   help="suppress the graph summary (findings + tally only)")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("query", aliases=["ls"],
                       help="list items matching a filter expression")
    s.add_argument("expr", nargs="?", default="",
                   help="SR-0045 filter, e.g. \"status == 'draft'\" or "
                        "\"type == 'system_requirement' and "
                        "attrs.get('priority') == 'must'\"; omit to list all")
    s.add_argument("--format", choices=["text", "json"], default="text")
    s.add_argument("--all", action="store_true",
                   help="include deleted (tombstoned) items")
    s.set_defaults(func=cmd_query)

    s = sub.add_parser("shape",
                       help="report the graph's (from)-[link]->(to) type shape")
    s.add_argument("--format", choices=["text", "json"], default="text")
    s.set_defaults(func=cmd_shape)

    s = sub.add_parser(
        "dump",
        help="export the whole project as one documented JSON structure "
             "(SR-0055) — the sanctioned interchange surface")
    s.add_argument("-o", "--output", default=None, metavar="FILE",
                   help="write to FILE (default: stdout)")
    s.set_defaults(func=cmd_dump)

    s = sub.add_parser("diagram",
                       help="emit Mermaid diagrams of the type model and status lifecycle")
    s.add_argument("kind", nargs="?", choices=["types", "transitions", "both"],
                   default="both")
    s.add_argument("--format", choices=["markdown", "mermaid"], default="markdown")
    s.set_defaults(func=cmd_diagram)

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
    s.set_defaults(func=cmd_docs)

    s = sub.add_parser(
        "context",
        help="emit an agent-facing Markdown brief (IDD + this project's model)")
    s.set_defaults(func=cmd_context)

    s = sub.add_parser("trace", help="print the link tree from a UID")
    s.add_argument("uid", nargs="?", default=None,
                   help="UID to trace (omit on a terminal to pick one)")
    s.add_argument("--direction", choices=["in", "out"], default="out")
    s.add_argument("--depth", type=int, default=0, help="0 = unbounded")
    s.set_defaults(func=cmd_trace)

    s = sub.add_parser("blast", help="show the blast radius (dependents) of a UID")
    s.add_argument("uid", nargs="?", default=None,
                   help="UID to inspect (omit on a terminal to pick one)")
    s.add_argument("--format", choices=["text", "json"], default="text")
    s.set_defaults(func=cmd_blast)

    s = sub.add_parser("ratify", help="a human takes accountability for an item")
    s.add_argument("uid", nargs="?", default=None,
                   help="UID to ratify (omit on a terminal to pick one)")
    s.add_argument("--by", default=None,
                   help="ratifier name (omit on a terminal to be prompted; "
                        "defaults to the identity this repository signs with)")
    s.add_argument("--by-id", default=None, metavar="SCHEME:VALUE",
                   help="optional stable identifier for that human, e.g. "
                        "github:octocat or email:ada@example.com")
    s.set_defaults(func=cmd_ratify)

    s = sub.add_parser("invalidate", help="falsify an item; cascade suspect")
    s.add_argument("uid", nargs="?", default=None,
                   help="UID to invalidate (omit on a terminal to pick one)")
    s.add_argument("--reason", default="")
    s.set_defaults(func=cmd_invalidate)

    s = sub.add_parser("status",
                       help="move an item to a status (transition-validated)")
    s.add_argument("uid", nargs="?", default=None,
                   help="UID to change (omit on a terminal to pick one)")
    s.add_argument("status", nargs="?", default=None,
                   help="target status (omit on a terminal to pick one)")
    s.set_defaults(func=cmd_status)

    return p


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
