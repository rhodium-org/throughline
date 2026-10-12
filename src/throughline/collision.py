# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Resolve a parallel-branch UID collision inside the merge that exposed it
(SR-0248, refining SR-0006).

Git already detects the collision. An item lives at ``<register>/<UID>.yml``
(SR-0249), so two branches that allocate the same number add the same path, and
the merge stops on an add/add conflict. No git hook can apply the fix — neither
``post-merge`` nor ``pre-merge-commit`` runs on a conflicted merge, and a merge
driver runs per file under the index lock — but the conflicted index holds both
versions, and that is all a whole-graph command needs:

* an unmerged path with stages 2 and 3 and no stage 1 was *added* on both sides;
* the side already on the trunk keeps the UID, so nothing merged ever moves;
* the other side's item takes the next number unused on either side or the trunk;
* every link the moved side's changes made to the old UID is rewritten. Inside the
  merge this is unambiguous: a file that only the moved side changed can only have
  meant the moved side's item. Where the kept side also linked to the old UID from
  the same file, no rule can say which link meant which item, so the plan refuses.

Everything is planned before anything is written, so a refusal changes nothing.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .signed_links import LINKS_ATTR
from .storage import CONFIG_NAME, MANIFEST_NAME, _load_yaml
from .uid import UID_RE, format_uid

OURS, THEIRS = "ours", "theirs"


class CollisionError(Exception):
    """The merge cannot be resolved as asked; nothing has been changed."""


@dataclass
class Move:
    """One collision: ``old`` stays on the kept side, the other side's item
    becomes ``new``."""
    old: str
    new: str
    path: str                 # repo-relative path both sides added
    new_path: str             # repo-relative path the moved item is written to
    kept_text: str
    moved_text: str           # already renumbered and relinked
    kept_title: str = ""
    moved_title: str = ""
    moved_ratified: bool = False


@dataclass
class Relink:
    """A file outside the collisions whose links to a moved UID are rewritten."""
    path: str
    text: str
    count: int
    signed_stale: bool = False   # its ratification record names an old UID


@dataclass
class Plan:
    top: Path
    keep: str                 # OURS or THEIRS
    kept_rev: str
    moved_rev: str
    base: str
    trunk: str | None
    moves: list[Move] = field(default_factory=list)
    relinks: list[Relink] = field(default_factory=list)
    mentions: list[tuple[str, str, str]] = field(default_factory=list)  # (old, where, line)
    commits: list[tuple[str, str]] = field(default_factory=list)       # (old, "sha subject")
    other_conflicts: list[str] = field(default_factory=list)


# --------------------------------------------------------------------- git plumbing

def _git(top: Path, *args: str, check: bool = True) -> str | None:
    done = subprocess.run(["git", "-C", str(top), *args],
                          capture_output=True, text=True)
    if done.returncode != 0:
        if check:
            raise CollisionError(f"git {' '.join(args)} failed: "
                                 f"{done.stderr.strip() or done.returncode}")
        return None
    return done.stdout


def _show(top: Path, spec: str) -> str | None:
    """A blob by ``rev:path`` or ``:stage:path``, or None when it is absent."""
    return _git(top, "show", spec, check=False)


def _rev(top: Path, name: str) -> str | None:
    out = _git(top, "rev-parse", "-q", "--verify", f"{name}^{{commit}}", check=False)
    return out.strip() if out else None


def _git_path_exists(top: Path, name: str) -> bool:
    out = _git(top, "rev-parse", "--git-path", name, check=False)
    if not out:
        return False
    p = Path(out.strip())
    return (p if p.is_absolute() else top / p).exists()


def _is_ancestor(top: Path, rev: str, of: str) -> bool:
    return subprocess.run(["git", "-C", str(top), "merge-base", "--is-ancestor", rev, of],
                          capture_output=True).returncode == 0


def _default_trunk(top: Path) -> str | None:
    """The branch whose items are taken as already merged: the remote's default
    branch where git knows it, else the conventional names."""
    out = _git(top, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD",
               check=False)
    if out and out.strip():
        return out.strip()
    for name in ("origin/main", "origin/master", "main", "master"):
        if _rev(top, name):
            return name
    return None


def _unmerged(top: Path, scope: str) -> dict[str, set[int]]:
    """``path -> stages present`` for every unmerged path under ``scope``."""
    out = _git(top, "ls-files", "-u", "-z", "--", scope or ".") or ""
    stages: dict[str, set[int]] = {}
    for entry in out.split("\0"):
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        stages.setdefault(path, set()).add(int(meta.split()[2]))
    return stages


def _tree_names(top: Path, rev: str, directory: str) -> list[str]:
    out = _git(top, "ls-tree", "-z", "--name-only", rev, "--", f"{directory}/",
               check=False) or ""
    return [Path(n).name for n in out.split("\0") if n]


def _changed(top: Path, a: str, b: str, scope: str) -> list[str]:
    out = _git(top, "diff", "--name-only", "-z", "--no-renames", a, b, "--",
               scope or ".") or ""
    return [p for p in out.split("\0") if p]


# ----------------------------------------------------------------- text rewriting

def _load(text: str | None) -> dict:
    if text is None:
        return {}
    try:
        data = _load_yaml(text)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _targets(data: dict) -> list[str]:
    links = data.get("links")
    if not isinstance(links, list):
        return []
    return [ln.get("target") for ln in links
            if isinstance(ln, dict) and isinstance(ln.get("target"), str)]


_UID_LINE = re.compile(r"^(uid:[ \t]*)(['\"]?)([A-Z][A-Z0-9]*-[0-9]+)\2([ \t]*)$",
                       re.MULTILINE)
_TARGET = re.compile(r"^(\s*(?:-\s+)?target:[ \t]*)(['\"]?)([A-Z][A-Z0-9]*-[0-9]+)\2"
                     r"([ \t]*)$")


def _expected(data: dict, mapping: dict[str, str], uid: str | None) -> dict:
    want = dict(data)
    if uid is not None:
        want["uid"] = uid
    if isinstance(data.get("links"), list):
        want["links"] = [
            {**ln, "target": mapping.get(ln["target"], ln["target"])}
            if isinstance(ln, dict) and isinstance(ln.get("target"), str) else ln
            for ln in data["links"]]
    return want


def rewrite(text: str, mapping: dict[str, str], *, uid: str | None = None,
            where: str = "") -> tuple[str, int]:
    """Rewrite the item's own ``links`` targets through ``mapping`` and, given
    ``uid``, its ``uid`` — touching nothing else, the ratification record
    included: that record is what a human signed, and rewriting it would forge
    a signature (SR-0244). Returns the new text and the number of links moved.

    The edit is made on the text so the file keeps its exact layout, and then
    proven on the parse: anything other than the intended change is refused."""
    before = _load(text)
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    in_links = False
    count = 0
    for line in lines:
        if line[:1] not in (" ", "\t", "-", "\n", "\r", "#", ""):
            in_links = line.startswith("links:")
        if in_links:
            m = _TARGET.match(line.rstrip("\r\n"))
            if m and m.group(3) in mapping:
                ending = line[len(line.rstrip("\r\n")):]
                line = (f"{m.group(1)}{m.group(2)}{mapping[m.group(3)]}"
                        f"{m.group(2)}{m.group(4)}{ending}")
                count += 1
        out.append(line)
    new = "".join(out)
    if uid is not None:
        new, n = _UID_LINE.subn(lambda m: f"{m.group(1)}{m.group(2)}{uid}"
                                          f"{m.group(2)}{m.group(4)}", new, count=1)
        if n != 1:
            raise CollisionError(f"{where}: cannot find its uid line to renumber it")
    if _load(new) != _expected(before, mapping, uid):
        raise CollisionError(f"{where}: its layout is not one tl wrote, so its links "
                             "cannot be rewritten safely — fix it by hand")
    return new, count


# ------------------------------------------------------------------------ planning

def _register(top: Path, directory: str, revs: list[str]) -> tuple[str, int, set[int]]:
    """(prefix, digits, reserved) for the register in ``directory``, read from the
    working tree or, failing that, from whichever side has the manifest."""
    rel = f"{directory}/{MANIFEST_NAME}"
    texts = [(top / rel).read_text(encoding="utf-8")
             if (top / rel).is_file() else None]
    texts += [_show(top, f"{rev}:{rel}") for rev in revs]
    reserved: set[int] = set()
    prefix, digits = None, 4
    for text in texts:
        data = _load(text)
        if not data.get("prefix"):
            continue
        prefix = prefix or str(data["prefix"])
        digits = int(data.get("digits", digits) or digits)
        reserved |= {int(n) for n in data.get("reserved") or [] if str(n).isdigit()}
    if prefix is None:
        raise CollisionError(f"{directory} has no register manifest")
    return prefix, digits, reserved


def _used(top: Path, directory: str, prefix: str, revs: list[str]) -> set[int]:
    names = {p.name for p in (top / directory).glob("*.yml")}
    for rev in revs:
        names |= set(_tree_names(top, rev, directory))
    used: set[int] = set()
    for name in names:
        m = UID_RE.match(Path(name).stem)
        if m and m.group(1) == prefix and name.endswith(".yml"):
            used.add(int(m.group(2)))
    return used


def _choose_keep(top: Path, keep: str | None, trunk: str | None) -> str:
    if keep:
        return keep
    if trunk is None:
        raise CollisionError("cannot tell which side is already merged: no trunk "
                             "branch found — pass --trunk <branch> or "
                             "--keep ours|theirs")
    ours = _is_ancestor(top, "HEAD", trunk)
    theirs = _is_ancestor(top, "MERGE_HEAD", trunk)
    if ours != theirs:
        return OURS if ours else THEIRS
    which = "both sides are" if ours else "neither side is"
    raise CollisionError(f"{which} on {trunk}, so the UID's owner cannot be chosen "
                         "for you — pass --keep ours|theirs (the side whose item "
                         "keeps its number)")


def plan(project_path: str | Path, *, keep: str | None = None,
         trunk: str | None = None) -> Plan:
    """Work out the whole resolution without writing anything (SR-0248)."""
    root = Path(project_path).resolve()
    if not (root / CONFIG_NAME).is_file():
        raise CollisionError(f"no {CONFIG_NAME} at {root} — not a throughline project")
    out = _git(root, "rev-parse", "--show-toplevel", check=False)
    if not out:
        raise CollisionError(f"{root} is not inside a git repository")
    top = Path(out.strip()).resolve()
    scope = root.relative_to(top).as_posix()
    scope = "" if scope == "." else scope

    for marker, what in (("rebase-merge", "a rebase"), ("rebase-apply", "a rebase"),
                         ("CHERRY_PICK_HEAD", "a cherry-pick"),
                         ("REVERT_HEAD", "a revert")):
        if _git_path_exists(top, marker):
            raise CollisionError(
                f"{what} is in progress — tl resolve works inside a merge, whose two "
                "sides say which item each link meant. Abort it, merge instead, and "
                "run tl resolve when the merge stops")
    if not _git_path_exists(top, "MERGE_HEAD"):
        raise CollisionError("no merge in progress — run tl resolve when "
                             "`git merge` stops on an add/add conflict")
    heads = Path(_git(top, "rev-parse", "--git-path", "MERGE_HEAD").strip())
    heads = heads if heads.is_absolute() else top / heads
    if len(heads.read_text(encoding="utf-8").split()) != 1:
        raise CollisionError("an octopus merge is in progress — merge one branch at "
                             "a time to resolve UID collisions")

    trunk = trunk or _default_trunk(top)
    if trunk is not None and not _rev(top, trunk):
        raise CollisionError(f"trunk {trunk} is not a commit here")
    keep = _choose_keep(top, keep, trunk)
    kept_rev, moved_rev = ("HEAD", "MERGE_HEAD") if keep == OURS else ("MERGE_HEAD", "HEAD")
    kept_stage, moved_stage = (2, 3) if keep == OURS else (3, 2)
    base = (_git(top, "merge-base", "HEAD", "MERGE_HEAD", check=False) or "").strip()
    if not base:
        raise CollisionError("the two sides share no history, so neither can be "
                             "taken as having added an item first")

    unmerged = _unmerged(top, scope)
    collisions: list[tuple[str, str, int, str]] = []   # (path, uid, digits, dir)
    for path, stages in sorted(unmerged.items()):
        p = Path(path)
        m = UID_RE.match(p.stem)
        if stages != {2, 3} or p.suffix != ".yml" or not m:
            continue
        directory = p.parent.as_posix()
        prefix, digits, _ = _register(top, directory, [kept_rev, moved_rev])
        if m.group(1) != prefix:
            continue
        collisions.append((path, p.stem, digits, directory))
    result = Plan(top=top, keep=keep, kept_rev=kept_rev, moved_rev=moved_rev,
                  base=base, trunk=trunk)
    collided = {c[0] for c in collisions}
    result.other_conflicts = sorted(p for p in unmerged if p not in collided)
    if not collisions:
        return result

    # Number the moved items. Every number either side or the trunk has ever used
    # is off limits, as is every number either side reserved (SR-0003).
    revs = [kept_rev, moved_rev] + ([trunk] if trunk else [])
    mapping: dict[str, str] = {}
    paths: dict[str, str] = {}
    taken: dict[str, set[int]] = {}
    for path, uid, digits, directory in collisions:
        prefix, digits, reserved = _register(top, directory, revs)
        used = taken.setdefault(directory, _used(top, directory, prefix, revs) | reserved)
        n = max(used) + 1
        used.add(n)
        new = format_uid(prefix, n, digits)
        mapping[uid] = new
        paths[uid] = f"{directory}/{new}.yml"

    # Which files may be relinked: those the moved side changed. A file the kept
    # side (or the merge base) also points at an old UID from cannot be told apart.
    moved_changed = set(_changed(top, base, moved_rev, scope))
    kept_changed = set(_changed(top, base, kept_rev, scope))
    for path in sorted(moved_changed - collided):
        if not path.endswith(".yml") or Path(path).name == MANIFEST_NAME:
            continue
        hits = sorted(set(_targets(_load(_show(top, f"{moved_rev}:{path}")))) & set(mapping))
        if not hits:
            continue
        theirs_too = set(_targets(_load(_show(top, f"{base}:{path}"))))
        if path in kept_changed:
            theirs_too |= set(_targets(_load(_show(top, f"{kept_rev}:{path}"))))
        clash = sorted(theirs_too & set(hits))
        if clash:
            raise CollisionError(
                f"{path} links to {', '.join(clash)} on both sides of the merge, so "
                "which item each link means cannot be told — resolve this collision "
                "by hand")
        if path in unmerged:
            raise CollisionError(
                f"{path} is itself in conflict and links to {', '.join(hits)} — "
                "resolve that conflict first, then run tl resolve again")
        work = top / path
        if not work.is_file():
            continue                      # the kept side deleted it; nothing to relink
        text, count = rewrite(work.read_text(encoding="utf-8"), mapping, where=path)
        if count:
            signed = {t for _, t in _signed(text)}
            result.relinks.append(Relink(path=path, text=text, count=count,
                                         signed_stale=bool(signed & set(mapping))))

    for path, uid, _digits, _dir in collisions:
        kept_text = _show(top, f":{kept_stage}:{path}") or ""
        moved_text = _show(top, f":{moved_stage}:{path}") or ""
        for side, text in (("kept", kept_text), ("moved", moved_text)):
            if _load(text).get("uid") != uid:
                raise CollisionError(f"{path}: the {side} side's file does not say "
                                     f"uid: {uid}, so it is not that item — resolve "
                                     "this conflict by hand")
        moved_data = _load(moved_text)
        new_text, _ = rewrite(moved_text, mapping, uid=mapping[uid],
                              where=f"{path} ({'theirs' if keep == OURS else 'ours'})")
        attrs = moved_data.get("attrs") if isinstance(moved_data.get("attrs"), dict) else {}
        result.moves.append(Move(
            old=uid, new=mapping[uid], path=path, new_path=paths[uid],
            kept_text=kept_text, moved_text=new_text,
            kept_title=str(_load(kept_text).get("title") or ""),
            moved_title=str(moved_data.get("title") or ""),
            moved_ratified=bool(attrs.get("ratified_fingerprint"))))

    _what_cannot_move(top, result, mapping)
    return result


def _signed(text: str) -> list[tuple[str, str]]:
    attrs = _load(text).get("attrs")
    raw = attrs.get(LINKS_ATTR) if isinstance(attrs, dict) else None
    return [(e.get("type"), e.get("target")) for e in raw or []
            if isinstance(e, dict)] if isinstance(raw, list) else []


def _what_cannot_move(top: Path, result: Plan, mapping: dict[str, str]) -> None:
    """The old UIDs the moved side cited where no tool can follow: prose it added
    and the subjects of its commits. Reported, never rewritten."""
    words = re.compile(r"\b(" + "|".join(map(re.escape, mapping)) + r")\b")
    diff = _git(top, "diff", "-U0", "--no-color", "--no-renames", result.base,
                result.moved_rev, check=False) or ""
    current = ""
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else ""
            continue
        if not line.startswith("+") or not current:
            continue
        body = line[1:]
        if re.match(r"\s*(?:-\s+)?(?:uid|target):", body) and current.endswith(".yml"):
            continue                      # rewritten above
        for m in words.finditer(body):
            result.mentions.append((m.group(1), current, body.strip()))
    log = _git(top, "log", "--format=%h %s", f"{result.base}..{result.moved_rev}",
               check=False) or ""
    for line in log.splitlines():
        for m in set(words.findall(line)):
            result.commits.append((m, line))


# ------------------------------------------------------------------------ applying

def apply(result: Plan) -> list[str]:
    """Write and stage the planned resolution; returns the paths staged. The
    merge is left for the author to check and commit."""
    staged: list[str] = []
    for move in result.moves:
        (result.top / move.path).write_text(move.kept_text, encoding="utf-8")
        (result.top / move.new_path).write_text(move.moved_text, encoding="utf-8")
        staged += [move.path, move.new_path]
    for relink in result.relinks:
        (result.top / relink.path).write_text(relink.text, encoding="utf-8")
        staged.append(relink.path)
    if staged:
        _git(result.top, "add", "--", *staged)
    return staged


__all__ = ["CollisionError", "Move", "Plan", "Relink", "apply", "plan", "rewrite",
           "OURS", "THEIRS"]
