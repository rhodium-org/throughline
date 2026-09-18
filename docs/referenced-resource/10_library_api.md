# 10 — The library API

What `import throughline` offers a program embedding the Tool, and what it promises about it.

This list **is** the compatibility surface NFR-0011 names for the library (SR-0228). A test fails when the names the package exports differ from the ones below, so a name is added or removed by a decision rather than by accident. Anything not listed here is internal: it may be renamed or removed in any release, and a program relying on it is relying on nothing.

## What the interface promises

- **It returns data.** These functions return values and raise the Tool's own kinds of error. They print nothing and do not end the program; rendering belongs to whoever called them (SR-0224).
- **The command line is one of its callers.** `tl` reads its arguments, calls these functions and renders the result, so a capability the command line has is one this list has too (SR-0225).
- **Work only the host can do is a parameter.** Reaching a source held elsewhere, or asking a repository which identity it signs with, is passed in, with a default for an ordinary environment (SR-0226).
- **No subprocess, no socket, no terminal.** Given those parameters, every name here completes its work where the caller has none of the three — a browser runtime, for instance. A test proves it by taking all three away (SR-0227).

## The names

### The model and its storage

`Project` · `Register` · `Item` · `Link` · `load_project` · `read_project` · `load_project_at_ref` · `init_project` · `migrate_project` · `write_item` · `write_manifest` · `ProjectError` · `CONFIG_NAME` · `MANIFEST_NAME`

### Identity of an item

`UID_RE` · `parse_uid` · `format_uid` · `next_uid` · `collisions` · `Index` · `fingerprint`

### The schema a project declares

`Schema` · `AttrSpec` · `LinkRule` · `SchemaError`

### Creating and changing an item

`birth_item` · `parse_attrs` · `coerce_attr` · `amend_item` · `Amendment` · `newly_suspect` · `delete_item` · `review_items` · `add_link` · `remove_link` · `retype_link` · `LinkError`

### The gate

`validate` · `Finding` · `is_external` · `is_namespace_qualified` · `query_items` · `eval_filter` · `FilterError`

### The grounding layer

`GroundingError` · `reaches_root` · `grounding_gap` · `is_unserved` · `set_status` · `transition_refusal` · `ratify` · `ratification_obstacle` · `invalidate` · `withdraw` · `flag` · `clarify` · `is_flagged_ambiguous` · `ambiguity_report` · `attribute_owner` · `change_since_ratification` · `ratification_is_committed`

### Who signs

`default_ratifier` · `git_identity` · `normalise_identifier` · `IdentityError`

### Publishing

`inject_text` · `referenced_uids` · `has_markers` · `render_item` · `register_directive` · `TargetResolver` · `InjectError` · `build_dump`

### The running build

`distribution_version` · `is_editable` · `__version__`
