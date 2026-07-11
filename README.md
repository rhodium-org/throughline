# throughline

[![ci](https://github.com/rhodium-org/throughline/actions/workflows/ci.yml/badge.svg)](https://github.com/rhodium-org/throughline/actions/workflows/ci.yml)

A **Git-native requirements management tool** with a built-in **scope-avalanche
grounding layer**. Requirements live as one small YAML file per item under
version control; a `check` command validates the whole graph and gates CI.

Two ideas, one system:

1. **throughline core** — permanent, position-independent UIDs (never renumbered,
   never reused; deletion is a tombstone), one file per item, typed directed
   links, SHA-256 *normative* fingerprints that turn a real content change into a
   **suspect** link, and a `check` CLI with stable exit codes.
2. **grounding layer** — every non-root item must justify itself by reaching a
   *root* ("why"); AI/scout-generated items enter `proposed` and must be
   **ratified** by a human; assumptions are first-class and invalidating one
   cascades **suspect** across its blast radius. Unbounded generation yields a
   bounded, ranked review queue instead of silent sprawl.

> **What is scout, and do I need it?** No. **scout** is a *separate*, optional
> AI tool that reads a codebase or corpus and *proposes* requirements, roots, and
> ambiguities for a human to ratify — it feeds throughline through `tl scout`, but
> throughline is a complete, standalone tool without it. Everything below works
> whether requirements are hand-authored or scout-proposed; the grounding layer
> treats a scout proposal exactly like any other `proposed` item awaiting a
> human's sign-off. scout is released separately (expected in the coming weeks);
> until then, ignore the `scout` references — they change nothing about the core
> workflow.

The build contract is the throughline spec in
[`docs/referenced-resource/`](docs/referenced-resource) (docs 04 system
requirements, 06 data format, 07 architecture).

> **New here?** [`HOW_TO_USE.md`](HOW_TO_USE.md) is a fifteen-minute hands-on
> quick start: scaffold a project, add three linked requirements, watch the
> validator reject an ungrounded graph, fix it, and trace a requirement back to
> its reason for existing. Curious how the workflow relates to test-first
> practice? [`HOW_IDD_DIFFERS_FROM_BDD.md`](HOW_IDD_DIFFERS_FROM_BDD.md) explains
> Intent-Driven Development and why it is the *why* axis to BDD's *what*.

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
local checkout you can develop against, see [`CONTRIBUTING.md`](CONTRIBUTING.md).

Or run it containerised, no local Python at all:

```
docker build -t throughline .
docker run --rm -v "$PWD/my-project:/work" throughline -C /work check --strict
```

---

## The format

A project is a directory: `throughline.toml` (config) + per-document folders, each
with a `.document.yml` manifest and one `<UID>.yml` per item.

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

**Roots** (`intent`, `business_need`, `risk`, `constraint`, `assumption`) may
exist ungrounded — they are the roots of "why". Everything else must reach a root
through a grounding link (`derives_from`, `mitigates`, `implements`, `verifies`),
which together form a DAG — circular justification is rejected.

---

## CLI

```
tl init [--name NAME]                         # scaffold a project
tl doc new <PREFIX> <dir> [--parent P]        # add a document
tl new <PREFIX> [--uid U] [--type T] [--ground UID]  # allocate + create (grounded at birth)
tl link <SRC> <DST> --type <kind> [--stamp]   # add a typed link
throughline delete <UID> --reason "…"                  # tombstone (never erased)
throughline review [<UID> | --all-clean]               # mark reviewed at current content
tl check [--strict] [--format json]           # validate the graph — the CI gate
tl trace <UID> [--direction in|out] [--depth N]
tl blast <UID> [--format json]                # everything depending on an item
tl shape [--format json]                      # observed (from)-[link]->(to) triples
tl diagram [types|transitions|both]           # Mermaid of the model / lifecycle
tl docs [--doc PREFIX] [--at REF]             # render a Markdown requirements document
tl context                                    # agent-facing brief (IDD + this project's model)
tl ratify <UID> --by <who>                    # a human takes accountability
throughline invalidate <UID> --reason "…"              # falsify; cascade suspect
throughline scout <report.json>                         # ingest scout proposals
```

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
| `schema` | missing required attr or out-of-enum value |
| `suspect-link` | target changed since the link was last confirmed |
| `unreviewed` | item content changed since last review |
| `unratified` | AI/scout-origin item still `proposed` |
| `ambiguous` | flagged ambiguous — blocked from ratification |
| `coverage` | a declared `[[rules.coverage]]` link requirement is unmet |

Every rule's severity is configurable per project under `[rules]`; `--strict`
promotes every warning to an error for CI.

---

## Try it

The [`examples/grounding-demo/`](examples/grounding-demo) project is a small, fully
grounded graph (intents, a business need, a risk, a constraint, an assumption,
requirements, an NFR, and verifying tests):

```
tl -C examples/grounding-demo check --strict     # green, exit 0
tl -C examples/grounding-demo trace FR-0055      # walk its justification tree
tl -C examples/grounding-demo blast ASM-0002     # what a bad assumption would take down
```

---

## Self-hosting — throughline's own requirements

throughline manages its own spec. The [`requirements/`](requirements) project is
throughline's vision, goals, user requirements, system requirements, and NFRs seeded
as throughline items, with the full grounding chain wired up
(`SR/NFR --implements--> UR --derives_from--> goal --derives_from--> vision`):

```
tl -C requirements check --strict     # green, exit 0 — the tool gates its own scope
tl -C requirements trace SR-0001      # walk a system requirement up to the vision
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
- **scout ingest** — scout *proposes*, humans *ratify*. Proposed roots enter as
  `origin: scout, status: proposed`; ambiguities set `attrs.ambiguous` and block
  ratification; coverage gaps corroborate the structural `unserved-root` finding.

```json
{
  "proposed_roots": [{"id": "BN-0009", "type": "business_need", "title": "…", "rationale": "…"}],
  "ambiguities":    [{"id": "FR-0055", "reason": "'fast' is unquantified"}],
  "coverage_gaps":  [{"root": "CON-0002", "detail": "no requirement implements it"}]
}
```

---

## Testing & gates

```
pytest -q                                    # model, UID, fingerprint, validate, grounding
tl -C examples/grounding-demo check --strict   # the demo graph gate (exit 1 = broken)
tl -C requirements check --strict              # throughline's own requirements (self-host gate)
```

- [`.pre-commit-config.yaml`](.pre-commit-config.yaml) runs the gate on commits
  touching either project (demo and self-host). Enable with `pre-commit install`.
- [`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs the tests, both
  grounding gates, and a Docker build + image smoke-test on push/PR.

---

## What this is

**M0 — Core**: UID model, one-file-per-item storage, typed links,
fingerprints/suspect, the validation pipeline, the grounding layer, and the
`tl` CLI. **Not included**: baselines/diff, HTML/PDF publish, and CSV/ReqIF
import-export. Assumption items carry provenance attributes (`attrs.owner` /
`attrs.last_validated` / `attrs.confidence`) alongside their content.

throughline is **early software (alpha)** and unfinished by design — see
[`ROADMAP.md`](ROADMAP.md) for what's built, what's next, and where help is most
useful.

---

## Contributing

Contributions are welcome. [`CONTRIBUTING.md`](CONTRIBUTING.md) gets you to a
checked development environment; [`ROADMAP.md`](ROADMAP.md) lists good first work;
all participation is under the [`Code of Conduct`](CODE_OF_CONDUCT.md). To report a
vulnerability, see [`SECURITY.md`](SECURITY.md).

## License

Copyright © 2026 Time Back Solutions Limited. Released under the
**Apache License 2.0** — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE). Provenance
and prior-art are recorded in [`PROVENANCE.md`](PROVENANCE.md).
