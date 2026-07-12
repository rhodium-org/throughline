# 05 · Non-Functional Requirements (NFR)

Each entry is **generated from the graph** by `tl docs`; the section headings
are the only hand-owned structure. Regenerate with `tl docs` and gate it in CI
with `tl docs --check` (SR-0094).

## Licensing and openness

<!-- tl:item NFR-0001 -->
**NFR-0001 — Open-source license** — `nfr`, status `approved`

> All Tool code shall be released under an OSI-approved permissive license (Apache-2.0 recommended), with no proprietary components required for any capability.

**priority**: must · **verification**: inspection
<!-- tl:end -->

<!-- tl:item NFR-0002 -->
**NFR-0002 — Open file format** — `nfr`, status `approved`

> The on-disk format shall be fully documented (doc 06), schema-validatable, and readable/writable without the Tool.

**priority**: must · **verification**: inspection
<!-- tl:end -->

<!-- tl:item NFR-0003 -->
**NFR-0003 — Offline and private** — `nfr`, status `approved`

> All specified functionality shall work with no network access, and the Tool shall send no telemetry or project data anywhere by default.

**priority**: must · **verification**: test
<!-- tl:end -->

## Portability and installation

<!-- tl:item NFR-0004 -->
**NFR-0004 — Cross-platform** — `nfr`, status `approved`

> The Tool shall run on current Linux, macOS, and Windows, treating paths, line endings (store LF), and Unicode identically on all three.

**priority**: must · **verification**: test
<!-- tl:end -->

<!-- tl:item NFR-0005 -->
**NFR-0005 — Simple installation** — `nfr`, status `approved`

> Installation shall require a single standard package-manager command with no database or service setup.

**priority**: must · **verification**: demonstration
<!-- tl:end -->

## Performance and scale

<!-- tl:item NFR-0006 -->
**NFR-0006 — Reference scale** — `nfr`, status `approved`

> On the reference project (10,000 items, 25 registers, 30,000 links) and reference hardware, the Tool shall complete: full check <=10 s, query <=1 s, full HTML publish <=60 s, incremental publish of one document <=5 s.

**priority**: must · **verification**: analysis
<!-- tl:end -->

<!-- tl:item NFR-0007 -->
**NFR-0007 — Memory bound** — `nfr`, status `approved`

> Peak memory for the reference project shall not exceed 1 GB.

**priority**: should · **verification**: analysis
<!-- tl:end -->

<!-- tl:item NFR-0008 -->
**NFR-0008 — Startup latency** — `nfr`, status `approved`

> CLI startup to first output on a small project (<=100 items) shall be under 1 second.

**priority**: should · **verification**: analysis
<!-- tl:end -->

## Reliability and data safety

<!-- tl:item NFR-0009 -->
**NFR-0009 — No silent data loss** — `nfr`, status `approved`

> The Tool shall never discard or rewrite user content it does not understand: unknown fields are preserved on read-modify-write, and destructive operations require an explicit flag or run as status changes (SR-0012).

**priority**: must · **verification**: test
<!-- tl:end -->

<!-- tl:item NFR-0010 -->
**NFR-0010 — Versioned format with migrations** — `nfr`, status `approved`

> The file format shall carry an explicit schema version; the Tool shall read all prior 1.x format versions and provide a migration command for major changes.

**priority**: must · **verification**: test
<!-- tl:end -->

<!-- tl:item NFR-0011 -->
**NFR-0011 — Semantic versioning** — `nfr`, status `approved`

> Tool releases shall follow SemVer; CLI flags, exit codes, the library API, and the JSON export shape are the compatibility surface.

**priority**: must · **verification**: inspection
<!-- tl:end -->

<!-- tl:item NFR-0012 -->
**NFR-0012 — Deterministic outputs** — `nfr`, status `approved`

> Given identical input state, publish/export outputs shall be byte-identical (no embedded timestamps by default) to keep artifacts diffable and builds reproducible.

**priority**: should · **verification**: test
<!-- tl:end -->

<!-- tl:item NFR-0022 -->
**NFR-0022 — Safe handling of untrusted project data** — `nfr`, status `approved`

> Project files are untrusted input. The Tool shall parse all YAML with a safe loader that cannot construct arbitrary Python objects or execute code (no yaml.load without SafeLoader; no !!python/... tags honoured), and shall write every field value through a YAML emitter so that no field value — for example a crafted ratified_by, title, or text — can alter document structure or inject sibling keys. Malformed input shall fail fast with an error rather than be silently coerced or partially applied.

*Rationale:* A requirements graph is edited by many hands and, increasingly, by machines; a single hostile or malformed value must not become code execution or silent structural corruption. The tool already parses with safe_load and emits through a SafeDumper — this NFR pins that guarantee so a future refactor to a convenience loader fails the gate instead of quietly reopening the classic deserialization RCE.

**priority**: must · **verification**: test
<!-- tl:end -->

## Usability and documentation

<!-- tl:item NFR-0013 -->
**NFR-0013 — Quick start** — `nfr`, status `approved`

> Documentation shall include a quick start achieving UR-0020's 15-minute scenario, plus a complete CLI and format reference.

**priority**: must · **verification**: demonstration
<!-- tl:end -->

<!-- tl:item NFR-0014 -->
**NFR-0014 — Actionable errors** — `nfr`, status `approved`

> Every validation and CLI error shall state the offending file/UID, the rule violated, and where applicable a suggested fix.

**priority**: must · **verification**: inspection
<!-- tl:end -->

<!-- tl:item NFR-0015 -->
**NFR-0015 — Accessible HTML output** — `nfr`, status `approved`

> Published HTML shall meet WCAG 2.1 AA basics: semantic headings, contrast, keyboard navigation, alt text passthrough.

**priority**: should · **verification**: inspection
<!-- tl:end -->

<!-- tl:item NFR-0016 -->
**NFR-0016 — Internationalized content** — `nfr`, status `approved`

> All content fields shall support full Unicode; UID prefixes remain ASCII for portability.

**priority**: must · **verification**: test
<!-- tl:end -->

<!-- tl:item NFR-0021 -->
**NFR-0021 — Conceptual model explained** — `nfr`, status `approved`

> Documentation shall explain throughline's Intent-Driven Development (IDD) model and how it differs from adjacent practices (notably BDD and TDD), so newcomers understand why grounding-at-birth exists and adopt it deliberately rather than treating check as an after-the-fact linter.

**priority**: should · **verification**: inspection
<!-- tl:end -->

## Quality of implementation

<!-- tl:item NFR-0017 -->
**NFR-0017 — Test coverage** — `nfr`, status `approved`

> Core library statement coverage shall be >=85%, with an end-to-end test for every Must SR before it is marked Verified.

**priority**: must · **verification**: analysis
<!-- tl:end -->

<!-- tl:item NFR-0018 -->
**NFR-0018 — CI on all platforms** — `nfr`, status `approved`

> Every merge to main shall pass automated tests on Linux, macOS, and Windows.

**priority**: must · **verification**: inspection
<!-- tl:end -->

<!-- tl:item NFR-0019 -->
**NFR-0019 — Dependency discipline** — `nfr`, status `approved`

> Runtime dependencies shall be few, permissively licensed, and pinned by a lockfile for releases; a dependency review is required to add one.

**priority**: should · **verification**: inspection
<!-- tl:end -->

<!-- tl:item NFR-0020 -->
**NFR-0020 — Long-term maintainability** — `nfr`, status `approved`

> Implementation shall use a mainstream language and avoid exotic runtime services, per the maintainability principle in doc 01 §6.

**priority**: should · **verification**: inspection
<!-- tl:end -->
