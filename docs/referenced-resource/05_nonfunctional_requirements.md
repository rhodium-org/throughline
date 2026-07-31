# 05 · Non-Functional Requirements (NFR)

Each entry is **generated from the graph** by `tl docs`; the section headings
are the only hand-owned structure. Regenerate with `tl docs` and gate it in CI
with `tl docs --check` (SR-0094).

## Licensing and openness

<!-- tl:item NFR-0001 -->
**NFR-0001 — Open-source license** — `nfr`, status `ratified`

> All Tool code shall be released under an OSI-approved permissive license (Apache-2.0 recommended), with no proprietary components required for any capability.

*Implements:* UR-0015

**priority**: must · **verification**: inspection · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:43d9dcde41fa3c8195c08d59345913cf93b6964d6bb10ae9d0de718b9fbb7425 · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item NFR-0002 -->
**NFR-0002 — Open file format** — `nfr`, status `ratified`

> The on-disk format shall be fully documented (doc 06), schema-validatable, and readable/writable without the Tool.

*Implements:* UR-0015

**priority**: must · **verification**: inspection · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:40008e0d3ca1ede7bfc51a125da71f22aadee887b2091796f11cce81802efc88 · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item NFR-0003 -->
**NFR-0003 — Offline and private** — `nfr`, status `ratified`

> All specified functionality shall work with no network access, and the Tool shall send no telemetry or project data anywhere by default.

*Implements:* UR-0015

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:114baa091965e3bdf9ebea6de123e059c68b3c8f583356933551dba3e708b09d · **ratified_backfilled**: True
<!-- tl:end -->

## Portability and installation

<!-- tl:item NFR-0004 -->
**NFR-0004 — Cross-platform** — `nfr`, status `ratified`

> The Tool shall run on current Linux, macOS, and Windows, treating paths, line endings (store LF), and Unicode identically on all three.

*Derives from:* BN-0009

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:a966a60c09f4a50de7b6db01020f42923c31d9aac3849d3cec5df7752a89b2b8 · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item NFR-0005 -->
**NFR-0005 — Simple installation** — `nfr`, status `ratified`

> Installation shall require a single standard package-manager command with no database or service setup.

*Implements:* UR-0020

**priority**: must · **verification**: demonstration · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:c3c0cc49aee52701fb37aa8d3f3ffb1c2714aa0bf157b8ef262dd5d1dc89724e · **ratified_backfilled**: True
<!-- tl:end -->

## Performance and scale

<!-- tl:item NFR-0006 -->
**NFR-0006 — Reference scale** — `nfr`, status `ratified`

> On the reference project (10,000 items, 25 registers, 30,000 links) and reference hardware, the Tool shall complete: full check <=10 s, query <=1 s, full Markdown render of all documents <=60 s, incremental render of one document <=5 s.

*Derives from:* BN-0009

**priority**: must · **verification**: analysis · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:422b6e92fddd81709edd05b1c52983764d5149335a8b4d5cda129e2f02c0d67e · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item NFR-0007 -->
**NFR-0007 — Memory bound** — `nfr`, status `ratified`

> Peak memory for the reference project shall not exceed 1 GB.

*Derives from:* BN-0009

**priority**: should · **verification**: analysis · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:def01898af4c778e71802a6da833f78b5daea917de7f73557b5926b259b97502 · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item NFR-0008 -->
**NFR-0008 — Startup latency** — `nfr`, status `ratified`

> CLI startup to first output on a small project (<=100 items) shall be under 1 second.

*Derives from:* BN-0009

**priority**: should · **verification**: analysis · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:ca061948113eb5f722c049433e3eb1bb5d4f4b84fb4f16cc4ed285df1808ffa5 · **ratified_backfilled**: True
<!-- tl:end -->

## Reliability and data safety

<!-- tl:item NFR-0009 -->
**NFR-0009 — No silent data loss** — `nfr`, status `ratified`

> The Tool shall never discard or rewrite user content it does not understand: unknown fields are preserved on read-modify-write, and destructive operations require an explicit flag or run as status changes (SR-0012).

*Derives from:* BN-0009

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:48188c704a7bc2f8418a8b36a4846890e360305b0e324e144a70f50fecafcca9 · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item NFR-0010 -->
**NFR-0010 — Versioned format with migrations** — `nfr`, status `implemented`

> The file format shall carry an explicit schema version in throughline.toml, and on load the Tool shall compare it against the format the Tool implements and act by the on-disk major. When the on-disk major equals the Tool's, the Tool shall read the project transparently with no warning whatever the minor, so every earlier minor of the same major stays readable without migration. When the on-disk major is newer than the Tool understands, the Tool shall refuse to load and tell the user to upgrade the Tool rather than silently mis-parse a format from the future. When the on-disk major is older than the Tool's, the Tool shall decline to load until the user runs the migration command the Tool provides, which rewrites the project to the current format and bumps the recorded version. When the version field is absent the Tool shall infer the major from the on-disk layout, so an unversioned older project is still routed to migration rather than mis-read as current.

*Implements:* UR-0015

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:53ebdfd93580423eb21d45f2e0d68d5e40f8dab879fd65678a7d3cbfa3d5d92b · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item NFR-0011 -->
**NFR-0011 — Semantic versioning** — `nfr`, status `ratified`

> Tool releases shall follow SemVer; CLI flags, exit codes, the library API, and the JSON export shape are the compatibility surface.

*Derives from:* BN-0009

**priority**: must · **verification**: inspection · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:b280b9f2c4b976bde6a1fafd028ecdaa487ea3ebbe2fc9a77cee027cdf3e888c · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item NFR-0012 -->
**NFR-0012 — Deterministic outputs** — `nfr`, status `ratified`

> Given identical input state, publish/export outputs shall be byte-identical (no embedded timestamps by default) to keep artifacts diffable and builds reproducible.

*Implements:* UR-0007

**priority**: should · **verification**: test · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:7e078d4537eefca69e5bd4eeb3d632a572f0a5ced72aeb77612b0d8cb1437275 · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item NFR-0022 -->
**NFR-0022 — Safe handling of untrusted project data** — `nfr`, status `ratified`

> Project files are untrusted input. The Tool shall parse all YAML with a safe loader that cannot construct arbitrary Python objects or execute code (no yaml.load without SafeLoader; no !!python/... tags honoured), and shall write every field value through a YAML emitter so that no field value — for example a crafted ratified_by, title, or text — can alter document structure or inject sibling keys. Malformed input shall fail fast with an error rather than be silently coerced or partially applied.

*Rationale:* A requirements graph is edited by many hands and, increasingly, by machines; a single hostile or malformed value must not become code execution or silent structural corruption. The tool already parses with safe_load and emits through a SafeDumper — this NFR pins that guarantee so a future refactor to a convenience loader fails the gate instead of quietly reopening the classic deserialization RCE.

*Derives from:* BN-0009

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:2852de555e72c9fedcbf9d7f214d8b7359f4270980ca6f4cfdd76baabc2c5b3b · **ratified_backfilled**: True
<!-- tl:end -->

## Usability and documentation

<!-- tl:item NFR-0013 -->
**NFR-0013 — Quick start** — `nfr`, status `ratified`

> Documentation shall include a quick start achieving UR-0020's 15-minute scenario, plus a complete CLI and format reference.

*Implements:* UR-0020
*Relates:* SR-0074

**priority**: must · **verification**: demonstration · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:864985dfb4c48befcf12cbcc02cb9c67e15d9f72d4ab9f4961b131b57f2c71e0 · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item NFR-0014 -->
**NFR-0014 — Actionable errors** — `nfr`, status `ratified`

> Every validation and CLI error shall state the offending file/UID, the rule violated, and where applicable a suggested fix.

*Derives from:* BN-0009

**priority**: must · **verification**: inspection · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:fc502147ae663d3deb905018d03f16729256dfb533366198bdd99a3f1bc11417 · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item NFR-0015 -->
**NFR-0015 — Accessible HTML output** — `nfr`, status `rejected`

> Published HTML shall meet WCAG 2.1 AA basics: semantic headings, contrast, keyboard navigation, alt text passthrough.

*Rationale:* Rejected as out of scope under NG-0005. This NFR only existed to constrain the HTML output of SR-0050, which is itself withdrawn; accessibility of the rendered site is the concern of the external tool that renders it. Tombstoned, never reused.

*Implements:* UR-0008
*Relates:* NG-0005

**priority**: should · **verification**: inspection
<!-- tl:end -->

<!-- tl:item NFR-0016 -->
**NFR-0016 — Internationalized content** — `nfr`, status `ratified`

> All content fields shall support full Unicode; UID prefixes remain ASCII for portability.

*Derives from:* BN-0009

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:49604d8c30b62c23a0a8e12c8ca560a2fd3293885871cf91f56a96c5fa2e95e1 · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item NFR-0021 -->
**NFR-0021 — Conceptual model explained** — `nfr`, status `ratified`

> Documentation shall explain throughline's Intent-Driven Development (IDD) model and how it differs from adjacent practices (notably BDD and TDD), so newcomers understand why grounding-at-birth exists and adopt it deliberately rather than treating check as an after-the-fact linter.

*Implements:* UR-0020

**priority**: should · **verification**: inspection · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:f8d16c7d2dc81636fee05fff258fda0060133a0bc883b75a3c155ccf08ca0e7e · **ratified_backfilled**: True
<!-- tl:end -->

## Quality of implementation

<!-- tl:item NFR-0017 -->
**NFR-0017 — Test coverage** — `nfr`, status `ratified`

> Core library statement coverage shall be >=85%, with an end-to-end test for every Must SR before it is marked Verified.

*Derives from:* BN-0009

**priority**: must · **verification**: analysis · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:fa04b7bee603d68b89cd47cacb4932ecd49075cb2dfdcf3c0db89202c012f37d · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item NFR-0018 -->
**NFR-0018 — CI on all platforms** — `nfr`, status `ratified`

> Every merge to main shall pass automated tests on Linux, macOS, and Windows.

*Derives from:* BN-0009

**priority**: must · **verification**: inspection · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:3f1a798a291857143f2c1fc04bb9e8cd7cdeba9a3f314c49cf0f5c23e7b57af9 · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item NFR-0019 -->
**NFR-0019 — Dependency discipline** — `nfr`, status `ratified`

> Runtime dependencies shall be few, permissively licensed, and pinned by a lockfile for releases; a dependency review is required to add one.

*Implements:* UR-0015

**priority**: should · **verification**: inspection · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:6e7e9b461e3ab465b7c4c35d6d0cd567e0e3eb908571da1551628432e6caa78f · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item NFR-0020 -->
**NFR-0020 — Long-term maintainability** — `nfr`, status `ratified`

> Implementation shall use a mainstream language and avoid exotic runtime services, per the maintainability principle in doc 01 §6.

*Derives from:* BN-0009

**priority**: should · **verification**: inspection · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:343f87023664f04b6d354ae7bd37ee78d455eb72b681948ce4d6f8d18d5c6c39 · **ratified_backfilled**: True
<!-- tl:end -->
