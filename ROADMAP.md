# Roadmap & status

throughline is **early software (alpha)**. The core works, is tested, and gates its
own development — but it is deliberately narrow, and there is a lot left to build.
This document is an honest map of what exists, what doesn't, and where help is most
useful. If something here is unclear or you disagree with a priority, open an issue.

## What works today (M0 — core + grounding)

- Permanent, position-independent UIDs (never renumbered, never reused; deletion is
  a tombstone).
- One YAML file per item; typed directed links; a configurable schema.
- SHA-256 *normative* fingerprints → **suspect** links when a target's normative
  content changes; content-change → **unreviewed**.
- The grounding layer: root-reachability, grounding-DAG acyclicity, proposed-by-
  default for AI origin, human **ratify**, assumptions as first-class nodes
  whose invalidation cascades *suspect*.
- `tl check` with a stable exit-code contract (0 / 1 / 2) — drops into pre-commit
  and CI.
- Self-hosting: throughline manages its own requirements (`requirements/`) and a
  worked demo (`examples/grounding-demo/`), both gated in CI.

## Not built yet (M1+)

These are named in the spec (`docs/referenced-resource/07_*`) but not implemented:

- **Baselines & diff** — snapshot a graph at a point in time and diff two baselines.
- **Publishing** — HTML / PDF rendering of a requirements document (today `tl docs`
  emits Markdown only).
- **Import / export** — CSV and ReqIF, for interchange with existing tools (DOORS,
  Jama, Polarion, ReqView).
- **Integrations** — e.g. syncing requirement UIDs with an issue tracker (Jira /
  GitHub / Forgejo) so delivery tickets trace to requirement nodes.
- **Editor integration (VS Code extension)** — meet developers where they work: a
  VS Code plugin that surfaces throughline in the editor (inline node/link status,
  suspect & unreviewed warnings, quick create/ratify, jump-to-grounding) so using
  throughline is a natural part of the coding loop rather than a separate CLI chore.
  The goal is adoption — lowering the friction that keeps developers from grounding
  their work.

## Exploratory / parked (post-M1)

Two larger layers are designed but intentionally not started; they are only worth
building once the basics are solid and real usage justifies them:

- **Scale layer** — validating grounding *plausibility* at scale (many cheap,
  decorrelated checks rather than one expensive judgement) for when AI-generated
  volume outgrows a manual review queue.
- **Assumption-health layer** — stakeholder-driven push-revalidation that fights
  assumption decay over time: foundational assumptions silently going invalid while
  everything built on them carries on.

Both are exploratory and their detailed designs are not part of this repository yet.

## Known rough edges

Good first contributions — small, well-scoped, and real:

- **`tl ratify` hardcodes `status: ratified`.** It ignores the project's configured
  status vocabulary, so on a project whose accepted state is `approved` (like the
  self-host spec) ratification produces an out-of-enum status and a `check` error.
  It should read the project's status set / a configurable "ratified" state.
- **Windows path/enc coverage is untested in CI.** CI runs on Linux only; the code
  is cross-platform in principle but unverified on Windows/macOS runners.
- **No `--version` polish / man pages / shell completions.**

## Where to help

- Pick a **Known rough edge** above, or anything labelled `good first issue`.
- Try throughline on a *real* requirements set and report where the model or the CLI
  fights you — friction reports are as valuable as code.
- **Measure throughline's effect on token usage.** Deliver the same project
  end-to-end twice from the same user description — once with throughline (grounded,
  IDD loop) and once without — and compare total LLM token usage across the two runs.
  Quantifies whether the grounding discipline pays for itself in tokens and where the
  overhead (or saving) lands.
- Work follows **Intent-Driven Development**: write or update the grounded
  requirement first, keep the grounding gate green, then implement. See
  [`CONTRIBUTING.md`](CONTRIBUTING.md) and
  [`HOW_IDD_DIFFERS_FROM_BDD.md`](HOW_IDD_DIFFERS_FROM_BDD.md).

Nothing here is fixed. throughline is a synthesis in progress, and the roadmap is a
starting point for discussion, not a contract.
