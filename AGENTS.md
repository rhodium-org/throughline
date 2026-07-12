<!--
  This project is managed by throughline (Git-native requirements, Intent-Driven
  Development). The authoritative brief for an AI agent is GENERATED FROM THE
  LIVE CONFIG, not written by hand here — so it can never drift from the rules
  `tl check` actually enforces as throughline.toml changes.

  Do not paste a static copy of the brief into this file: run the command.
-->

# Working in this project (for AI agents)

Before you make any change, generate the project brief and read it:

```
tl context
```

It is a self-contained Markdown document — the Intent-Driven Development
contract, this project's item types and their attributes, the link and status
vocabularies and their constraints, the on-disk YAML format, the commands you
will use, and a live snapshot of the current graph. It is generated from the
project's `throughline.toml`, so it always reflects the current rules.

Pipe it straight into your context, or save it where you need it:

```
tl context > /tmp/throughline-brief.md
```

The one rule to internalise first: **author the grounded requirement before you
build it.** Create it as a `draft` (throughline's "red test"), implement, then flip
it to `approved`; `tl check` must stay green, and it gates the commit. Run
`tl context` for the full contract.

## Ratification is a human act — never sign on someone's behalf

`tl ratify <UID> --by <who>` records that a **named human** took accountability
for an item. The `--by` / `ratified_by` value is that person's identity — it is
evidence, not a formality.

If you do not already know who is ratifying, **ask the user and use exactly what
they give you. Do not guess, do not invent a name or email, and do not reuse a
value you saw elsewhere in the repo.** A fabricated `ratified_by` is a false
accountability record — the one thing this tool exists to prevent. When in
doubt, stop and ask.
