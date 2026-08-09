# throughline

[![ci](https://github.com/rhodium-org/throughline/actions/workflows/ci.yml/badge.svg)](https://github.com/rhodium-org/throughline/actions/workflows/ci.yml)

A **Git-native requirements management tool** with a built-in **scope-avalanche
grounding layer**. Requirements live as one small YAML file per item under
version control; a `check` command validates the whole graph and gates CI.

**Dogfooded:** throughline's own spec is itself a throughline project —
<!-- tl:count type == 'system_requirement' -->
145
<!-- tl:end --> system requirements,
<!-- tl:count type == 'user_requirement' -->
25
<!-- tl:end --> user requirements, and
<!-- tl:count type == 'nfr' -->
21
<!-- tl:end --> NFRs, every one grounded to a root and green under `tl check --strict`.
These counts are rendered from the live graph by the `tl:count` directive (SR-0109)
and gated fresh by `tl docs --check`, so they can never drift from the spec they describe.

Two ideas, one system:

1. **throughline core** — permanent, position-independent UIDs (never renumbered,
   never reused; deletion is a tombstone), one file per item, typed directed
   links, SHA-256 *normative* fingerprints that turn a real content change into a
   **suspect** link, and a `check` CLI with stable exit codes.
2. **grounding layer** — every non-root item must justify itself by reaching a
   *root* ("why"); AI-generated items enter `proposed` and must be
   **ratified** by a human; assumptions are first-class and invalidating one
   cascades **suspect** across its blast radius. Unbounded generation yields a
   bounded, ranked review queue instead of silent sprawl.

The build contract is the throughline spec in
[`docs/referenced-resource/`](https://github.com/rhodium-org/throughline/tree/main/docs/referenced-resource) (docs 04 system
requirements, 06 data format, 07 architecture).

> **New here?** [`HOW_TO_USE.md`](https://github.com/rhodium-org/throughline/blob/main/HOW_TO_USE.md) is a fifteen-minute hands-on
> quick start: scaffold a project, add three linked requirements, watch the
> validator reject an ungrounded graph, fix it, and trace a requirement back to
> its reason for existing. Curious how the workflow relates to test-first
> practice? [`HOW_IDD_DIFFERS_FROM_BDD.md`](https://github.com/rhodium-org/throughline/blob/main/HOW_IDD_DIFFERS_FROM_BDD.md) explains
> Intent-Driven Development and why it is the *why* axis to BDD's *what*.

---

## How it differs

Doorstop, StrictDoc, and OpenFastTrace already do Git-native, plain-text
requirements with permanent IDs and link-based traceability — and so does
throughline's core. The difference is the **grounding layer**. Those tools trace
links *once the items exist*; none of them gate on whether an item has any
*reason* to exist, or on whether a machine-generated item has been signed off by
a person. throughline makes both a build failure: an item that reaches no root
is an `orphan`, a delivery root nobody serves is `unserved-root`, and an
AI-`proposed` item stays `unratified` until a human ratifies it. That is the axis
this tool adds — not "are the links well-formed?" but **"should this scope exist
at all, and who took responsibility for it?"** It is why throughline is built for
an age where a machine can generate a hundred plausible requirements an hour:
generation is bounded by a ranked review queue instead of silent sprawl.

---

## Install

throughline is pure Python (one dependency, `pyyaml`) and needs **Python >= 3.11**
(for the stdlib `tomllib`). The same steps work on **Linux, macOS, and Windows** —
only how you obtain Python and put scripts on `PATH` differs.

**Recommended — [`pipx`](https://pipx.pypa.io)** (installs the CLI in its own
isolated environment):

```
pipx install throughline
```

> Prefer the bleeding edge? Install straight from Git instead:
> `pipx install "git+https://github.com/rhodium-org/throughline.git"`

Per-OS notes:

- **Linux** — system Python is often "externally managed" (PEP 668); don't
  `pip install` into it. Use `pipx` (`sudo apt install pipx` / `pacman -S python-pipx`)
  or a virtual environment.
- **macOS** — `brew install python pipx && pipx ensurepath`, then install as above.
- **Windows** — install Python 3.12 from python.org or `winget install Python.Python.3.12`
  (tick *Add to PATH*), then `python -m pip install --user pipx && pipx ensurepath`.
  Both `tl.exe` and `throughline.exe` are generated.

Either way you get `tl` (and the long form `throughline`) on your `PATH`. For a
local checkout you can develop against, see [`CONTRIBUTING.md`](https://github.com/rhodium-org/throughline/blob/main/CONTRIBUTING.md).

Or run it containerised, no local Python at all:

```
docker build -t throughline .
docker run --rm -v "$PWD/my-project:/work" throughline -C /work check --strict
```

---

## The format

A project is a directory: `throughline.toml` (config) + per-register folders, each
with a `.register.yml` manifest and one `<UID>.yml` per item.

```yaml
uid: FR-0022                 # permanent, immutable, never reused
type: requirement
status: approved
title: Guided setup wizard
text: The system shall walk new users through a 3-step setup.
normative: true
links:
  - target: BN-0003          # this requirement derives from a business need
    type: derives_from
  - target: ASM-0002         # …and depends on an assumption's validity
    type: assumes
    stamp: sha256:…          # target fingerprint when last confirmed (suspect tracking)
```

**Roots** (`intent`, `business_need`, `risk`, `constraint`, `assumption`,
`non_goal`) may exist ungrounded — they are the roots of "why". Everything else
must reach a root through a grounding link (`derives_from`, `mitigates`,
`implements`, `verifies`), which together form a DAG — circular justification is
rejected. A **`non_goal`** records deliberately-excluded scope: it is a root but
not a *delivery* root, so nothing has to derive from it and it is never flagged
unserved. Non-goals surface in `tl context` so excluded scope is visible to
reviewers and agents rather than living only in prose. throughline never tries to
detect items that "violate" a non-goal — that judgement stays with a human.

---

## CLI

```
tl init [--name NAME]                         # scaffold a project
tl register new <PREFIX> <dir> [--parent P]   # add a register
tl new <PREFIX> [--uid U] [--type T] [--ground UID]  # allocate + create (grounded at birth)
tl link <SRC> <DST> --type <kind> [--stamp]   # add a typed link
tl delete <UID> --reason "…"                  # tombstone (never erased)
tl review [<UID> | --all-clean]               # mark reviewed at current content
tl check [--strict] [--format json]           # validate the graph — the CI gate
tl trace <UID> [--direction in|out] [--depth N]
tl blast <UID> [--format json]                # everything depending on an item
tl shape [--format json]                      # observed (from)-[link]->(to) triples
tl diagram [types|transitions|both]           # Mermaid of the model / lifecycle
tl docs [FILE ...] [--at REF]                 # inject graph content into marked Markdown regions
tl docs [FILE ...] --check                     # CI gate: fail if any document is out of date
tl context                                    # agent-facing brief (IDD + this project's model)
tl ratify <UID> --by <who>                    # a human takes accountability
tl invalidate <UID> --reason "…"              # falsify; cascade suspect
```

> `tl` and `throughline` are the same command — `tl` is the short alias, and
> every subcommand above works under either name.

Exit codes are a stable contract: **0** ok · **1** findings at error severity ·
**2** usage/internal error. So `tl check` drops straight into a pre-commit hook
or CI gate — an ungrounded, unserved, or otherwise invalid graph fails the build.

### What `check` enforces

Upward and downward coverage are independent and both matter:

| Rule | Meaning |
|------|---------|
| `orphan` | a non-root item with no grounding chain to a root |
| `unserved-root` | a delivery root nobody derives from / mitigates |
| `grounding-cycle` | circular justification |
| `dangling-link` / `deleted-link-target` | link to a missing/tombstoned item |
| `uid-grammar` / `uid-collision` | malformed UID, or one UID in two places (merge) |
| `tombstone-deleted` | a retired UID's tombstone was erased (bad merge / stray `git rm`) |
| `schema` | missing required attr or out-of-enum value |
| `suspect-link` | target changed since the link was last confirmed |
| `unreviewed` | item content changed since last review |
| `unratified` | AI-origin item still `proposed` |
| `ambiguous` | flagged ambiguous — blocked from ratification |
| `coverage` | a declared `[[rules.coverage]]` link requirement is unmet |
| `unpublished` | a normative item is referenced by no published document (inert until `[docs] paths` are set) |

Every rule's severity is configurable per project under `[rules]`; `--strict`
promotes every warning to an error for CI.

---

## Try it

The [`examples/grounding-demo/`](https://github.com/rhodium-org/throughline/tree/main/examples/grounding-demo) project is a small, fully
grounded graph (intents, a business need, a risk, a constraint, an assumption,
requirements, an NFR, and verifying tests):

```
tl -C examples/grounding-demo check --strict     # green, exit 0
tl -C examples/grounding-demo trace FR-0055      # walk its justification tree
tl -C examples/grounding-demo blast ASM-0002     # what a bad assumption would take down
```

---

## Self-hosting — throughline's own requirements

throughline manages its own spec. The [`idd/`](https://github.com/rhodium-org/throughline/tree/main/idd) project is
throughline's vision, goals, user requirements, system requirements, and NFRs seeded
as throughline items, with the full grounding chain wired up
(`SR/NFR --implements--> UR --derives_from--> goal --derives_from--> vision`):

```
tl -C idd check --strict     # green, exit 0 — the tool gates its own scope
tl -C idd trace SR-0001      # walk a system requirement up to the vision
```

This is the discipline the tool exists to provide, applied to the tool itself: a
new system requirement that doesn't justify itself against a user requirement —
or a user requirement that doesn't derive from a goal — fails the build. Both the
demo and the self-host graph are gated in CI and by the pre-commit hook.

---

## Grounding operations

- **ratify** — a human takes accountability. Refused for ambiguous or ungrounded
  items: the two states that must never be signed off.
- **invalidate** — falsify an assumption (or any node): it is rejected and every
  transitive dependent is marked `suspect` (its blast radius).

---

## Testing & gates

```
pytest -q                                    # model, UID, fingerprint, validate, grounding
tl -C examples/grounding-demo check --strict   # the demo graph gate (exit 1 = broken)
tl -C idd check --strict              # throughline's own requirements (self-host gate)
```

- [`.pre-commit-config.yaml`](https://github.com/rhodium-org/throughline/blob/main/.pre-commit-config.yaml) runs the gate on commits
  touching either project (demo and self-host). Enable with `pre-commit install`.
- [`.github/workflows/ci.yml`](https://github.com/rhodium-org/throughline/blob/main/.github/workflows/ci.yml) runs the tests, both
  grounding gates, and a Docker build + image smoke-test on push/PR.

---

## What this is

**M0 — Core**: UID model, one-file-per-item storage, typed links,
fingerprints/suspect, the validation pipeline, the grounding layer, and the
`tl` CLI. **Not included**: baselines/diff, HTML/PDF publish, and CSV/ReqIF
import-export. Assumption items carry provenance attributes (`attrs.owner` /
`attrs.last_validated` / `attrs.confidence`) alongside their content.

throughline is **early software (alpha)** and unfinished by design — see
[`ROADMAP.md`](https://github.com/rhodium-org/throughline/blob/main/ROADMAP.md) for what's built, what's next, and where help is most
useful.

---

## Contributing

Contributions are welcome. [`CONTRIBUTING.md`](https://github.com/rhodium-org/throughline/blob/main/CONTRIBUTING.md) gets you to a
checked development environment; [`ROADMAP.md`](https://github.com/rhodium-org/throughline/blob/main/ROADMAP.md) lists good first work;
all participation is under the [`Code of Conduct`](https://github.com/rhodium-org/throughline/blob/main/CODE_OF_CONDUCT.md). To report a
vulnerability, see [`SECURITY.md`](https://github.com/rhodium-org/throughline/blob/main/SECURITY.md).

## License

Created by Dr Henry J Grech-Cini ([ORCID 0009-0007-1565-7530](https://orcid.org/0009-0007-1565-7530)).
Copyright © 2026 Henry J Grech-Cini. Released under the
**Apache License 2.0** — see [`LICENSE`](https://github.com/rhodium-org/throughline/blob/main/LICENSE) and [`NOTICE`](https://github.com/rhodium-org/throughline/blob/main/NOTICE). Provenance
and prior-art are recorded in [`PROVENANCE.md`](https://github.com/rhodium-org/throughline/blob/main/PROVENANCE.md).
