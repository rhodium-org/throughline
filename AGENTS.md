<!--
  This project is managed by throughline (Git-native requirements, Intent-Driven
  Development). This file is the CANONICAL agent-guidance document for the repo;
  CLAUDE.md, GEMINI.md, .github/copilot-instructions.md and .cursor/rules all
  point here so there is one source of truth, not N drifting copies.

  The operative rules for the graph are GENERATED FROM THE LIVE CONFIG by
  `tl context` — do not paste a static copy of that brief into this file. This
  file holds only the durable guidance the generator cannot produce.
-->

# Working with throughline (for AI agents)

This repository is **throughline**, a Git-native requirements / Intent-Driven
Development (IDD) tool (CLI `tl`). It is also *self-hosting*: its own requirements
live as a throughline graph under [`idd/`](idd). Read the hat that matches what
you're doing:

- **Using throughline inside another project?** → [Using throughline](#using-throughline-in-a-project).
- **Changing throughline itself (the tool's code)?** → [Working on this repo](#working-on-this-repo-contributing).

---

## Using throughline in a project

throughline stores requirements as one small YAML file per item, each with a
permanent UID, under version control. `tl check` validates the whole graph and
gates CI. The discipline it enforces: **every requirement must justify itself by
grounding upward to a root ("why"), and any machine-authored item must be
ratified by a named human before it counts.**

### 1. Keep the graph in a folder named `idd/` (best practice)

Put the requirements graph in its own top-level `idd/` directory, separate from
source code. It is the estate convention and every command below assumes it:

```
your-project/
  idd/                 # the throughline graph  (idd = Intent-Driven Development)
    throughline.toml
    <register folders>/…   # e.g. intents/ user-requirements/ system-requirements/
  src/  …              # your code
```

Drive throughline against it with `-C idd`, e.g. `tl -C idd check --strict`.

### 2. Read the generated brief FIRST — never a hand-written copy

The authoritative instructions for an agent are **generated from the project's
live `throughline.toml`**, so they can never drift from what the validator
actually enforces:

```
tl -C idd context
```

That brief lists the exact item types, attributes, link vocabulary, status
lifecycle, grounding rules, and commands for *this* project. Read it before you
create or edit any item, and trust it over any static list (including this file).

### 3. Starting throughline on a project

- **New / empty project:** `tl -C idd init` scaffolds the graph (it creates
  `idd/` if missing). Then author the intents and requirements.
- **Existing codebase — reverse-engineer (offer this to the user):** you can read
  the code and **propose** the requirements it already implements — the intents,
  user requirements and system requirements latent in what has been built — so
  the project starts with a real spine instead of a blank one. Create them as
  machine-origin items (`tl -C idd new … --origin ai`), which enter `proposed`;
  ground each one upward; then **stop and hand off**. A named human ratifies
  (`tl ratify <UID> --by <name>`, or the [`tl-ratify`](https://github.com/rhodium-org/throughline-ratify)
  cockpit). Propose, then wait.

### 4. The working loop

1. **Author the why first.** Before building, create the grounded requirement
   (throughline's "red test"):
   `tl -C idd new SR --type system_requirement --ground UR-0001 --ground-type implements --title "…" --origin ai --no-interactive`.
   Machine-origin items you create are `proposed` and need human ratification.
2. **Implement** the change.
3. **Cite the item** — reference the UID(s) it satisfies in the commit message.
4. **Gate:** keep `tl -C idd check --strict` green (and `tl -C idd docs --check`
   if the project publishes documents). Exit codes are stable: `0` ok · `1`
   findings · `2` usage.

> Editing content: `tl new` sets structure (uid, type, status, grounding, title);
> `tl amend <UID> [--title …] [--text …] [--rationale …] [--attr K=V]` changes it
> afterwards (SR-0144). Amend rather than opening the file — the command quotes
> whatever you give it, so the `: ` (colon-space) that silently reparses a plain
> scalar and surfaces later as a loader error naming an unrelated file cannot
> happen. It reports what the change made suspect and whether the item's review
> and ratification records still match, and it will not write the ratification
> record itself — `tl ratify` owns that.

## Ratification is a human act — never sign on someone's behalf

`tl ratify <UID> --by <who>` records that a **named human** took accountability
for an item. The `--by` / `ratified_by` value is that person's identity — it is
evidence, not a formality.

If you do not already know who is ratifying, **ask the user and use exactly what
they give you. Do not guess, do not invent a name or email, and do not reuse a
value you saw elsewhere in the repo.** A fabricated `ratified_by` is a false
accountability record — the one thing this tool exists to prevent. When in doubt,
stop and ask.

## How to guide an agent to *use* throughline (the pattern)

The best-practice way to give an AI agent guidance on **using** a tool (as
opposed to working on the tool's own repo) is not to hand-write a brief that
rots — it is to **generate the brief from the tool and point the agent at it.**
In the *consuming* project, add a short `AGENTS.md` (the vendor-neutral standard)
that says little more than:

> This project is managed by throughline. Run `tl -C idd context` and follow it.
> Two invariants: **ground every item upward before you build it**, and **only a
> named human ratifies** machine-proposed items.

Then let each coding framework's own file (`CLAUDE.md`, `GEMINI.md`,
`.github/copilot-instructions.md`, `.cursor/rules/…`) be a **one-line pointer** to
that `AGENTS.md` — never a parallel copy. This repo does exactly that.

---

## Working on this repo (contributing)

throughline is open source (Apache-2.0) and contributions are welcome — improving
the tool itself is as valued as using it. It is a pure-Python package
(`src/throughline`, CLI `tl` / `throughline`).

```
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q                    # unit tests
tl -C idd check --strict     # this repo's own requirements graph — must stay green
```

### Working on more than one package at once — chain the editable installs

throughline, [throughline-compose](https://github.com/rhodium-org/throughline-compose)
and [throughline-ratify](https://github.com/rhodium-org/throughline-ratify) are
separate repositories that install one another. `pip install -e ".[dev]"` makes
*this* package editable and resolves the others **from PyPI** — so you can be
editing one while running the published copy of the next, and every version string
will agree. Two people (or a person and an agent) then run the same command in the
same repo, get different answers, and argue about the graph rather than about the
toolchain.

Check the repos out side by side and chain them in a **single** command, so the
resolver never reaches the index:

```sh
# from whichever repo you are working in — name every sibling you have checked out
pip install -e ../throughline -e ../throughline-compose -e ".[dev]"
```

Then verify rather than assume; every path must be your checkout, never
`site-packages`:

```sh
python -c "import throughline as m; print(m.__file__)"
```

For the CLIs you use day to day, pipx needs the same treatment — with two traps
that both fail *silently*:

```sh
pipx install --editable ./throughline
pipx install --editable ./throughline-compose
pipx inject --force --editable throughline-compose ./throughline
pipx install --editable ./throughline-ratify
pipx inject --force --editable throughline-ratify ./throughline-compose
pipx inject --force --editable throughline-ratify ./throughline   # core LAST
```

- **Inject the core last.** Injecting a dependent afterwards re-resolves its
  requirements and quietly pulls the published core back over your editable one.
- **`pipx install --force --editable` does not convert an existing venv.** It
  reports success while leaving the published copy in place. `pipx uninstall` first.

Because throughline manages its own requirements, changes here follow the same
IDD discipline: ground the change in an `idd/` item (create it and get it ratified
if it is new), reference the UID in your commit, and keep `tl -C idd check
--strict` and `tl -C idd docs --check` green. See [`CONTRIBUTING.md`](CONTRIBUTING.md)
and [`HOW_TO_USE.md`](HOW_TO_USE.md).
