# Copyright (c) 2026 Time Back Solutions Limited
# SPDX-License-Identifier: Apache-2.0
"""Parser/Writer — the only module that touches disk (arch doc 07 §2).

Loads a project (throughline.toml + per-register .register.yml + one <UID>.yml per
item) into the pure model, and writes items deterministically: stable key
order, LF endings, UTF-8, final newline, no timestamp churn (SR-0072). Unknown
keys survive read-modify-write (NFR-0009).
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable
from pathlib import Path

import yaml

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
# SR-0102) renamed it to `.register.yml`.
FORMAT_VERSION = 2

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
            f"{FORMAT_VERSION} — run `tl migrate` to upgrade the project")


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


# Structural migrations keyed by the source major they upgrade FROM; each rewrites
# the project tree in place to the next major. `tl migrate` walks this chain from
# the on-disk major to the current one (NFR-0010).
_MIGRATIONS: dict[int, Callable[[Path], None]] = {1: _migrate_1_to_2}


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


def migrate_project(path: str | Path) -> tuple[int, int]:
    """Upgrade a project's on-disk format to the current major (NFR-0010).

    Returns ``(from_version, to_version)``. A project already current is a no-op
    returning ``(FORMAT_VERSION, FORMAT_VERSION)``. Raises ``ProjectError`` when
    the project is newer than this Tool (upgrade tl instead) or when no migration
    step is registered for an older major.
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
    return start, current


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

def load_project(path: str | Path) -> Project:
    root = Path(path)
    cfg_file = root / CONFIG_NAME
    if not cfg_file.exists():
        raise ProjectError(f"no {CONFIG_NAME} at {root} — not a throughline project (run `tl init`)")
    config = tomllib.loads(cfg_file.read_text(encoding="utf-8"))
    _gate_format_version(config, root)

    project = Project(path=root, config=config)
    try:
        project.schema  # build + validate now, so bad config fails fast (SR-0082)
    except SchemaError as e:
        raise ProjectError(f"invalid configuration in {cfg_file}: {e}") from e
    for manifest in sorted(root.rglob(MANIFEST_NAME)):
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
            if item_file.name == MANIFEST_NAME:
                continue
            d = yaml.safe_load(item_file.read_text(encoding="utf-8")) or {}
            item = Item.from_dict(d, path=item_file)
            item._register_prefix = reg.prefix
            if item.uid in reg.items:
                # A second file in the same folder claims a UID already loaded.
                # The dict overwrite below would silently drop the loser, so
                # record it for `uid-collision` before it vanishes (SR-0006).
                project.duplicate_uids.add(item.uid)
            reg.items[item.uid] = item
        project.registers[reg.prefix] = reg
    return project


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
    bare: bool = False,
    on_progress: Callable[[int], None] | None = None,
) -> Project:
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
    cfg_text = _DEFAULT_CONFIG.format(name=name)
    # The seeded starter publishes through a document, so it needs [docs] wired;
    # a --bare project ships only the schema (SR-0100).
    if not bare:
        cfg_text += _DOCS_CONFIG
    cfg.write_text(cfg_text, encoding="utf-8")
    if not bare:
        _seed_starter(root, name)
    return load_project(root)


def _seed_starter(root: Path, name: str) -> None:
    """Seed a small, self-consistent example graph and one published document so a
    fresh project passes ``tl check`` and renders content immediately, instead of
    forcing the newcomer to reverse-engineer the schema (SR-0100). Everything
    written here is ordinary project content the user may edit, move, or delete.

    The graph exercises the shipped default configuration end to end: a root
    intent, a requirement and a non-functional requirement grounded to it, a test
    that verifies the requirement (satisfying the coverage rule), and a non-goal.
    ``docs/overview.md`` carries tl:item / tl:table / tl:matrix regions and is
    injected before return, so it ships already rendered."""
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
format_version = 2

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

# Allowed status moves (SR-0083). `tl check` compares each item against its
# status in the previous commit and flags any change these do not permit. Delete
# this table to leave every status freely reachable.
[transitions]
proposed    = ["draft", "approved", "ratified", "deferred", "rejected", "deleted"]
draft       = ["approved", "deferred", "rejected", "deleted"]
deferred    = ["draft", "approved", "rejected", "deleted"]
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
