# How to use throughline

A hands-on quick start. In about fifteen minutes you'll scaffold a project, add
three linked requirements, watch the validator reject an ungrounded graph, fix
it, and trace a requirement back to its reason for existing.

This guide documents what the tool does. If a capability isn't covered here,
it isn't in the tool.

---

## 1. Install

throughline is a small Python package (needs Python ≥ 3.11 for `tomllib`). Install it
into a virtual environment:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"        # the tool + pytest
```

That puts a **`tl`** command on your PATH.

> **Name note.** The command is `tl`, *not* `rmt` — `rmt` is a standard Unix
> tape-drive utility (`/usr/sbin/rmt`) and throughline deliberately does not shadow it
> (see requirement SR-0074). If `tl` isn't found after install, your venv
> isn't active, or `~/.local/bin` (or the venv's `bin`) isn't on your PATH.

No local Python? Run it containerised:

```bash
docker build -t throughline .
docker run --rm -v "$PWD/my-project:/work" throughline -C /work check --strict
```

---

## 2. The whole idea in 60 seconds

- Every requirement is **one small YAML file** with a permanent UID (e.g.
  `FR-0001`). The files are the product; they live in your Git repo.
- **Roots** — `intent`, `business_need`, `risk`, `constraint`, `assumption`,
  `non_goal` — are the "why". They may exist on their own. A **`non_goal`** records
  deliberately-excluded scope (it surfaces in `tl context`); it is a root but not a
  *delivery* root, so nothing has to derive from it.
- **Everything else must justify itself** by linking up to a root through a
  *grounding link* (`derives_from`, `implements`, `verifies`, `mitigates`). Those
  links form a DAG — circular justification is rejected.
- **`tl check`** validates the whole graph and returns a stable exit code
  (**0** ok · **1** error-severity findings · **2** usage error), so it drops
  straight into a pre-commit hook or CI gate. An ungrounded, unserved, or
  otherwise invalid graph **fails the build.**

The point: unbounded requirement generation turns into a bounded, checkable
graph instead of silent scope sprawl.

---

## 3. Quick start — from empty folder to grounded graph

Everything below is a real terminal session. Run it and you'll see the same
output.

### Scaffold a project

```bash
cd ~/my-project
tl init --name "acme-app"        # writes ./throughline.toml
```

### Create a document folder per group of items

```bash
tl doc new INT vision   --title "Product vision"          # ./vision/.document.yml
tl doc new BN  goals    --title "Business goals"          # ./goals/.document.yml
tl doc new FR  features --title "Functional requirements" # ./features/.document.yml
```

A *document* is just a folder with a `.document.yml` manifest that owns a UID
prefix (`INT`, `BN`, `FR`, …) and hands out sequential numbers.

### Add some items (UIDs are allocated for you)

```bash
tl new INT --type intent        --title "Ship value to users every week"
tl new BN  --type business_need --title "Cut new-user onboarding time in half"
tl new FR  --type requirement   --title "Guided 3-step setup wizard" \
    --text "The system shall walk a new user through setup in 3 steps."
# -> vision/INT-0001.yml, goals/BN-0001.yml, features/FR-0001.yml
```

### Check *before* grounding — and watch it fail (this is the point)

```bash
tl check --strict
```

```
4 error(s), 0 warning(s)
[ERROR] BN-0001   unserved-root  business_need has nothing deriving from / mitigating it — unserved
[ERROR] FR-0001   orphan         requirement has no grounding link — nothing justifies it
[ERROR] FR-0001   coverage       needs incoming 'verifies' link (incoming:verifies)
[ERROR] INT-0001  unserved-root  intent has nothing deriving from / mitigating it — unserved
```

The graph is rejected: the feature justifies nothing, and the roots serve
nothing. That red-to-green transition you're about to make **is** the value.

### Add the grounding links

```bash
tl link BN-0001 INT-0001 --type derives_from   # goal justifies itself to the vision
tl link FR-0001 BN-0001  --type derives_from   # feature justifies itself to the goal
```

> **Ground it at birth instead.** You don't have to create an item orphaned and
> link it afterwards. `tl new` grounds as you go: pass `--ground <UID>` (and
> optionally `--ground-type`, default `derives_from`) to attach a parent the
> moment the item exists —
>
> ```bash
> tl new FR --type requirement --title "Guided 3-step setup wizard" \
>     --text "The system shall walk a new user through setup in 3 steps." \
>     --ground BN-0001                 # born justified, not caught later by check
> ```
>
> Run it on a terminal with no `--ground` and it offers the existing roots and
> already-grounded items as a numbered parent picker (skippable, never blocks a
> script or pipe). This is Intent-Driven Development: state the *why* as you
> write the *what*, so an ungrounded graph is the exception you have to opt into,
> not the default you have to remember to fix.

### The default config wants every requirement verified — add a test

```bash
tl doc new TEST tests --title "Verification"
tl new TEST --type test --title "Wizard completes in 3 steps"
tl link TEST-0001 FR-0001 --type verifies
```

### Check *after* grounding — green

```bash
tl check --strict        # 0 error(s), 0 warning(s)  → exit 0
```

### Explore what you built

```bash
tl trace FR-0001         # walk the feature up to its "why"
```

```
FR-0001  [requirement/draft] Guided 3-step setup wizard
└─(derives_from) BN-0001  [business_need/draft] Cut new-user onboarding time in half
└─(derives_from) INT-0001  [intent/draft] Ship value to users every week
```

```bash
tl blast BN-0001         # everything that would break if this goal were wrong
```

That's the loop: **`new` → `link` → `check`**, with `trace` and `blast` to
navigate. Commit the `.yml` files to Git like any other source.

---

## 4. The file format

A project is a directory containing `throughline.toml` plus per-document folders.

**An item** (`features/FR-0022.yml`):

```yaml
uid: FR-0022                 # permanent, immutable, never reused
type: requirement
status: approved
title: Guided setup wizard
text: The system shall walk new users through a 3-step setup.
normative: true              # content changes to a normative item mark dependents suspect
links:
  - target: BN-0003          # this requirement derives from a business need
    type: derives_from
  - target: ASM-0002         # …and depends on an assumption's validity
    type: assumes
    stamp: sha256:…          # target fingerprint when last confirmed (suspect tracking)
attrs:
  priority: must             # project-defined attributes live under attrs
  verification: test
```

**A document manifest** (`features/.document.yml`) records the prefix, the digit
width, and which UIDs have been handed out. You normally let the CLI maintain it.

**The project config** (`throughline.toml`) declares which types are roots, which
link types count as grounding, the allowed link types and statuses, and any
per-type attribute schema or coverage rules. `tl init` writes a sensible
default; edit it to fit your project.

---

## 5. Command reference

```
tl init [--name NAME]                          # scaffold a project
tl doc new <PREFIX> <dir> [--parent P]         # add a document
tl new <PREFIX> [--uid U] [--type T] [--ground UID [--ground-type K]]  # allocate + create (ground at birth)
tl link <SRC> <DST> --type <kind> [--stamp]    # add a typed link
throughline delete <UID> --reason "…"                   # tombstone (never erased)
throughline review [<UID> | --all-clean]                # mark reviewed at current content
tl check [--strict] [--format json]            # validate the graph — the CI gate
tl trace <UID> [--direction in|out] [--depth N]
tl blast <UID> [--format json]                 # everything depending on an item
tl shape [--format json]                       # observed (from)-[link]->(to) triples
tl diagram [types|transitions|both]            # Mermaid of the model / lifecycle
tl docs [FILE ...] [--at REF]                  # inject graph content into marked Markdown regions
tl docs [FILE ...] --check                      # CI gate: fail if any document is out of date
tl context                                     # agent-facing brief (IDD + this project's model)
tl ratify <UID> --by <who>                     # a human takes accountability
throughline invalidate <UID> --reason "…"               # falsify; cascade suspect
```

### What `check` enforces

| Rule | Meaning |
|------|---------|
| `orphan` | a non-root item with no grounding chain to a root |
| `unserved-root` | a delivery root nobody derives from / mitigates |
| `grounding-cycle` | circular justification |
| `dangling-link` / `deleted-link-target` | link to a missing / tombstoned item |
| `uid-grammar` / `uid-collision` | malformed UID, or one UID in two places (a merge clash) |
| `tombstone-deleted` | a retired UID's tombstone was erased (bad merge / stray `git rm`) |
| `schema` | missing required attribute or out-of-enum value |
| `suspect-link` | a link's target changed since it was last confirmed |
| `unreviewed` | item content changed since last review |
| `unratified` | an AI-origin item is still `proposed` |
| `ambiguous` | flagged ambiguous — blocked from ratification |
| `coverage` | a declared `[[rules.coverage]]` link requirement is unmet |
| `unpublished` | a normative item is referenced by no published document (inert until `[docs] paths` are set) |

Every rule's severity is configurable per project under `[rules]`; `--strict`
promotes every warning to an error — use it in CI.

---

## 6. Wire it into your pipeline

**Pre-commit** (`.pre-commit-config.yaml`): a local hook running
`tl -C <project> check --strict` so a broken graph can't be committed.

**CI**: run `tl check --strict` on push/PR. Exit ≠ 0 fails the build. The
`Dockerfile` in this repo builds an image whose entrypoint is `tl`, so you
can gate a project with no local Python at all.

See this repository's own [`.pre-commit-config.yaml`](.pre-commit-config.yaml)
and [`.github/workflows/ci.yml`](.github/workflows/ci.yml) — throughline gates both
the [demo project](examples/grounding-demo) and [its own
requirements](requirements) exactly this way.

---

## 7. Not included

throughline is **M0 — Core**. `tl docs` **injects** graph content into the marked
regions (`<!-- tl:item … -->` … `<!-- tl:end -->`) of your own Markdown files —
it never authors or stores documents itself (that is a recorded non-goal). You
own the prose; throughline keeps the referenced item content, tables, and
traceability matrices from drifting. Converting an injected `.md` to **HTML/PDF**
is a wrapper's job (pandoc, mdBook), kept out of the text-only core. Still not
included: named baselines and a version-to-version **diff**, and CSV/ReqIF
import-export. Beyond `docs`, "publish" means: your requirements are plain YAML in
Git — diff, review, and browse them with the same tools you already use for code.
