# 09 · Glossary

**Attribute** — A named, typed field on an item, either built-in (SR-0022)
or project-defined (SR-0020).

**Baseline** — A named, immutable snapshot of the entire requirement set,
pinned to a VCS revision plus a manifest of UIDs and fingerprints.

**Coverage rule** — A declared obligation that certain items must have
certain links (e.g. every system requirement is verified by a test).

**Derived (item)** — An item that does not trace to a parent requirement by
design; exempt from "must have parent" coverage rules.

**EARS** — Easy Approach to Requirements Syntax; sentence patterns
(ubiquitous, event-driven, state-driven, unwanted-behavior, optional) used
as authoring templates by the lint feature (SR-0043).

**Fingerprint** — SHA-256 hash of an item's normative content (doc 06 §5);
the basis for review tracking and suspect links.

**Impact analysis** — Listing everything transitively linked *to* an item,
to estimate the blast radius of changing it (SR-0035).

**Item** — The unit of management: a requirement, heading, test case, risk,
or other typed object with a UID.

**Link role / link type** — The semantic label on a link (refines, verifies,
satisfies, implements, relates), configurable per project.

**Manifest** — Per-register metadata file (`.register.yml`) holding prefix,
ordering, sections, and reserved numbers.

**Normative** — Content whose change is a real requirement change (statement
text, key attributes) and therefore included in the fingerprint — as opposed
to ordering, status, or comments.

**ReqIF** — Requirements Interchange Format, the OMG XML standard for
lossless requirements exchange between RM tools (SR-0056).

**Register** — A named collection of items with one UID prefix, a manifest,
and a place in the project tree. A *view* over items, not their identity.

**Reserved number** — A UID number recorded as used-forever (retired or
manually blocked) so allocation can never reissue it.

**Retired / Tombstone** — The permanent record of a deleted or withdrawn
item: UID, dates, reason. Guarantees IDs are never reused and deletions stay
auditable (SR-0012; live example: SR-0007).

**Suspect link** — A link whose stored stamp of the target's fingerprint no
longer matches the target's current fingerprint: the target changed since
the relationship was last confirmed (SR-0034).

**Traceability matrix** — A generated table mapping items of one set to
linked items of another (e.g. UR ↔ SR), with gaps highlighted.

**UID** — Unique identifier of an item: `PREFIX-NNNN`. Immutable, unique
forever, position-independent (SR-0001..04).

**View** — Any generated presentation of items: ordered document, table,
matrix, graph. Views may renumber for print; identity never follows.
