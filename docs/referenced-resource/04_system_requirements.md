# 04 · System Requirements (SR)

Functional requirements on the Tool. Grouped by capability; **IDs are
permanent and gaps are meaningless**. "The Tool" = the software specified by
this set.

Each requirement below is **generated from the graph** by `tl docs`; only the
section headings and the tombstone note are hand-owned. Regenerate with
`tl docs` and gate it in CI with `tl docs --check` (SR-0094). Retired and
rejected IDs keep their tombstones in the graph but are excluded from this
published view (SR-0012); the SR-0007 note below is kept by hand as a live
example of the tombstone convention.

## 1. Identification and numbering

<!-- tl:item SR-0001 -->
**SR-0001 — Unique immutable UID** — `system_requirement`, status `ratified`

> The Tool shall assign every item a project-unique UID that never changes for the life of the project.

*Implements:* UR-0001

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0002 -->
**SR-0002 — UID format** — `system_requirement`, status `ratified`

> The Tool shall form UIDs as <PREFIX>-<NUMBER> where PREFIX is the owning register's configured prefix and NUMBER is a zero-padded positive integer of configured width (default 4).

*Rationale:* A mandatory separator keeps UIDs unambiguous and sortable; grammar in doc 06 §3.

*Implements:* UR-0001

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0003 -->
**SR-0003 — No UID reuse** — `system_requirement`, status `ratified`

> The Tool shall never assign a UID that has ever existed in the project, including UIDs of deleted items; retired numbers shall be recorded so allocation skips them.

*Implements:* UR-0001, UR-0002

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0004 -->
**SR-0004 — Identity independent of position** — `system_requirement`, status `ratified`

> The Tool shall not derive, change, or validate any UID based on an item's section, order, level, or file location; moving or reordering items shall leave UIDs untouched.

*Implements:* UR-0001, UR-0002

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0005 -->
**SR-0005 — Automatic UID allocation** — `system_requirement`, status `ratified`

> On item creation the Tool shall allocate the next unused number for the register's prefix automatically, while also accepting an explicit unused UID.

*Implements:* UR-0002

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0006 -->
**SR-0006 — Merge-safe allocation** — `system_requirement`, status `ratified`

> The Tool shall detect UID collisions arising from parallel branches at validation time and provide a conflict-resolution command that reassigns the younger item to a fresh UID and rewrites references to it.

*Rationale:* Detection plus an assisted single-direction fix keeps SR-0001 while making merges tractable.

*Implements:* UR-0014

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

**SR-0007 — RETIRED (tombstone).**
*(Was: "The Tool shall provide a renumber command to compact UID sequences."
Withdrawn during drafting because it contradicts SR-0001/SR-0003/SR-0004. The
ID is retired and will never be reused. It lives in the graph at status
`deleted`, so `tl docs` deliberately does not render it — this hand note is
kept as a live example of the tombstone convention.)*

<!-- tl:item SR-0008 -->
**SR-0008 — Human-readable aliases** — `system_requirement`, status `deferred`

> The Tool may support optional, non-unique display titles and searchable aliases, provided all references and links use UIDs only.

*Implements:* UR-0001

**priority**: could · **verification**: inspection · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0093 -->
**SR-0093 — Tombstone permanence** — `system_requirement`, status `ratified`

> The Tool shall report, at validation time, any UID whose last recorded status was deleted but whose item file is absent from the working tree, because a tombstone is the permanent record that a UID was retired and must never be removed.

*Rationale:* A tombstone is the only record that a UID was retired; if it is erased by a bad merge or a stray git rm, the never-reused guarantee (SR-0001) silently breaks. The gate already reads each item's status at the previous commit (SR-0083), so a vanished tombstone is detectable there without a redundant second ledger.

*Implements:* UR-0014

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

## 2. Data model, storage, and editing

<!-- tl:item SR-0010 -->
**SR-0010 — One file per item** — `system_requirement`, status `ratified`

> The Tool shall store each item as one UTF-8 text file (YAML per doc 06), named by its UID, inside its register's directory.

*Implements:* UR-0007, UR-0014

**priority**: must · **verification**: inspection · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0011 -->
**SR-0011 — Register tree** — `system_requirement`, status `ratified`

> The Tool shall organize items into registers (a directory with a manifest defining prefix, title, attribute schema, and ordering) and registers into a project tree with declared parent relationships.

*Implements:* UR-0011

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0012 -->
**SR-0012 — Soft delete with tombstone** — `system_requirement`, status `ratified`

> The Tool shall implement deletion as a status change to 'deleted', retaining the item file with UID, deletion date, reason, and last content hash; deleted items are excluded from publishing/exports by default but included in history, diffs, and UID-reuse prevention.

*Implements:* UR-0002, UR-0003

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0013 -->
**SR-0013 — Ordering as metadata** — `system_requirement`, status `ratified`

> The Tool shall represent presentation order with an explicit per-register ordering that can be edited freely without touching item identity or content fingerprints.

*Implements:* UR-0002

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0014 -->
**SR-0014 — Project initialization and templates** — `system_requirement`, status `ratified`

> The Tool shall provide an init operation creating a valid empty project, and should ship starter templates aligned with ISO/IEC/IEEE 29148 register types.

*Implements:* UR-0020

**priority**: must · **verification**: demonstration · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0015 -->
**SR-0015 — Rich text in statements** — `system_requirement`, status `ratified`

> The Tool shall support CommonMark Markdown (subset in doc 06 §6) in item text fields, including tables, lists, code blocks, and images by relative path.

*Implements:* UR-0008

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0016 -->
**SR-0016 — Move between registers** — `system_requirement`, status `rejected`

> The Tool shall support moving an item to another register; the moved item keeps its UID and the Tool records its new location.

*Rationale:* Identity outranks tidy prefixes.

*Implements:* UR-0002

**priority**: should · **verification**: test
<!-- tl:end -->

<!-- tl:item SR-0077 -->
**SR-0077 — Safe, clear project initialisation** — `system_requirement`, status `ratified`

> The init command shall report the created project's absolute path, and shall refuse to create a project that would nest with an existing one — whether an ancestor directory or a descendant directory already contains a throughline.toml — unless --force is given. On refusal it shall change nothing and exit with a usage error. While scanning descendants for existing projects, if the scan takes noticeable time it shall show live progress on an interactive terminal rather than appear to hang (actionable output, NFR-0014; no accidental broken layouts).

*Implements:* UR-0020

**origin**: human · **priority**: should · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0100 -->
**SR-0100 — Guided starter project on init** — `system_requirement`, status `ratified`

> The init command shall by default seed a small, self-consistent starter graph and a published document, so that a freshly initialised project passes `tl check` and renders content immediately rather than presenting an empty project the user must reverse-engineer from the schema. The starter shall exercise the shipped default configuration end to end — a root intent, a requirement and a non-functional requirement grounded to it, a test that verifies the requirement, and a non-goal — plus a docs/overview.md carrying tl:item, tl:table and tl:matrix regions with [docs] paths configured, so publication coverage is active and the document ships already rendered. The seeded content shall be independently suppressible so the newcomer default does not trap experienced users — a --no-demo flag shall omit the seeded example items and rendered document while still creating the default registers (an empty but scaffolded project), a --no-defaults flag shall omit the default registers (and, since seeded items have nowhere to live, the demo with them), and a --bare flag shall suppress all seeded content and write only throughline.toml (equivalent to --no-demo and --no-defaults together). Every seeded item, register, and published document is ordinary project content the user may freely edit, move, or delete; the starter is a runway, not a fixture.

*Rationale:* An opinionated schema with no content makes a newcomer reverse-engineer the grounding model from configuration alone, which is exactly the friction UR-0020 forbids. Shipping a check-clean, already-rendered example turns the first minute into a working demonstration of grounding, injection, and publication coverage, and the --bare escape hatch keeps the empty-project workflow for those who want it.

*Implements:* UR-0020

**origin**: human · **priority**: should · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0101 -->
**SR-0101 — Unique register prefixes** — `system_requirement`, status `ratified`

> Each register's UID prefix shall be unique across the project. The register new command shall refuse to create a register whose prefix another register already declares, changing nothing and exiting with a usage error; and validation shall report a prefix-collision error when two registers on disk declare the same prefix. A prefix names the register that owns a UID namespace (SR-0002), so if two registers share one their UID numbering overlaps and the loader would silently drop one register's items — a data-loss trap — therefore the clash shall fail fast rather than corrupt the graph. Registers remain orthogonal to item types (a register owns a prefix and numbering, not a type), so this rule constrains prefixes only and never restricts which item types a register may contain.

*Rationale:* The loader keys registers by prefix, so a second register reusing a prefix silently clobbers the first and its items vanish from the graph while UID allocation collides — a corruption that undermines stable identity (UR-0001) invisibly. register new already guards one folder against a repeated manifest; extending that guard to prefixes, with a validation backstop for clashes introduced by a merge or a hand edit, closes the gap fail-fast.

*Implements:* UR-0001

**origin**: human · **priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0102 -->
**SR-0102 — Register names the prefix-owning collection** — `system_requirement`, status `ratified`

> The tool shall name the prefix-owning, numbered collection of items a "register" — the on-disk folder with a `.register.yml` manifest, created by `tl register new`, that owns a UID prefix and its numbering (SR-0002) — and shall reserve the word "document" for the reader-facing published Markdown that `tl docs` injects graph content into (SR-0094). The two concepts shall not share one word anywhere a user meets them; the CLI command, the model type, the manifest filename, the query filter field, and the guides shall each name exactly one of register or document per concept.

*Rationale:* The word document was overloaded — the `.document.yml` folder holds no prose; it is a register of items under one prefix, while the readable documents are the Markdown files publication injects into. The ambiguity was severe enough that the authors themselves lost the thread, which is precisely the comprehensibility failure UR-0020 and UR-0022 forbid. Naming each concept once removes it.

*Implements:* UR-0020, UR-0022

**origin**: human · **priority**: should · **verification**: inspection · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0103 -->
**SR-0103 — Filter expressions never reach eval or exec** — `system_requirement`, status `ratified`

> The Tool shall never pass any project-supplied value — a coverage-rule filter, a query expression, or any other string read from project files or configuration — to eval, exec, or an equivalent dynamic-code primitive. The one boolean filter language (SR-0045) shall be evaluated through a constrained parser that reads only the published filter namespace (attributes, tags, text, status, type, register, and link predicates) and can reach neither Python builtins nor object internals nor imports. A filter that cannot be parsed shall fail fast with an error rather than fall back to dynamic evaluation.

*Rationale:* NFR-0022 declares project files untrusted input that must not become code execution, but its wording pins only the YAML loader and emitter; the filter path evaluates expressions with eval against a namespace whose only guard is an emptied builtins, which sandbox-escape techniques defeat, so the exact threat NFR-0022 names is still open on the filter surface. A crafted filter in a committed coverage rule already runs on every contributor's and CI machine at tl check; the risk sharpens once graphs are composed across authorities, because a filter authored by one party would then execute inside another party's environment. A parser that evaluates the fixed grammar directly removes the primitive rather than trying to fence it.

*Implements:* UR-0010
*Refines:* SR-0045
*Relates:* NFR-0022

**origin**: human · **priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

## 3. Attributes and schema

<!-- tl:item SR-0020 -->
**SR-0020 — Custom attributes** — `system_requirement`, status `ratified`

> The Tool shall let each project define custom item attributes with name, type, default value, and required flag.

*Implements:* UR-0011

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0021 -->
**SR-0021 — Item types** — `system_requirement`, status `ratified`

> The Tool shall support project-defined item types with per-type attribute schemas; UID prefixes remain per register, not per type.

*Implements:* UR-0011

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0022 -->
**SR-0022 — Built-in core fields** — `system_requirement`, status `ratified`

> The Tool shall reserve and manage these fields on every item: uid, type, status, text, title, links, order/level, normative, derived, reviewed, created, modified.

*Implements:* UR-0011

**priority**: must · **verification**: inspection · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0023 -->
**SR-0023 — Schema validation** — `system_requirement`, status `ratified`

> The Tool shall validate every item against the project schema (types, required fields, enum membership, ID regex) and report violations with file, field, and reason.

*Implements:* UR-0016

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0024 -->
**SR-0024 — Status vocabulary and transitions** — `system_requirement`, status `ratified`

> The Tool shall provide a default status set that projects can replace, and should support declaring allowed transitions which validation enforces.

*Implements:* UR-0011

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0080 -->
**SR-0080 — Deferred (parked) status** — `system_requirement`, status `ratified`

> The Tool's default status set shall include a 'deferred' status for an item that is acknowledged and grounded but deliberately not scheduled — a parked backlog item, distinct from 'draft' (actively moving toward approval). Deferred items remain live (they are not tombstoned like 'deleted') so they still ground and appear in queries, letting authors separate the active work-front from the wish-list.

*Implements:* UR-0011

**priority**: should · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0081 -->
**SR-0081 — Status membership validation** — `system_requirement`, status `ratified`

> When a project declares a status vocabulary ([status] values), the Tool's check shall validate every live item's status against it and report a finding (rule 'bad-status', severity configurable per SR-0041) for any status outside the declared set, so typos and stale statuses cannot silently enter the graph. When no vocabulary is declared the rule is inert.

*Implements:* UR-0011

**priority**: should · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0082 -->
**SR-0082 — Single validated project schema** — `system_requirement`, status `ratified`

> The Tool shall load a project's configuration into one validated schema object when the project is opened, reporting a clear, actionable error for malformed or internally inconsistent configuration (for example a rule that references an unknown type, or a grounding link type absent from the declared link set) instead of silently mis-behaving. The schema object shall expose a stable set of typed accessor and predicate helpers (for example the attributes and normative attributes of a type, whether a status or link type is declared, and the grounding and root types) so that each lookup, check, and indirection is defined once and reused. All components — validation, fingerprinting, UID allocation, publishing, and the CLI — shall derive their behaviour from these helpers rather than reading configuration ad hoc, so that new domain concepts (types, link types, statuses, and rules) can be introduced through configuration alone without code changes.

*Implements:* UR-0011

**priority**: should · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0083 -->
**SR-0083 — Enforced status transitions** — `system_requirement`, status `ratified`

> The Tool shall let a project declare, in configuration, the status transitions it permits (for each status, the set of statuses it may move to), and validation shall report a clear, actionable finding when an item's status changes to one the declared transitions do not allow. The baseline status is the item's status in a git reference (the previous commit by default), so `check` gates the change actually being introduced; creating a new item is not a transition. When no transition table is declared, or the baseline cannot be read (the project is not in a git work tree), transition checking is inert and every status is reachable, matching the tool's other optional vocabularies. Declared transition endpoints must be members of the declared status set, reported as a configuration error at load time otherwise. This completes the transition half of SR-0024, whose status vocabulary is already enforced.

*Implements:* UR-0011

**priority**: should · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

## 4. Links and traceability

<!-- tl:item SR-0030 -->
**SR-0030 — Typed, directed links** — `system_requirement`, status `ratified`

> The Tool shall support links from item to item carrying a project-defined type/role, stored on the source item and indexed in both directions.

*Implements:* UR-0004

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0031 -->
**SR-0031 — External references** — `system_requirement`, status `ratified`

> The Tool shall support links to external targets: URLs and repository file paths (optionally with line ranges).

*Implements:* UR-0004

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0032 -->
**SR-0032 — Link integrity** — `system_requirement`, status `ratified`

> Validation shall flag links to unknown UIDs, links to deleted items, and (configurably) circular refines chains.

*Implements:* UR-0004, UR-0016

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0107 -->
**SR-0107 — Namespace-qualified references fail fast toward composition** — `system_requirement`, status `ratified`

> A reference target of the form `<namespace>:<UID>` — a namespace name, a colon, and an otherwise-valid UID, for example `gds:SR-0001` — asserts that the target resolves inside a declared external source. The core Tool performs no composition and cannot resolve such a target, so `tl check` shall recognise this form from the reference alone and fail with a distinct finding that names the composing tool (`tl-compose`), rather than report it as an ordinary dangling link to a missing local UID. The Tool shall reach this verdict from the reference's syntax only — it shall not read any source configuration and shall remain entirely source-unaware. Free external references — a URL, a repository path, or any other out-of-graph pointer (SR-0031) — shall stay opaque and shall not trigger this rule, because being unresolvable is those forms' intended purpose.

*Rationale:* Without a distinct rule the namespace-qualified form falls through to the dangling-link check (SR-0032) and is reported as a missing local UID, which misleads a composer into hunting for a typo when the real remedy is to run the composing tool. Recognising the syntax as a first-class token turns the wrong tool into a signpost to the right one. It also keeps the core's only concession to composition minimal — the Tool gains no ability to resolve, fetch, or merge; it merely refuses to pretend a cross-source reference is a broken local one. Composition itself stays outside the core and lives in throughline-compose.

*Implements:* UR-0004
*Refines:* SR-0032
*Relates:* SR-0031

**origin**: human · **priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0108 -->
**SR-0108 — Reference classification is available to library consumers** — `system_requirement`, status `ratified`

> The predicates by which the Tool recognises a free external reference (SR-0031) and a namespace-qualified reference (SR-0107) shall be part of throughline's public library surface, exported under stable, non-underscore names and covered by the same compatibility promise as the rest of that surface. A library consumer — in practice the composing tool, which must classify every link target the same way the core does before it can prepare a union (NG-0001) — shall obtain that classification by calling the core, not by reimplementing the grammar. The Tool shall keep exactly one definition of each rule — the public predicates are the same functions the internal validation pipeline uses, so a consumer and the core can never disagree about whether a given target is external, namespace-qualified, or an ordinary local UID.

*Rationale:* SR-0107 makes the core the single authority on what a namespace-qualified reference is, but a composer must reach the same verdict one step earlier, while rewriting targets into a union. If that classification is not on the public surface the composer either couples to a private, underscore-named helper — fragile, and prone to vanish between releases — or copies the grammar, at which point two definitions can drift and a reference the core rejects the composer might silently accept. Publishing the predicates keeps a single source of truth across the library boundary at effectively no cost — they already exist and are already what the core itself runs.

*Implements:* UR-0004
*Refines:* SR-0107
*Relates:* SR-0031

**origin**: human · **priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0033 -->
**SR-0033 — Content fingerprints** — `system_requirement`, status `ratified`

> The Tool shall compute a SHA-256 fingerprint of each item's normative content (fields in doc 06 §5; excludes ordering, comments, and non-normative metadata).

*Implements:* UR-0005, UR-0006

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0034 -->
**SR-0034 — Suspect links** — `system_requirement`, status `ratified`

> Each link shall store the target's fingerprint when last confirmed; when stored and current fingerprints differ, the Tool reports the link suspect and provides a command to re-confirm (re-stamp) links after review.

*Implements:* UR-0005

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0035 -->
**SR-0035 — Impact analysis** — `system_requirement`, status `ratified`

> Given a UID, the Tool shall report the transitive set of items reachable via incoming links, grouped by link type and depth.

*Implements:* UR-0012

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0036 -->
**SR-0036 — Baselines** — `system_requirement`, status `ratified`

> The Tool shall create named baselines pinning the exact state of all items, list baselines, and check out or read any baseline.

*Implements:* UR-0013

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0037 -->
**SR-0037 — Diff between versions** — `system_requirement`, status `ratified`

> The Tool shall compare two baselines, revisions, or working state and report per item added/deleted/modified and links added/removed, as text and JSON.

*Implements:* UR-0003

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0038 -->
**SR-0038 — Review workflow states** — `system_requirement`, status `ratified`

> The Tool shall record a per-item reviewed fingerprint set by an explicit review command, and validation shall list items whose current fingerprint differs from it.

*Implements:* UR-0006

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0084 -->
**SR-0084 — Type-constrained links** — `system_requirement`, status `ratified`

> The Tool shall let a project declare, per link type, the item types permitted at each end of that link — the set of source types it may originate from and the set of target types it may point to — and validation shall report a clear, actionable finding when a link's endpoints violate the declared shape (for example a `mitigates` link whose source is not a risk-bearing type, or that points at something other than a risk). A link type with no declared rule is unconstrained, and the target-side check is skipped when the target is external or absent, matching the tool's other optional vocabularies. A rule may constrain only the source, only the target, or both. Any link type named in a rule must be a member of the declared link vocabulary, reported as a configuration error at load time otherwise. This lets a new domain concept — a `risk` type that only requirements may mitigate, say — be expressed and its graph shape enforced through configuration alone, without code changes.

*Implements:* UR-0011

**priority**: should · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0085 -->
**SR-0085 — Report the graph's link shape** — `system_requirement`, status `ratified`

> The Tool shall be able to report the actual link shape of a project — the set of distinct (source type, link type, target type) triples present in the graph, with a count of each — as a first-class operation on the link index rather than something a user must reconstruct by hand. The CLI shall surface this report so that a maintainer can see how the graph is currently wired and author or tighten `[link_rules]` (SR-0084) from observed reality. Targets that are not known items (external or dangling) are reported with an empty target type so the report stays total.

*Implements:* UR-0012

**priority**: should · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

## 5. Validation, quality, and CI

<!-- tl:item SR-0040 -->
**SR-0040 — Single check command** — `system_requirement`, status `ratified`

> The Tool shall provide a check command running all validations with a non-zero exit code on error, suitable for CI.

*Implements:* UR-0016

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0041 -->
**SR-0041 — Severity configuration** — `system_requirement`, status `ratified`

> Projects shall be able to set each validation rule to error, warning, or off, and the Tool shall support promoting all warnings to errors for CI.

*Implements:* UR-0016

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0042 -->
**SR-0042 — Coverage rules** — `system_requirement`, status `ratified`

> Projects shall be able to declare coverage rules of the form 'every item of type/register X with status in S must have >=1 link of type T to register Y', which check enforces.

*Implements:* UR-0012

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0043 -->
**SR-0043 — Requirement quality lint** — `system_requirement`, status `ratified`

> The Tool should provide optional lint rules for statement quality: missing shall/should/may, multiple shall in one statement, vague terms, empty rationale on Must items, and EARS templates.

*Implements:* UR-0017

**priority**: should · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0044 -->
**SR-0044 — Machine-readable check output** — `system_requirement`, status `ratified`

> check shall optionally emit findings as JSON (rule id, severity, uid, file, message) for tooling and CI annotations.

*Implements:* UR-0016

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0078 -->
**SR-0078 — Informative check summary** — `system_requirement`, status `ratified`

> By default in text output, the check command shall print a summary of the validated graph — live item counts by type, link counts by type, and grounding health (how many non-root items reach a root and how many delivery roots are served) — so a run communicates what was actually validated, not only an error/warning tally. A --quiet flag shall suppress the summary for CI and scripting. The --format json output is the machine contract and shall be unaffected.

*Implements:* UR-0012

**origin**: human · **priority**: should · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

## 6. Search, filter, and query

<!-- tl:item SR-0045 -->
**SR-0045 — Filter expression language** — `system_requirement`, status `ratified`

> The Tool shall provide one boolean filter language over attributes, tags, text, status, type, register, and link predicates, usable identically in search, table generation, exports, and coverage rules.

*Implements:* UR-0010

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0104 -->
**SR-0104 — Filter grammar is a closed, defined language** — `system_requirement`, status `ratified`

> The filter language (SR-0045) shall be a fixed, closed grammar defined by this requirement, not by whichever expressions a test or a document happens to exercise. The grammar is deliberately a strict subset of Python expression syntax — so that the common case is instantly familiar to the tool's Python and data audience with nothing new to learn, and no separate parser generator or grammar file is carried — using Python's spelling of the operators (and, or, not, in; never the C-style && or ||). It is a subset, not the whole language, and the tool shall reject every construct outside the set below rather than evaluate it. A filter is a boolean expression and shall support exactly these constructs — the logical operators and, or, and not, with parentheses for grouping; the comparisons ==, !=, <, <=, >, and >=; the membership tests in and not in; literal values — single- or double-quoted strings, integers, decimals, the booleans true and false, and none — and literal lists, tuples, and sets of them; references to the fixed field namespace (uid, type, status, register, title, text, rationale, normative, derived, attrs, and links) and no other bare names; indexing a field by a literal or field key, for example attrs['priority']; and a closed allow-list of read-only accessor methods on a referenced value, for example attrs.get('priority'), title.lower(), or the link predicates links.outgoing('implements') and links.incoming('verifies') (SR-0106). Every construct outside this grammar — arithmetic, assignment, comprehensions, generator or conditional expressions, lambdas, attribute access other than the allowed accessor calls, a call to anything but an allowed accessor, or any name absent from the namespace — shall be rejected as a malformed filter and never evaluated. The grammar shall be identical in search, query, table and matrix generation, exports, and coverage rules.

*Rationale:* SR-0045 named the language's dimensions but never its syntax, so the accepted grammar was implicitly whatever the evaluator could parse — the whole of Python, which is simultaneously the deserialisation-class RCE that SR-0103 closes and an undefined contract no test set can pin down. Defining the grammar makes the language a deliberate design surface — a contributor or integrator knows precisely what a filter may contain, and the constrained parser (SR-0103) has a fixed specification to enforce rather than a moving target inferred from examples. A Python-expression subset was chosen over a bespoke or C-style grammar because it needs no dependency and no syntax lessons for the likely audience; the risk it carries — expressions that look like Python but are not the whole language — is answered by naming the subset here and failing fast on anything outside it. Should non-Python integrators later make a cross-language standard worthwhile, this is the item where a move to one (for example CEL) would be recorded.

*Implements:* UR-0010
*Refines:* SR-0045
*Relates:* SR-0103

**origin**: human · **priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0105 -->
**SR-0105 — Tags as a first-class filter field** — `system_requirement`, status `deferred`

> The filter grammar (SR-0104) shall expose an item's tags as a first-class field so a filter can test tag membership directly — for example 'security' in tags — delivering the "tags" dimension SR-0045 names without requiring the author to know that tags are stored inside a particular attribute. Until this is built, tags have no dedicated field and a filter must reach them through attrs (for example 'security' in attrs.get('tags', [])); this requirement records that gap as a deliberate, grounded backlog item rather than leaving the SR-0045 promise silently unmet.

*Rationale:* SR-0045 lists tags as one of the dimensions the one filter language ranges over, but the tool has no tag concept distinct from ordinary custom attributes, so the grammar (SR-0104) exposes no tags name. Rather than narrow SR-0045 to what is implemented, the shortfall is captured as its own item so the decision to add a first-class tag field — or to fold tags formally into attrs — is made openly.

*Implements:* UR-0010
*Refines:* SR-0045

**origin**: human · **priority**: could · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0106 -->
**SR-0106 — Link predicates in the filter grammar** — `system_requirement`, status `ratified`

> The filter grammar (SR-0104) shall let a filter select items by their graph edges, not only by scalar fields, through a links value exposing read-only predicates in the same accessor style as attrs.get. It shall provide links.outgoing(type) — true when the item has an outgoing link of that type; links.incoming(type) — true when another item links to it with that type; and links.to(uid) — true when the item has an outgoing link to that target UID (matching external targets by their literal string too). Each predicate called with no type argument shall match a link of any type, so links.outgoing() means "has any outgoing link" and links.incoming() means "is pointed at by anything". This delivers the "link predicates" dimension SR-0045 names inside the shared expression language, so a query or an exported view can match on connectivity the way coverage rules (SR-0042) already can through their incoming/outgoing selector — for example finding requirements with no verifying test as type=='requirement' and not links.incoming('verifies'). Incoming predicates read the whole graph; where a caller cannot supply it the incoming predicate shall fail fast as a malformed filter rather than silently report false.

*Rationale:* SR-0045 lists link predicates among the filter language's dimensions, and coverage rules already express incoming/outgoing link requirements, but the shared expression grammar itself exposed no way to reach an item's links — so a query could not ask "requirements with no verifying test" the way a coverage rule can. Reusing the accessor-call shape the grammar already defines (SR-0104) adds the capability without a new call form or a bespoke syntax, keeping the language a small Python-expression subset.

*Implements:* UR-0010
*Refines:* SR-0045, SR-0104

**origin**: human · **priority**: should · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0046 -->
**SR-0046 — Full-text search** — `system_requirement`, status `ratified`

> The Tool shall support case-insensitive substring and regex search across statements, titles, and rationale, returning UIDs and locations.

*Implements:* UR-0010

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0079 -->
**SR-0079 — CLI query command** — `system_requirement`, status `ratified`

> The CLI shall provide a query command (alias ls) that lists the items matching an SR-0045 filter expression over type, status, register, attributes, normative flag, and text. It shall print each match as UID, type/status, and title, or emit the full items as JSON with --format json, and report the match count — so users can find requirements by attribute and status without external tools. A malformed expression shall produce an actionable error and a usage exit code.

*Implements:* UR-0010

**origin**: human · **priority**: should · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

## 7. Publishing and reporting

<!-- tl:item SR-0050 -->
**SR-0050 — HTML publishing** — `system_requirement`, status `rejected`

> The Tool shall publish the project as a static HTML site with a document view, per-item anchors by UID, cross-document hyperlinks, and an index.

*Rationale:* Rejected as out of scope under NG-0005. Generating an HTML site would make the core a rendering engine; throughline injects Markdown (SR-0094) and delegates HTML to external tools such as pandoc or mdBook. Tombstoned, never reused.

*Implements:* UR-0008
*Relates:* NG-0005

**priority**: must · **verification**: demonstration
<!-- tl:end -->

<!-- tl:item SR-0051 -->
**SR-0051 — Traceability views** — `system_requirement`, status `ratified`

> Publishing shall include generated traceability views rendered into Markdown by reference: a table view and a traceability matrix between two registers/link types. Graphical exports (e.g. DOT/Graphviz) are out of scope (NG-0005); a wrapper may derive them from the JSON dump (SR-0055).

*Implements:* UR-0004, UR-0012
*Relates:* NG-0005

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0052 -->
**SR-0052 — Coverage report** — `system_requirement`, status `ratified`

> The Tool shall generate a coverage report per coverage rule (SR-0042) with counts, percentages, and the list of uncovered UIDs.

*Implements:* UR-0012

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0053 -->
**SR-0053 — PDF output** — `system_requirement`, status `rejected`

> The Tool should produce PDF from the HTML document view.

*Rationale:* Rejected as out of scope under NG-0005. PDF is a downstream conversion of the injected Markdown, delegated to external tools (pandoc), not something the pure text core produces. Tombstoned, never reused.

*Implements:* UR-0008
*Relates:* NG-0005

**priority**: should · **verification**: demonstration
<!-- tl:end -->

<!-- tl:item SR-0086 -->
**SR-0086 — Visual model and lifecycle diagrams** — `system_requirement`, status `ratified`

> The Tool shall emit a visual representation of a project's shape as Mermaid diagram source — a type model (item types as nodes joined by labelled edges for the links observed between them) and a status-transition state machine (the declared [transitions], each move an edge between states) — so a maintainer or newcomer can see how the graph is wired and how items move through their lifecycle without reading the configuration by hand. Mermaid is chosen because it is plain text (so a diagram lives in version control and diffs cleanly) and renders directly in Markdown and on the forge. The CLI shall offer each diagram and, by default, both, wrapped as fenced Markdown blocks ready to embed; a raw Mermaid form shall be available for piping into a renderer. When a project declares no transitions, the lifecycle diagram is reported as absent rather than emitted empty.

*Implements:* UR-0012

**priority**: could · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0090 -->
**SR-0090 — Reproduce a document at a past revision** — `system_requirement`, status `ratified`

> The Tool shall render the requirements document as the graph stood at a named git revision, so a stakeholder can reproduce exactly what a baseline said. Given a commit-ish, the Tool shall reconstruct the project from that revision's tracked files without touching the working tree, render the document from it, and stamp the provenance line with the revision and its resolved commit hash. Because items are plain YAML under version control, this makes any past state addressable by commit rather than only the current checkout. When the project is not inside a git work tree, or the revision cannot be resolved, the Tool shall fail with a clear message rather than silently render the working tree.

*Implements:* UR-0013

**priority**: should · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

## 8. Import and export

<!-- tl:item SR-0054 -->
**SR-0054 — CSV/Excel round-trip** — `system_requirement`, status `rejected`

> The Tool shall export any register or filter result to CSV and XLSX and import CSV/XLSX, matching on UID and assigning fresh UIDs to rows without one.

*Rationale:* Rejected as out of scope under NG-0005 — both halves. throughline neither exports to nor imports from CSV/XLSX; the JSON dump (SR-0055) is the sanctioned interchange surface and a graph is started natively rather than migrated in from a spreadsheet. Tombstoned, never reused.

*Implements:* UR-0009, UR-0018
*Relates:* NG-0005

**priority**: must · **verification**: test
<!-- tl:end -->

<!-- tl:item SR-0055 -->
**SR-0055 — Canonical JSON export** — `system_requirement`, status `implemented`

> The Tool shall export the entire project (items, schema, links, baselines) as a single documented JSON structure for third-party tooling.

*Implements:* UR-0015

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0056 -->
**SR-0056 — ReqIF exchange** — `system_requirement`, status `rejected`

> The Tool shall export registers to ReqIF preserving hierarchy, attributes, and links, and should import ReqIF including iterative re-import preserving UIDs via stored foreign IDs.

*Rationale:* Rejected as out of scope under NG-0005. ReqIF is an RM-vendor interchange format; supporting it would pull throughline into the round-tripping it explicitly disclaims. Tombstoned, never reused.

*Implements:* UR-0009
*Relates:* NG-0005

**priority**: should · **verification**: test
<!-- tl:end -->

<!-- tl:item SR-0057 -->
**SR-0057 — Markdown export** — `system_requirement`, status `ratified`

> The Tool shall export each register as standalone Markdown (for wikis and code review).

*Implements:* UR-0008

**priority**: should · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

## 9. Interfaces

<!-- tl:item SR-0060 -->
**SR-0060 — CLI** — `system_requirement`, status `ratified`

> The Tool shall expose all capabilities through a scriptable CLI with stable exit codes: 0 ok, 1 validation errors, 2 usage/internal error.

*Implements:* UR-0016

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0061 -->
**SR-0061 — Library API and self-hosting** — `system_requirement`, status `ratified`

> The Tool shall expose its core as a documented library API, and the project shall manage its own requirements with itself before 1.0.

*Implements:* UR-0015

**priority**: must · **verification**: inspection · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0062 -->
**SR-0062 — Source-code traceability scan** — `system_requirement`, status `rejected`

> The Tool could scan configured source trees for UID markers in comments and treat them as implements links for coverage.

*Rationale:* Withdrawn with the requirement it implements (UR-0019). Requirement-to-code linking is already served by external references (SR-0031), leaving this item as the only unserved residue — and it is a materially different capability from the one its parent advertised, because it makes the tool read arbitrary source trees and infer links rather than validate the ones the graph declares. If coverage over source code is wanted later it should be proposed on its own terms and grounded under UR-0012, where coverage and impact analysis already live.

*Implements:* UR-0019

**priority**: could · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0063 -->
**SR-0063 — Local web viewer** — `system_requirement`, status `rejected`

> The Tool could serve the published site with live reload for authoring; web-UI editing is out of scope for 1.0.

*Implements:* UR-0020
*Relates:* NG-0006

**priority**: could · **verification**: demonstration
<!-- tl:end -->

<!-- tl:item SR-0074 -->
**SR-0074 — CLI invocation must not silently collide with existing tools** — `system_requirement`, status `ratified`

> The Tool's installed command shall not silently shadow, or be shadowed by, a standard system utility of the same name (e.g. the POSIX 'rmt' tape tool). The Tool shall either ship a non-colliding invocation name, or detect the clash at install / first run and warn the user with remediation. A newcomer's first command must run the Tool, not an unrelated utility.

*Implements:* UR-0020

**priority**: should · **verification**: inspection · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0075 -->
**SR-0075 — Contributor environment doctor** — `system_requirement`, status `ratified`

> The project shall provide a contributor setup/doctor script that verifies development prerequisites, runs the grounding gate, and can wire the local pre-commit grounding hook on request, reporting an actionable pass/fix status per check and exiting non-zero on failure. It is developer tooling maintained in the repository — not part of the shipped throughline package — and may therefore be environment-specific.

*Implements:* UR-0021

**origin**: human · **priority**: should · **verification**: demonstration · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0076 -->
**SR-0076 — CLI version reporting** — `system_requirement`, status `ratified`

> The Tool's CLI shall report its own version via a --version flag, printing the installed package version and exiting 0, so users and CI can record which build produced a result (supports the SemVer compatibility surface, NFR-0011).

*Implements:* UR-0016

**origin**: human · **priority**: should · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

## 10. Configuration and extensibility

<!-- tl:item SR-0070 -->
**SR-0070 — Project configuration file** — `system_requirement`, status `ratified`

> The Tool shall read project settings (schema, link types, rules, publishing options) from a single versioned TOML file at the project root.

*Implements:* UR-0011

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0071 -->
**SR-0071 — Plugin points** — `system_requirement`, status `ratified`

> The Tool should define a plugin interface for custom validation rules, discoverable without modifying core code. Exporter and importer plugins are out of scope (NG-0005); third-party tooling integrates through the documented JSON dump (SR-0055) instead.

*Implements:* UR-0015
*Relates:* NG-0005

**priority**: should · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0072 -->
**SR-0072 — Deterministic file writes** — `system_requirement`, status `ratified`

> All Tool writes shall be deterministic and minimal-diff (stable key order, stable quoting, trailing newline, no timestamp churn) so Git diffs show only real changes.

*Implements:* UR-0007, UR-0014

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

## 11. Grounding and intent-driven development

<!-- tl:item SR-0073 -->
**SR-0073 — Grounding-assisted authoring** — `system_requirement`, status `ratified`

> When creating a non-root item, the Tool should help the author attach it to a valid parent at creation time (offering the existing roots and grounding candidates to link against), so an item is grounded at birth rather than caught as an orphan by a later check. Interactive prompts are optional and non-blocking; the same operation stays scriptable via flags.

*Implements:* UR-0017

**priority**: should · **verification**: demonstration · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0091 -->
**SR-0091 — Grounding flags must never fail silently** — `system_requirement`, status `ratified`

> When 'tl new' is invoked with a --ground flag, the Tool shall either attach the requested grounding link or, if it declines to add it, reject the command with a clear, actionable error. It shall never create the item while silently discarding the requested link — including for root-type sources, which may legitimately carry an explicit grounding link (e.g. a business_need that derives_from the vision). Rationale: a silently dropped grounding link yields an item the author believes is grounded but which surfaces only later as an orphan or unserved-root check failure — a fail-fast violation and a silent loss of authoring intent. Observed in throughline 0.1.0 (2026-07-10): 'tl new BN --ground INT-0001 --ground-type derives_from' created the business_need with no links block because the grounding block was skipped for root types, and the delivery-root intent then reported unserved-root under 'tl check'.

*Implements:* UR-0017
*Relates:* SR-0073

**priority**: must · **verification**: test · **origin**: ai · **ratified_by**: henry
<!-- tl:end -->

<!-- tl:item SR-0092 -->
**SR-0092 — Unratified machine-origin items fail the gate** — `system_requirement`, status `ratified`

> The Tool's check shall report an unratified finding for any item whose origin is in the configured machine-origin set (ai_origins) while its status is still proposed, so a machine-proposed item cannot pass the gate without human ratification.

*Implements:* UR-0023

**origin**: human · **priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0097 -->
**SR-0097 — non_goal root type** — `system_requirement`, status `ratified`

> The Tool's default project scaffold shall include a non_goal root item type, self-justifying like other roots, and non_goals shall appear in the agent-facing context brief so deliberately-excluded scope is visible to humans and agents. The Tool shall not attempt to automatically detect items that violate a non_goal.

*Rationale:* A non_goal is the negative space of the grounding layer — the object a human points at to reject a category of proposed scope with authority. Passive in this cut (documents and traces) because detecting a 'violation' of a non_goal is undecidable in general and would be scope creep.

*Implements:* UR-0025

**origin**: ai · **priority**: should · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0098 -->
**SR-0098 — Ratified_by records a genuine human identity** — `system_requirement`, status `ratified`

> The identity recorded by ratify (the ratified_by value) shall be a genuine human identity supplied by the ratifying person. Project guidance for AI agents shall direct them, when the ratifier is unknown, to ask the user rather than invent, guess, or reuse a value seen elsewhere — a fabricated ratified_by is a false accountability record, the exact failure the grounding layer exists to prevent.

*Rationale:* UR-0023 makes accountability rest with a person; that guarantee is only as good as the identity captured at ratification. The Tool cannot verify a name is real, so the control is a documented obligation on the human and their agents, reviewed rather than machine-checked. Named here so the AGENTS.md guidance has a requirement to trace to instead of living only as prose.

*Implements:* UR-0023

**origin**: ai · **priority**: must · **verification**: inspection · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

## 12. Narrative documents and agent interfaces

<!-- tl:item SR-0088 -->
**SR-0088 — Generate an agent-facing project brief** — `system_requirement`, status `ratified`

> The Tool shall emit, as a single Markdown document on demand, a briefing that equips an AI coding agent to work in the project under its own rules — combining the fixed conceptual contract of Intent-Driven Development (author a grounded requirement as a draft "red test" first, implement it, flip it to approved, and keep `check` green; roots justify themselves while non-roots must reach a root through a grounding link; AI-origin items enter the proposed state and require human ratification), the on-disk YAML item format, and the commands the agent will use. The project-specific half — the declared item types and their attributes (kind, whether required, whether normative, and any enum values), which types are roots and delivery roots, the link vocabulary and its per-type endpoint rules, the status vocabulary and the permitted transitions, the grounding configuration and the AI origins, and any coverage rules — shall be rendered from the loaded schema rather than restated in code, so the brief cannot drift from the configuration the validator enforces and stays correct as that configuration changes. The brief shall also include a live snapshot of the current graph (item and link counts and the observed link shape) so the agent sees the project as it actually stands. The document shall be plain text, so it can be committed as an AGENTS-style file or piped straight into an agent's context.

*Implements:* UR-0022

**priority**: should · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0094 -->
**SR-0094 — In-place marker injection** — `system_requirement`, status `ratified`

> The Tool shall render item content into HTML-comment marker regions (tl:item / tl:table / tl:matrix ... tl:end) within existing Markdown files, overwriting only the marked regions and leaving all other content unchanged. Table and matrix directives shall select items using the SR-0045 filter grammar. Only these three directives shall be provided in this cut; other output formats are delegated to external tools (pandoc, mdBook). Every configured document shall be processed uniformly — a document that currently contains no marker regions is a no-op, left byte-for-byte unchanged, not an error and not a special case, so a published document with nothing to inject is treated no differently from one full of markers.

*Rationale:* Injecting item content into human-owned files (terraform-docs style) keeps the document valid Markdown that renders on GitHub and shows generated changes in the PR diff, and it removes the parallel hand-maintained artifact that caused the docs/referenced-resource drift. Supersedes the stdout whole-document render (SR-0089).

*Implements:* UR-0024

**origin**: ai · **priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0095 -->
**SR-0095 — Document staleness gate (separate command)** — `system_requirement`, status `ratified`

> The Tool shall provide a dedicated command that reports a document as stale when re-rendering its marker regions would change their content (write-then-diff), exiting non-zero so a drifted document fails a CI gate. This staleness check shall NOT run as part of tl check. The command shall be inert for files that contain no tl: markers.

*Rationale:* Staleness is a publication-time concern, not an authoring-time one. Folding it into tl check would force every routine check to also reconcile docs, introducing friction in general use; keeping doc freshness a separate CI gate lets authors run tl check freely while CI still refuses to merge a drifted document.

*Implements:* UR-0024

**origin**: ai · **priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0096 -->
**SR-0096 — Publication coverage (unpublished)** — `system_requirement`, status `ratified`

> The Tool shall report, as unpublished, any live normative item referenced by no published document, once published-document paths are configured. The finding shall default to warning severity and be inert when no document paths are configured. A terminal-status item (rejected or deleted) is not live scope — it need never reach a reader — and shall be excluded from the rule, using the same terminal-status set as the invalidate cascade so "live" means one thing across the tool.

*Rationale:* The publication analogue of orphan — scope that cannot justify itself and also cannot hide from the reader. Doorstop, StrictDoc and Sphinx-Needs all transclude but none gate on it; this is the differentiator.

*Implements:* UR-0012

**origin**: ai · **priority**: should · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0099 -->
**SR-0099 — Directional traceability matrix in documents** — `system_requirement`, status `ratified`

> The tl:matrix document directive shall accept an optional <direction>:<link_type> selector (direction one of incoming or outgoing, reusing the coverage-rule grammar) preceding its filter. With a selector, each matching item is rendered as a row whose relationship column lists the items linked to it in that direction by that link type — so a document can render, for example, each user_requirement and the items that implement it (incoming:implements). With no selector the directive keeps its default behaviour (grounding trace plus verifying items). A relationship cell shall list only live items — an item that is rejected or deleted does not realize anything and shall be omitted (the terminal-status set is the same one the invalidate cascade uses). A malformed selector or filter fails injection rather than rendering silently wrong.

*Rationale:* UR-0004 requires navigating links in both directions; the matrix could previously only render the outgoing side, so the classic UR-to-realizers coverage matrix (the artifact doc 08 maintains by hand) could not be generated from the graph. One reused selector grammar covers either direction without a new directive, keeping the injector's surface small (NG-0001).

*Implements:* UR-0004

**origin**: ai · **priority**: should · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0109 -->
**SR-0109 — Count directive in documents** — `system_requirement`, status `ratified`

> The Tool shall provide a fourth document directive, tl:count <filter>, that renders the number of live items matching an SR-0045 filter as a plain integer in place of its marked region. Like tl:table and tl:matrix it selects with the filter grammar and counts only live items (an item that is deleted or rejected is not counted, matching the terminal-status set the matrix renderers already use). A malformed filter fails injection rather than rendering a silently wrong number, and a filter that matches nothing renders 0 — an honest count, never an error. The items a tl:count filter selects are published references for the purpose of the unpublished coverage rule (SR-0096), exactly as a tl:table filter is, so a count of a set of items also satisfies their publication. tl:count adds no new grammar and no new output format; it is the existing filter language rendered as a single scalar, keeping the injector's surface small (NG-0001).

*Rationale:* A document (or an index such as a README badge) frequently needs a live tally — how many requirements of a kind, how many tests, how many open items — that must not be hand-maintained, because a stale number is drift exactly as a stale table is (UR-0024). The matrix and table directives could already render the set; what was missing was rendering only its cardinality. One more directive over the same filter grammar closes that gap without widening the language.

*Implements:* UR-0024
*Refines:* SR-0094

**origin**: human · **priority**: should · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0138 -->
**SR-0138 — Schema-declared attribute defaults are applied at item creation** — `system_requirement`, status `ratified`

> SR-0020 lets a project declare a default value for a custom attribute, but the Tool does not yet apply it. This refinement fixes the behaviour — when an item is created, any attribute the schema declares with a default and that the new item does not set is populated with that declared default, so a schema sentinel (for example a priority value meaning "a human has not decided yet") lands on every new item automatically instead of being added by hand. A default is only ever applied at birth to an unset attribute; it never overwrites a value the author supplied and never rewrites existing items on later reads.

*Rationale:* SR-0020 promised a declarable default, but the Tool never applied one, so a schema sentinel meaning a human has not yet decided had to be typed onto every new item by hand. The omission left the feature half-built and the stated contract unmet.

*Implements:* UR-0011
*Refines:* SR-0020

**origin**: ai · **priority**: should · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0139 -->
**SR-0139 — UTF-8 console output** — `system_requirement`, status `ratified`

> The Tool shall force UTF-8 encoding on its standard output and standard error streams before it emits any text, so that a non-ASCII character the Tool prints — the arrow glyph in a grounding summary, an em dash in a finding — renders on every platform instead of aborting the command. On a console whose stream cannot be reconfigured the Tool shall leave it unchanged and carry on.

*Rationale:* On a Windows console left at the legacy cp1252 code page, tl context aborted with a UnicodeEncodeError the first time it reached the arrow glyph, so the one command an agent is told to run first could not run at all. Forcing the stream encoding turns a hard crash into correct output.

*Implements:* UR-0016
*Refines:* SR-0060

**priority**: must · **verification**: test · **origin**: ai · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0140 -->
**SR-0140 — Register prefix must satisfy the UID grammar** — `system_requirement`, status `ratified`

> When a register is created the Tool shall reject a prefix that does not satisfy the UID prefix grammar — an uppercase letter followed by one to fifteen uppercase letters or digits — and shall state that grammar in the error. A single-character prefix is therefore refused at registration rather than accepted and left to fail later.

*Rationale:* A one-character prefix parses ambiguously against the greedily matched number, so every existing item of such a register failed to parse and allocation silently reset to 1, minting duplicate UIDs on the next create. Rejecting the prefix at registration turns that silent corruption into a loud, early error the author can act on.

*Implements:* UR-0001
*Refines:* SR-0002

**priority**: must · **verification**: test · **origin**: ai · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0141 -->
**SR-0141 — Machine-origin items are born proposed** — `system_requirement`, status `ratified`

> When the Tool creates an item whose origin is in the configured machine-origin set, and the schema binds a proposed status role, the item shall be born in that proposed status rather than the initial draft status, so a machine-authored item enters the graph already awaiting human ratification. An explicit status argument overrides this, and an item with a human origin is unaffected.

*Rationale:* The guidance tells agents that machine-authored items enter proposed and must be ratified, but tl new created every item as draft, so the ratification gate never engaged and the documented discipline was silently unenforced. An item's birth status must match the promise made about it.

*Implements:* UR-0023
*Relates:* SR-0092

**priority**: must · **verification**: test · **origin**: ai · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0142 -->
**SR-0142 — Set attributes at item creation** — `system_requirement`, status `ratified`

> The Tool shall accept repeated key=value attribute arguments when an item is created, coercing each value to the kind the schema declares for that attribute and rejecting a value the schema cannot accept, so an author sets typed attributes in one step instead of creating the item and then hand-editing its YAML.

*Rationale:* Setting a declared attribute such as a priority meant opening the freshly created file and editing it by hand — an avoidable second step that invited typos in exactly the typed fields the schema exists to police.

*Implements:* UR-0011
*Refines:* SR-0020

**priority**: must · **verification**: test · **origin**: ai · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item SR-0143 -->
**SR-0143 — Remove and retype links** — `system_requirement`, status `ratified`

> The Tool shall let an author remove a link between two items and re-type an existing link in place, so a mislabelled or obsolete edge is corrected without hand-editing YAML. A remove or a retype shall name the single matching edge unambiguously, and shall fail clearly when no such edge exists or when the target is joined by several links of differing type and none is named.

*Rationale:* Links could only ever be added, so a wrong link type could be fixed only by editing the file by hand — the precise manual editing the CLI exists to spare the author, and a step that risks corrupting the surrounding structure.

*Implements:* UR-0004
*Refines:* SR-0030

**priority**: should · **verification**: test · **origin**: ai · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->
