# 07 · Reference Architecture (Non-normative guidance)

## 1. Layered view

```
┌────────────────────────────────────────────────────────┐
│ Interfaces:  CLI (tl)  │  Library API                  │
├────────────────────────────────────────────────────────┤
│ Services                                               │
│  validate │ trace/impact │ diff/baseline │ query        │
│  render (Markdown by reference) │ JSON dump             │
├────────────────────────────────────────────────────────┤
│ Core model:  Project · Register · Item · Link · Schema │
│ Indexes:     UID map · link graph (fwd/back) · attrs   │
├────────────────────────────────────────────────────────┤
│ Storage:  YAML item files · manifests · TOML config    │
│ VCS adapter (git): revisions, tags, blame (optional)   │
└────────────────────────────────────────────────────────┘
```

Dependency rule: arrows point downward only. Custom validation rules attach
via the plugin registry (SR-0071). The tool is **a validator and an injector**
(NG-0001): it renders item content into Markdown documents *by reference*
(SR-0094) and dumps the whole project to a documented JSON structure (SR-0055).
It does not generate presentation or exchange formats — HTML, PDF, CSV/XLSX and
ReqIF are out of scope (NG-0005), delegated to external tools such as pandoc or
mdBook that consume the rendered Markdown or the JSON dump.

## 2. Component responsibilities

- **Parser/Writer** — loads YAML/TOML into the model; writes
  deterministically (SR-0072); preserves unknown fields (NFR-0009).
- **Model** — pure in-memory objects; no I/O; enforces UID grammar.
- **Index builder** — one pass builds UID map, bidirectional link graph, and
  per-attribute indexes so common filters are O(1) lookups (NFR-0006 —
  the approach Sphinx-Needs documents for large projects).
- **Validator** — pipeline of rule objects (schema, links, cycles, suspect,
  review, coverage, lint), each yielding findings {rule, severity, uid,
  file, message} (SR-0040..44). Custom rules plug in without core changes
  (SR-0071).
- **Trace service** — reachability queries over the link graph (SR-0035),
  matrix generation (SR-0051).
- **Baseline/Diff service** — manifest creation, fingerprint comparison,
  revision access through the VCS adapter (SR-0036..37); diff reported as
  text and JSON.
- **Renderer** — injects item content into Markdown documents by reference,
  updating marked regions in place (SR-0094, SR-0057). It produces Markdown
  only; converting that Markdown to a navigable HTML site or PDF is a
  wrapper's job (pandoc, mdBook), not the core's (NG-0005).
- **JSON dump** — one documented, schema-versioned machine-readable dump of
  the whole project (SR-0055), the single sanctioned interchange surface for
  third-party tooling.

## 3. Key decision records

**D1 — One file per item (vs one file per register).**
Chosen: per item (Doorstop model). Merge conflicts localize to a single
requirement; Git history and blame are per-requirement; renames are moves.
Trade-off (StrictDoc argues the per-register side): whole-register reading
order lives in metadata rather than file layout — mitigated by the manifest
`sections` + `order` fields and by generated document views.

**D2 — YAML items + TOML config (vs custom DSL).**
Chosen: standard formats with a strict subset and schema validation.
Trade-off: a DSL (SDoc) reads better for huge single documents; standard
formats win on tool ecosystem, parsers in every language, and NFR-0002.

**D3 — Suspect detection via stored stamps (vs VCS-diff heuristics).**
Chosen: Doorstop-style fingerprint stamps stored on links; works without
VCS, survives history rewrites, and is exact w.r.t. normative content.

**D4 — Baselines = VCS tag + manifest (vs copied snapshots).**
Chosen: tag for reproducibility, manifest for tool-level verification and
tool-agnostic diffing. Copies rejected: duplication and drift.

**D5 — Collaboration = Git (vs server with locking).**
Chosen: branch/PR workflow; UID collision repair command (SR-0006).
A reservation server (Doorstop has one) is out of scope.

**D6 — Render Markdown by reference; delegate presentation (vs built-in
HTML/PDF/exchange generation).**
Chosen: the core injects content into Markdown documents (SR-0094) and dumps
JSON (SR-0055); external tools turn that into HTML/PDF. Rejected: a built-in
renderer for stakeholder or RM-vendor formats (HTML/PDF/CSV/XLSX/ReqIF) and
foreign-list import — every comparable tool that grew one acquired a
rendering engine to maintain and drifted from the git-native,
reference-not-copy discipline (NG-0005, NG-0001; SR-0089 was rejected on the
same grounds).

## 4. Technology options

| Option | Pros | Cons |
|---|---|---|
| **Python 3.11+** (recommended) | Ecosystem overlap with Doorstop/StrictDoc/Sphinx-Needs users; ruamel.yaml round-trip preserves unknown keys; fast to build; pipx install | Startup latency near NFR-0008 limit; packaging care needed |
| Rust | Speed, single binary, easy NFR-0006/0008 | Slower iteration; smaller contributor pool in this niche |
| TypeScript/Node | Web viewer synergy | Weaker YAML round-trip story; runtime dependency |

Suggested Python stack: `ruamel.yaml`, `tomllib`, `markdown-it-py`
(CommonMark, for locating and rewriting injected regions), `click` or
`argparse` (CLI), `pytest` + `hypothesis` (tests). No HTML-templating,
spreadsheet or ReqIF/XML dependencies are needed — those formats are out of
scope (NG-0005).

## 5. Performance strategy

Parse once → build indexes → all services query indexes. Cache parsed model
keyed by (file mtime, size) under `.cache/` for incremental runs. Rendering
runs per document, parallelizable. Benchmarks live in CI against a
generated 10k-item reference project (NFR-0006, NFR-0017).

## 6. CLI surface (sketch, binary name `tl`)

```
tl init [--template 29148]
tl register new <PREFIX> <path> [--parent PREFIX]
tl new <PREFIX> [--uid SR-0107] [--edit]         # allocate + open $EDITOR
tl edit <UID> | tl move <UID> <PREFIX> | tl reorder <PREFIX>
tl delete <UID> --reason "…"                     # tombstone, never erase
tl link <SRC> <DST> --type verifies | tl unlink <SRC> <DST>
tl review <UID|--all-clean> | tl stamp <SRC> <DST>
tl check [--strict] [--format json]
tl query '<filter expression>' [--columns …] [--format json|table]
tl trace <UID> [--direction in|out] [--depth N]
tl baseline create <name> | list | diff <a> <b>  # text or json
tl docs [--check]                                # render Markdown by reference
tl dump [-o FILE]                                # documented whole-project JSON dump
tl fix uid-collision <UID>                       # post-merge repair (SR-0006)
```

Exit codes: 0 ok · 1 findings at error severity · 2 usage/internal (SR-0060).

## 7. Testing strategy

- Golden-file tests for writer determinism (SR-0072) and the Markdown
  renderer (SR-0094, SR-0057).
- Property tests: UID allocation never collides/reuses across random
  add/delete/merge sequences (SR-0003/0006).
- Dump stability tests: the JSON dump is deterministic and schema-versioned
  (SR-0055, NFR-0011).
- Scenario test mirroring UR-0020's 15-minute quick start.
- Self-hosting check: this spec set imported and `tl check --strict` green
  (SR-0061).

## 8. Delivered scope

| Scope | Contents (requirement IDs) |
|---|---|
| **M0 — Core** | SR-0001..06, 0010..15, 0020..24, 0030..34, 0040..46, 0060..61, 0070, 0072; NFR-0001..05, 0009 |
