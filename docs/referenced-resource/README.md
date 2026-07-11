# Requirements Management Tool — Specification Set

**Working codename:** `throughline` (replace with your chosen project name)
**Spec version:** 0.1.0 · **Date:** 2026-07-08 · **License of these documents:** CC-BY-4.0 (suggested)

This is a complete, buildable specification set for an open-source, plain-text,
Git-native requirements management tool. Its features are distilled from the
public documentation of Doorstop, StrictDoc, Sphinx-Needs, ReqView, and the
common capability set of enterprise tools (IBM DOORS, Jama Connect, Polarion).

These documents practice what they specify: every requirement has a
**permanent, unique ID** that is never renumbered and never reused, plus a
status, priority, verification method, and trace links.

## Document map

| # | File | Contents |
|---|------|----------|
| — | `README.md` | This file: conventions and how to use the set |
| 01 | `01_vision_and_scope.md` | Problem statement, goals, non-goals, guiding principles, licensing |
| 02 | `02_feature_analysis.md` | Features distilled from public docs of existing tools + adoption decisions |
| 03 | `03_user_requirements.md` | Stakeholder/user requirements (`UR-xxxx`) |
| 04 | `04_system_requirements.md` | Functional system requirements (`SR-xxxx`) |
| 05 | `05_nonfunctional_requirements.md` | Quality requirements (`NFR-xxxx`) |
| 06 | `06_data_model_and_format.md` | Entities, file format, UID grammar, fingerprint algorithm |
| 07 | `07_architecture.md` | Reference architecture, tech options, CLI surface, delivered scope |
| 08 | `08_traceability.md` | UR ↔ SR traceability matrix and coverage summary |
| 09 | `09_glossary.md` | Terms used across the set |

## Conventions used in this specification

### Requirement identifiers
- Every requirement has an ID of the form `<PREFIX>-<NNNN>`: `UR-` (user),
  `SR-` (system/functional), `NFR-` (non-functional).
- IDs are **immutable**. They never change when requirements are added,
  removed, or reordered.
- IDs are **never reused**. A withdrawn requirement keeps its ID with status
  `Retired` and a short tombstone note (see SR-0007 for a live example).
- Gaps in the numbering are normal and carry no meaning.
- Document position (section, order) carries **no meaning** for identity.

### Requirement statements
Statement keywords follow ISO/IEC/IEEE 29148 style:
- **shall** — binding requirement, must be verified.
- **should** — recommended; deviation requires a documented reason.
- **may** — optional/allowed.

Each requirement is a single, verifiable sentence where possible, followed by
optional rationale and a metadata line:

`Priority: Must | Verification: Test | Traces: UR-0001 | Status: Approved`

### Priorities (MoSCoW)
- **Must** — required (MVP).
- **Should** — strongly desired.
- **Could** — nice to have; out of scope.
- **Won't (this scope)** — explicitly out of scope.

### Verification methods
**Test** (automated test), **Demonstration** (manual walkthrough),
**Inspection** (review of artifact/code/docs), **Analysis** (measurement,
benchmark, or reasoning).

### Statuses
`Draft → Approved → Implemented → Verified`, plus `Retired` (withdrawn, ID
kept forever).

## How to use this set

1. Read `01` and `02` to confirm scope and adoption decisions.
2. Treat `04` + `05` as the build contract; `06` is normative for the file
   format; `07` is guidance (non-normative except where it repeats SR/NFR IDs).
3. As you build, update each requirement's `Status` — do not delete or
   renumber anything.
4. When the tool can host its own requirements, import these documents into it
   (self-hosting is requirement SR-0061).
