/-
Copyright (c) 2026 Henry J Grech-Cini
SPDX-License-Identifier: Apache-2.0
-/
import Throughline

/-!
The axioms each headline theorem rests on. A proof left as `sorry` shows here as
`sorryAx`; `tests/test_formal_model.py` requires every line to name only Lean's
three standard axioms (`propext`, `Classical.choice`, `Quot.sound`).
-/

#print axioms Throughline.Graph.reachesB_iff
#print axioms Throughline.Graph.gapFree_iff
#print axioms Throughline.guard_accepts_iff
#print axioms Throughline.Graph.adding_accepted
#print axioms Throughline.Graph.nonground_change_preserves
#print axioms Throughline.orphan_depends_on_source
#print axioms Throughline.restamp_clears
#print axioms Throughline.unchanged_not_suspect
#print axioms Throughline.unstamped_not_suspect
#print axioms Throughline.absent_target_not_suspect
#print axioms Throughline.edit_makes_suspect
#print axioms Throughline.suspect_iff
