# Declared registers: a worked example

A concrete version of the proposal in
[rhodium-org/throughline#127](https://github.com/rhodium-org/throughline/pull/127),
amended so that a standard declares its registers as **items** in a REG register
instead of as a `[[registers]]` table. Three graphs:

| graph | what it is | `tl check` today | under the proposal |
|---|---|---|---|
| [`standard/`](standard) | a cut-down presentation standard: declares the kinds of item a deck holds, and four `register` items | 0 errors | 0 errors |
| [`series/`](series) | a deck series that follows it and holds what it declares | 0 errors | 0 errors |
| [`series-drifted/`](series-drifted) | a series copied and then changed | 0 errors | **3 errors** |

Every item is `origin: ai` and `proposed`: this is an example to review, not a
graph to sign.

## The standard declares registers as items

The standard holds three registers of its own: `INT` (why), `RULE` (what a deck
must do) and `REG` (the registers a deck must hold). Each REG item is the contract
for one register:

| item | title | prefix | dir | holds | required |
|---|---|---|---|---|---|
| REG-0001 | Claims live in CLM | `CLM` | `claims` | `claim` | yes |
| REG-0002 | Slides live in SLD | `SLD` | `slides` | `slide` | yes |
| REG-0003 | Threads live in THR | `THR` | `threads` | `thread` | yes |
| REG-0004 | Roles, when a deck has them, live in ROLE | `ROLE` | `roles` | `role` | no |

For example, [`standard/registers/REG-0002.yml`](standard/registers/REG-0002.yml)
holds:

```yaml
uid: REG-0002
type: register
title: Slides live in SLD
text: A deck graph holds its slides in the register SLD, folder slides/.
links:
- target: INT-0001
  type: derives_from
attrs:
  prefix: SLD
  dir: slides
  holds: slide
```

Because it is an item, it is ratified like any other. Moving slides to `pages/`
becomes an amendment: the item goes ratified-stale, the strict gate blocks
until a person signs the new folder, and the diff shows the decision. In a
`[[registers]]` table the same move would be one unsigned line.

**The standard holds no SLD register itself.** A declaration binds the graphs
that follow the standard, not the standard. The presentation standard holds
rules, not slides.

The type `register` is declared in [`standard/throughline.toml`](standard/throughline.toml)
today only so this example runs on current tl. Under the proposal, tl supplies
the type and a standard writes no table for it.

## A series that conforms

[`series/throughline.toml`](series/throughline.toml) is what `tl init --from-schema`
writes, the standard and nothing else:

```toml
[[sources]]
namespace = "deck"
path = "../standard"
schema = true
```

Under the proposal, init then creates `CLM claims/`, `SLD slides/` and
`THR threads/`, every **required** REG item and no optional one. Here they
were created by hand because the code does not exist yet. The series added an
`INT vision/` register of its own, which is allowed.

The REG items are borrowed into the series as `deck:REG-0001` to `deck:REG-0004`
(`tl -C series dump`), so a series can trace its SLD register to the signed
decision that requires it.

Predicted under the proposal: `0 error(s)`. It holds every required register at
the declared folder with the declared type, and has no ROLE register, which is
allowed because REG-0004 is optional.

## A series that has drifted

[`series-drifted/`](series-drifted) passes `tl check` today. That is the
problem: a renderer looking for `slides/` or `THR` finds nothing, and nothing said
so. Its registers:

| register | folder | type | against the standard |
|---|---|---|---|
| CLM | `claims` | claim | matches REG-0001 |
| SLD | `pages` | slide | REG-0002 says `slides` |
| — | — | — | no THR, which REG-0003 requires |
| ROLE | `people` | role | optional, but REG-0004 says `roles` |
| INT | `vision` | intent | its own, allowed |

Predicted under the proposal (rule `declared-register`, SR-0286):

```text
[ERROR] SLD          declared-register  register SLD is in pages/, but deck:REG-0002 declares slides/
[ERROR] THR          declared-register  no register THR — deck:REG-0003 requires one (threads/, holding thread)
[ERROR] ROLE         declared-register  register ROLE is in people/, but deck:REG-0004 declares roles/ (optional: absent is fine, different is not)
```

`tl register new SLD pages --type slide` would also be refused, before the
folder exists.

## What the example leaves out

- **The meta-standard.** If the deck standard followed the standard for
  standards, it would be held to that standard's REG items (INT, RULE, REG and
  so on). The series would not, because declarations bind only the graph that
  follows directly and do not pass down a chain.
- **Ratification.** Every item here is proposed. In a real standard the REG
  items are signed, and only signed REG items bind.
