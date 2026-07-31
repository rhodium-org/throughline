# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Parser/Writer — the only module that touches disk (arch doc 07 §2).

Loads a project (throughline.toml + per-register .register.yml + one <UID>.yml per
item) into the pure model, and writes items deterministically: stable key
order, LF endings, UTF-8, final newline, no timestamp churn (SR-0072). Unknown
keys survive read-modify-write (NFR-0009).
"""
from __future__ import annotations

import copy
import io
import os
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import yaml

from .fingerprint import fingerprint
from .graph import Index
from .grounding import ratification_refusal
from .model import Item, Link, Project, Register
from .schema import SchemaError

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

CONFIG_NAME = "throughline.toml"
MANIFEST_NAME = ".register.yml"

# The on-disk format major this build of the Tool reads and writes (NFR-0010).
# Bump only for a breaking structural change; additive (minor) evolution keeps
# the same major so older projects of that major stay readable without migration.
# History: v1 named a register manifest `.document.yml`; v2 (the register rename,
# SR-0102) renamed it to `.register.yml`; v3 (SR-0131) requires a [status.roles]
# table so operations resolve statuses by semantic role, never by a fixed literal.
FORMAT_VERSION = 3

# The major from which a project must declare [status.roles] — the one whose
# operations resolve a written status through a semantic role (SR-0131). Named
# rather than inlined because both the repair and the gate that reports a missing
# table (SR-0136) are anchored to it.
STATUS_ROLES_MAJOR = 3

# Manifest filename per format major — the sole structural marker that changed
# between v1 and v2, so it doubles as the discriminator when a hand-authored
# config omits format_version (see _infer_format_version).
_MANIFEST_BY_VERSION = {1: ".document.yml", 2: ".register.yml"}


class ProjectError(Exception):
    pass


def _infer_format_version(root: Path) -> int:
    """Guess the format major of a config that omits format_version, by content.

    A missing field means a hand-authored or pre-versioning project. Rather than
    blindly assume the current major — which would silently load a v1 tree as an
    empty v2 graph, since the v2 loader rglobs for `.register.yml` and never sees
    the v1 `.document.yml` — we read the layout on disk. A `.register.yml` present
    means v2; only `.document.yml` present means v1; neither (a bare/empty project)
    assumes the current major. This is the content inference that lets an
    unversioned old project still be routed to `tl migrate` (NFR-0010, UR-0015).
    """
    if next(root.rglob(_MANIFEST_BY_VERSION[2]), None) is not None:
        return 2
    if next(root.rglob(_MANIFEST_BY_VERSION[1]), None) is not None:
        return 1
    return FORMAT_VERSION


def _read_format_version(config: dict, root: Path) -> int:
    """The recorded format major, or one inferred from content when absent.

    A present value is authoritative and must be an integer major. When absent,
    fall back to inferring the major from the on-disk layout (UR-0015).
    """
    project = config.get("project", {})
    if "format_version" not in project:
        return _infer_format_version(root)
    raw = project["format_version"]
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ProjectError(
            f"project.format_version must be an integer major, got {raw!r}")
    return raw


def _gate_format_version(config: dict, root: Path) -> None:
    """Refuse to load a project whose format major differs from ours (NFR-0010).

    Newer than we understand -> tell the user to upgrade the Tool rather than
    mis-parse a format from the future. Older than ours -> point at `tl migrate`
    rather than silently rewrite. An equal major reads transparently.
    """
    disk = _read_format_version(config, root)
    if disk > FORMAT_VERSION:
        raise ProjectError(
            f"{root / CONFIG_NAME} declares format version {disk}, but this tl "
            f"understands up to {FORMAT_VERSION} — upgrade tl to open this project")
    if disk < FORMAT_VERSION:
        raise ProjectError(
            f"{root / CONFIG_NAME} is at format version {disk}; this tl uses "
            f"{FORMAT_VERSION} — run `tl migrate` to upgrade the project "
            f"(a lossless, in-place rewrite)")


def _migrate_1_to_2(root: Path) -> None:
    """Upgrade a v1 tree to v2 by renaming every register manifest from the old
    `.document.yml` to `.register.yml` (SR-0102). Only the filename changed — the
    manifest's keys are identical across the two majors — so the rename is the
    whole migration and it preserves every item untouched."""
    old, new = _MANIFEST_BY_VERSION[1], _MANIFEST_BY_VERSION[2]
    for manifest in sorted(root.rglob(old)):
        target = manifest.with_name(new)
        if target.exists():
            raise ProjectError(
                f"cannot migrate {manifest}: {target} already exists")
        manifest.rename(target)


# The default role -> status binding a v2 project is given when it is upgraded to
# v3 (SR-0131). These are exactly the status literals the pre-v3 operations had
# baked in, so the backfill preserves behaviour: what `ratify` wrote, what the
# invalidate cascade treated as dead, and so on. Literals are legitimate here —
# this is config *generation*, not operation code reading a status by value.
_DEFAULT_STATUS_ROLES = {
    "initial": "draft",
    "proposed": "proposed",
    "ratified": "ratified",
    "invalidated": "rejected",
    "suspect": "suspect",
    "tombstone": "deleted",
}


def _backfill_status_roles(root: Path) -> dict[str, str] | None:
    """Bind each semantic role to one of the project's statuses (SR-0131), writing
    a [status.roles] table. Returns the bindings written, or ``None`` when the
    project already declares the table and was left untouched.

    v3 operations resolve every status they write through a semantic role, so a
    project must bind each role to one of its statuses. A pre-v3 project has no
    such table; we add one using the same status literals the old code hardcoded,
    which keeps behaviour identical. A role is bound only when its target status
    is among the project's declared [status] values (an unconstrained project
    takes the whole default map), so the backfill never references a status the
    project does not know — it would fail the schema consistency check — and never
    guesses a binding for a vocabulary that has no counterpart for a role.

    Presence of the table, not its contents, is what marks the project as done
    (SR-0137): a project that declares the table and deliberately binds nothing
    has made a choice, and re-appending would emit a duplicate TOML table."""
    cfg_file = root / CONFIG_NAME
    config = tomllib.loads(cfg_file.read_text(encoding="utf-8"))
    status_cfg = config.get("status") or {}
    if status_cfg.get("roles") is not None:
        return None
    declared = status_cfg.get("values")
    roles = {
        role: status
        for role, status in _DEFAULT_STATUS_ROLES.items()
        if not declared or status in declared
    }
    _append_status_roles(cfg_file, roles)
    return roles


def _migrate_2_to_3(root: Path) -> None:
    """Upgrade a v2 project to v3 (SR-0131) — the backfill is the whole step."""
    _backfill_status_roles(root)


def _append_status_roles(cfg_file: Path, roles: dict[str, str]) -> None:
    """Append a [status.roles] table to the config, preserving existing content,
    comments and key order (NFR-0009) — a targeted append, not a reserialize."""
    text = cfg_file.read_text(encoding="utf-8").rstrip("\n")
    lines = [text, "", "# Semantic status roles (SR-0131): which declared status",
             "# plays each role the tool's operations act on. Backfilled by",
             "# `tl migrate`; edit to match your own status vocabulary."]
    if not roles:
        # No declared status matches any default, so there is nothing honest to
        # bind. The empty table is the record of that — it says "considered", so
        # the repair does not run again and the gate does not nag (SR-0136/0137).
        lines += ["# No declared status matched a role, so nothing is bound: fill",
                  "# these in yourself, and leave a role out when this project's",
                  "# vocabulary has no status that honestly plays it."]
    lines.append("[status.roles]")
    lines += [f'{role} = "{status}"' for role, status in roles.items()]
    cfg_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _backfill_ratification_stamps(root: Path, *,
                                  index: Index | None = None) -> dict[str, str]:
    """Bind a ratification record that names a ratifier but carries no
    fingerprint (SR-0152). Returns ``uid -> fingerprint`` for every record bound.

    The stamp tying a signature to the content signed (SR-0148) arrived after most
    graphs were already written, so an item ratified before it proves *who*
    accepted the item but not *what* they accepted, and the drift finding stays
    silent over it forever. This heals that backlog through the one command that
    already repairs the rest of the major's configuration, so a consumer gets it
    by upgrading rather than by knowing some script exists.

    Three things it will not do:

    * **Reattribute.** The ratifier already recorded on the item is reused
      verbatim and no substitute is accepted, so the repair can only *complete* an
      accountability record, never author one or move it to someone else.
    * **Sign the unsignable.** Legitimacy is decided by
      :func:`~throughline.grounding.ratification_refusal` — the same predicate
      ``ratify`` refuses on — so an ambiguous or ungrounded item is passed over.
    * **Move an item.** No status is written. An item whose status has since moved
      on past ratification keeps both its record and its current state; the
      fingerprint covers normative content only, which the move did not touch.

    Every record it writes is marked ``ratified_backfilled``, and that marking is
    what keeps the repair honest: the Tool can fingerprint only the content as it
    stands the day migration runs, and cannot know that is the content the ratifier
    read. A stamp written at sign-off attests to words a human saw; this one
    attests to what was on disk. Were the text to have drifted in between, an
    unmarked backfill would quietly bless the drift — precisely the failure the
    stamp exists to catch — so the two must stay distinguishable forever.

    Idempotent: a bound record carries a fingerprint and so never matches again.

    ``index`` supplies a prebuilt grounding index in place of the one built from
    the project on disk (SR-0153), the same seam :func:`~throughline.grounding.ratify`
    carries for the same reason (SR-0151). An item that reaches a root only through
    a composed source reads as orphaned to the bare Tool, so the refusal above
    rightly declines to complete its record — refusing to bind what it cannot
    justify is this repair working, not failing. A composing tool *can* justify it,
    and this is its route to the same result; without it, the only route would be a
    copy of this function, and a repair that heals unbound records would be a
    bitter thing to fork into a second implementation that writes them. Note what
    the seam deliberately does not offer: the grounding view is all a caller may
    vary, so it gains the completed record in full and cannot obtain a partial one.
    """
    project = load_project(root)
    schema = project.schema
    idx = index if index is not None else Index.build(project)
    bound: dict[str, str] = {}
    for item in project.items():
        if not item.attrs.get("ratified_by") or item.attrs.get("ratified_fingerprint"):
            continue
        if ratification_refusal(schema, idx, item) is not None:
            continue
        stamp = fingerprint(item, schema)
        item.attrs["ratified_fingerprint"] = stamp
        item.attrs["ratified_backfilled"] = True
        write_item(item)
        bound[item.uid] = stamp
    return bound


class RepairResult(NamedTuple):
    """What a major's repair wrote: the configuration bindings it backfilled
    (SR-0137), and the ratification records it bound (SR-0152)."""
    config: dict[str, str] | None
    stamps: dict[str, str]


def _repair_status_roles_major(root: Path, index: Index | None) -> RepairResult:
    """The repair for the major that requires [status.roles] (SR-0137, SR-0152).

    Ordered, not merely grouped: the record backfill resolves the project's
    schema, so the configuration the major requires has to be in place before it
    runs. Both halves are idempotent, so the pair is.

    ``index`` reaches only the record backfill (SR-0153): a grounding view has
    nothing to say about which declared status plays which role, so passing it to
    the configuration half would imply an influence it does not have."""
    return RepairResult(_backfill_status_roles(root),
                        _backfill_ratification_stamps(root, index=index))


# Structural migrations keyed by the source major they upgrade FROM; each rewrites
# the project tree in place to the next major. `tl migrate` walks this chain from
# the on-disk major to the current one (NFR-0010).
_MIGRATIONS: dict[int, Callable[[Path], None]] = {
    1: _migrate_1_to_2, 2: _migrate_2_to_3}

# Repairs keyed by the major they BELONG to, run against a project already at that
# major (SR-0137). A project hand-authored at the current major never passed
# through the upgrade that introduces the major's required configuration, so the
# chain above cannot reach it; the repair brings it to what the major requires.
# Each must be idempotent — it runs on every `tl migrate`, sound project or not.
_REPAIRS: dict[int, Callable[[Path, "Index | None"], RepairResult]] = {
    STATUS_ROLES_MAJOR: _repair_status_roles_major}


def _rewrite_format_version(cfg_file: Path, version: int) -> None:
    """Set project.format_version by targeted line rewrite, not a reserialize, so
    the config's comments and key order survive (NFR-0009)."""
    lines = cfg_file.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("format_version") and "=" in line:
            lines[i] = f"format_version = {version}"
            break
    else:
        for i, line in enumerate(lines):
            if line.strip() == "[project]":
                lines.insert(i + 1, f"format_version = {version}")
                break
        else:  # pragma: no cover - config always has a [project] table
            lines.insert(0, f"format_version = {version}")
    cfg_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


class MigrationResult(NamedTuple):
    """What `tl migrate` did: the majors it moved between, and any repair the
    destination major applied on the way (SR-0137). ``repaired`` is ``None`` when
    no repair wrote anything — the project was already sound — and otherwise the
    bindings written, which may legitimately be empty when no declared status
    matched a role and the repair recorded that as an empty table.

    ``bound`` maps ``uid -> fingerprint`` for every ratification record the repair
    completed (SR-0152), and is empty when there was nothing to bind. It is
    reported separately from ``repaired`` because the two are different kinds of
    change — one corrects configuration, the other writes to an accountability
    record — and an operator reading the output should never have to guess which
    they are looking at."""
    start: int
    end: int
    repaired: dict[str, str] | None
    bound: dict[str, str]


def migrate_project(path: str | Path, *,
                    index: Index | None = None) -> MigrationResult:
    """Bring a project's on-disk format to what the current major requires
    (NFR-0010).

    Returns ``(from_version, to_version, repaired)``. Raises ``ProjectError`` when
    the project is newer than this Tool (upgrade tl instead) or when no migration
    step is registered for an older major.

    A project already at the current major is not automatically a no-op (SR-0137):
    one hand-authored at this major never passed through the upgrade that
    introduces the major's required configuration, so the destination major's
    repair runs either way. Every repair is idempotent, so a sound project is
    still left untouched and reports nothing.

    ``index`` is passed through to the destination major's repair (SR-0153), so a
    composing caller can have a record judged against the union it grounds over
    while migration still writes only to the project at ``path``. Omitted, the
    index is built from that project and the behaviour is exactly as without the
    argument.
    """
    root = Path(path)
    cfg_file = root / CONFIG_NAME
    if not cfg_file.exists():
        raise ProjectError(
            f"no {CONFIG_NAME} at {root} — not a throughline project (run `tl init`)")
    config = tomllib.loads(cfg_file.read_text(encoding="utf-8"))
    start = _read_format_version(config, root)
    if start > FORMAT_VERSION:
        raise ProjectError(
            f"{cfg_file} declares format version {start}, newer than this tl "
            f"({FORMAT_VERSION}) — upgrade tl rather than migrate down")
    current = start
    while current < FORMAT_VERSION:
        step = _MIGRATIONS.get(current)
        if step is None:
            raise ProjectError(
                f"no migration path from format version {current} to {current + 1}")
        step(root)
        current += 1
    if current != start:
        _rewrite_format_version(cfg_file, current)
    repair = _REPAIRS.get(current)
    result = repair(root, index) if repair is not None else RepairResult(None, {})
    return MigrationResult(start, current, result.config, result.stamps)


# ------------------------------------------------------------------- YAML dump

class _Dumper(yaml.SafeDumper):
    pass


def _str_presenter(dumper, data):
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_Dumper.add_representer(str, _str_presenter)


def _dump_yaml(data: dict) -> str:
    return yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True,
                     default_flow_style=False, width=79)


# ----------------------------------------------------------------------- load

# Every manifest name the tooling can read. The tolerant read path (read_project)
# discovers registers across all of them, so a source at an older major whose only
# structural difference is the manifest filename (v1's `.document.yml`) still loads
# read-only, without the on-disk rename `tl migrate` would do.
_ALL_MANIFEST_NAMES = {MANIFEST_NAME, *_MANIFEST_BY_VERSION.values()}


def _backfill_status_roles_config(config: dict) -> dict:
    """The v2->v3 upgrade (SR-0131) as a pure, in-memory config transform: bind each
    semantic role to a status without touching disk. Mirrors `_migrate_2_to_3`, whose
    disk-writing form `tl migrate` uses; this form is for the read-only tolerant load
    of an older source (SR-0017). A config that already declares roles is returned
    unchanged."""
    status_cfg = config.get("status") or {}
    if status_cfg.get("roles"):
        return config
    declared = status_cfg.get("values")
    roles = {role: status
             for role, status in _DEFAULT_STATUS_ROLES.items()
             if not declared or status in declared}
    upgraded = copy.deepcopy(config)
    upgraded.setdefault("status", {})["roles"] = roles
    return upgraded


# In-memory config upgrades keyed by the major they upgrade FROM. Only a migration
# whose essence is a config edit appears here; a purely structural one (the v1->v2
# manifest rename) needs no config change and is absorbed by _ALL_MANIFEST_NAMES.
_CONFIG_UPGRADES: dict[int, Callable[[dict], dict]] = {2: _backfill_status_roles_config}


def _build_project(root: Path, config: dict, manifest_names: set[str]) -> Project:
    """Assemble a :class:`Project` from an already-parsed config and the register
    manifests found under ``root``. ``manifest_names`` bounds which filenames count
    as a register manifest — one name for the strict current-major load, every known
    name for the tolerant read of a possibly-older source."""
    cfg_file = root / CONFIG_NAME
    project = Project(path=root, config=config)
    try:
        project.schema  # build + validate now, so bad config fails fast (SR-0082)
    except SchemaError as e:
        raise ProjectError(f"invalid configuration in {cfg_file}: {e}") from e
    manifests = sorted(m for name in manifest_names for m in root.rglob(name))
    for manifest in manifests:
        reg_dir = manifest.parent
        raw = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        reg = Register.from_manifest(raw, path=reg_dir)
        if reg.prefix in project.registers:
            # A second register folder claims a prefix already loaded. Keeping the
            # first-seen register is deterministic (rglob is sorted); merging the
            # duplicate's items would clobber UID numbering, so record the clash
            # for `check` to fail on (SR-0101) and skip loading this folder.
            conflict = project.prefix_conflicts.setdefault(
                reg.prefix, [str(project.registers[reg.prefix].path)])
            conflict.append(str(reg_dir))
            continue
        for item_file in sorted(reg_dir.glob("*.yml")):
            if item_file.name in manifest_names:
                continue
            d = yaml.safe_load(item_file.read_text(encoding="utf-8")) or {}
            item = Item.from_dict(d, path=item_file)
            item._register_prefix = reg.prefix
            for msg in item._load_errors:
                # A malformed structure (e.g. a link missing its target) was
                # tolerated at parse time; record it for `check` to fail on as a
                # named finding rather than a raw traceback (SR-0134).
                project.load_errors.append((item.uid, str(item_file), msg))
            if item.uid in reg.items:
                # A second file in the same folder claims a UID already loaded.
                # The dict overwrite below would silently drop the loser, so
                # record it for `uid-collision` before it vanishes (SR-0006).
                project.duplicate_uids.add(item.uid)
            reg.items[item.uid] = item
        project.registers[reg.prefix] = reg
    return project


def load_project(path: str | Path) -> Project:
    root = Path(path)
    cfg_file = root / CONFIG_NAME
    if not cfg_file.exists():
        raise ProjectError(f"no {CONFIG_NAME} at {root} — not a throughline project (run `tl init`)")
    config = tomllib.loads(cfg_file.read_text(encoding="utf-8"))
    _gate_format_version(config, root)
    return _build_project(root, config, {MANIFEST_NAME})


def read_project(path: str | Path) -> Project:
    """Load a project read-only, tolerating an on-disk format major older than this
    build's (SR-0017). Where :func:`load_project` gates on an exact major and points
    an older project at ``tl migrate``, this upgrades the format *in memory* — each
    config-level migration applied to the parsed config, registers discovered across
    every manifest name the tooling knows — and never writes to the tree, so
    consuming a source never forces it to be migrated first (UR-0006). A major newer
    than this build still fails: the future cannot be parsed."""
    root = Path(path)
    cfg_file = root / CONFIG_NAME
    if not cfg_file.exists():
        raise ProjectError(f"no {CONFIG_NAME} at {root} — not a throughline project (run `tl init`)")
    config = tomllib.loads(cfg_file.read_text(encoding="utf-8"))
    disk = _read_format_version(config, root)
    if disk > FORMAT_VERSION:
        raise ProjectError(
            f"{cfg_file} declares format version {disk}, but this tl understands up "
            f"to {FORMAT_VERSION} — upgrade tl to read this source")
    major = disk
    while major < FORMAT_VERSION:
        upgrade = _CONFIG_UPGRADES.get(major)
        if upgrade is not None:
            config = upgrade(config)
        major += 1
    return _build_project(root, config, _ALL_MANIFEST_NAMES)


# -------------------------------------------------------------- git baseline

def baseline_statuses(project: Project, ref: str = "HEAD") -> dict[str, str] | None:
    """Map ``uid -> status`` as each item stood at git ``ref`` — the snapshot the
    working tree is measured against.

    Two consumers read this: transition legality (SR-0083), which only looks up
    items still present in the working tree, and tombstone permanence (SR-0093),
    which needs items that existed at ``ref`` but are *gone* now — a UID whose
    file was erased by a bad merge or a stray ``git rm``. So the map covers both
    the current items' prior status and any file present at ``ref`` but absent
    today.

    Returns ``None`` — both checks then silently no-op — when the project is not
    inside a git work tree or ``ref`` cannot be resolved. Items absent at ``ref``
    (newly added) are simply omitted: creation is not a transition.
    """
    root = Path(project.path).resolve()
    try:
        top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    top = Path(top)

    # Resolve the ref once: an unborn HEAD or a bad --base makes the whole
    # baseline unavailable (inert) rather than "everything looks new".
    if subprocess.run(["git", "-C", str(top), "rev-parse", "--verify", "--quiet",
                       f"{ref}^{{commit}}"], capture_output=True).returncode != 0:
        return None

    out: dict[str, str] = {}
    for item in project.items():
        if item._path is None:
            continue
        try:
            rel = item._path.resolve().relative_to(top)
        except ValueError:
            continue
        try:
            blob = subprocess.run(
                ["git", "-C", str(top), "show", f"{ref}:{rel.as_posix()}"],
                capture_output=True, text=True, check=True).stdout
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            continue  # not present at ref (new file) or bad ref for this path
        data = yaml.safe_load(blob) or {}
        status = data.get("status")
        if isinstance(status, str):
            out[item.uid] = status

    # Files that existed at `ref` but are gone from the working tree now. No
    # current item carries their status, so a transition can't be measured — but
    # a vanished tombstone must still reach the gate (SR-0093). Read their status
    # straight from the tree.
    present = {i._path.resolve() for i in project.items() if i._path is not None}
    # Scope the scan to this project's own subtree: a project may be a
    # subdirectory of a larger repo (e.g. examples/ alongside the self-host
    # graph), and a tombstone in a *sibling* project must not be read as this
    # project's erased record.
    try:
        proj_rel = root.relative_to(top)
        prefix = "" if proj_rel == Path(".") else proj_rel.as_posix() + "/"
    except ValueError:
        prefix = ""
    try:
        tree = subprocess.run(
            ["git", "-C", str(top), "ls-tree", "-r", "--name-only", ref,
             "--", prefix or "."],
            capture_output=True, text=True, check=True).stdout.splitlines()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        tree = []
    for rel in tree:
        if not rel.endswith(".yml") or Path(rel).name == MANIFEST_NAME:
            continue
        if (top / rel).resolve() in present:
            continue  # a current item — already handled above
        try:
            blob = subprocess.run(
                ["git", "-C", str(top), "show", f"{ref}:{rel}"],
                capture_output=True, text=True, check=True).stdout
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            continue
        data = yaml.safe_load(blob) or {}
        uid, status = data.get("uid"), data.get("status")
        if isinstance(uid, str) and isinstance(status, str):
            out.setdefault(uid, status)
    return out


def load_project_at_ref(path: str | Path, ref: str) -> tuple[Project, str]:
    """Reconstruct the project *exactly as it stood* at git ``ref`` and load it
    into the pure model, without touching the working tree (SR-0090).

    The revision's tracked project subtree is exported with ``git archive`` into
    a throwaway temp dir and read with the normal loader, so no historical-format
    parsing is reimplemented. Returns ``(project, sha)`` where ``sha`` is the
    resolved commit hash — the caller stamps it as provenance. Raises
    ``ProjectError`` when the path is not inside a git work tree, the ref cannot
    be resolved, or the tree at that ref holds no throughline project.
    """
    root = Path(path).resolve()
    try:
        top = Path(subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True).stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as e:
        raise ProjectError(
            f"{root} is not inside a git work tree — cannot render `--at {ref}`") from e

    resolved = subprocess.run(
        ["git", "-C", str(top), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        capture_output=True, text=True)
    if resolved.returncode != 0:
        raise ProjectError(f"cannot resolve git revision {ref!r}")
    sha = resolved.stdout.strip()

    rel = root.relative_to(top)
    treeish = sha if rel == Path(".") else f"{sha}:{rel.as_posix()}"
    try:
        archive = subprocess.run(
            ["git", "-C", str(top), "archive", "--format=tar", treeish],
            capture_output=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as e:
        raise ProjectError(f"no project tree at {ref} ({rel.as_posix()})") from e

    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            try:
                tar.extractall(tmp, filter="data")   # py>=3.12 / backports
            except TypeError:  # pragma: no cover - older stdlib without filter=
                tar.extractall(tmp)
        try:
            project = load_project(tmp)
        except ProjectError as e:
            raise ProjectError(f"no throughline project at {ref} — {e}") from e
        # load_project reads every item into memory here, so the project is
        # fully materialised and safe to use after the temp dir is removed.
        return project, sha


# ---------------------------------------------------------------------- write

def write_item(item: Item, reg: Register | None = None) -> Path:
    if item._path is None:
        if reg is None or reg.path is None:
            raise ProjectError(f"cannot write {item.uid}: no path known")
        item._path = reg.path / f"{item.uid}.yml"
    text = _dump_yaml(item.to_dict())
    if not text.endswith("\n"):
        text += "\n"
    item._path.write_text(text, encoding="utf-8")
    return item._path


def write_manifest(reg: Register) -> Path:
    if reg.path is None:
        raise ProjectError(f"register {reg.prefix} has no path")
    path = reg.path / MANIFEST_NAME
    text = _dump_yaml(reg.manifest_dict())
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    return path


def find_enclosing_project(start: Path) -> Path | None:
    """Return the root of an existing project that would *enclose* ``start``
    (a strict ancestor holding a config), or None. Used to refuse accidentally
    nesting one project inside another (SR-0077)."""
    start = start.resolve()
    for parent in start.parents:
        if (parent / CONFIG_NAME).exists():
            return parent
    return None


def find_nested_project(
    start: Path, on_progress: Callable[[int], None] | None = None
) -> Path | None:
    """Return the root of an existing project *below* ``start`` (a descendant
    directory holding a config), or None. The mirror of enclosing detection:
    creating a project here would wrap that child project (SR-0077).

    The common (None) result walks the whole tree, which can be slow, so an
    optional ``on_progress`` callback is invoked with the running directory
    count. Presentation of that count is the caller's concern — this layer does
    no I/O. Symlinks are not followed, so the walk cannot loop.
    """
    start = start.resolve()
    scanned = 0
    for dirpath, _dirnames, filenames in os.walk(start):
        scanned += 1
        if on_progress is not None:
            on_progress(scanned)
        if CONFIG_NAME in filenames and Path(dirpath).resolve() != start:
            return Path(dirpath)
    return None


def init_project(
    path: str | Path,
    name: str = "Example",
    force: bool = False,
    defaults: bool = True,
    demo: bool = True,
    bare: bool = False,
    on_progress: Callable[[int], None] | None = None,
) -> Project:
    """Create a new project. The seeded content is independently suppressible so the
    newcomer-friendly default (SR-0100) does not trap experienced users:

    - ``defaults`` (default True) creates the default registers (INT/REQ/NFR/NG/TEST).
    - ``demo`` (default True) additionally seeds the example items and a rendered
      ``docs/overview.md``; it needs somewhere to put those items, so it is forced
      off when ``defaults`` is off.
    - ``bare`` is a convenience for "schema only" — equivalent to ``defaults`` and
      ``demo`` both off — retained so existing ``--bare`` callers are unaffected.
    """
    root = Path(path)
    root.mkdir(parents=True, exist_ok=True)
    cfg = root / CONFIG_NAME
    if cfg.exists():
        raise ProjectError(f"{cfg} already exists")
    if not force:
        enclosing = find_enclosing_project(root)
        if enclosing is not None:
            raise ProjectError(
                f"{root.resolve()} is inside an existing throughline project at "
                f"{enclosing} — refusing to nest; pass --force to override"
            )
        nested = find_nested_project(root, on_progress=on_progress)
        if nested is not None:
            raise ProjectError(
                f"{root.resolve()} already contains a throughline project at "
                f"{nested} — refusing to wrap it; pass --force to override"
            )
    if bare:
        defaults = demo = False
    if not defaults:
        # Seeded demo items have no register to live in, so the demo goes with the
        # registers (SR-0100).
        demo = False
    cfg_text = _DEFAULT_CONFIG.format(name=name)
    # Only the seeded demo publishes through a document, so [docs] is wired only when
    # the demo is written; a registers-only or bare project ships no document.
    if demo:
        cfg_text += _DOCS_CONFIG
    cfg.write_text(cfg_text, encoding="utf-8")
    by_prefix = _seed_registers(root) if defaults else {}
    if demo:
        _seed_demo(root, name, by_prefix)
    return load_project(root)


def _seed_registers(root: Path) -> dict[str, Register]:
    """Create the default registers (INT/REQ/NFR/NG/TEST) so a project has a place to
    author each kind of item. Returns them keyed by prefix for the demo seeder."""
    registers = [
        Register(prefix="INT", title="Vision", path=root / "vision"),
        Register(prefix="REQ", title="Requirements", path=root / "requirements"),
        Register(prefix="NFR", title="Non-functional requirements",
                 path=root / "nonfunctional"),
        Register(prefix="NG", title="Non-goals", path=root / "non-goals"),
        Register(prefix="TEST", title="Tests", path=root / "tests"),
    ]
    by_prefix = {r.prefix: r for r in registers}
    for reg in registers:
        reg.path.mkdir(parents=True, exist_ok=True)
        write_manifest(reg)
    return by_prefix


def _seed_demo(root: Path, name: str, by_prefix: dict[str, Register]) -> None:
    """Seed a small, self-consistent example graph and one published document so a
    fresh project passes ``tl check`` and renders content immediately, instead of
    forcing the newcomer to reverse-engineer the schema (SR-0100). Everything
    written here is ordinary project content the user may edit, move, or delete.

    The graph exercises the shipped default configuration end to end: a root
    intent, a requirement and a non-functional requirement grounded to it, a test
    that verifies the requirement (satisfying the coverage rule), and a non-goal.
    ``docs/overview.md`` carries tl:item / tl:table / tl:matrix regions and is
    injected before return, so it ships already rendered."""
    items = [
        Item(uid="INT-0001", type="intent", status="approved", normative=False,
             title=f"Deliver {name}",
             text="Describe the outcome this project exists to create. Every "
                  "requirement below grounds back to this intent."),
        Item(uid="REQ-0001", type="requirement", status="approved", normative=True,
             title="First requirement",
             text="State something the system shall do, then replace this with a "
                  "real requirement.",
             links=[Link(target="INT-0001", type="implements")],
             attrs={"priority": "must", "origin": "human"}),
        Item(uid="NFR-0001", type="nfr", status="approved", normative=True,
             title="First quality attribute",
             text="State a quality the system shall have (performance, security, "
                  "usability), then replace this.",
             links=[Link(target="INT-0001", type="implements")],
             attrs={"origin": "human"}),
        Item(uid="TEST-0001", type="test", status="approved", normative=False,
             title="Verifies the first requirement",
             text="Describe how REQ-0001 is checked. This verifies link satisfies "
                  "the coverage rule declared in throughline.toml.",
             links=[Link(target="REQ-0001", type="verifies")]),
        Item(uid="NG-0001", type="non_goal", status="approved", normative=False,
             title="First non-goal",
             text="Record something deliberately out of scope, so nobody proposes "
                  "it later. Non-goals are negative space; nothing grounds to them.",
             attrs={"origin": "human"}),
    ]
    for item in items:
        prefix = item.uid.split("-")[0]
        item._register_prefix = prefix
        write_item(item, by_prefix[prefix])

    docs_dir = root / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    overview = docs_dir / "overview.md"
    overview.write_text(_STARTER_OVERVIEW.format(name=name), encoding="utf-8")

    # Ship the document already rendered, so `tl docs --check` is green on a fresh
    # project and the newcomer sees content, not empty marker pairs (SR-0100).
    from .inject import inject_text
    project = load_project(root)
    rendered = inject_text(project, overview.read_text(encoding="utf-8"))
    if not rendered.endswith("\n"):
        rendered += "\n"
    overview.write_text(rendered, encoding="utf-8")


_DEFAULT_CONFIG = '''\
[project]
name = "{name}"
format_version = 3

# Root item types may exist ungrounded; everything else must reach a root
# through a grounding link (the scope-avalanche grounding layer). A non_goal is
# a root but NOT a delivery root: it is negative space, so nothing derives from
# it and it is never flagged 'unserved'.
[grounding]
root_types = ["intent", "business_need", "risk", "constraint", "assumption", "non_goal"]
delivery_roots = ["intent", "business_need", "risk", "constraint"]
ground_link_types = ["derives_from", "mitigates", "implements", "verifies"]
ai_origins = ["ai", "hybrid"]

[types.requirement]
attrs.priority = {{ type = "enum", values = ["must", "should", "could"], normative = true }}
attrs.origin   = {{ type = "enum", values = ["human", "ai", "hybrid"] }}

[types.nfr]
attrs.origin = {{ type = "enum", values = ["human", "ai", "hybrid"] }}

# A non_goal records deliberately-excluded scope — the object a human points at
# to reject a category of proposed work. Passive by design: throughline surfaces
# non_goals in `tl context` but never tries to detect items that 'violate' one.
[types.non_goal]
attrs.origin = {{ type = "enum", values = ["human", "ai", "hybrid"] }}

[links]
types = ["refines", "verifies", "satisfies", "implements", "relates",
         "derives_from", "mitigates", "assumes"]

# Type-level link constraints (SR-0084): the item types each link may join.
# Domain-specific, so left off by default — run `tl shape` to see how your
# graph is actually wired, then declare rules to lock that shape in, e.g.:
# [link_rules]
# mitigates = {{ from = ["requirement"], to = ["risk"] }}
# verifies  = {{ from = ["test"], to = ["requirement"] }}

[status]
# draft = actively moving toward approval; deferred = acknowledged and grounded
# but deliberately parked (a backlog wish-list item, not on the active front).
values = ["proposed", "draft", "deferred", "approved", "ratified", "implemented",
          "verified", "suspect", "rejected", "deleted"]

# Semantic status roles (SR-0131): which declared status plays each role the
# tool's operations act on — `tl new` births at 'initial', `tl ratify` writes
# 'ratified', `tl invalidate` writes 'invalidated' and reads 'invalidated' +
# 'tombstone' as dead, `tl delete` writes 'tombstone'. Operations resolve status
# through these roles, never a literal, so a project renames a status freely by
# editing here. Delete this table only if you drive every status move by hand.
[status.roles]
initial = "draft"
proposed = "proposed"
ratified = "ratified"
invalidated = "rejected"
suspect = "suspect"
tombstone = "deleted"

# Allowed status moves (SR-0083). `tl check` compares each item against its
# status in the previous commit and flags any change these do not permit. Delete
# this table to leave every status freely reachable.
# Every live status keeps a route to 'ratified' (SR-0150) — in particular the two
# statuses below 'proposed' can move back to it, so scope that was authored or
# parked without ratification can still be put forward for a human to accept.
[transitions]
proposed    = ["draft", "approved", "ratified", "deferred", "rejected", "deleted"]
draft       = ["proposed", "approved", "deferred", "rejected", "deleted"]
deferred    = ["proposed", "draft", "approved", "rejected", "deleted"]
approved    = ["ratified", "implemented", "deferred", "suspect", "rejected", "deleted"]
ratified    = ["implemented", "suspect", "rejected", "deleted"]
implemented = ["verified", "suspect", "rejected", "deleted"]
verified    = ["implemented", "suspect", "deleted"]
suspect     = ["approved", "ratified", "implemented", "verified", "rejected", "deleted"]
rejected    = ["draft", "deleted"]

# Coverage: every live requirement should be verified by a test.
[[rules.coverage]]
filter = "type == 'requirement' and status != 'deleted'"
needs = "incoming:verifies"
severity = "warning"
'''


# Appended to the config only when init seeds a starter project (SR-0100). The
# seeded example publishes through docs/overview.md, so publication coverage must
# be on; a --bare project omits this and stays purely a schema.
_DOCS_CONFIG = '''
# Published documents (SR-0094 / SR-0096). `tl docs` injects item content into the
# tl: marker regions in these files; `tl docs --check` gates their freshness in
# CI. With paths set, `tl check` also flags any live normative item that no
# published document references. Delete this section to turn publication off.
[docs]
paths = ["docs/*.md"]
'''


# The starter document seeded by init (SR-0100). Prose is hand-owned; the regions
# between tl: markers are regenerated from the graph by `tl docs`. Injected once
# at init time so the file ships already rendered.
_STARTER_OVERVIEW = '''\
# {name}

Welcome to your new throughline project. This document mixes prose you own with
regions generated from the requirements graph. Edit the prose freely; the marker
regions (each a tl: opener paired with a tl:end line) are regenerated by
`tl docs`, so change the underlying items (in `vision/`, `requirements/`, ...)
rather than the generated text. Run `tl docs` after editing items, and `tl check`
to validate the graph.

Delete anything here you do not need — the starter is a runway, not a fixture.

## Vision

<!-- tl:item INT-0001 -->
<!-- tl:end -->

## Requirements

<!-- tl:table type == 'requirement' -->
<!-- tl:end -->

## Non-functional requirements

<!-- tl:table type == 'nfr' -->
<!-- tl:end -->

## Non-goals

<!-- tl:table type == 'non_goal' -->
<!-- tl:end -->

## Traceability

Each requirement, what it grounds up to, and what verifies it.

<!-- tl:matrix type == 'requirement' -->
<!-- tl:end -->
'''
