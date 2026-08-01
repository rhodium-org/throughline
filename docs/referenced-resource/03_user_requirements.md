# 03 · User Requirements (UR)

The people and problems these come from are described in doc 01 §5.

Each entry below is **generated from the graph** by `tl docs` — the statement,
rationale, and metadata are re-rendered from the user-requirement items, so this
document cannot drift from the requirement set it describes. IDs are permanent.
Regenerate with `tl docs` and gate it in CI with `tl docs --check` (SR-0094).

---

<!-- tl:item UR-0001 -->
**UR-0001 — Stable requirement identity** — `user_requirement`, status `ratified`

> Users shall be able to refer to any requirement by an identifier that never changes for the life of the project, regardless of edits to any register.

*Rationale:* The root pain point: positional numbering breaks every reference on insert/delete.

*Derives from:* BN-0001

**priority**: must · **verification**: demonstration · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:b3eeba01ea0e28e2b95daf75f0f343dc81a4177aae4a6ca0207ae9ff8f19befc · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item UR-0002 -->
**UR-0002 — Frictionless add/remove** — `user_requirement`, status `ratified`

> Users shall be able to add and remove requirements anywhere in a register without affecting the identifiers, links, or history of other requirements.

*Derives from:* BN-0001

**priority**: must · **verification**: demonstration · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:9774c5b86f5b7f4db4d11fd532ea2552cd87d27c339615c88ecf72fc837e82b7 · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item UR-0003 -->
**UR-0003 — Version-to-version comparison** — `user_requirement`, status `ratified`

> Users shall be able to see exactly which requirements were added, removed, or modified between any two versions of the requirement set.

*Derives from:* BN-0004

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:7e5180cb8813d61f4d77490a595398d4aeb7f319e3cc9cf2884cde4aa6eb1847 · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item UR-0004 -->
**UR-0004 — Traceability** — `user_requirement`, status `ratified`

> Users shall be able to link requirements to parent requirements, tests, design artifacts, and external references, and navigate those links in both directions.

*Derives from:* BN-0003

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:8127d87c75bef2180ebf1a9a582f3ba89815c9b9c135b372230287f1a5a9f19d · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item UR-0005 -->
**UR-0005 — Stale-link awareness** — `user_requirement`, status `ratified`

> When a requirement changes, users shall be alerted that items linked to it may need re-examination (suspect links).

*Derives from:* BN-0003

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:622befb765b1d84b41de0eebdf636473bd88090331ff16f80904593514256666 · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item UR-0006 -->
**UR-0006 — Review state** — `user_requirement`, status `ratified`

> Users shall be able to mark requirements as reviewed and detect any requirement that changed after its last review.

*Derives from:* BN-0004

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:c563b661cdb2a94406e7bbb5c6cf7d909d8af2b6a30365133ee1464778059478 · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item UR-0007 -->
**UR-0007 — Plain text under version control** — `user_requirement`, status `ratified`

> Users shall be able to store all requirement data as human-readable text files in Git, using branches, merges, and pull requests as the change workflow.

*Rationale:* Reuses the team's existing review tooling and permissions; avoids a database server.

*Derives from:* BN-0002

**priority**: must · **verification**: inspection · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:a142dc26e095a614394ccaabb32235f72c3d8928efa386fdf10fc720f4eed001 · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item UR-0008 -->
**UR-0008 — Stakeholder-quality publishing** — `user_requirement`, status `ratified`

> Users shall be able to publish requirement documents as portable Markdown — rendered from the graph by reference — suitable for customers, auditors, and reviewers who don't use the tool, and convertible by external tools (pandoc, mdBook) to navigable HTML or PDF. The tool renders Markdown; it does not itself produce HTML or PDF (NG-0005).

*Derives from:* BN-0005

**priority**: must · **verification**: demonstration · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:33387e612660a23f123a1a1709f3d2dedf40d20065a2e6cac1dab30ea15b54cd · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item UR-0009 -->
**UR-0009 — Exchange with other tools** — `user_requirement`, status `rejected`

> Users shall be able to exchange requirements with other RM tools via standard formats, at minimum CSV/Excel and ReqIF.

*Rationale:* Rejected as out of scope under NG-0005. Exchange with RM vendors via their formats is a class of scope throughline declines; the open, documented JSON dump (SR-0055) plus the git-native YAML files are the interchange story. Tombstoned, never reused.

*Derives from:* BN-0005
*Relates:* NG-0005

**priority**: must · **verification**: test
<!-- tl:end -->

<!-- tl:item UR-0010 -->
**UR-0010 — Search and filter** — `user_requirement`, status `ratified`

> Users shall be able to find requirements by any combination of attribute values, text search, tags, and link conditions.

*Derives from:* BN-0008

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:a3eac3ac57b6a12fe478218c00ef06674f1654405607364ad9bf53434f6e6395 · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item UR-0011 -->
**UR-0011 — Project-specific data model** — `user_requirement`, status `ratified`

> Users shall be able to define their own item types, attributes (with types and allowed values), and link types per project.

*Derives from:* BN-0008

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:12a2bf79cfbb5778fde33bab6be4db7948813f1c834a5bd498c6275727c9d228 · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item UR-0012 -->
**UR-0012 — Coverage and impact analysis** — `user_requirement`, status `ratified`

> Users shall be able to answer 'is every requirement traced/verified?' and 'what is affected if this requirement changes?' without manual inspection.

*Derives from:* BN-0003

**priority**: must · **verification**: demonstration · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:a1d81cfa3672f6bf2fc99e519986c8144f68a9df445526ba2b6f8136832d6320 · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item UR-0013 -->
**UR-0013 — Baselines** — `user_requirement`, status `ratified`

> Users shall be able to freeze a named snapshot of the requirement set and later compare against it or reproduce it exactly.

*Derives from:* BN-0004

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:87f6eadcadd03f5734f0e53c6fcdf930cba7e135237f6fa7999b6ef31c68da03 · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item UR-0014 -->
**UR-0014 — Team collaboration without a server** — `user_requirement`, status `ratified`

> Multiple users shall be able to work on the same requirement set concurrently via Git, with merge conflicts rare and resolvable at the level of individual requirements.

*Derives from:* BN-0002

**priority**: must · **verification**: analysis · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:18e7df8d1dd5bd3f56b8add85faf141ea7a9cd6af84827efd6e56719a12385c6 · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item UR-0015 -->
**UR-0015 — No lock-in** — `user_requirement`, status `ratified`

> Users shall be able to read, migrate, and process their data with ordinary tools using a documented open format, offline, under an OSI-approved license.

*Derives from:* BN-0006

**priority**: must · **verification**: inspection · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:12e5411c13dd4a0d3af1e4ef5211bda7029deb57e1e4c1924bc35193b88a9033 · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item UR-0016 -->
**UR-0016 — CI automation** — `user_requirement`, status `ratified`

> Users shall be able to run the tool's validation in continuous integration so that broken traceability or invalid data fails the build.

*Derives from:* BN-0007

**priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:d6b8e3d3df4f70a813778c3c4c46b383ed1539ca61d524e53d5eb2dc15f675f8 · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item UR-0017 -->
**UR-0017 — Requirement quality support** — `user_requirement`, status `ratified`

> Users should be offered assistance writing well-formed requirements (EARS templates, lint warnings for ambiguity, missing rationale, compound statements).

*Derives from:* BN-0008

**priority**: should · **verification**: test · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:05fe66b71560b76d313c4c922073f7105c2f4ce1f12fb2d0e4cdda6aadbb3110 · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item UR-0018 -->
**UR-0018 — Migration path in** — `user_requirement`, status `rejected`

> Users shall be able to import an existing requirements list (spreadsheet or CSV export from another tool) and have stable IDs assigned or preserved.

*Rationale:* Rejected as out of scope under NG-0005. Ingesting a foreign requirements list is the import counterpart of the format generation the tool declines; a throughline graph is started natively (tl init/new), not migrated in from a spreadsheet. Tombstoned, never reused.

*Derives from:* BN-0006
*Relates:* NG-0005

**priority**: must · **verification**: test
<!-- tl:end -->

<!-- tl:item UR-0019 -->
**UR-0019 — Requirements-to-code traceability** — `user_requirement`, status `rejected`

> Users could link requirements to source code and test code locations and report coverage of implementation.

*Rationale:* Withdrawn as redundant. The linking half of this requirement is already delivered by SR-0031, which is ratified and priority must — an item may link to an external target, including a repository file path with an optional line range, so pointing a requirement at the source or test code that realises it needs nothing new. What remained was the second clause, reporting implementation coverage by scanning source trees for UID markers (SR-0062), which is a different capability wearing this requirement's name — it inverts the direction of reference, making the tool read arbitrary source trees and discover links rather than the graph declaring them. Carrying a could-priority requirement whose principal clause is already met invites a reader to conclude the need is unserved when it is not. Should coverage over source ever be wanted, it returns as its own item grounded under UR-0012, not by reviving this one.

*Derives from:* BN-0003
*Relates:* SR-0031

**priority**: could · **verification**: test
<!-- tl:end -->

<!-- tl:item UR-0020 -->
**UR-0020 — Comprehensible to newcomers** — `user_requirement`, status `ratified`

> A new user shall be able to initialize a project, add three linked requirements, validate, and render a Markdown document within 15 minutes using only the quick-start guide.

*Derives from:* BN-0008

**priority**: should · **verification**: demonstration · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:7b3b500e08392e0f4d829feaaa45c82138684a7d51ca0a1ce747defc3c76596e · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item UR-0021 -->
**UR-0021 — Verifiable development environment** — `user_requirement`, status `ratified`

> A contributor shall be able to run a single command that diagnoses their development environment — Python version, an importable/installed throughline, the test runner, whether the local grounding gate is wired, and whether each package of the toolchain they have checked out is the one actually being run rather than a published release standing in for it — and reports, per check, either pass or a specific remediation, exiting non-zero if the environment is not ready.

*Rationale:* The checks are the ones whose failure is silent. A missing interpreter or test runner announces itself the moment work starts, but an environment that runs a published package while the contributor edits its source announces nothing at all — the code imports, the tests pass, and the version string names the release it is not. A full day was lost to that here, comparing a cockpit against a validator that were different builds of the same toolchain. Checking out a sibling package is the signal that it is being worked on, so the diagnosis is drawn from what the contributor has on disk rather than from anything they must remember to declare.

*Derives from:* BN-0010

**origin**: human · **priority**: should · **verification**: demonstration · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:e7bacdcd4c0016f9a0ac2f7b66a9c7c229e8f53fa12a74237ada2ac011ae106c · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item UR-0022 -->
**UR-0022 — Comprehensible to AI agents** — `user_requirement`, status `ratified`

> An AI coding agent joining a project shall be able to obtain, from the tool itself, everything it needs to work correctly under this project's rules — the Intent-Driven Development contract, the project's own item types and their attributes, the link and status vocabularies and their constraints, and the on-disk YAML format — without a human hand-authoring an agent guide or the agent reverse-engineering the configuration. Because the configuration is the source of truth and may change, this material shall be derived from the live project rather than restated by hand, so it can never drift from the rules the validator actually enforces.

*Derives from:* BN-0008

**priority**: should · **verification**: demonstration · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:25aa29e88040289648e9527d78083b224ac7750d49e4a59b9be0004b61a93e5b · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item UR-0023 -->
**UR-0023 — Machine-authored items require human ratification** — `user_requirement`, status `ratified`

> Items whose origin is machine-generated rather than human-authored shall enter the graph as proposed and shall not count as accepted until a human explicitly ratifies them, so accountability for every requirement rests with a person.

*Derives from:* BN-0009

**origin**: human · **priority**: must · **verification**: test · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:b7702ba187402b838e970a22cdf45dbdc81a52d62e48a15871ce30c73cc111be · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item UR-0024 -->
**UR-0024 — Drift-free narrative documents** — `user_requirement`, status `ratified`

> Users shall be able to author requirement documents that interleave their own narrative with item content drawn from the graph, such that item content is regenerated in place and cannot silently drift from the graph.

*Rationale:* A requirements document is includes prose the human owns; generating the whole document from the graph guarantees a parallel hand-maintained artifact and therefore guarantees drift. The seam must be a reference, not a copy.

*Derives from:* BN-0005

**origin**: ai · **priority**: must · **verification**: demonstration · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:6bd289507d64c1f76a85e1abc80011907b103ec52bb6c637b099858b5dcac728 · **ratified_backfilled**: True
<!-- tl:end -->

<!-- tl:item UR-0025 -->
**UR-0025 — Explicit non-goals as first-class items** — `user_requirement`, status `ratified`

> Users shall be able to declare explicit non-goals (deliberately out-of-scope statements) as first-class, traceable items, so that excluded scope is visible to reviewers and agents rather than living only in prose.

*Rationale:* throughline can already say an item has no reason to exist (orphan) but has no way to say a thing is deliberately out of scope. For a tool whose thesis is scope discipline, recorded non-goals are the primary defence against scope creep.

*Derives from:* BN-0003

**origin**: ai · **priority**: should · **verification**: demonstration · **ratified_by**: Henry Grech-Cini · **ratified_fingerprint**: sha256:d0027a121c44d10491bc5b043d2c83eba0294fc96f6331fe2ce94bf0f758369a · **ratified_backfilled**: True
<!-- tl:end -->
