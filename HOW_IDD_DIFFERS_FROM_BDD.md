# Intent-Driven Development, and how it differs from BDD

> This page exists because a requirement said it should: **NFR-0021 —
> "Conceptual model explained"** (`requirements/nonfunctional/NFR-0021.yml`),
> which implements **UR-0020 "Comprehensible to newcomers"**. Newcomers adopt a
> practice faster when they understand *why* it is shaped the way it is. This is
> that "why".

If you have done any test-first development, throughline's workflow will feel
familiar — and that familiarity is worth a moment's care, because IDD and BDD
answer **different questions** and sit on **different axes**. Confusing them is
the fastest way to misuse the tool.

---

## The one-sentence difference

- **BDD** proves a feature **behaves** as agreed. It points *outward*, from a
  requirement to concrete examples and the tests that verify them.
- **IDD** proves a feature **deserves to exist**. It points *upward*, from a
  requirement to the reason it is there.

They are **orthogonal and complementary**, not rivals. BDD guards the *what*;
IDD guards the *why*. A mature project wants both.

---

## Intent-Driven Development in one minute

The idea is a straight lift of the test-first instinct, moved one level up:

- In **TDD** you don't write code without a **failing test** first.
- In **BDD** you don't build a feature without an agreed **scenario** first.
- In **IDD** you don't create a requirement without its **justification** first.

In throughline that justification is a real, checkable thing: every non-root item
must reach a **root** — an `intent`, `business_need`, `risk`, `constraint`, or
`assumption` — through a **grounding link** (`derives_from`, `implements`,
`mitigates`, `verifies`). Those links form a DAG. `tl check` **fails the
build** if an item can't reach a root, or if a root nobody serves is left
dangling.

So a `draft` item that is grounded-but-unbuilt is throughline's version of a **red
test**: specified and justified, not yet delivered. You flip it to `approved` /
`implemented` when the thing exists. The `tl new --ground <UID>` flag makes
this the default motion — you state the *why* in the same breath as the *what*.

---

## Side by side

| | **TDD** | **BDD** | **IDD** |
|---|---|---|---|
| First question | "Does the code do what I intend?" | "What should the system do, by example?" | "Why should this exist at all?" |
| Written first | a failing unit test | a behaviour scenario (Given/When/Then) | a requirement linked to its *why* |
| Axis | downward — code correctness | outward — observable behaviour | upward — justification |
| Unit of work | test → code | scenario → acceptance | item → root it grounds to |
| Artifact | the test suite | feature files | the grounding graph |
| "Red" state | test fails | scenario pending | `draft` item, grounded but unbuilt |
| "Green" state | test passes | scenario passes | item `approved` **and** reaches a served root; `check` is clean |
| Guards against | broken code | misunderstood behaviour | scope sprawl / orphaned work |
| Verification lives in | assertions | acceptance criteria | `tl check` (the grounding gate) |

---

## "But doesn't BDD already capture the *why*?"

This is the sharp question, and the honest answer is: **partly, as prose — not
as an enforced fact.**

A BDD feature file often opens with a narrative:

```gherkin
Feature: Guided setup wizard
  In order to cut onboarding time      # ← the "why", as a comment
  As a new user
  I want a 3-step guided setup
```

That first line *gestures* at justification. But it is **advisory text in a
header**. Nothing checks that "cut onboarding time" is a real, agreed goal;
nothing fails your build if a feature's stated benefit is invented, duplicated,
or contradicts a constraint. BDD's job is to fail when **behaviour** is wrong —
not when **justification** is missing.

IDD makes the why a **first-class, validated edge**. `derives_from BN-0001` is
not a comment — it is a link to a business need that itself must trace to the
vision, and `tl check` rejects the graph if that chain is broken or if the
business need serves nothing. The why stops being a story you tell and becomes a
property the build enforces.

---

## They compose — the `verifies` seam

IDD does not replace BDD; it gives BDD somewhere to attach. In throughline the
**`verifies`** link is exactly the seam where behaviour-verification plugs into
the justification graph:

```
INT-0001  (intent — the why)
   ▲ derives_from
BN-0001   (business need)
   ▲ derives_from
FR-0001   (requirement — the what)          ← IDD grounds this upward
   ▲ verifies
TEST-0001 (a behaviour scenario / test)     ← BDD/TDD attach here, downward
```

- **IDD** owns the vertical axis: *does this requirement trace to a real why, and
  is every why served?*
- **BDD/TDD** own the connection downward from the requirement to the behaviour
  and code that satisfy it.

A fully healthy item is grounded **up** to a root (IDD) and covered **down** by a
verifying test (BDD/TDD). throughline's default config asks for both — that is why
the quick-start makes you add a `verifies` test before `check` goes green.

---

## An honest boundary

IDD is a **framing**, not a tooling ecosystem. BDD has decades of practice and
mature runners (Cucumber, SpecFlow, Behave); "Intent-Driven Development" is
simply the name we give to the discipline throughline's grounding gate already
imposes — write the intent first, and let the build reject work that can't
justify itself. Do not read it as a claim to replace BDD or TDD. Read it as the
**missing upward axis**: the practice that keeps the requirement set honest, so
that the behaviour you specify with BDD and the code you drive with TDD are spent
on things that actually deserve to exist.

---

## See also

- [`HOW_TO_USE.md`](HOW_TO_USE.md) — the 15-minute hands-on quick start, including
  grounding an item at birth with `tl new --ground`.
- [`README.md`](README.md) — the format, the CLI, and what `check` enforces.
