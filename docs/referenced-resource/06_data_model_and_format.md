# 06 · Data Model and File Format (Normative)

This document is normative for the on-disk format (NFR-0002). Format schema
version: `1`.

## 1. Entities

| Entity | Identity | Stored as |
|---|---|---|
| **Project** | root directory | `throughline.toml` at root |
| **Register** | directory + `PREFIX` | directory containing `.register.yml` manifest |
| **Item** (requirement, heading, test, …) | **UID**, immutable | one YAML file `<UID>.yml` |
| **Link** | (source UID, target, type) | list entry inside the source item |
| **Tombstone** | retired UID | retained item file with `status: deleted`, or stub |
| **Baseline** | name | manifest under `.baselines/` + VCS tag |

## 2. Directory layout

```
project/
├─ throughline.toml               # project config: schema, link types, rules
├─ .baselines/
│  └─ SRS-2.0.yml             # baseline manifest (UID → fingerprint map)
├─ ur/                        # a register
│  ├─ .register.yml           # prefix: UR, title, parent, order list
│  ├─ UR-0001.yml
│  └─ UR-0002.yml
└─ srs/
   ├─ .register.yml           # prefix: SR, parent: UR
   ├─ SR-0001.yml
   └─ SR-0007.yml             # tombstone (status: deleted/retired)
```

## 3. UID grammar

```
uid     = prefix "-" number
prefix  = UPPER (UPPER | DIGIT){1,15}        ; ASCII, per register, unique in project
number  = DIGIT{width}                       ; zero-padded, width per register (default 4);
                                             ; numbers beyond width grow naturally (no rollover)
```
Regex (width 4): `^[A-Z][A-Z0-9]{1,15}-[0-9]{4,}$`

Allocation rule: next number = 1 + max(number ever used for prefix), where
"ever used" includes deleted/retired items and the reserved list in the
register manifest (enforces SR-0003).

## 4. Item file (YAML)

```yaml
# srs/SR-0034.yml
uid: SR-0034
type: requirement            # from project schema
status: approved             # from project status vocabulary
title: Suspect links
text: |
  Each link shall store the target's fingerprint at the time the link was
  last confirmed; when the stored and current fingerprints differ, the Tool
  shall report the link as suspect.
rationale: |
  Detects silently stale traceability after upstream edits.
normative: true
derived: false
order: 4.2                   # presentation only; never part of identity
links:
  - target: UR-0005
    type: refines
    stamp: "sha256:9f2c…e1"  # target fingerprint when link last confirmed
  - target: "src/trace/suspect.py#L10-L42"
    type: implements
attrs:                       # project-defined custom attributes
  priority: must
  verification: test
reviewed: "sha256:77ab…04"   # own fingerprint at last review (SR-0038)
created: 2026-07-08
modified: 2026-07-08
```

Rules:
- Unknown keys are preserved verbatim on rewrite (NFR-0009).
- Files are written with stable key order as above, LF endings, UTF-8,
  final newline (SR-0072).
- A tombstone is the same file with `status: deleted`, plus
  `deleted: {date, reason}`; `text` may be truncated to a hash reference.

## 5. Fingerprint (normative content hash)

`fingerprint = SHA-256( canonical( uid, type, text, normative, derived,
attrs* marked normative in schema ) )`

- Canonicalization: UTF-8 NFC, LF endings, trailing whitespace stripped,
  fields concatenated as `key "\x1f" value "\x1e"` in the fixed order above.
- Explicitly **excluded**: `order`, `title`, `status`, `links`, `reviewed`,
  timestamps, comments — so reordering and workflow changes don't raise
  suspects (SR-0033).
- Link `stamp` and item `reviewed` store this value prefixed `sha256:`.

## 6. Text fields

`text` and `rationale` are CommonMark, restricted to: paragraphs, emphasis,
inline code, code blocks, ordered/unordered lists, tables (GFM), block
quotes, images and links by relative path or URL. Raw HTML is ignored by
publishers. `[[UID]]` is an inline cross-reference resolved at publish time.

## 7. Register manifest

```yaml
# srs/.register.yml
prefix: SR
digits: 4
title: System Requirements
parent: UR                   # expected direction of refines links
reserved: [7]                # retired numbers (belt-and-braces vs tombstones)
sections:                    # optional named sections for publishing
  - {level: 1, title: Identification and numbering}
```

## 8. Project configuration (throughline.toml)

```toml
[project]
name = "Example"
format_version = 2

[types.requirement]
attrs.priority     = {type = "enum", values = ["must","should","could"], required = true, normative = true}
attrs.verification = {type = "enum", values = ["test","demonstration","inspection","analysis"]}

[links]
types = ["refines", "verifies", "satisfies", "implements", "relates"]

[status]
values = ["draft","approved","implemented","verified","rejected","deleted"]
transitions = [["draft","approved"], ["approved","implemented"], ["implemented","verified"]]

[rules]                       # coverage + lint severities (SR-0041/42/43)
uncovered = [{filter = "type=='requirement' && register=='SR' && status!='deleted'", needs = "incoming:verifies from TST", severity = "error"}]
vague_words = {severity = "warning", words = ["fast","user-friendly","etc"]}
```

## 9. Baseline manifest

```yaml
# .baselines/SRS-2.0.yml
name: SRS-2.0
created: 2026-07-08T14:02:00Z
vcs: {system: git, revision: "a1b2c3d…", tag: baseline/SRS-2.0}
items:
  SR-0001: "sha256:aa10…"
  SR-0034: "sha256:77ab…"
counts: {total: 63, deleted: 1}
```

A baseline is valid iff the VCS revision exists and every listed fingerprint
matches recomputation at that revision (SR-0036). Diff (SR-0037) is defined
as set/fingerprint comparison between two manifests or working state.

## 10. Canonical JSON export (shape)

Single object: `{format_version, project, registers:[{prefix, manifest,
items:[…full item objects…]}], links_index, baselines}` — field names
identical to the YAML keys (SR-0055). This is the interchange surface for
third-party tools and the web viewer.

## 11. Invariants (validated by `check`)

1. Every UID matches the grammar and its file name.
2. UIDs unique project-wide; numbers never below the reserved/tombstone set.
3. Every link target resolves to an existing UID, path, or URL.
4. No `refines` cycles (configurable).
5. Every item validates against its type schema.
6. `format_version` supported.
