# 05 · Non-Functional Requirements (NFR)

## Licensing and openness

**NFR-0001 — Open-source license.**
All Tool code shall be released under an OSI-approved permissive license
(Apache-2.0 recommended), with no proprietary components required for any
specified capability.
`Priority: Must | Verification: Inspection | Traces: UR-0015 | Status: Approved`

**NFR-0002 — Open file format.**
The on-disk format shall be fully documented (doc 06), schema-validatable,
and readable/writable without the Tool.
`Priority: Must | Verification: Inspection | Traces: UR-0015 | Status: Approved`

**NFR-0003 — Offline and private.**
All specified functionality shall work with no network access, and the Tool
shall send no telemetry or project data anywhere by default.
`Priority: Must | Verification: Test | Traces: UR-0015 | Status: Approved`

## Portability and installation

**NFR-0004 — Cross-platform.**
The Tool shall run on current Linux, macOS, and Windows, treating paths,
line endings (store LF), and Unicode identically on all three.
`Priority: Must | Verification: Test | Status: Approved`

**NFR-0005 — Simple installation.**
Installation shall require a single standard package-manager command (e.g.
`pip install`/`pipx`, `cargo install`, or an OS package) with no database or
service setup.
`Priority: Must | Verification: Demonstration | Traces: UR-0020 | Status: Approved`

## Performance and scale

**NFR-0006 — Reference scale.**
On the reference project (10,000 items, 25 documents, 30,000 links) and
reference hardware (4-core laptop, SSD), the Tool shall complete: full
`check` ≤ 10 s, UID/attribute query ≤ 1 s, full HTML publish ≤ 60 s,
incremental publish of one document ≤ 5 s.
*Rationale:* Sphinx-Needs documents index-based O(1) key filtering for large
projects; our index design targets the same behavior.
`Priority: Must | Verification: Analysis (benchmark) | Status: Approved`

**NFR-0007 — Memory bound.**
Peak memory for the reference project shall not exceed 1 GB.
`Priority: Should | Verification: Analysis | Status: Approved`

**NFR-0008 — Startup latency.**
CLI startup to first output on a small project (≤100 items) shall be under
1 second.
`Priority: Should | Verification: Analysis | Status: Approved`

## Reliability and data safety

**NFR-0009 — No silent data loss.**
The Tool shall never discard or rewrite user content it does not understand:
unknown fields are preserved on read-modify-write, and destructive
operations require an explicit flag or run as status changes (SR-0012).
`Priority: Must | Verification: Test | Status: Approved`

**NFR-0010 — Versioned format with migrations.**
The file format shall carry an explicit schema version; the Tool shall read
all prior format versions and provide a migration command for major
changes.
`Priority: Must | Verification: Test | Traces: UR-0015 | Status: Approved`

**NFR-0011 — Semantic versioning.**
Tool releases shall follow SemVer; CLI flags, exit codes, the library API,
and the JSON export shape are the compatibility surface.
`Priority: Must | Verification: Inspection | Status: Approved`

**NFR-0012 — Deterministic outputs.**
Given identical input state, publish/export outputs shall be byte-identical
(no embedded timestamps by default) to keep artifacts diffable and builds
reproducible.
`Priority: Should | Verification: Test | Traces: UR-0007 | Status: Approved`

## Usability and documentation

**NFR-0013 — Quick start.**
Documentation shall include a quick start achieving UR-0020's 15-minute
scenario, plus a complete CLI and format reference.
`Priority: Must | Verification: Demonstration | Traces: UR-0020 | Status: Approved`

**NFR-0014 — Actionable errors.**
Every validation and CLI error shall state the offending file/UID, the rule
violated, and where applicable a suggested fix.
`Priority: Must | Verification: Inspection | Status: Approved`

**NFR-0015 — Accessible HTML output.**
Published HTML shall meet WCAG 2.1 AA basics: semantic headings, contrast,
keyboard navigation, alt text passthrough.
`Priority: Should | Verification: Inspection | Traces: UR-0008 | Status: Approved`

**NFR-0016 — Internationalized content.**
All content fields shall support full Unicode; UID prefixes remain ASCII for
portability.
`Priority: Must | Verification: Test | Status: Approved`

## Quality of implementation

**NFR-0017 — Test coverage.**
Core library statement coverage shall be ≥ 85%, with an end-to-end test for
every `Must` SR before it is marked `Verified`.
`Priority: Must | Verification: Analysis | Status: Approved`

**NFR-0018 — CI on all platforms.**
Every merge to main shall pass automated tests on Linux, macOS, and Windows.
`Priority: Must | Verification: Inspection | Status: Approved`

**NFR-0019 — Dependency discipline.**
Runtime dependencies shall be few, permissively licensed, and pinned by a
lockfile for releases; a dependency review is required to add one.
`Priority: Should | Verification: Inspection | Traces: UR-0015 | Status: Approved`

**NFR-0020 — Long-term maintainability.**
Implementation shall use a mainstream language and avoid exotic runtime
services, per the maintainability principle in doc 01 §6.
`Priority: Should | Verification: Inspection | Status: Approved`
