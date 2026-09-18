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

# One loader for every YAML the Tool reads (SR-0193). PyYAML's C loader parses the
# same files about nine times faster than the pure-Python one and constructs the
# same result; both are *safe* loaders that build no Python objects (NFR-0022).
# Chosen once here, so no module reads YAML by another route.
_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def _load_yaml(text: str):
    """Parse one YAML document safely, through the loader chosen above."""
    return yaml.load(text, Loader=_YAML_LOADER)

from collections.abc import Mapping
from types import MappingProxyType

from .fingerprint import content_fingerprint, fingerprint, signed_content
from .graph import Index
from .grounding import ratification_refusal
from .model import Item, Link, Project, Register
from .schema import SchemaError
from .tomledit import TomlDocument, TomlEditError
from .version import distribution_version

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


def _running_tool() -> str:
    """Describe the copy of the Tool that is executing, as `version (path)`.

    A version refusal has to name *which* tl refused (SR-0168). The reader
    usually has a current tl on their own machine, so a message that says only
    "this tl" reads as the Tool being wrong about itself; the copy that actually
    refused is often on another machine entirely, such as a long-lived build
    runner.

    Asks `version.distribution_version` rather than reading the metadata here, so
    this reports the same string as `tl --version` and keeps the editable marker
    (SR-0164) — an install resolving back to a working tree is exactly the case
    where "which copy is this?" is being asked.
    """
    return (f"throughline {distribution_version('throughline')} "
            f"at {Path(__file__).resolve().parent}")


def _upgrade_command() -> str:
    """The command that upgrades *this* installation of the Tool (SR-0168).

    Derived from where the running interpreter lives rather than assumed, because
    a remedy naming the wrong installer sends the reader to change an environment
    other than the one that refused. A pipx install is laid out as
    `<pipx home>/venvs/<name>/`, and `pip install` into it is the wrong move — it
    either misses the venv entirely or leaves a second copy that shadows the
    first. Anything else is upgraded through the interpreter that is running, so
    the command cannot land in a different environment than the one that refused.
    """
    parts = Path(sys.prefix).resolve().parts
    for i, part in enumerate(parts):
        if part == "venvs" and i and parts[i - 1] == "pipx" and i + 1 < len(parts):
            return f"pipx upgrade {parts[i + 1]}"
    return f"{sys.executable} -m pip install --upgrade throughline"


def _gate_format_version(config: dict, root: Path) -> None:
    """Refuse to load a project whose format major differs from ours (NFR-0010).

    Newer than we understand -> tell the user to upgrade the Tool rather than
    mis-parse a format from the future. Older than ours -> point at `tl migrate`
    rather than silently rewrite. An equal major reads transparently.

    Both refusals name the running Tool and, where the remedy is to upgrade it,
    the command for how that copy was installed (SR-0168).
    """
    disk = _read_format_version(config, root)
    if disk > FORMAT_VERSION:
        raise ProjectError(
            f"{root / CONFIG_NAME} declares format version {disk}, but this tl "
            f"({_running_tool()}) implements format {FORMAT_VERSION} — upgrade "
            f"that installation to open this project: {_upgrade_command()}")
    if disk < FORMAT_VERSION:
        raise ProjectError(
            f"{root / CONFIG_NAME} is at format version {disk}; this tl "
            f"({_running_tool()}) uses {FORMAT_VERSION} — run `tl migrate` to "
            f"upgrade the project (a lossless, in-place rewrite)")


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
        # Every stamp the Tool writes carries the content it was taken over
        # (SR-0215); the backfilled marking beside it still says nobody is known
        # to have read that content.
        item.attrs["ratified_content"] = signed_content(item, schema)
        write_item(item)
        bound[item.uid] = stamp
    return bound


def _backfill_ratification_revisions(root: Path) -> dict[str, str]:
    """Cache, on each ratification record, the revision whose content reproduces
    its stamp (SR-0166). Returns ``uid -> sha`` for every record populated.

    Resolution is a walk back through an item's history, and the surfaces that
    need it are interactive — a reviewer moving down a worklist would pay that
    walk on every item they land on. Held on the record it is a single read.

    Here rather than at ratify time, and here rather than in a script beside the
    tool: the content being ratified is still uncommitted when ratify runs, so the
    revision that holds it does not exist yet, and recording HEAD then would
    anchor to the state *before* the change — the wrong side of it. Migration is
    the first moment the answer can be found, and repairing records in place is
    what it is for.

    Only a record whose stamp still matches its current content is cached, because
    that is the case where the revision is a fact rather than a guess: an item
    that has since drifted will be resolved on demand and the answer then belongs
    to whatever it has drifted to. A record already carrying a verifiable revision
    is left alone, which is what makes this idempotent; one carrying a revision the
    stamp disagrees with is re-resolved, so a hint left by rewritten history heals
    rather than persisting.
    """
    from .ratification import REVISION_ATTR, resolve_revision

    project = load_project(root)
    schema = project.schema
    cached: dict[str, str] = {}
    for item in project.items():
        if not item.attrs.get("ratified_fingerprint"):
            continue
        if fingerprint(item, schema) != item.attrs["ratified_fingerprint"]:
            continue                      # drifted: resolved on demand, not here
        before = item.attrs.get(REVISION_ATTR)
        sha, _reason, was_cached = resolve_revision(project, item)
        if sha is None or (was_cached and sha == before):
            continue
        item.attrs[REVISION_ATTR] = sha
        write_item(item)
        cached[item.uid] = sha
    return cached


def _backfill_ratification_contents(root: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Record, on each ratification record that has a stamp and no recorded content,
    the content the stamp was taken over — wherever it can be proved (SR-0218).
    Returns ``(completed, unprovable)``: ``uid -> where`` for each record completed
    (``"current"``, or the revision the content came from), and ``uid -> reason``
    for each whose content could not be proved, so the run can say why rather than
    name a revision problem a project with no repository never had.

    Proof is the stamp alone, as it is for the revision cache (SR-0166): the item as
    it stands when its content still reproduces the stamp, otherwise the revision
    history resolves for it. Nothing else is written. No stamp, ratifier, identifier,
    backfill marking or status moves, so a record completed here says no more about
    who read what than it said before; it only makes the words it already vouched
    for readable without version control.

    A record whose content cannot be proved is left as it is, and keeps resolving
    from history wherever history exists. A record already carrying content is
    left alone, which is what makes this idempotent — including content that does
    not reproduce its stamp, which is a finding for a person (SR-0217), not
    something a repair may overwrite.
    """
    from .ratification import CONTENT_ATTR, STAMP_ATTR, content_at_stamp, repo_top

    project = load_project(root)
    # Asked once. Without a work tree no record resolves from history, and asking
    # per item would run git for every one of them to learn the same thing.
    history = repo_top(root) is not None
    completed: dict[str, str] = {}
    unprovable: dict[str, str] = {}
    for item in project.items():
        if not item.attrs.get(STAMP_ATTR) or CONTENT_ATTR in item.attrs:
            continue
        content, why = content_at_stamp(project, item, history=history)
        if content is None:
            unprovable[item.uid] = why
            continue
        where = why
        item.attrs[CONTENT_ATTR] = content
        write_item(item)
        completed[item.uid] = where
    return completed, unprovable


def _backfill_vocabularies(root: Path) -> dict[str, list[str]]:
    """Declare each vocabulary the project leaves absent, as what it already
    relies on (SR-0185). Returns ``key -> members`` for each one written, and is
    empty when both were already declared.

    An undeclared vocabulary permits every value, which the gate now reports as
    an error — so a project that never declared one gets a red build from an
    upgrade it did not ask for. This is what bounds that cost to a single command.

    What it writes is the project's own current reliance: the values its live
    items hold, together with any the rest of its configuration names. That list
    is chosen for what it cannot do — it cannot invalidate an item, because every
    item's value is in it, and it cannot leave the schema unbuildable, because
    every value the configuration cites is in it too. The declaration therefore
    changes nothing about what the graph admits *today* while making the project
    say what it means from here on, which the author can then narrow deliberately
    through `tl schema`.

    Presence of the key, never its contents, is what marks it done (SR-0137): a
    project that declares a vocabulary has made a choice, and a repair that
    second-guessed the contents would rewrite a deliberate one on every migrate.
    """
    cfg_file = root / CONFIG_NAME
    text = cfg_file.read_text(encoding="utf-8")
    config = tomllib.loads(text)
    absent = [(t, k, w) for t, k, w in (("status", "values", "statuses"),
                                        ("links", "types", "link types"))
              if (config.get(t) or {}).get(k) is None]
    if not absent:
        return {}
    project = load_project(root)
    relied = {"status": project.relied_on_statuses,
              "links": project.relied_on_link_types}
    doc = TomlDocument(text)
    written: dict[str, list[str]] = {}
    for table, key, what in absent:
        values = sorted(relied[table]())
        doc.set_key(table, key, values, because=(
            f"Declared by `tl migrate` (SR-0185) as the {what} this project "
            "already relies on, because an undeclared vocabulary permits every "
            "value and so validates none of them. Narrow it deliberately with "
            "`tl schema` once you have decided what it should be."))
        written[f"[{table}] {key}"] = values
    cfg_file.write_text(doc.text(), encoding="utf-8")
    return written


def _backfill_suspect_routes(root: Path) -> dict[str, str]:
    """Add a move to the suspect status from every live status the lifecycle
    leaves unable to reach it (SR-0188). Returns ``status -> suspect`` for each
    row widened, and is empty when suspicion already reaches everywhere it must.

    SR-0175 corrected the lifecycle the Tool *ships*, and left an existing project
    with the table it already had — so every project scaffolded before it carries
    a suspect role that is inert for exactly the statuses `tl new` births and parks
    items in. The gate reports that (SR-0174) and, until this, offered no remedy but
    editing each status by hand. This is what bounds it to the one command that
    already repairs the rest of the major's configuration.

    Which statuses are at issue is asked of
    :meth:`~throughline.model.Project.suspect_unreachable_statuses` — the same
    predicate the gate reports from — so the repair writes exactly what the gate
    complains about, no more and no less. Reimplementing "live" here would let the
    two drift into churning every project's configuration over statuses the gate
    ignores, or leaving the ones it reports.

    A route is *added* to the row rather than the row rewritten, so the moves
    already declared keep their order and any comment grouping them (SR-0183). A
    document this editor will not touch on a guess is refused whole, before
    anything is written, and named with the command that makes the change by hand
    — a corrupted `throughline.toml` invalidates the entire project, so a repair
    that cannot be made safely must not be made at all.

    Idempotent: a widened row permits the move and so is no longer reported.
    """
    project = load_project(root)
    gaps = project.suspect_unreachable_statuses()
    if not gaps:
        return {}
    suspect = project.schema.status_roles["suspect"]
    declared = project.config.get("transitions") or {}
    cfg_file = root / CONFIG_NAME
    doc = TomlDocument(cfg_file.read_text(encoding="utf-8"))
    if not doc.has_table("transitions"):
        # The schema saw a `transitions` key, so the project declares one — but not
        # as a [transitions] header, and writing one here would define the table
        # twice and stop the file parsing at all. Refuse rather than corrupt it.
        raise ProjectError(
            f"{cfg_file} declares its transitions somewhere other than a "
            "[transitions] table, which `tl migrate` will not edit on a guess; "
            f"allow the moves to '{suspect}' by hand, or one at a time with "
            f"`tl schema transition allow <status> {suspect} --because …`")
    for status in gaps:
        because = (
            f"`tl migrate` added the move to '{suspect}' (SR-0188): this "
            f"lifecycle gave '{status}' no route to it, so an item resting in "
            f"'{status}' could never be marked suspect and invalidating anything "
            "it grounds in would have left it unflagged. Narrow it back "
            f"deliberately with `tl schema transition deny {status} {suspect}`.")
        try:
            if status in declared:
                doc.add_to_array("transitions", status, [suspect], because=because)
            else:
                doc.set_key("transitions", status, [suspect], because=because)
        except TomlEditError as e:
            raise ProjectError(
                f"cannot add the '{status}' -> '{suspect}' transition to "
                f"{cfg_file} safely: {e}. Add it by hand, or with `tl schema "
                f"transition allow {status} {suspect} --because …`") from e
    cfg_file.write_text(doc.text(), encoding="utf-8")
    return {status: suspect for status in gaps}


#: The empty mapping the two record fields default to, shared but unwritable.
_NO_STRINGS: Mapping[str, str] = MappingProxyType({})


class RepairResult(NamedTuple):
    """What a major's repair wrote: the configuration bindings it backfilled
    (SR-0137), the routes to suspicion it restored (SR-0188), the vocabularies it
    declared (SR-0185), the ratification records it bound (SR-0152), and the
    ratified revisions it cached (SR-0166)."""
    config: dict[str, str] | None
    routes: dict[str, str]
    vocabularies: dict[str, list[str]]
    stamps: dict[str, str]
    revisions: dict[str, str]
    # The normative flags rewritten to the type's value (SR-0203), the link stamps
    # refreshed because they matched the content before the rewrite, and the
    # ratified items the rewrite left awaiting a person.
    normative: dict[str, bool] = {}
    restamped: list[tuple[str, str]] = []
    stale: list[str] = []
    # The ratification records given the content their stamp was taken over, and
    # the reason each unproved one could not be. Immutable defaults: a NamedTuple
    # evaluates a default once and hands the same object to every caller.
    contents: Mapping[str, str] = _NO_STRINGS
    unprovable: Mapping[str, str] = _NO_STRINGS


def _repair_normative_flags(root: Path) -> tuple[dict[str, bool],
                                                 list[tuple[str, str]], list[str]]:
    """Rewrite every item whose ``normative`` flag disagrees with what its type
    declares (SR-0203). Returns the flags rewritten, the link stamps refreshed and
    the ratified items now stale. A type that declares nothing is left alone, so
    a project that never opted in is untouched by upgrading.

    Before a type could declare the flag (SR-0201) every item was written
    normative, so a graph that now declares its intents and non-goals otherwise
    holds items that disagree with their own kind. The flag is a fingerprint
    input, so this is a content change and is treated as one: the ratification
    record is not touched — only ratification writes it (SR-0170), and a stamp
    replaced without the graph showing the change would be the thing SR-0148
    forbids — so a rewritten item that is ratified reads as stale until a person,
    shown that only the flag moved, signs again.

    A link stamp is different. It names nobody and records only the content a
    link was confirmed against, so a stamp equal to the item's fingerprint before
    the rewrite is refreshed to the fingerprint after it: the wording it confirmed
    has not moved. A stamp that already disagreed was suspect before this and is
    left as it was.

    Idempotent: a rewritten item agrees with its type and never matches again.
    """
    project = load_project(root)
    schema = project.schema
    before: dict[str, str] = {}
    after: dict[str, str] = {}
    rewritten: dict[str, bool] = {}
    stale: list[str] = []
    for item in project.items():
        if item.is_deleted:
            continue
        want = schema.declares_normative(item.type)
        if want is None or item.normative == want:
            continue
        before[item.uid] = fingerprint(item, schema)
        item.normative = want
        after[item.uid] = fingerprint(item, schema)
        write_item(item)
        rewritten[item.uid] = want
        if item.attrs.get("ratified_fingerprint"):
            stale.append(item.uid)
    restamped: list[tuple[str, str]] = []
    if rewritten:
        for item in project.items():
            if item.is_deleted:
                continue
            touched = False
            for link in item.links:
                if link.target in before and link.stamp == before[link.target]:
                    link.stamp = after[link.target]
                    restamped.append((item.uid, link.target))
                    touched = True
            if touched:
                write_item(item)
    return rewritten, restamped, stale


def _repair_status_roles_major(root: Path, index: Index | None) -> RepairResult:
    """The repair for the major that requires [status.roles] (SR-0137, SR-0152,
    SR-0185, SR-0188).

    Ordered, not merely grouped, and the order is load-bearing three times over.
    The role bindings come first because everything after them reads a status
    through a role — the suspect route has no target until the suspect role is
    bound — and because the status vocabulary is written from what the
    configuration relies on, so a role written second would be omitted from the
    very vocabulary that has to contain it and leave the schema refusing to build.
    The suspect routes come before that vocabulary for the same reason from the
    other side: they widen the transitions table, and the vocabulary is derived
    from the table's endpoints, so declaring it first could leave it short of a
    status the table now names. The record backfill comes last because it resolves
    the project's schema, so everything that schema requires has to be on disk
    before it runs. Each part is idempotent, so the sequence is.

    ``index`` reaches only the record backfill (SR-0153): a grounding view has
    nothing to say about which declared status plays which role, which moves a
    lifecycle should permit, or which values a vocabulary should hold, so passing
    it to any configuration half would imply an influence it does not have."""
    roles = _backfill_status_roles(root)
    routes = _backfill_suspect_routes(root)
    vocabularies = _backfill_vocabularies(root)
    # Signed content before the flags (SR-0218): a record whose item reproduces its
    # stamp now is proved by the item as it stands, and the flag repair below may
    # change a fingerprint input and leave only history to prove it from.
    contents, unprovable = _backfill_ratification_contents(root)
    # The flags before the record backfill (SR-0203): a record bound below is
    # bound to the content as it stands, which should be the content after the
    # flag has been put right, not a stamp that goes stale a line later.
    normative, restamped, stale = _repair_normative_flags(root)
    stamps = _backfill_ratification_stamps(root, index=index)
    # Last, and after the stamps: a record bound a moment ago has a stamp to
    # resolve against, so binding first is what lets one `tl migrate` both
    # complete a record and cache its revision.
    revisions = _backfill_ratification_revisions(root)
    return RepairResult(roles, routes, vocabularies, stamps, revisions,
                        normative, restamped, stale, contents, unprovable)


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
    they are looking at.

    ``declared`` maps each vocabulary the repair declared to the members it wrote
    (SR-0185), and is empty when the project already declared them. Separate again
    for the same reason: this one decides what the project's items will be
    validated against from now on, so it is the part an author is most likely to
    want to narrow, and it cannot be left to be inferred from a count.

    ``routed`` maps each status the repair gave a route to suspicion to the suspect
    status it can now reach (SR-0188), and is empty when suspicion already reached
    them all. Separate for the third time, and for the sharpest version of the same
    reason: this one widens what the lifecycle permits, which is the only part of
    the repair that lets an item make a move the project previously forbade."""
    start: int
    end: int
    repaired: dict[str, str] | None
    routed: dict[str, str]
    declared: dict[str, list[str]]
    bound: dict[str, str]
    cached: dict[str, str]
    # Normative flags rewritten to the type's value (SR-0203), the link stamps
    # refreshed because they matched the content before it, and the ratified
    # items the rewrite left awaiting re-ratification.
    normative: dict[str, bool] = {}
    restamped: list[tuple[str, str]] = []
    stale: list[str] = []
    # Ratification records completed with their signed content, and the reason each
    # unproved one could not be (SR-0218).
    contents: Mapping[str, str] = _NO_STRINGS
    unprovable: Mapping[str, str] = _NO_STRINGS


def migrate_project(path: str | Path, *,
                    index: Index | None = None) -> MigrationResult:
    """Bring a project's on-disk format to what the current major requires
    (NFR-0010).

    Returns a :class:`MigrationResult`. Raises ``ProjectError`` when
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
    result = (repair(root, index) if repair is not None
              else RepairResult(None, {}, {}, {}, {}))
    return MigrationResult(start, current, result.config, result.routes,
                           result.vocabularies, result.stamps, result.revisions,
                           result.normative, result.restamped, result.stale,
                           result.contents, result.unprovable)


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
        raw = _load_yaml(manifest.read_text(encoding="utf-8")) or {}
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
            d = _load_yaml(item_file.read_text(encoding="utf-8")) or {}
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

class Baseline(NamedTuple):
    """The status each item had at the baseline check measures the working tree
    against (SR-0083, SR-0093), or why it could not be read (SR-0209).

    ``statuses`` maps ``uid -> status`` and is ``None`` exactly when
    ``unavailable`` says why. A baseline that was read but holds nothing, as in a
    repository with no commits yet, is an empty map: every item is new."""
    statuses: dict[str, str] | None
    unavailable: str | None = None


class _GitTree:
    """The project's files as they stood at a git revision, read through one
    batched `git cat-file` rather than a process per file."""

    def __init__(self, top: Path, ref: str, prefix: str):
        self.top, self.ref, self.prefix = top, ref, prefix

    def files(self) -> list[str]:
        out = subprocess.run(
            ["git", "-C", str(self.top), "ls-tree", "-r", "-z", "--name-only",
             self.ref, "--", self.prefix or "."],
            capture_output=True, check=True).stdout
        names = [n.decode("utf-8", "surrogateescape") for n in out.split(b"\0") if n]
        return [n[len(self.prefix):] for n in names if n.startswith(self.prefix)]

    def read(self, rels: list[str]) -> dict[str, str]:
        if not rels:
            return {}
        request = "".join(f"{self.ref}:{self.prefix}{rel}\n" for rel in rels)
        out = subprocess.run(
            ["git", "-C", str(self.top), "cat-file", "--batch"],
            input=request.encode("utf-8", "surrogateescape"),
            capture_output=True, check=True).stdout
        found: dict[str, str] = {}
        at = 0
        for rel in rels:
            nl = out.index(b"\n", at)
            header = out[at:nl].decode("utf-8", "surrogateescape")
            at = nl + 1
            if header.endswith((" missing", " ambiguous")):
                continue
            _sha, kind, size = header.rsplit(" ", 2)
            body = out[at:at + int(size)]
            at += int(size) + 1
            if kind == "blob":
                found[rel] = body.decode("utf-8", "replace")
        return found


class _DirectoryTree:
    """The project's files as they stood, supplied by a host as a directory laid
    out as the working tree is (SR-0210)."""

    def __init__(self, root: Path):
        self.root = root

    def files(self) -> list[str]:
        return sorted(p.relative_to(self.root).as_posix()
                      for p in self.root.rglob("*.yml") if p.is_file())

    def read(self, rels: list[str]) -> dict[str, str]:
        found: dict[str, str] = {}
        for rel in rels:
            path = self.root / rel
            if path.is_file():
                found[rel] = path.read_text(encoding="utf-8", errors="replace")
        return found


def _statuses_in(project: Project, tree) -> dict[str, str]:
    """Map ``uid -> status`` from the project's files as ``tree`` holds them.

    The one reading of a baseline, whichever source supplies the files. Two rules
    consume it: transition legality (SR-0083), which looks up items still present,
    and tombstone permanence (SR-0093), which needs items present at the baseline
    but gone now, a UID whose file was erased by a bad merge or a stray `git rm`.
    A current item's status is read from its own path first; a file at a path no
    current item occupies is keyed by the UID it names, so an item moved between
    registers keeps the status it had."""
    root = Path(project.path).resolve()
    present: dict[str, str] = {}
    for item in project.items():
        if item._path is None:
            continue
        try:
            present[item._path.resolve().relative_to(root).as_posix()] = item.uid
        except ValueError:
            continue
    rels = [r for r in tree.files() if Path(r).name not in _ALL_MANIFEST_NAMES]
    out: dict[str, str] = {}
    absent: list[tuple[str, str]] = []
    for rel, text in tree.read(rels).items():
        try:
            data = _load_yaml(text) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue
        status = data.get("status")
        if not isinstance(status, str):
            continue
        if rel in present:
            out[present[rel]] = status
        elif isinstance(data.get("uid"), str):
            absent.append((data["uid"], status))
    for uid, status in absent:
        out.setdefault(uid, status)
    return out


def read_baseline(project: Project, *, ref: str | None = "HEAD",
                  base_dir: str | Path | None = None) -> Baseline:
    """Read the baseline from ``base_dir``, a copy of the project as it stood
    (SR-0210), or else from git revision ``ref``. A falsy ``ref`` means the caller
    disabled the baseline. Where it cannot be read, the result says why rather than
    passing for a baseline in which nothing moved (SR-0209).

    Raises ``ProjectError`` when ``base_dir`` names no directory: a baseline the
    caller supplied and mistyped fails the command, rather than reading as absent."""
    if base_dir is not None:
        root = Path(base_dir)
        if not root.is_dir():
            raise ProjectError(f"baseline directory {root} does not exist")
        return Baseline(_statuses_in(project, _DirectoryTree(root.resolve())))
    if not ref:
        return Baseline(None, "the baseline was disabled")
    root = Path(project.path).resolve()
    try:
        top = Path(subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True).stdout.strip())
    except subprocess.CalledProcessError:
        return Baseline(None, "the project is not in a git work tree")
    except (FileNotFoundError, OSError):
        return Baseline(None, "git cannot be run")
    try:
        if subprocess.run(["git", "-C", str(top), "rev-parse", "--verify", "--quiet",
                           f"{ref}^{{commit}}"], capture_output=True).returncode != 0:
            # With no commits at all there is nothing earlier to compare, so every
            # item is new; otherwise the revision named really is missing.
            commits = subprocess.run(["git", "-C", str(top), "rev-list", "-n", "1", "--all"],
                                     capture_output=True, text=True).stdout.strip()
            if not commits:
                return Baseline({})
            return Baseline(None, f"the revision '{ref}' cannot be resolved")
        # Scope to this project's own subtree: a project may sit beside another in
        # one repository, and a sibling's tombstone is not this project's record.
        try:
            proj_rel = root.relative_to(top)
            prefix = "" if proj_rel == Path(".") else proj_rel.as_posix() + "/"
        except ValueError:
            prefix = ""
        return Baseline(_statuses_in(project, _GitTree(top, ref, prefix)))
    except (FileNotFoundError, OSError):
        return Baseline(None, "git cannot be run")
    except subprocess.CalledProcessError:
        return Baseline(None, f"the revision '{ref}' cannot be read")


def baseline_note(schema, baseline: Baseline) -> str | None:
    """The line check prints beside its result when the baseline rules did not run
    (SR-0209): which rules, and why. ``None`` when they ran. Transition legality is
    named only where the project declares transitions, since without a table it
    has nothing to check."""
    if baseline.unavailable is None:
        return None
    rules = (["transition legality"] if schema.transitions is not None else [])
    rules.append("tombstone permanence")
    return f"not checked: {' and '.join(rules)} — {baseline.unavailable}"


def baseline_statuses(project: Project, ref: str = "HEAD") -> dict[str, str] | None:
    """``uid -> status`` at git ``ref``, or ``None`` where it cannot be read. Kept
    for callers of earlier releases; :func:`read_baseline` also says why."""
    return read_baseline(project, ref=ref).statuses


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
    # The flag on each seeded item is the type's (SR-0202): the demo and `tl new`
    # read the same declaration, so a fresh project holds one value per kind.
    normative = load_project(root).schema.is_normative
    items = [
        Item(uid="INT-0001", type="intent", status="approved",
             normative=normative("intent"),
             title=f"Deliver {name}",
             text="Describe the outcome this project exists to create. Every "
                  "requirement below grounds back to this intent."),
        Item(uid="REQ-0001", type="requirement", status="approved",
             normative=normative("requirement"),
             title="First requirement",
             text="State something the system shall do, then replace this with a "
                  "real requirement.",
             links=[Link(target="INT-0001", type="implements")],
             attrs={"priority": "must", "origin": "human"}),
        Item(uid="NFR-0001", type="nfr", status="approved",
             normative=normative("nfr"),
             title="First quality attribute",
             text="State a quality the system shall have (performance, security, "
                  "usability), then replace this.",
             links=[Link(target="INT-0001", type="implements")],
             attrs={"origin": "human"}),
        Item(uid="TEST-0001", type="test", status="approved",
             normative=normative("test"),
             title="Verifies the first requirement",
             text="Describe how REQ-0001 is checked. This verifies link satisfies "
                  "the coverage rule declared in throughline.toml.",
             links=[Link(target="REQ-0001", type="verifies")]),
        Item(uid="NG-0001", type="non_goal", status="approved",
             normative=normative("non_goal"),
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

# `normative = false` on a type means its items are not binding statements:
# they feed no ratification drift of their own and need reach no published
# document. An intent is the why, a non_goal is excluded scope, a test verifies
# rather than binds. Absent, a type is normative.
[types.intent]
normative = false

[types.requirement]
attrs.priority = {{ type = "enum", values = ["must", "should", "could"], normative = true }}
attrs.origin   = {{ type = "enum", values = ["human", "ai", "hybrid"] }}

[types.nfr]
attrs.origin = {{ type = "enum", values = ["human", "ai", "hybrid"] }}

[types.test]
normative = false

# A non_goal records deliberately-excluded scope — the object a human points at
# to reject a category of proposed work. Passive by design: throughline surfaces
# non_goals in `tl context` but never tries to detect items that 'violate' one.
[types.non_goal]
normative = false
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
# Every live status also keeps a route to 'suspect' (SR-0175), so that an item
# whose grounding is withdrawn can be flagged wherever it currently sits — the
# statuses below 'approved' hold the items whose grounding is least settled.
[transitions]
proposed    = ["draft", "approved", "ratified", "deferred", "suspect", "rejected", "deleted"]
draft       = ["proposed", "approved", "deferred", "suspect", "rejected", "deleted"]
deferred    = ["proposed", "draft", "approved", "suspect", "rejected", "deleted"]
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


def create_register(project, prefix: str, directory, *, title: str | None = None,
                    digits: int = 4, parent: str | None = None):
    """Add a register — the prefix-owning collection a UID namespace belongs to
    (SR-0011, SR-0102) — and write its manifest.

    The prefix must satisfy the UID grammar (SR-0140): one that does not would be
    accepted here and then break allocation for every item it owns. It must also be
    unused, because a prefix owns a namespace across the whole project (SR-0101)
    and a duplicate makes the loader drop one register's items silently.
    """
    from .model import Register
    from .uid import PREFIX_GRAMMAR, valid_prefix
    root = Path(project.path)
    if not valid_prefix(prefix):
        raise ProjectError(f"prefix '{prefix}' is not a valid UID prefix — expected "
                           f"{PREFIX_GRAMMAR}; see doc 06 §3")
    existing = project.registers.get(prefix)
    if existing is not None:
        raise ProjectError(f"prefix '{prefix}' is already used by the register at "
                           f"{existing.path} — prefixes must be unique across the "
                           "project")
    reg_dir = root / directory
    if (reg_dir / MANIFEST_NAME).exists():
        raise ProjectError(f"{reg_dir} already has a {MANIFEST_NAME}")
    reg_dir.mkdir(parents=True, exist_ok=True)
    reg = Register(prefix=prefix, title=title or prefix, digits=digits,
                   parent=parent, path=reg_dir)
    write_manifest(reg)
    project.registers[prefix] = reg
    return reg
