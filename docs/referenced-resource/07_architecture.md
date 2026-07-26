# 07 · Reference Architecture (Non-normative guidance)

## 1. Layered view

```
┌────────────────────────────────────────────────────────┐
│ Interfaces:  CLI  │  Library API                       │
├────────────────────────────────────────────────────────┤
│ Services                                               │
│  validate │ trace/impact │ diff/baseline │ query        │
│  publish (HTML/MD/PDF) │ import/export (CSV/JSON/ReqIF) │
├────────────────────────────────────────────────────────┤
│ Core model:  Project · Register · Item · Link · Schema │
│ Indexes:     UID map · link graph (fwd/back) · attrs   │
├────────────────────────────────────────────────────────┤
│ Storage:  YAML item files · manifests · TOML config    │
│ VCS adapter (git): revisions, tags, blame (optional)   │
└────────────────────────────────────────────────────────┘
```

Dependency rule: arrows point downward only. Exporters/importers and
validation rules attach via the plugin registry (SR-0071).

## 2. Component responsibilities

- **Parser/Writer** — loads YAML/TOML into the model; writes
  deterministically (SR-0072); preserves unknown fields (NFR-0009).
- **Model** — pure in-memory objects; no I/O; enforces UID grammar.
- **Index builder** — one pass builds UID map, bidirectional link graph, and
  per-attribute indexes so common filters are O(1) lookups (NFR-0006 —
  the approach Sphinx-Needs documents for large projects).
- **Validator** — pipeline of rule objects (schema, links, cycles, suspect,
  review, coverage, lint), each yielding findings {rule, severity, uid,
  file, message} (SR-0040..44).
- **Trace service** — reachability queries over the link graph (SR-0035),
  matrix generation (SR-0051).
- **Baseline/Diff service** — manifest creation, fingerprint comparison,
  revision access through the VCS adapter (SR-0036..37).
- **Publisher** — templates → static HTML site; Markdown export; PDF via
  headless HTML rendering (SR-0050..53, SR-0057).
- **Exchange** — CSV/XLSX, canonical JSON, ReqIF mapping (SR-0054..56).

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

## 4. Technology options

| Option | Pros | Cons |
|---|---|---|
| **Python 3.11+** (recommended) | Ecosystem overlap with Doorstop/StrictDoc/Sphinx-Needs users; ruamel.yaml round-trip preserves unknown keys; fast to build; pipx install | Startup latency near NFR-0008 limit; packaging care needed |
| Rust | Speed, single binary, easy NFR-0006/0008 | Slower iteration; smaller contributor pool in this niche |
| TypeScript/Node | Web viewer synergy | Weaker YAML round-trip story; runtime dependency |

Suggested Python stack: `ruamel.yaml`, `tomllib`, `jinja2` (publish),
`markdown-it-py` (CommonMark), `click` or `argparse` (CLI), `openpyxl`
(XLSX), `lxml` (ReqIF), `pytest` + `hypothesis` (tests).

## 5. Performance strategy

Parse once → build indexes → all services query indexes. Cache parsed model
keyed by (file mtime, size) under `.cache/` for incremental runs. Publishing
renders per register, parallelizable. Benchmarks live in CI against a
generated 10k-item reference project (NFR-0006, NFR-0017).

## 6. CLI surface (sketch, binary name `rmt`)

```
rmt init [--template 29148]
rmt register new <PREFIX> <path> [--parent PREFIX]
rmt new <PREFIX> [--uid SR-0107] [--edit]        # allocate + open $EDITOR
rmt edit <UID> | rmt move <UID> <PREFIX> | rmt reorder <PREFIX>
rmt delete <UID> --reason "…"                    # tombstone, never erase
rmt link <SRC> <DST> --type verifies
rmt review <UID|--all-clean> | rmt stamp <SRC> <DST>
rmt check [--strict] [--format json]
rmt query '<filter expression>' [--columns …] [--format csv|json|table]
rmt trace <UID> [--direction in|out] [--depth N]
rmt baseline create <name> | list | diff <a> <b>
rmt publish html <dir> | md <dir> | pdf <file>
rmt export csv|xlsx|json|reqif … / rmt import csv|xlsx|reqif …
rmt fix uid-collision <UID>                      # post-merge repair (SR-0006)
```

Exit codes: 0 ok · 1 findings at error severity · 2 usage/internal (SR-0060).

## 7. Testing strategy

- Golden-file tests for writer determinism (SR-0072) and publishers.
- Property tests: UID allocation never collides/reuses across random
  add/delete/merge sequences (SR-0003/0006).
- Round-trip tests: CSV and ReqIF export→import→export stability (SR-0054/56).
- Scenario test mirroring UR-0020's 15-minute quick start.
- Self-hosting check: this spec set imported and `rmt check --strict` green
  (SR-0061).

## 8. Delivered scope

| Scope | Contents (requirement IDs) |
|---|---|
| **M0 — Core** | SR-0001..06, 0010..15, 0020..24, 0030..34, 0040..46, 0060..61, 0070, 0072; NFR-0001..05, 0009 |
