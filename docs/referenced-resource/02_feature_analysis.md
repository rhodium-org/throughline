# 02 · Feature Analysis of Existing Tools

Features below are distilled from the public documentation of each tool
(sources at the end of each section). Adoption decisions: **Adopt** (build it),
**Adapt** (build a modified form), **Exclude** (out of scope), **Reject** (with
reason).

## 1. Doorstop (LGPL-3.0, Python)

Requirements stored alongside code in version control; each item is a YAML
file in a directory; directories form documents; documents form a tree.

| Feature (from public docs) | Decision | Notes for our tool |
|---|---|---|
| One YAML file per item, one directory per document | **Adopt** | Best merge behavior in Git; our decision record in doc 07 §3 |
| UID = document prefix + number (e.g. `REQ001`), configurable digits/separator | **Adapt** | We mandate a separator (`REQ-0001`) and forbid reuse (SR-0003) |
| Item attributes: `active`, `derived`, `normative`, `level`, `links`, `ref` | **Adapt** | We keep the concepts; `level` becomes ordering-only metadata |
| Fingerprint (hash) of an item's normative content; `review`/`clear` commands mark items reviewed and stamp links | **Adopt** | Core of suspect-link detection; use SHA-256 (as the RTEMS project did when adopting Doorstop) |
| Suspect links: child stores stamp of parent; parent change ⇒ link suspect | **Adopt** | SR-0033/34 |
| Tree/link validation CLI with warnings promotable to errors (`--warn-all`, `--error-all`) | **Adopt** | CI gate, SR-0040..44 |
| Publish to HTML (with index + traceability), Markdown, text | **Adopt** | SR-0050 |
| Import/export CSV, TSV, XLSX | **Adopt** | SR-0054 |
| `doorstop reorder` to reorganize document structure | **Adapt** | Reordering must never touch UIDs |
| UID-reservation REST server for concurrent teams | **Adapt** | We solve allocation offline first (SR-0006); server is out of scope |
| Tkinter GUI | **Reject** | CLI + published HTML; web editor out of scope |

Sources: github.com/doorstop-dev/doorstop · doorstop.readthedocs.io ·
PyPI changelog (review/clear/reorder/server features) · RTEMS Software
Engineering manual §Tooling (docs.rtems.org — Doorstop evaluation, SHA-256
fingerprints, cyclic-dependency and ReqIF wishes).

## 2. StrictDoc (Apache-2.0, Python)

Technical documentation + requirements tool with its own text format (SDoc);
one `.sdoc` file per document; explicit, extensible grammar.

| Feature (from public docs) | Decision | Notes |
|---|---|---|
| Explicit document grammar with custom typed fields per element (e.g. PRIORITY, OWNER, ASIL) | **Adopt** | Our project-level attribute schema (SR-0020..23) |
| UID + separate auto-generated Machine ID (MID) per node | **Adapt** | We use one human UID + optional content fingerprint; MID concept folded into fingerprint/tombstone design |
| Bi-directional parent/child relations with **link roles** ("verifies", "implements", "satisfies") | **Adopt** | Typed links, SR-0030 |
| Multiple generated views: document, table, traceability, deep traceability | **Adopt** | HTML publisher views, SR-0050..52 |
| Requirements-to-source-code traceability (markers in source files) | **Exclude** | High value, but out of scope (SR-0062) |
| Web UI editing `.sdoc` files in place | **Exclude** | Out of scope |
| Exports: HTML, RST, PDF, Excel, JSON; ReqIF import/export | **Adopt** | SR-0053..55 |
| Diff/changelog between two versions of the documentation tree | **Adopt** | SR-0037 |
| Auto-generation of requirement UIDs (next free number) | **Adopt** | SR-0005 |
| Composable documents (include fragments) | **Exclude** | Reduces exchange portability (StrictDoc's own docs flag this) |
| Project config file (`strictdoc.toml`): feature flags, paths | **Adopt** | SR-0070 |
| Project statistics / progress KPIs | **Adapt** | Coverage report first (SR-0052) |
| Open "Requirements Tool Specification (L1)" — StrictDoc publishes generic requirements for any requirements tool | — | **Prior art**: cross-check our SR set against it |

Sources: github.com/strictdoc-project/strictdoc · strictdoc.readthedocs.io
(User Guide, Feature Map, FAQ, "Requirements Tool Specification (L1)").

## 3. Sphinx-Needs (MIT, Python/Sphinx extension)

Docs-as-code life-cycle objects ("needs") embedded in Sphinx documentation.

| Feature (from public docs) | Decision | Notes |
|---|---|---|
| Configurable need types (req, spec, impl, test, …) | **Adopt** | Item `type` from project schema (SR-0021) |
| Custom options/attributes per need, usable in filters and styling | **Adopt** | SR-0020 |
| Configurable extra link types with incoming/outgoing semantics | **Adopt** | SR-0030 |
| Powerful filter strings (boolean expressions over attributes, tags, links) applied uniformly across views | **Adopt** | Query language, SR-0045..46 |
| Generated views: needtable, needlist, needflow (graphs), needpie/needbar (charts), traceability matrices | **Adapt** | Tables + matrix + graph export (DOT); charts out of scope |
| `needs.json` builder: full machine-readable export; import of external needs across projects | **Adopt** | Canonical JSON export, SR-0055 |
| Validation/constraints: allowed statuses, ID regex, link constraints, schema checks | **Adopt** | SR-0041..43 |
| Dynamic functions computing field values | **Exclude** | Complexity vs. benefit; out of scope |
| Services importing external items (GitHub, Jira) as needs | **Exclude** | Plugin territory (SR-0071) |
| Performance: pre-indexed keys so `id == "…"` filters are O(1) | **Adopt** | Informs NFR-0006 and index design (doc 07 §5) |

Sources: sphinx-needs.readthedocs.io (filter, needtable, needflow, roles,
changelog) · sphinx-needs.com feature overview.

## 4. ReqView (proprietary, but openly documented JSON format)

Desktop RM tool; relevant here because its **file format and feature docs are
public** and it is explicitly designed for Git/SVN storage.

| Feature (from public docs) | Decision | Notes |
|---|---|---|
| Human-readable JSON project files intended for VCS storage; published JSON schemas; `validate` CLI command | **Adopt** (concept) | Open, schema-validated format is our core principle (doc 06) |
| Custom attributes with data types incl. enum, number, date, rich text | **Adopt** | SR-0020 |
| Typed, direction-aware link types configured per project (e.g. "satisfies" from low- to high-level docs) | **Adopt** | SR-0030 |
| Live Requirements Traceability Matrix as configurable table columns; traceability wizard | **Adapt** | Generated matrix views (SR-0051) |
| Filters to find **missing links** and **suspect flags** | **Adopt** | Coverage rules + suspect queries (SR-0035, SR-0052) |
| Change management: requirements history, deleted objects retained and excluded from export | **Adopt** | Tombstones (SR-0012) |
| Baselining via Git; branch/merge collaboration documented | **Adopt** | Baselines as tags (SR-0036) |
| Import: Word, Excel/CSV, ReqIF (iterative re-import preserving IDs) | **Adapt** | CSV/ReqIF; Word import out of scope (Pandoc) |
| Export: DOCX, XLSX, PDF, HTML, CSV, ReqIF; custom Handlebars templates | **Adapt** | HTML/CSV/ReqIF/JSON; template engine optional |
| Templates based on ISO/IEC/IEEE 29148 | **Adopt** | Ship starter register templates (SR-0014) |
| Linked projects (cross-project requirement reuse) | **Exclude** | Out of scope |

Sources: reqview.com/doc (Traceability Links, File Data Format, Export ReqIF,
Git collaboration) · reqview.com feature pages and blog.

## 5. Enterprise tools — common capability set (IBM DOORS, Jama Connect, Polarion)

Distilled from their public marketing/docs; these define what "serious" RM
means to regulated industries.

| Capability | Decision | Notes |
|---|---|---|
| Baselines: named frozen snapshots; baseline comparison | **Adopt** | Git tags + baseline manifest + diff (SR-0036..37) |
| Suspect-link / impact analysis on change | **Adopt** | SR-0033..35 |
| Formal reviews, approvals, e-signatures, audit trail | **Adapt** | PR-based review + reviewed-fingerprints; e-signature records out of scope |
| Configurable workflows (state machines per item type) | **Adapt** | Configurable status enum + allowed-transition validation (Should) |
| Variant/reuse management across product lines | **Exclude** | Out of scope; significant complexity |
| ReqIF exchange with suppliers/OEMs | **Adopt** | SR-0056 (export Must, import Should) |
| Dashboards, coverage metrics, progress KPIs | **Adapt** | Coverage report (SR-0052); dashboards out of scope |
| Fine-grained access control | **Reject** (tool level) | Delegated to Git hosting permissions; document the pattern |

## 6. Synthesis — the differentiating recipe

No existing open tool combines **all** of: (a) one-file-per-item Git-native
storage (Doorstop), (b) an explicit typed schema with link roles (StrictDoc),
(c) a uniform filter language + machine-readable export (Sphinx-Needs),
(d) an openly specified, schema-validated format designed for outside
implementations (ReqView's format philosophy), and (e) enterprise-grade
baselines/suspect-link/coverage discipline — under a permissive license.
That combination is this project's scope; the requirement documents that
follow specify it.
