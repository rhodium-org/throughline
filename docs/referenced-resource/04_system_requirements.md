# 04 · System Requirements (SR)

Functional requirements on the Tool. Grouped by capability; **IDs are
permanent and gaps are meaningless**. "The Tool" = the software specified by
this set. Normative terms per README conventions.

## 1. Identification and numbering

**SR-0001 — Unique immutable UID.**
The Tool shall assign every item a project-unique UID that never changes for
the life of the project.
`Priority: Must | Verification: Test | Traces: UR-0001 | Status: Approved`

**SR-0002 — UID format.**
The Tool shall form UIDs as `<PREFIX>-<NUMBER>` where PREFIX is the owning
document's configured prefix (uppercase letters/digits) and NUMBER is a
zero-padded positive integer of configured width (default 4).
*Rationale:* A mandatory separator keeps UIDs unambiguous and sortable;
grammar in doc 06 §3.
`Priority: Must | Verification: Test | Traces: UR-0001 | Status: Approved`

**SR-0003 — No UID reuse.**
The Tool shall never assign a UID that has ever existed in the project,
including UIDs of deleted items; retired numbers shall be recorded so
allocation can skip them.
`Priority: Must | Verification: Test | Traces: UR-0001, UR-0002 | Status: Approved`

**SR-0004 — Identity independent of position.**
The Tool shall not derive, change, or validate any UID based on an item's
section, order, level, or file location; moving or reordering items shall
leave UIDs untouched.
`Priority: Must | Verification: Test | Traces: UR-0001, UR-0002 | Status: Approved`

**SR-0005 — Automatic UID allocation.**
On item creation the Tool shall allocate the next unused number for the
document's prefix automatically, while also accepting an explicit unused UID.
`Priority: Must | Verification: Test | Traces: UR-0002 | Status: Approved`

**SR-0006 — Merge-safe allocation.**
The Tool shall detect UID collisions arising from parallel branches at
validation time and shall provide a conflict-resolution command that reassigns
the *younger* item to a fresh UID and rewrites references to it.
*Rationale:* Two branches can both allocate `REQ-0107`; detection plus an
assisted, single-direction fix keeps the invariant SR-0001 while making
merges tractable. An optional reservation service is out of scope (doc 02 §1).
`Priority: Must | Verification: Test | Traces: UR-0014 | Status: Approved`

**SR-0007 — RETIRED.**
*(Was: "The Tool shall provide a renumber command to compact UID sequences."
Withdrawn during drafting because it contradicts SR-0001/SR-0003/SR-0004.
The ID is retired and will never be reused — this entry is intentionally kept
as a live example of the tombstone convention.)*
`Status: Retired`

**SR-0008 — Human-readable aliases.**
The Tool may support optional, non-unique display titles and searchable
aliases, provided all references and links use UIDs only.
`Priority: Could | Verification: Inspection | Traces: UR-0001 | Status: Draft`

## 2. Data model, storage, and editing

**SR-0010 — One file per item.**
The Tool shall store each item as one UTF-8 text file (YAML per doc 06),
named by its UID, inside its document's directory.
`Priority: Must | Verification: Inspection | Traces: UR-0007, UR-0014 | Status: Approved`

**SR-0011 — Document tree.**
The Tool shall organize items into documents (a directory with a manifest
defining prefix, title, attribute schema, and ordering) and documents into a
project tree with declared parent relationships.
`Priority: Must | Verification: Test | Traces: UR-0011 | Status: Approved`

**SR-0012 — Soft delete with tombstone.**
The Tool shall implement deletion as a status change to `deleted`, retaining
the item file (or a tombstone stub) with UID, deletion date, reason, and last
content hash; deleted items shall be excluded from publishing and exports by
default but included in history, diffs, and UID-reuse prevention.
`Priority: Must | Verification: Test | Traces: UR-0002, UR-0003 | Status: Approved`

**SR-0013 — Ordering as metadata.**
The Tool shall represent presentation order with an explicit per-document
ordering (fractional `level` values or a manifest list) that can be edited
freely without touching item identity or content fingerprints.
`Priority: Must | Verification: Test | Traces: UR-0002 | Status: Approved`

**SR-0014 — Project initialization and templates.**
The Tool shall provide an `init` operation creating a valid empty project,
and should ship starter templates aligned with ISO/IEC/IEEE 29148 document
types (stakeholder, system, software requirements).
`Priority: Must / Should (templates) | Verification: Demonstration | Traces: UR-0020 | Status: Approved`

**SR-0015 — Rich text in statements.**
The Tool shall support CommonMark Markdown (subset defined in doc 06 §6) in
item text fields, including tables, lists, code blocks, and images by
relative path.
`Priority: Must | Verification: Test | Traces: UR-0008 | Status: Approved`

**SR-0016 — Move between documents.**
The Tool shall support moving an item to another document; because prefixes
encode the original document, the moved item keeps its UID and the Tool shall
record its new location.
*Rationale:* Identity outranks tidy prefixes; StrictDoc lists node moves as a
core capability.
`Priority: Should | Verification: Test | Traces: UR-0002 | Status: Approved`

## 3. Attributes and schema

**SR-0020 — Custom attributes.**
The Tool shall let each project define custom item attributes with name,
type (string, markdown, integer, float, boolean, date, enum, multi-enum,
UID-reference, URL), default value, and required flag.
`Priority: Must | Verification: Test | Traces: UR-0011 | Status: Approved`

**SR-0021 — Item types.**
The Tool shall support project-defined item types (e.g. requirement,
heading/section, test case, risk) with per-type attribute schemas; UID
prefixes remain per document, not per type.
`Priority: Must | Verification: Test | Traces: UR-0011 | Status: Approved`

**SR-0022 — Built-in core fields.**
The Tool shall reserve and manage these fields on every item: `uid`, `type`,
`status`, `text` (statement), `title` (optional), `links`, `order/level`,
`normative` (bool), `derived` (bool), `reviewed` (fingerprint), `created`,
`modified`.
`Priority: Must | Verification: Inspection | Traces: UR-0011 | Status: Approved`

**SR-0023 — Schema validation.**
The Tool shall validate every item against the project schema (types,
required fields, enum membership, ID regex) and report violations with file,
field, and reason.
`Priority: Must | Verification: Test | Traces: UR-0016 | Status: Approved`

**SR-0024 — Status vocabulary and transitions.**
The Tool shall provide a default status set
(`draft, approved, implemented, verified, deleted, rejected`) that projects
can replace, and should support declaring allowed transitions which
validation enforces.
`Priority: Must / Should (transitions) | Verification: Test | Traces: UR-0011 | Status: Approved`

## 4. Links and traceability

**SR-0030 — Typed, directed links.**
The Tool shall support links from item to item carrying a project-defined
type/role (default set: `refines` parent, `verifies`, `satisfies`,
`implements`, `relates`), stored on the source item and indexed in both
directions.
`Priority: Must | Verification: Test | Traces: UR-0004 | Status: Approved`

**SR-0031 — External references.**
The Tool shall support links to external targets: URLs and repository file
paths (optionally with line ranges).
`Priority: Must | Verification: Test | Traces: UR-0004 | Status: Approved`

**SR-0032 — Link integrity.**
Validation shall flag links to unknown UIDs, links to deleted items, and
(configurably) circular `refines` chains.
`Priority: Must | Verification: Test | Traces: UR-0004, UR-0016 | Status: Approved`

**SR-0033 — Content fingerprints.**
The Tool shall compute a SHA-256 fingerprint of each item's normative
content (fields listed in doc 06 §5; excludes ordering, comments, and
non-normative metadata).
`Priority: Must | Verification: Test | Traces: UR-0005, UR-0006 | Status: Approved`

**SR-0034 — Suspect links.**
Each link shall store the target's fingerprint at the time the link was last
confirmed; when the stored and current fingerprints differ, the Tool shall
report the link as suspect, and shall provide a command to re-confirm
(re-stamp) links after review.
`Priority: Must | Verification: Test | Traces: UR-0005 | Status: Approved`

**SR-0035 — Impact analysis.**
Given a UID, the Tool shall report the transitive set of items reachable via
incoming links (what depends on this), grouped by link type and depth.
`Priority: Must | Verification: Test | Traces: UR-0012 | Status: Approved`

**SR-0036 — Baselines.**
The Tool shall create named baselines that pin the exact state of all items
(via VCS revision + a baseline manifest containing every UID and
fingerprint), list existing baselines, and check out or read any baseline.
`Priority: Must | Verification: Test | Traces: UR-0013 | Status: Approved`

**SR-0037 — Diff between versions.**
The Tool shall compare two baselines, revisions, or working state and report
per item: added, deleted, modified (with changed fields), and links
added/removed — as human-readable text/HTML and machine-readable JSON.
`Priority: Must | Verification: Test | Traces: UR-0003 | Status: Approved`

**SR-0038 — Review workflow states.**
The Tool shall record a per-item `reviewed` fingerprint set by an explicit
review command, and validation shall list items whose current fingerprint
differs from their reviewed fingerprint.
`Priority: Must | Verification: Test | Traces: UR-0006 | Status: Approved`

## 5. Validation, quality, and CI

**SR-0040 — Single check command.**
The Tool shall provide a `check` command running all validations (schema,
links, suspect, review, coverage rules) with a non-zero exit code on error,
suitable for CI.
`Priority: Must | Verification: Test | Traces: UR-0016 | Status: Approved`

**SR-0041 — Severity configuration.**
Projects shall be able to set each validation rule to `error`, `warning`, or
`off`, and the Tool shall support promoting all warnings to errors for CI.
`Priority: Must | Verification: Test | Traces: UR-0016 | Status: Approved`

**SR-0042 — Coverage rules.**
Projects shall be able to declare coverage rules of the form "every item of
type/document X with status in S must have ≥1 outgoing/incoming link of type
T to document Y", which `check` enforces.
`Priority: Must | Verification: Test | Traces: UR-0012 | Status: Approved`

**SR-0043 — Requirement quality lint.**
The Tool should provide optional lint rules for statement quality: missing
"shall/should/may", multiple "shall" in one statement, vague terms from a
configurable word list ("fast", "user-friendly", "etc."), empty rationale on
`Must` items, and EARS-pattern templates for new items.
`Priority: Should | Verification: Test | Traces: UR-0017 | Status: Approved`

**SR-0044 — Machine-readable check output.**
`check` shall optionally emit findings as JSON (rule id, severity, uid, file,
message) for tooling and CI annotations.
`Priority: Must | Verification: Test | Traces: UR-0016 | Status: Approved`

## 6. Search, filter, and query

**SR-0045 — Filter expression language.**
The Tool shall provide one boolean filter language over attributes, tags,
text, status, type, document, and link predicates (e.g.
`type == "req" and status in ["draft"] and links_to("TST")`), usable
identically in search, table generation, exports, and coverage rules.
`Priority: Must | Verification: Test | Traces: UR-0010 | Status: Approved`

**SR-0046 — Full-text search.**
The Tool shall support case-insensitive substring and regular-expression
search across statements, titles, and rationale, returning UIDs and
locations.
`Priority: Must | Verification: Test | Traces: UR-0010 | Status: Approved`

## 7. Publishing and reporting

**SR-0050 — HTML publishing.**
The Tool shall publish the project as a static HTML site with a document
view (ordered, numbered-for-print sections), per-item anchors by UID,
cross-document hyperlinks, and an index.
`Priority: Must | Verification: Demonstration | Traces: UR-0008 | Status: Approved`

**SR-0051 — Traceability views.**
Publishing shall include generated traceability views: a table view
(configurable columns), a traceability matrix between two chosen
documents/link types, and a link-graph export (DOT/Graphviz).
`Priority: Must | Verification: Test | Traces: UR-0004, UR-0012 | Status: Approved`

**SR-0052 — Coverage report.**
The Tool shall generate a coverage report per coverage rule (SR-0042) with
counts, percentages, and the list of uncovered UIDs.
`Priority: Must | Verification: Test | Traces: UR-0012 | Status: Approved`

**SR-0053 — PDF output.**
The Tool should produce PDF from the HTML document view.
`Priority: Should | Verification: Demonstration | Traces: UR-0008 | Status: Approved`

## 8. Import and export

**SR-0054 — CSV/Excel round-trip.**
The Tool shall export any document or filter result to CSV and XLSX with a
chosen column set, and shall import CSV/XLSX creating or updating items
(matching on UID; assigning fresh UIDs to rows without one).
`Priority: Must | Verification: Test | Traces: UR-0009, UR-0018 | Status: Approved`

**SR-0055 — Canonical JSON export.**
The Tool shall export the entire project (items, schema, links, baselines)
as a single documented JSON structure for third-party tooling.
`Priority: Must | Verification: Test | Traces: UR-0015 | Status: Approved`

**SR-0056 — ReqIF exchange.**
The Tool shall export documents to ReqIF (.reqif/.reqifz) preserving
hierarchy, attributes, and links, and should import ReqIF including
iterative re-import that preserves previously assigned UIDs via stored
foreign IDs.
`Priority: Should | Verification: Test | Traces: UR-0009 | Status: Approved`

**SR-0057 — Markdown export.**
The Tool shall export each document as standalone Markdown (for wikis and
code review).
`Priority: Should | Verification: Test | Traces: UR-0008 | Status: Approved`

## 9. Interfaces

**SR-0060 — CLI.**
The Tool shall expose all capabilities through a scriptable CLI (command
sketch in doc 07 §6) with stable exit codes: 0 ok, 1 validation errors,
2 usage/internal error.
`Priority: Must | Verification: Test | Traces: UR-0016 | Status: Approved`

**SR-0061 — Library API and self-hosting.**
The Tool shall expose its core as a documented library API (load project,
query, mutate, validate, publish), and the project shall manage its own
requirements with itself.
`Priority: Must | Verification: Inspection | Traces: UR-0015 | Status: Approved`

**SR-0062 — Source-code traceability scan.**
The Tool could scan configured source trees for UID markers in comments
(e.g. `# impl: REQ-0031`) and treat them as `implements` links for coverage.
`Priority: Could | Verification: Test | Traces: UR-0019 | Status: Draft`

**SR-0063 — Local web viewer.**
The Tool could serve the published site with live reload for authoring;
editing via web UI is out of scope.
`Priority: Could | Verification: Demonstration | Traces: UR-0020 | Status: Draft`

## 10. Configuration and extensibility

**SR-0070 — Project configuration file.**
The Tool shall read project settings (schema, link types, rules, publishing
options) from a single versioned TOML file at the project root.
`Priority: Must | Verification: Test | Traces: UR-0011 | Status: Approved`

**SR-0071 — Plugin points.**
The Tool should define plugin interfaces for exporters, importers, and
validation rules, discoverable without modifying core code.
`Priority: Should | Verification: Test | Traces: UR-0015 | Status: Approved`

**SR-0072 — Deterministic file writes.**
All Tool writes shall be deterministic and minimal-diff (stable key order,
stable quoting, trailing newline, no timestamp churn) so that Git diffs show
only real changes.
`Priority: Must | Verification: Test | Traces: UR-0007, UR-0014 | Status: Approved`
