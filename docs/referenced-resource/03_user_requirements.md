# 03 · User Requirements (UR)

The people and problems these come from are described in doc 01 §5.

Each entry below is **generated from the graph** by `tl docs` — the statement,
rationale, and metadata are re-rendered from the user-requirement items, so this
document cannot drift from the requirement set it describes. IDs are permanent.
Regenerate with `tl docs` and gate it in CI with `tl docs --check` (SR-0094).

---

<!-- tl:item UR-0001 -->
**UR-0001 — Stable requirement identity** — `user_requirement`, status `approved`

> Users shall be able to refer to any requirement by an identifier that never changes for the life of the project, regardless of edits to any document.

*Rationale:* The root pain point: positional numbering breaks every reference on insert/delete.

**priority**: must · **verification**: demonstration
<!-- tl:end -->

<!-- tl:item UR-0002 -->
**UR-0002 — Frictionless add/remove** — `user_requirement`, status `approved`

> Users shall be able to add and remove requirements anywhere in a document without affecting the identifiers, links, or history of other requirements.

**priority**: must · **verification**: demonstration
<!-- tl:end -->

<!-- tl:item UR-0003 -->
**UR-0003 — Version-to-version comparison** — `user_requirement`, status `approved`

> Users shall be able to see exactly which requirements were added, removed, or modified between any two versions of the requirement set.

**priority**: must · **verification**: test
<!-- tl:end -->

<!-- tl:item UR-0004 -->
**UR-0004 — Traceability** — `user_requirement`, status `approved`

> Users shall be able to link requirements to parent requirements, tests, design artifacts, and external references, and navigate those links in both directions.

**priority**: must · **verification**: test
<!-- tl:end -->

<!-- tl:item UR-0005 -->
**UR-0005 — Stale-link awareness** — `user_requirement`, status `approved`

> When a requirement changes, users shall be alerted that items linked to it may need re-examination (suspect links).

**priority**: must · **verification**: test
<!-- tl:end -->

<!-- tl:item UR-0006 -->
**UR-0006 — Review state** — `user_requirement`, status `approved`

> Users shall be able to mark requirements as reviewed and detect any requirement that changed after its last review.

**priority**: must · **verification**: test
<!-- tl:end -->

<!-- tl:item UR-0007 -->
**UR-0007 — Plain text under version control** — `user_requirement`, status `approved`

> Users shall be able to store all requirement data as human-readable text files in Git, using branches, merges, and pull requests as the change workflow.

*Rationale:* Reuses the team's existing review tooling and permissions; avoids a database server.

**priority**: must · **verification**: inspection
<!-- tl:end -->

<!-- tl:item UR-0008 -->
**UR-0008 — Stakeholder-quality publishing** — `user_requirement`, status `approved`

> Users shall be able to publish requirement documents as navigable HTML and as PDF suitable for customers, auditors, and reviewers who don't use the tool.

**priority**: must · **verification**: demonstration
<!-- tl:end -->

<!-- tl:item UR-0009 -->
**UR-0009 — Exchange with other tools** — `user_requirement`, status `approved`

> Users shall be able to exchange requirements with other RM tools via standard formats, at minimum CSV/Excel and ReqIF.

**priority**: must · **verification**: test
<!-- tl:end -->

<!-- tl:item UR-0010 -->
**UR-0010 — Search and filter** — `user_requirement`, status `approved`

> Users shall be able to find requirements by any combination of attribute values, text search, tags, and link conditions.

**priority**: must · **verification**: test
<!-- tl:end -->

<!-- tl:item UR-0011 -->
**UR-0011 — Project-specific data model** — `user_requirement`, status `approved`

> Users shall be able to define their own item types, attributes (with types and allowed values), and link types per project.

**priority**: must · **verification**: test
<!-- tl:end -->

<!-- tl:item UR-0012 -->
**UR-0012 — Coverage and impact analysis** — `user_requirement`, status `approved`

> Users shall be able to answer 'is every requirement traced/verified?' and 'what is affected if this requirement changes?' without manual inspection.

**priority**: must · **verification**: demonstration
<!-- tl:end -->

<!-- tl:item UR-0013 -->
**UR-0013 — Baselines** — `user_requirement`, status `approved`

> Users shall be able to freeze a named snapshot of the requirement set and later compare against it or reproduce it exactly.

**priority**: must · **verification**: test
<!-- tl:end -->

<!-- tl:item UR-0014 -->
**UR-0014 — Team collaboration without a server** — `user_requirement`, status `approved`

> Multiple users shall be able to work on the same requirement set concurrently via Git, with merge conflicts rare and resolvable at the level of individual requirements.

**priority**: must · **verification**: analysis
<!-- tl:end -->

<!-- tl:item UR-0015 -->
**UR-0015 — No lock-in** — `user_requirement`, status `approved`

> Users shall be able to read, migrate, and process their data with ordinary tools using a documented open format, offline, under an OSI-approved license.

**priority**: must · **verification**: inspection
<!-- tl:end -->

<!-- tl:item UR-0016 -->
**UR-0016 — CI automation** — `user_requirement`, status `approved`

> Users shall be able to run the tool's validation in continuous integration so that broken traceability or invalid data fails the build.

**priority**: must · **verification**: test
<!-- tl:end -->

<!-- tl:item UR-0017 -->
**UR-0017 — Requirement quality support** — `user_requirement`, status `approved`

> Users should be offered assistance writing well-formed requirements (EARS templates, lint warnings for ambiguity, missing rationale, compound statements).

**priority**: should · **verification**: test
<!-- tl:end -->

<!-- tl:item UR-0018 -->
**UR-0018 — Migration path in** — `user_requirement`, status `approved`

> Users shall be able to import an existing requirements list (spreadsheet or CSV export from another tool) and have stable IDs assigned or preserved.

**priority**: must · **verification**: test
<!-- tl:end -->

<!-- tl:item UR-0019 -->
**UR-0019 — Requirements-to-code traceability** — `user_requirement`, status `deferred`

> Users could link requirements to source code and test code locations and report coverage of implementation.

**priority**: could · **verification**: test
<!-- tl:end -->

<!-- tl:item UR-0020 -->
**UR-0020 — Comprehensible to newcomers** — `user_requirement`, status `approved`

> A new user shall be able to initialize a project, add three linked requirements, validate, and publish HTML within 15 minutes using only the quick-start guide.

**priority**: should · **verification**: demonstration
<!-- tl:end -->

<!-- tl:item UR-0021 -->
**UR-0021 — Verifiable development environment** — `user_requirement`, status `approved`

> A contributor shall be able to run a single command that diagnoses their development environment — Python version, an importable/installed throughline, the test runner, and whether the local grounding gate is wired — and reports, per check, either pass or a specific remediation, exiting non-zero if the environment is not ready.

**origin**: human · **priority**: should · **verification**: demonstration
<!-- tl:end -->

<!-- tl:item UR-0022 -->
**UR-0022 — Comprehensible to AI agents** — `user_requirement`, status `approved`

> An AI coding agent joining a project shall be able to obtain, from the tool itself, everything it needs to work correctly under this project's rules — the Intent-Driven Development contract, the project's own item types and their attributes, the link and status vocabularies and their constraints, and the on-disk YAML format — without a human hand-authoring an agent guide or the agent reverse-engineering the configuration. Because the configuration is the source of truth and may change, this material shall be derived from the live project rather than restated by hand, so it can never drift from the rules the validator actually enforces.

**priority**: should · **verification**: demonstration
<!-- tl:end -->

<!-- tl:item UR-0023 -->
**UR-0023 — Machine-authored items require human ratification** — `user_requirement`, status `ratified`

> Items whose origin is machine-generated rather than human-authored shall enter the graph as proposed and shall not count as accepted until a human explicitly ratifies them, so accountability for every requirement rests with a person.

**origin**: human · **priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item UR-0024 -->
**UR-0024 — Drift-free narrative documents** — `user_requirement`, status `ratified`

> Users shall be able to author requirement documents that interleave their own narrative with item content drawn from the graph, such that item content is regenerated in place and cannot silently drift from the graph.

*Rationale:* A requirements document is includes prose the human owns; generating the whole document from the graph guarantees a parallel hand-maintained artifact and therefore guarantees drift. The seam must be a reference, not a copy.

**origin**: ai · **priority**: must · **verification**: demonstration · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->

<!-- tl:item UR-0025 -->
**UR-0025 — Explicit non-goals as first-class items** — `user_requirement`, status `ratified`

> Users shall be able to declare explicit non-goals (deliberately out-of-scope statements) as first-class, traceable items, so that excluded scope is visible to reviewers and agents rather than living only in prose.

*Rationale:* throughline can already say an item has no reason to exist (orphan) but has no way to say a thing is deliberately out of scope. For a tool whose thesis is scope discipline, recorded non-goals are the primary defence against scope creep.

**origin**: ai · **priority**: should · **verification**: demonstration · **ratified_by**: Henry Grech-Cini
<!-- tl:end -->
