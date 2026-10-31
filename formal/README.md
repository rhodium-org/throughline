# formal — throughline's grounding rules, proved

A Lean 4 model of the rules that decide whether a requirement is grounded, whether
a link change is refused, and whether a link is suspect, with the theorems those
requirements ask for proved by the Lean kernel (NFR-0023). Lean core only; no
Mathlib.

| Requirement | What is proved | Theorem |
|---|---|---|
| `tl check` reachability | The fixpoint the executable computes answers `true` exactly for the items that reach a root, on every graph, cycles and links to missing items included | `Graph.reachesB_iff` |
| `grounding_gap` | Its two findings (no grounding link; a chain that never reaches a root) together give exactly that verdict | `Graph.gapFree_iff` |
| SR-0211 | The guard accepts a change exactly when no live item stops reaching a root and no served delivery root stops being served | `guard_accepts_iff` |
| SR-0211 | Adding links is always accepted, which is why `add_link` carries no guard | `Graph.adding_accepted` |
| SR-0211 | Removing or adding links of a type that confers no grounding never changes a verdict | `Graph.nonground_change_preserves` |
| SR-0211 | Every item the refusal names reached the edited item before the change | `orphan_depends_on_source` |
| SR-0034 | Restamping clears a suspect link; an unstamped link or a missing target is never suspect | `restamp_clears`, `unstamped_not_suspect`, `absent_target_not_suspect` |
| SR-0034 | Editing a stamped target makes the link suspect, *provided the fingerprint tells contents apart* | `edit_makes_suspect` |

The last row is what proving brings out: SR-0034 holds only because sha256 does
not collide, and the theorem says so as a hypothesis instead of leaving it unsaid.

## A proof is about the model

The theorems speak about `Throughline/*.lean`, not about `src/throughline/`. Two
things tie them to the code that ships, and both run in CI (the `formal` job):

* `tests/test_formal_model.py` feeds 2000 generated graphs to the model's
  executable (`tl-formal`) and to `throughline.grounding`, and requires every
  verdict to match.
* The same test runs `Audit.lean` and requires each headline theorem to rest on
  `propext`, `Classical.choice` and `Quot.sound` alone, so a proof left as `sorry`
  fails.

Change `grounding.py`, `links.py` or the suspect check and this model has to change
with it, or the agreement test goes red.

## Running it

```bash
curl -sSfL https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh | sh -s -- -y --default-toolchain none
cd formal && lake build
cd .. && TL_FORMAL=1 pytest -q tests/test_formal_model.py
```

Without a built model the test skips; `TL_FORMAL=1` makes it fail instead.
