# Copyright (c) 2026 Time Back Solutions Limited
# SPDX-License-Identifier: Apache-2.0
"""Parser/Writer — the only module that touches disk (arch doc 07 §2).

Loads a project (throughline.toml + per-document .document.yml + one <UID>.yml per
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

from .model import Document, Item, Project
from .schema import SchemaError

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

CONFIG_NAME = "throughline.toml"
MANIFEST_NAME = ".document.yml"


class ProjectError(Exception):
    pass


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

    project = Project(path=root, config=config)
    try:
        project.schema  # build + validate now, so bad config fails fast (SR-0082)
    except SchemaError as e:
        raise ProjectError(f"invalid configuration in {cfg_file}: {e}") from e
    for manifest in sorted(root.rglob(MANIFEST_NAME)):
        doc_dir = manifest.parent
        raw = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        doc = Document.from_manifest(raw, path=doc_dir)
        for item_file in sorted(doc_dir.glob("*.yml")):
            if item_file.name == MANIFEST_NAME:
                continue
            d = yaml.safe_load(item_file.read_text(encoding="utf-8")) or {}
            item = Item.from_dict(d, path=item_file)
            item._doc_prefix = doc.prefix
            doc.items[item.uid] = item
        project.documents[doc.prefix] = doc
    return project


# -------------------------------------------------------------- git baseline

def baseline_statuses(project: Project, ref: str = "HEAD") -> dict[str, str] | None:
    """Map ``uid -> status`` as each live item stood at git ``ref``, so a
    status transition can be measured against the change actually being
    committed (SR-0083).

    Returns ``None`` — transition checking then silently no-ops — when the
    project is not inside a git work tree or ``ref`` cannot be resolved. Items
    absent at ``ref`` (newly added) are simply omitted: creation is not a
    transition.
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

def write_item(item: Item, doc: Document | None = None) -> Path:
    if item._path is None:
        if doc is None or doc.path is None:
            raise ProjectError(f"cannot write {item.uid}: no path known")
        item._path = doc.path / f"{item.uid}.yml"
    text = _dump_yaml(item.to_dict())
    if not text.endswith("\n"):
        text += "\n"
    item._path.write_text(text, encoding="utf-8")
    return item._path


def write_manifest(doc: Document) -> Path:
    if doc.path is None:
        raise ProjectError(f"document {doc.prefix} has no path")
    path = doc.path / MANIFEST_NAME
    text = _dump_yaml(doc.manifest_dict())
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
    cfg.write_text(_DEFAULT_CONFIG.format(name=name), encoding="utf-8")
    return load_project(root)


_DEFAULT_CONFIG = '''\
[project]
name = "{name}"
format_version = 1

# Root item types may exist ungrounded; everything else must reach a root
# through a grounding link (the scope-avalanche grounding layer).
[grounding]
root_types = ["intent", "business_need", "risk", "constraint", "assumption"]
delivery_roots = ["intent", "business_need", "risk", "constraint"]
ground_link_types = ["derives_from", "mitigates", "implements", "verifies"]
ai_origins = ["ai", "scout", "hybrid"]

[types.requirement]
attrs.priority = {{ type = "enum", values = ["must", "should", "could"], normative = true }}
attrs.origin   = {{ type = "enum", values = ["human", "ai", "scout", "hybrid"] }}

[types.nfr]
attrs.origin = {{ type = "enum", values = ["human", "ai", "scout", "hybrid"] }}

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
