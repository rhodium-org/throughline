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
  decoupled from document position. (→ UR-0001, UR-0002)
- **G2 — Plain text, Git native.** All data in human-readable text files that
  merge, branch, diff, and review like source code. (→ UR-0007)
- **G3 — Traceability as a first-class feature.** Typed links, suspect-link
  detection, coverage and impact analysis. (→ UR-0004, UR-0005, UR-0012)
- **G4 — Change management.** Reviews, fingerprints, baselines, and
  version-to-version diffs. (→ UR-0003, UR-0006, UR-0013)
- **G5 — Publishable.** Stakeholder-quality HTML/PDF output and standard
  exchange formats (ReqIF, CSV/Excel, JSON). (→ UR-0008, UR-0009)
- **G6 — No lock-in.** Documented, versioned, open file format; OSI-approved
  license; offline operation. (→ UR-0015)
- **G7 — Automatable.** CLI + library API designed for CI gates. (→ UR-0016)
- **G8 — Approachable.** Usable by a single engineer in minutes; scales to a
  regulated multi-document project.

## 4. Non-goals (this scope)

- Real-time multi-user editing (Git workflow is the collaboration model; a
  web editor is out of scope).
- Test *execution* management (we link to tests; we don't run them).
- Project management features (scheduling, sprints, workload).
- A hosted SaaS offering.
- WYSIWYG Word-style editing of arbitrary documents.

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
- Round-trip: publish HTML, export ReqIF and CSV, re-import CSV losslessly
  for core fields.
- The project's own requirements (this set) are self-hosted in the tool.
