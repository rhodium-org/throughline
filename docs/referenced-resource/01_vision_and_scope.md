# 01 · Vision and Scope

## 1. Problem statement

Teams that maintain requirements documents across many product versions hit
the same failure mode: requirements are identified by their **position** in
the document (1, 2, 3… or 3.2.1). Every insertion or deletion shifts the
numbering of everything below it. Cross-references, test links, review notes,
and people's memory of "requirement 14" all silently break. Renumbering churn
makes diffs unreadable, destroys traceability, and makes version-to-version
comparison of the requirement set nearly impossible.

Commercial tools solve this but are closed, expensive, and lock data into
proprietary stores. Existing open-source tools (Doorstop, StrictDoc,
Sphinx-Needs) each solve parts of the problem with different trade-offs.

## 2. Vision

A completely open-source requirements management tool where:

> **A requirement is a permanently identified, individually versioned object.
> Documents are merely views over those objects.**

Adding, removing, and reordering requirements between versions is a
non-event: no identifier ever changes, deletions leave auditable tombstones,
and any two versions of the requirement set can be diffed meaningfully.

## 3. Goals

- **G1 — Stable identity.** Immutable, never-reused requirement UIDs fully
  decoupled from register position. (→ UR-0001, UR-0002)
- **G2 — Plain text, Git native.** All data in human-readable text files that
  merge, branch, diff, and review like source code. (→ UR-0007)
- **G3 — Traceability as a first-class feature.** Typed links, suspect-link
  detection, coverage and impact analysis. (→ UR-0004, UR-0005, UR-0012)
- **G4 — Change management.** Reviews, fingerprints, baselines, and
  version-to-version diffs. (→ UR-0003, UR-0006, UR-0013)
- **G5 — Publishable.** Requirements rendered by reference into portable
  Markdown for stakeholders who don't use the tool (external tools convert it to
  HTML/PDF), with a documented JSON dump as the interchange surface. The tool does
  not itself generate presentation or RM-vendor formats. (→ UR-0008, NG-0005)
- **G6 — No lock-in.** Documented, versioned, open file format; OSI-approved
  license; offline operation. (→ UR-0015)
- **G7 — Automatable.** CLI + library API designed for CI gates. (→ UR-0016)
- **G8 — Approachable.** Usable by a single engineer in minutes; scales to a
  regulated multi-register project.

## 4. Non-goals (this scope)

Informal scope boundaries:

- Real-time multi-user editing (Git workflow is the collaboration model; a
  web editor is out of scope).
- Test *execution* management (we link to tests; we don't run them).
- Project management features (scheduling, sprints, workload).
- A hosted SaaS offering.
- WYSIWYG Word-style editing of arbitrary documents.

Recorded non-goals are first-class items in the graph (SR-0097), so a reviewer
or agent sees the boundary — and its rationale — rather than inferring it from
absence. The entries below are **generated from the graph** by `tl docs`:

<!-- tl:item NG-0001 -->
**NG-0001 — Not a document authoring or editing system** — `non_goal`, status `ratified`

> throughline shall not become a surface for authoring, editing, or storing narrative document content. It is a validator and an injector: item content lives in the graph and is rendered into documents by reference, never edited through the tool. Interactive editing, WYSIWYG, and web-based document management are out of scope.

*Rationale:* The comparable tools that started as validators and grew editing surfaces became document-management systems and lost the git-native, reference-not-copy property that keeps content from drifting. Recording this as a first-class non-goal is the object a reviewer points at to reject that category of proposed scope.

**origin**: human · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:32a0fcd84dcba445e7785aa380208009029d3070ff75f2e20611e1226f5371b3 · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item NG-0002 -->
**NG-0002 — No separate documentation license** — `non_goal`, status `ratified`

> throughline shall not maintain a separate or dual license for its documentation. All artifacts in the repository — code, the specification under docs/referenced-resource/, and the guides — ship under the single repository license, Apache-2.0. The project deliberately does not adopt a distinct documentation license (for example CC-BY-4.0).

*Rationale:* An earlier draft suggested CC-BY-4.0 for the specification while the code is Apache-2.0, which contradicted the single LICENSE/NOTICE the repository already declares and forced readers to reason about which license applies to which file. Recording a single-license stance as an explicit non-goal removes that ambiguity and gives the license-alignment change a ratified intent to trace to.

**origin**: human · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:a150812336105b51774908eb804f226b805e05c8fd05bd971a0608ad774ed4ae · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item NG-0003 -->
**NG-0003 — Item content is not sanitized against prompt injection** — `non_goal`, status `ratified`

> throughline stores and renders human- and machine-authored natural-language content — titles, text, rationale, and attribute values. It shall not attempt to detect, strip, or neutralize adversarial instructions embedded in that content that target a downstream AI agent reading the graph, the tl context brief, an injected document, or the YAML files directly. Stored content is untrusted data; the responsibility to sandbox, delimit, or otherwise defend against prompt injection rests with the consuming agent or integration. throughline's guarantee is structural and provenance-level (the graph is well-formed, links resolve, and who authored/ratified what is honest), not semantic (that the words are safe for an agent to act on).

*Rationale:* Trying to filter natural language for "malicious instructions" is both undecidable and a false promise that would invite exactly the trust it cannot earn — the same reason NG-0001 keeps the tool out of content authoring. Naming the boundary explicitly tells integrators where the tool's guarantees stop, so they wrap graph content as untrusted data rather than assuming throughline made it safe. Structural safety is covered separately and positively by NFR-0022.

**origin**: human · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:21399c25134bc77c4f605c299520e5bf422064e964a4f541badcd100e1005e11 · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item NG-0004 -->
**NG-0004 — No central identity server** — `non_goal`, status `ratified`

> throughline does not provide, and does not require, an online counter or coordinating service to allocate UIDs. Allocation atomicity derives solely from compare-and-swap on the trunk ref, keeping identity allocation viable for fully offline and zero-backend (e.g. in-browser) clients.

**origin**: hybrid · **ratified_by**: henry · **ratified_fingerprint**: sha256:4aca0877318b77f7a4563ecd4e84bb8d9efff5c1a4691247947bf4ddc0bcc2d0 · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item NG-0005 -->
**NG-0005 — No presentation or exchange format generation** — `non_goal`, status `ratified`

> throughline shall not generate presentation or exchange formats, and shall not import them. It does not produce HTML sites, PDF, CSV/XLSX, or ReqIF, and it does not ingest a foreign requirements list (a spreadsheet or a CSV export from another tool). The core stays a pure text engine — item content lives in the graph and is rendered into Markdown documents by reference (SR-0094); converting that Markdown to navigable HTML or PDF is a wrapper's job, delegated to external tools such as pandoc or mdBook. A single documented JSON dump of the whole project (SR-0055) is the sanctioned interchange surface for third-party tooling; round-tripping through presentation or RM-vendor formats is not.

*Rationale:* Every tool that started as a validator and grew a renderer for stakeholder formats acquired a rendering engine to maintain and drifted from the git-native, reference-not-copy discipline that keeps content from going stale — the same failure NG-0001 guards against, one layer out. HTML/PDF/CSV/ReqIF generation and foreign-list import were carried as approved-but-unratified scope that contradicted the ratified architecture (SR-0094 injects Markdown; SR-0089 rejected even a whole-document Markdown generator so the core could stay pure). Recording the boundary as a first-class non-goal is the object a reviewer points at to reject that scope, and the ratified intent the withdrawals trace to.

**origin**: hybrid · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:b561f9b71646b36c9311b485fb58ba5dcbc9544006fcae43ecb21f16e92760f6 · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item NG-0006 -->
**NG-0006 — No server or long-running process in the core** — `non_goal`, status `ratified`

> throughline shall not ship a server, a daemon, or any long-running process that exposes a network endpoint. The tool is a command-line program and a Python library — it runs against the files, does its work, and exits. It shall not serve a preview or a published site, expose an HTTP or socket interface, or hold state between invocations. Where a browser-based experience is wanted — viewing, authoring, or administering a graph — it belongs in a separate product built on the core, for example throughline-web, which composes the library at a pinned version rather than growing a runtime inside it.

*Rationale:* A local preview server with live reload was carried as deferred scope (SR-0063) from before this boundary was drawn. A server is never a small addition — it brings a port, a process lifecycle to supervise, an asset pipeline, and a second route to the graph that must then be kept behaving identically to the CLI forever. It also pulls the core one layer further toward what NG-0001 and NG-0005 already refuse, a web document-management surface and HTML site generation. The estate already answers browser-shaped needs with separate products that compose the library — throughline-editor, throughline-ratify, throughline-console — so a runtime can be deployed, versioned and secured on its own terms while the core keeps no network surface to defend. Recording the boundary as a first-class non-goal is the object a reviewer points at to reject the next proposal to add a serve command, and the ratified intent that SR-0063's withdrawal traces to.

**origin**: ai · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:c01a6cdc13f9ea74b540fd5e2da84d90a30330277c7f99a3d5bfbdf86f2a6277 · **ratified_backfilled**: True
<!-- tl:end -->

## 5. Target users

- **The requirements author** — writes and restructures specs frequently;
  needs insert/delete without renumbering pain.
- **The systems/safety engineer** — needs traceability, coverage, baselines,
  suspect links, audit-friendly history (automotive/aero/medical style).
- **The open-source maintainer** — needs requirements in the repo, reviewed
  through pull requests, validated in CI.
- **The toolsmith** — scripts against the library API and file format.

## 6. Guiding principles

1. **The file format is the product.** The format is documented first
   (doc 06); the tool is one implementation of it.
2. **One requirement, one file.** Minimizes merge conflicts and makes Git
   history per-requirement. (Decision record in doc 07 §3.)
3. **Never renumber, never reuse.** Deletion = status change + tombstone.
4. **Validate loudly, fail in CI.** Broken links, unreviewed changes, and
   coverage gaps are machine-detectable.
5. **Views are generated.** Ordering, numbering-for-print, tables, matrices
   are all derived outputs, never stored identity.
6. **Boring technology, long-term maintainability.** (A principle StrictDoc's
   own open tool-specification also emphasizes.)

## 7. Licensing and governance

- **Code license:** Apache-2.0 (recommended; MIT acceptable). Rationale:
  permissive licensing maximizes adoption incl. commercial users;
  Apache-2.0 adds an explicit patent grant. (NFR-0001)
- **Spec/doc license:** Apache-2.0 — the whole repository ships under a single
  license; there is no separate documentation license (NG-0002).
- **Contributions:** DCO sign-off; maintainer review via pull requests.
- **Versioning:** Semantic Versioning for the tool; independent version
  number for the file-format schema (NFR-0010, NFR-0011).
- **No CLA that assigns copyright** — keep the project forkable.

## 8. Success criteria

- A user can add and delete requirements across 50 document revisions with
  zero identifier churn (demonstrable via the tombstone + gap model).
- `check` runs in CI and fails on broken/suspect links or schema violations.
- Documents render deterministically: item content injected into Markdown by
  reference, and the whole project dumps to a documented JSON structure
  losslessly (NG-0005 keeps HTML/PDF/CSV/ReqIF generation out of the core).
- The project's own requirements (this set) are self-hosted in the tool.
