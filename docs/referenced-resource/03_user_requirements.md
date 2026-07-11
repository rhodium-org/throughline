# 03 · User Requirements (UR)

The people and problems these come from are described in doc 01 §5.
Format: statement, optional rationale, metadata line. IDs are permanent.

---

**UR-0001 — Stable requirement identity.**
Users shall be able to refer to any requirement by an identifier that never
changes for the life of the project, regardless of edits to any document.
*Rationale:* This is the root pain point: positional numbering breaks every
reference on insert/delete.
`Priority: Must | Verification: Demonstration | Status: Approved`

**UR-0002 — Frictionless add/remove.**
Users shall be able to add and remove requirements anywhere in a document
without affecting the identifiers, links, or history of other requirements.
`Priority: Must | Verification: Demonstration | Status: Approved`

**UR-0003 — Version-to-version comparison.**
Users shall be able to see exactly which requirements were added, removed,
or modified between any two versions of the requirement set.
`Priority: Must | Verification: Test | Status: Approved`

**UR-0004 — Traceability.**
Users shall be able to link requirements to parent requirements, tests,
design artifacts, and external references, and navigate those links in both
directions.
`Priority: Must | Verification: Test | Status: Approved`

**UR-0005 — Stale-link awareness.**
When a requirement changes, users shall be alerted that items linked to it
may need re-examination (suspect links).
`Priority: Must | Verification: Test | Status: Approved`

**UR-0006 — Review state.**
Users shall be able to mark requirements as reviewed and detect any
requirement that changed after its last review.
`Priority: Must | Verification: Test | Status: Approved`

**UR-0007 — Plain text under version control.**
Users shall be able to store all requirement data as human-readable text
files in Git, using branches, merges, and pull requests as the change
workflow.
*Rationale:* Reuses the team's existing review tooling and permissions;
avoids a database server.
`Priority: Must | Verification: Inspection | Status: Approved`

**UR-0008 — Stakeholder-quality publishing.**
Users shall be able to publish requirement documents as navigable HTML and
as PDF suitable for customers, auditors, and reviewers who don't use the
tool.
`Priority: Must (HTML) / Should (PDF) | Verification: Demonstration | Status: Approved`

**UR-0009 — Exchange with other tools.**
Users shall be able to exchange requirements with other RM tools via
standard formats, at minimum CSV/Excel and ReqIF.
`Priority: Must (CSV) / Should (ReqIF) | Verification: Test | Status: Approved`

**UR-0010 — Search and filter.**
Users shall be able to find requirements by any combination of attribute
values, text search, tags, and link conditions.
`Priority: Must | Verification: Test | Status: Approved`

**UR-0011 — Project-specific data model.**
Users shall be able to define their own item types, attributes (with types
and allowed values), and link types per project.
`Priority: Must | Verification: Test | Status: Approved`

**UR-0012 — Coverage and impact analysis.**
Users shall be able to answer "is every requirement traced/verified?" and
"what is affected if this requirement changes?" without manual inspection.
`Priority: Must | Verification: Demonstration | Status: Approved`

**UR-0013 — Baselines.**
Users shall be able to freeze a named snapshot of the requirement set
(e.g. "SRS v2.0 as reviewed") and later compare against it or reproduce it
exactly.
`Priority: Must | Verification: Test | Status: Approved`

**UR-0014 — Team collaboration without a server.**
Multiple users shall be able to work on the same requirement set
concurrently via Git, with merge conflicts rare and resolvable at the level
of individual requirements.
`Priority: Must | Verification: Analysis | Status: Approved`

**UR-0015 — No lock-in.**
Users shall be able to read, migrate, and process their data with ordinary
tools (text editors, grep, scripts) using a documented open format, offline,
under an OSI-approved license.
`Priority: Must | Verification: Inspection | Status: Approved`

**UR-0016 — CI automation.**
Users shall be able to run the tool's validation in continuous integration
so that broken traceability or invalid data fails the build.
`Priority: Must | Verification: Test | Status: Approved`

**UR-0017 — Requirement quality support.**
Users should be offered assistance writing well-formed requirements
(templates such as EARS patterns, lint warnings for ambiguity markers,
missing rationale, compound statements).
`Priority: Should | Verification: Test | Status: Approved`

**UR-0018 — Migration path in.**
Users shall be able to import an existing requirements list (spreadsheet or
CSV export from another tool) and have stable IDs assigned or preserved.
`Priority: Must | Verification: Test | Status: Approved`

**UR-0019 — Requirements-to-code traceability.**
Users could link requirements to source code and test code locations and
report coverage of implementation.
`Priority: Could | Verification: Test | Status: Draft`

**UR-0020 — Comprehensible to newcomers.**
A new user shall be able to initialize a project, add three linked
requirements, validate, and publish HTML within 15 minutes using only the
quick-start guide.
`Priority: Should | Verification: Demonstration | Status: Approved`
