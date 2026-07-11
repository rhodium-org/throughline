# Provenance and prior art

throughline is an original, clean-room implementation. This note records what it
builds on, so novelty is neither overstated nor understated.

## Ownership and licence

Copyright © 2026 Time Back Solutions Limited. Released under the Apache License 2.0
— see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).

## Prior art

throughline stands on a well-established field and does not claim to have invented
requirements management:

- The one-file-per-item YAML store, SHA-256 normative fingerprint, and
  suspect-link / review model are in the spirit of **Doorstop** (LGPL-3.0).
- The typed schema with link roles draws on **StrictDoc**.
- The wider field includes **Sphinx-Needs, ReqView, IBM DOORS, Jama Connect,
  Polarion**, and the OMG **ReqIF** interchange standard.

`docs/referenced-resource/02_feature_analysis.md` records the Adopt / Adapt / Defer
decisions against this prior art.

**No third-party source code was copied.** throughline is a fresh Python
implementation; its models were drawn from public documentation, so no third-party
copyright or copyleft licence attaches to this codebase.

## The contribution

The synthesis, not the borrowed parts: groundedness as a structural invariant
(root-reachability over an acyclic grounding graph), origin-aware
proposed-by-default items, and assumptions as first-class nodes whose invalidation
cascades *suspect* across their blast radius.
