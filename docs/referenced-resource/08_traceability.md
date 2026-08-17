# 08 · Traceability and Verification Summary

Forward trace: every user requirement maps to the system requirements that
`implements` it. The table below is **generated from the graph** by
`tl docs` — it is not maintained by hand, so it cannot drift from the actual
links. Regenerate it with `tl docs` and gate it in CI with `tl docs --check`
(SR-0094, SR-0099). Backward trace (each SR up to its UR) is one `tl trace`
away, and the whole graph is validated by `tl check --strict`.

## UR → realizing SR matrix

<!-- tl:matrix incoming:implements type == 'user_requirement' -->
| UID | Title | Implements (incoming) |
|---|---|---|
| UR-0001 | Stable requirement identity | SR-0001, SR-0002, SR-0003, SR-0004, SR-0008, SR-0101, SR-0140, SR-0145 |
| UR-0002 | Frictionless add/remove | SR-0003, SR-0004, SR-0005, SR-0012, SR-0013 |
| UR-0003 | Version-to-version comparison | SR-0012, SR-0037 |
| UR-0004 | Traceability | SR-0030, SR-0031, SR-0032, SR-0051, SR-0099, SR-0107, SR-0108, SR-0110, SR-0143 |
| UR-0005 | Stale-link awareness | SR-0033, SR-0034, SR-0159, SR-0160, SR-0169, SR-0173, SR-0174, SR-0175, SR-0177, SR-0178, SR-0188 |
| UR-0006 | Review state | SR-0033, SR-0038 |
| UR-0007 | Plain text under version control | NFR-0012, SR-0010, SR-0072 |
| UR-0008 | Stakeholder-quality publishing | SR-0015, SR-0057 |
| UR-0009 | Exchange with other tools | — |
| UR-0010 | Search and filter | SR-0045, SR-0046, SR-0079, SR-0103, SR-0104, SR-0105, SR-0106 |
| UR-0011 | Project-specific data model | SR-0011, SR-0020, SR-0021, SR-0022, SR-0024, SR-0070, SR-0080, SR-0081, SR-0082, SR-0083, SR-0084, SR-0130, SR-0131, SR-0132, SR-0138, SR-0142, SR-0144, SR-0147, SR-0172, SR-0181, SR-0182, SR-0183, SR-0184 |
| UR-0012 | Coverage and impact analysis | SR-0035, SR-0042, SR-0051, SR-0052, SR-0078, SR-0085, SR-0086, SR-0096 |
| UR-0013 | Baselines | SR-0036, SR-0090 |
| UR-0014 | Team collaboration without a server | SR-0006, SR-0010, SR-0072, SR-0093 |
| UR-0015 | No lock-in | NFR-0001, NFR-0002, NFR-0003, NFR-0010, NFR-0019, SR-0055, SR-0061, SR-0071, SR-0133, SR-0137 |
| UR-0016 | CI automation | SR-0023, SR-0032, SR-0040, SR-0041, SR-0044, SR-0060, SR-0076, SR-0134, SR-0135, SR-0136, SR-0139, SR-0146, SR-0164, SR-0176, SR-0179, SR-0180, SR-0185 |
| UR-0017 | Requirement quality support | SR-0043, SR-0073, SR-0091, SR-0163 |
| UR-0018 | Migration path in | — |
| UR-0019 | Requirements-to-code traceability | — |
| UR-0020 | Comprehensible to newcomers | NFR-0005, NFR-0013, NFR-0021, SR-0014, SR-0074, SR-0077, SR-0100, SR-0102, SR-0120, SR-0121, SR-0168 |
| UR-0021 | Verifiable development environment | SR-0075 |
| UR-0022 | Comprehensible to AI agents | SR-0088, SR-0102, SR-0129, SR-0161, SR-0171 |
| UR-0023 | Machine-authored items require human ratification | SR-0092, SR-0098, SR-0141, SR-0148, SR-0149, SR-0150, SR-0151, SR-0152, SR-0153, SR-0154, SR-0156, SR-0157, SR-0162, SR-0170 |
| UR-0024 | Drift-free narrative documents | SR-0094, SR-0095, SR-0109, SR-0111, SR-0112, SR-0113, SR-0115, SR-0116, SR-0117, SR-0118, SR-0119, SR-0186, SR-0187 |
| UR-0025 | Explicit non-goals as first-class items | SR-0097 |
| UR-0026 | Conflict-free parallel identity allocation | SR-0122, SR-0123, SR-0124, SR-0125, SR-0126 |
| UR-0027 | Item identity is tamper-evident | SR-0127, SR-0128 |
| UR-0028 | A stale item shows what changed before it asks to be re-ratified | SR-0165, SR-0166, SR-0167 |
<!-- tl:end -->

## Coverage check

`tl check --strict` enforces this continuously: a user requirement that nothing
`implements` is an `unserved-root`, and a system requirement that reaches no
root is an `orphan` — either fails the build. Retired IDs keep their tombstones
and their numbers are never reused; unnumbered gaps carry no meaning.

## Verification method distribution

| Method | Where it dominates |
|---|---|
| Test | Identity, links, validation, exchange, diff (most SRs) |
| Demonstration | Publishing quality, quick start, init/templates |
| Inspection | Licensing, format documentation, API docs, error quality |
| Analysis | Performance/memory/startup benchmarks (NFR-0006..08) |

Definition of done: all `Must` SR/NFRs at status `Verified` via
their listed method, with the self-hosting check (doc 07 §7) green.
