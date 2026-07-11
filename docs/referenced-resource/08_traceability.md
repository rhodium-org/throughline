# 08 · Traceability and Verification Summary

Forward trace: every UR maps to the SR/NFRs that implement it. (Backward
trace is embedded in each SR/NFR's `Traces:` line.) This matrix is exactly
the artifact SR-0051 will generate automatically once the tool self-hosts.

## UR → SR/NFR matrix

| UR | Realized by |
|---|---|
| UR-0001 Stable identity | SR-0001, SR-0002, SR-0003, SR-0004, SR-0008 |
| UR-0002 Frictionless add/remove | SR-0003, SR-0004, SR-0005, SR-0012, SR-0013, SR-0016 |
| UR-0003 Version comparison | SR-0012, SR-0037 |
| UR-0004 Traceability | SR-0030, SR-0031, SR-0032, SR-0051 |
| UR-0005 Stale-link awareness | SR-0033, SR-0034 |
| UR-0006 Review state | SR-0033, SR-0038 |
| UR-0007 Plain text + Git | SR-0010, SR-0072, NFR-0012 |
| UR-0008 Publishing | SR-0015, SR-0050, SR-0053, SR-0057, NFR-0015 |
| UR-0009 Tool exchange | SR-0054, SR-0056 |
| UR-0010 Search & filter | SR-0045, SR-0046 |
| UR-0011 Custom data model | SR-0011, SR-0020, SR-0021, SR-0022, SR-0023, SR-0024, SR-0070 |
| UR-0012 Coverage & impact | SR-0035, SR-0042, SR-0051, SR-0052 |
| UR-0013 Baselines | SR-0036 |
| UR-0014 Serverless collaboration | SR-0006, SR-0010, SR-0072 |
| UR-0015 No lock-in | SR-0055, SR-0061, SR-0071, NFR-0001, NFR-0002, NFR-0003, NFR-0010, NFR-0019 |
| UR-0016 CI automation | SR-0023, SR-0032, SR-0040, SR-0041, SR-0044, SR-0060 |
| UR-0017 Quality support | SR-0043 |
| UR-0018 Migration in | SR-0054 |
| UR-0019 Code traceability | SR-0062 |
| UR-0020 Newcomer-friendly | SR-0014, SR-0063, NFR-0005, NFR-0013 |

## Coverage check (manual, pre-self-hosting)

- Every UR has ≥1 realizing SR/NFR: **yes (20/20)**.
- Every non-retired SR traces to ≥1 UR: **yes** (see `Traces:` lines).
- Retired IDs: **SR-0007** (tombstone retained; number reserved).
- Unnumbered gaps present by design (e.g. SR-0009, SR-0017..19, SR-0025..29,
  SR-0039, SR-0047..49, SR-0058..59, SR-0064..69): reserved headroom per
  group — gaps carry no meaning.

## Verification method distribution

| Method | Where it dominates |
|---|---|
| Test | Identity, links, validation, exchange, diff (most SRs) |
| Demonstration | Publishing quality, quick start, init/templates |
| Inspection | Licensing, format documentation, API docs, error quality |
| Analysis | Performance/memory/startup benchmarks (NFR-0006..08) |

Definition of done: all `Must` SR/NFRs at status `Verified` via
their listed method, with the self-hosting check (doc 07 §7) green.
