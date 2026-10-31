/-
Copyright (c) 2026 Henry J Grech-Cini
SPDX-License-Identifier: Apache-2.0
-/
import Throughline.Graph

/-!
# Suspect links (SR-0034)

"Each link shall store the target's fingerprint when last confirmed; when stored and
current fingerprints differ, the Tool reports the link suspect and provides a command
to re-confirm (re-stamp) links after review."

`fp u` is the target's current fingerprint, `none` when the target is external or
not in the graph, as `validate.py` skips both. A fingerprint is a hash of content,
and `edit_makes_suspect` states plainly what the requirement silently relies on:
that the hash tells different contents apart. With sha256 that is a collision
assumption, made explicit here as the hypothesis `hinj`.
-/

namespace Throughline

structure Link where
  target : Uid
  /-- The target's fingerprint when last confirmed; `none` when never stamped. -/
  stamp : Option String
deriving Repr, DecidableEq

/-- `validate.py`: a stamped link to a present target whose fingerprint has moved. -/
def suspect (fp : Uid → Option String) (l : Link) : Bool :=
  match l.stamp, fp l.target with
  | some s, some f => s != f
  | _, _ => false

/-- `add_link(..., stamp=True)`: the stamp becomes the target's current fingerprint. -/
def restamp (fp : Uid → Option String) (l : Link) : Link := { l with stamp := fp l.target }

theorem restamp_clears (fp : Uid → Option String) (l : Link) :
    suspect fp (restamp fp l) = false := by
  simp only [suspect, restamp]
  cases fp l.target <;> simp

theorem unchanged_not_suspect {fp : Uid → Option String} {l : Link}
    (h : l.stamp = fp l.target) : suspect fp l = false := by
  simp only [suspect, h]
  cases fp l.target <;> simp

theorem unstamped_not_suspect {fp : Uid → Option String} {l : Link}
    (h : l.stamp = none) : suspect fp l = false := by
  simp [suspect, h]

theorem absent_target_not_suspect {fp : Uid → Option String} {l : Link}
    (h : fp l.target = none) : suspect fp l = false := by
  simp only [suspect, h]
  cases l.stamp <;> rfl

/-- A link stamped against one content of its target turns suspect when the
target's content changes, provided the hash tells contents apart. -/
theorem edit_makes_suspect {C : Type} (hash : C → String)
    (hinj : ∀ a b, hash a = hash b → a = b)
    (before after : Uid → C) (l : Link)
    (hstamped : l.stamp = some (hash (before l.target)))
    (hchanged : after l.target ≠ before l.target) :
    suspect (fun u => some (hash (after u))) l = true := by
  simp only [suspect, hstamped, bne_iff_ne, ne_eq]
  exact fun h => hchanged (hinj _ _ h.symm)

/-- A stamped link to a present target is suspect exactly when the two fingerprints
differ. -/
theorem suspect_iff {fp : Uid → Option String} {l : Link} {s f : String}
    (hs : l.stamp = some s) (hf : fp l.target = some f) : suspect fp l = true ↔ s ≠ f := by
  simp [suspect, hs, hf]

end Throughline
