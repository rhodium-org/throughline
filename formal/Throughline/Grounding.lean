/-
Copyright (c) 2026 Henry J Grech-Cini
SPDX-License-Identifier: Apache-2.0
-/
import Throughline.Graph

/-!
# The grounding gate and the link guard (SR-0211)

Models of `grounding_gap`, `is_unserved`, `Index.with_edges` and `ungrounded_by`,
and the theorems that pin down what SR-0211 asks of them:

* `gapFree_iff` — the two-step `grounding_gap` (no grounding link at all, then a
  chain that never reaches a root) gives exactly the `Reaches` verdict.
* `guard_accepts_iff` — the guard lets a change through precisely when no live item
  that reached a root stops reaching one and no delivery root that was served stops
  being served: "judge this by the same reachability the gate applies".
* `adding_preserves` — adding links never ungrounds or unserves anything, which is
  why `add_link` carries no guard.
* `nonground_change_preserves` — removing or adding links of types that confer no
  grounding never changes a verdict.
* `orphan_depends_on_source` — every item the guard names as orphaned reached the
  edited item before the change, so the refusal never blames an item the change
  could not have touched.
-/

namespace Throughline

namespace Graph

variable (g : Graph)

/-- `grounding_gap(schema, idx, item) is None`, for a known item. -/
def gapFree (u : Uid) : Bool := g.isRoot u || (!(g.out u).isEmpty && g.reachesB u)

/-- `is_unserved(schema, idx, item)`. -/
def isUnserved (i : Item) : Bool := i.delivery && !g.served i.uid

/-- `idx.with_edges(src, remove=remove, add=add)`: every occurrence of a removed
`(target, type)` pair on `src` goes, then the added pairs are appended. -/
def withEdges (src : Uid) (remove add : List (Uid × String)) : Graph :=
  { g with edges := g.edges.filter (fun e => !(e.src == src && remove.contains (e.tgt, e.kind)))
                    ++ add.map fun p => ⟨src, p.1, p.2⟩ }

end Graph

/-- `ungrounded_by(...)[0]`: live items grounded before and not after. -/
def orphaned (before after : Graph) : List Uid :=
  (before.items.filter fun i =>
    !i.deleted && before.gapFree i.uid && !after.gapFree i.uid).map (·.uid)

/-- `ungrounded_by(...)[1]`: live delivery roots served before and not after. -/
def newlyUnserved (before after : Graph) : List Uid :=
  (before.items.filter fun i =>
    !i.deleted && !before.isUnserved i && after.isUnserved i).map (·.uid)

/-- `_refuse_ungrounding` lets the change through. -/
def accepts (before after : Graph) : Bool :=
  (orphaned before after).isEmpty && (newlyUnserved before after).isEmpty

namespace Graph

variable {g : Graph}

theorem gapFree_iff {u : Uid} (hk : g.known u = true) : g.gapFree u = true ↔ g.Reaches u := by
  simp only [gapFree, Bool.or_eq_true, Bool.and_eq_true, Bool.not_eq_true',
    List.isEmpty_eq_false_iff, reachesB_iff]
  constructor
  · rintro (hr | ⟨_, h⟩)
    · exact .here hk hr
    · exact h
  · intro h
    by_cases hr : g.isRoot u = true
    · exact .inl hr
    · refine .inr ⟨?_, h⟩
      cases h with
      | here _ hr' => exact absurd hr' hr
      | step _ hv _ => exact List.ne_nil_of_mem hv

/-- Reaching a root is preserved by any change that keeps the items and keeps
every grounding link of every item. -/
theorem Reaches.of_out_sub {g' : Graph} (hitems : g'.items = g.items)
    (hout : ∀ u v, v ∈ g.out u → v ∈ g'.out u) {u : Uid} (h : g.Reaches u) : g'.Reaches u := by
  have hfind : ∀ x, g'.find? x = g.find? x := by simp [find?, hitems]
  have hkn : ∀ x, g'.known x = g.known x := by simp [known, hfind]
  have hrt : ∀ x, g'.isRoot x = g.isRoot x := by simp [isRoot, hfind]
  induction h with
  | here hk hr => exact .here (by rw [hkn]; exact hk) (by rw [hrt]; exact hr)
  | step hk hv _ ih => exact .step (by rw [hkn]; exact hk) (hout _ _ hv) ih

theorem mem_out {u v : Uid} :
    v ∈ g.out u ↔ ∃ e ∈ g.edges, e.src = u ∧ e.tgt = v ∧ e.kind ∈ g.ground := by
  simp only [out, List.mem_map, List.mem_filter, Bool.and_eq_true, beq_iff_eq,
    List.contains_iff_mem]
  constructor
  · rintro ⟨e, ⟨he, hs, hk⟩, rfl⟩; exact ⟨e, he, hs, rfl, hk⟩
  · rintro ⟨e, he, hs, rfl, hk⟩; exact ⟨e, ⟨he, hs, hk⟩, rfl⟩

theorem served_iff {u : Uid} :
    g.served u = true ↔ ∃ e ∈ g.edges, e.tgt = u ∧ e.kind ∈ g.ground := by
  simp [served]

/-- Adding links never ungrounds an item (SR-0211: only removal and retyping are
guarded). -/
theorem adding_preserves_reaches {src : Uid} {add : List (Uid × String)} {u : Uid}
    (h : g.Reaches u) : (g.withEdges src [] add).Reaches u :=
  Reaches.of_out_sub (g := g) (g' := g.withEdges src [] add) rfl (fun _ _ hv => by
    obtain ⟨e, he, hs, ht, hk⟩ := mem_out.1 hv
    exact mem_out.2 ⟨e, by simp [withEdges, he], hs, ht, hk⟩) h

/-- Adding links never leaves a served delivery root unserved. -/
theorem adding_preserves_served {src : Uid} {add : List (Uid × String)} {u : Uid}
    (h : g.served u = true) : (g.withEdges src [] add).served u = true := by
  obtain ⟨e, he, ht, hk⟩ := served_iff.1 h
  exact served_iff.2 ⟨e, by simp [withEdges, he], ht, hk⟩

/-- So the guard always accepts an addition. -/
theorem adding_accepted {src : Uid} {add : List (Uid × String)} :
    accepts g (g.withEdges src [] add) = true := by
  have hk : ∀ i ∈ g.items, g.known i.uid = true := fun i hi =>
    known_iff.2 (List.mem_map_of_mem hi)
  have hk' : ∀ i ∈ g.items, (g.withEdges src [] add).known i.uid = true := fun i hi =>
    known_iff.2 (List.mem_map_of_mem (f := Item.uid) (by simpa [withEdges] using hi))
  simp only [accepts, orphaned, newlyUnserved, Bool.and_eq_true, List.isEmpty_iff,
    List.map_eq_nil_iff, List.filter_eq_nil_iff,
    Bool.not_eq_eq_eq_not, Bool.not_true]
  refine ⟨fun i hi => ?_, fun i hi => ?_⟩
  · by_cases hg : g.gapFree i.uid = true
    · have := (gapFree_iff (hk' i hi)).2 (adding_preserves_reaches ((gapFree_iff (hk i hi)).1 hg))
      simp [this]
    · simp_all
  · simp only [isUnserved, Bool.and_eq_true, Bool.not_eq_eq_eq_not,
      Bool.not_true, Bool.and_eq_false_iff]
    by_cases hs : g.served i.uid = true
    · have := adding_preserves_served (src := src) (add := add) hs
      simp_all
    · simp_all

/-- A change made only of links whose type confers no grounding keeps every
grounding link, in both directions. -/
theorem nonground_out {src : Uid} {remove add : List (Uid × String)}
    (hr : ∀ p ∈ remove, p.2 ∉ g.ground) (ha : ∀ p ∈ add, p.2 ∉ g.ground) (u v : Uid) :
    v ∈ (g.withEdges src remove add).out u ↔ v ∈ g.out u := by
  simp only [mem_out, withEdges, List.mem_append, List.mem_filter, List.mem_map,
    Bool.not_eq_true',
    Bool.eq_false_iff, ne_eq]
  constructor
  · rintro ⟨e, (⟨he, _⟩ | ⟨p, hp, rfl⟩), hs, ht, hk⟩
    · exact ⟨e, he, hs, ht, hk⟩
    · exact absurd hk (ha p hp)
  · rintro ⟨e, he, hs, ht, hk⟩
    refine ⟨e, .inl ⟨he, ?_⟩, hs, ht, hk⟩
    intro hc
    simp only [Bool.and_eq_true, beq_iff_eq, List.contains_iff_mem] at hc
    exact hr _ hc.2 hk

theorem nonground_change_preserves {src : Uid} {remove add : List (Uid × String)}
    (hr : ∀ p ∈ remove, p.2 ∉ g.ground) (ha : ∀ p ∈ add, p.2 ∉ g.ground) {u : Uid} :
    (g.withEdges src remove add).Reaches u ↔ g.Reaches u := by
  constructor
  · exact Reaches.of_out_sub (g := g.withEdges src remove add) (g' := g) rfl fun a b hv => (nonground_out hr ha a b).1 hv
  · exact Reaches.of_out_sub (g := g) (g' := g.withEdges src remove add) rfl
      fun a b hv => (nonground_out hr ha a b).2 hv

/-- `u` can walk to `w` over grounding links through known items. -/
inductive CanReach (g : Graph) : Uid → Uid → Prop
  | refl {u} : CanReach g u u
  | step {u v w} : g.known u = true → v ∈ g.out u → CanReach g v w → CanReach g u w

/-- A change to `src`'s links can only unground an item whose path ran through
`src`. -/
theorem reaches_of_not_canReach {src : Uid} {remove add : List (Uid × String)} {u : Uid}
    (hn : ¬ g.CanReach u src) (h : g.Reaches u) : (g.withEdges src remove add).Reaches u := by
  have hfind : ∀ x, (g.withEdges src remove add).find? x = g.find? x := by
    simp [find?, withEdges]
  have hkn : ∀ x, (g.withEdges src remove add).known x = g.known x := by simp [known, hfind]
  have hrt : ∀ x, (g.withEdges src remove add).isRoot x = g.isRoot x := by simp [isRoot, hfind]
  induction h with
  | here hk hr => exact .here (by rw [hkn]; exact hk) (by rw [hrt]; exact hr)
  | @step a b hk hv _ ih =>
    have hne : a ≠ src := fun he => hn (he ▸ .refl)
    refine .step (by rw [hkn]; exact hk) ?_ (ih fun hc => hn (.step hk hv hc))
    obtain ⟨e, he, hs, ht, hkd⟩ := mem_out.1 hv
    refine mem_out.2 ⟨e, ?_, hs, ht, hkd⟩
    simp only [withEdges, List.mem_append, List.mem_filter]
    exact .inl ⟨he, by simp [hs, hne]⟩

end Graph

/-- The guard accepts a change exactly when the gate's verdict gets no worse for any
live item: everything that reached a root still does, and every delivery root that
was served still is (SR-0211). -/
theorem guard_accepts_iff {before : Graph} {src : Uid} {remove add : List (Uid × String)} :
    accepts before (before.withEdges src remove add) = true ↔
      ∀ i ∈ before.items, i.deleted = false →
        (before.Reaches i.uid → (before.withEdges src remove add).Reaches i.uid) ∧
        (before.isUnserved i = false → (before.withEdges src remove add).isUnserved i = false) := by
  have hk : ∀ i ∈ before.items, before.known i.uid = true := fun i hi =>
    Graph.known_iff.2 (List.mem_map_of_mem hi)
  have hk' : ∀ i ∈ before.items, (before.withEdges src remove add).known i.uid = true :=
    fun i hi => Graph.known_iff.2 (List.mem_map_of_mem (f := Item.uid)
      (by simpa [Graph.withEdges] using hi))
  simp only [accepts, orphaned, newlyUnserved, Bool.and_eq_true, List.isEmpty_iff,
    List.map_eq_nil_iff, List.filter_eq_nil_iff]
  constructor
  · rintro ⟨ho, hu⟩ i hi hd
    refine ⟨fun hr => ?_, fun hs => ?_⟩
    · have := ho i hi
      rw [Graph.gapFree_iff (hk i hi) |>.2 hr] at this
      simp only [hd, Bool.not_false, Bool.not_eq_true']
        at this
      exact (Graph.gapFree_iff (hk' i hi)).1 (by simpa using this)
    · have := hu i hi
      simp_all
  · intro h
    refine ⟨fun i hi => ?_, fun i hi => ?_⟩
    · cases hd : i.deleted
      · have ⟨hr, _⟩ := h i hi hd
        by_cases hg : before.gapFree i.uid = true
        · have := (Graph.gapFree_iff (hk' i hi)).2 (hr ((Graph.gapFree_iff (hk i hi)).1 hg))
          simp [this]
        · simp_all
      · simp
    · cases hd : i.deleted
      · have ⟨_, hs⟩ := h i hi hd
        cases hb : before.isUnserved i <;> simp_all
      · simp

/-- Every item the guard names as orphaned reached the edited item before the
change: the refusal never blames an item the change could not have touched. -/
theorem orphan_depends_on_source {before : Graph} {src : Uid} {remove add : List (Uid × String)}
    {u : Uid} (h : u ∈ orphaned before (before.withEdges src remove add)) :
    before.CanReach u src := by
  simp only [orphaned, List.mem_map, List.mem_filter] at h
  obtain ⟨i, ⟨hi, hp⟩, rfl⟩ := h
  simp only [Bool.and_eq_true, Bool.not_eq_true'] at hp
  obtain ⟨⟨_, hb⟩, ha⟩ := hp
  have hk : before.known i.uid = true := Graph.known_iff.2 (List.mem_map_of_mem hi)
  have hk' : (before.withEdges src remove add).known i.uid = true :=
    Graph.known_iff.2 (List.mem_map_of_mem (f := Item.uid) (by simpa [Graph.withEdges] using hi))
  apply Classical.byContradiction
  intro hn
  have := (Graph.gapFree_iff hk').2
    (Graph.reaches_of_not_canReach hn ((Graph.gapFree_iff hk).1 hb))
  simp [this] at ha

end Throughline
