/-
Copyright (c) 2026 Henry J Grech-Cini
SPDX-License-Identifier: Apache-2.0
-/

/-!
# The graph `tl check` grounds over

A model of `throughline.graph.Index` as `grounding.py` uses it. Only what grounding
reads is kept: which items exist, which are roots and delivery roots (the answer
`schema.is_root` and `schema.delivery_roots` give for the item's type), which are
deleted, and the typed edges between them.

`Reaches` is the specification: an item reaches a root when it is a known root, or
when it is known and one of its grounding links leads to an item that reaches a root.
`reachesB` is the decision procedure the executable runs, and `reachesB_iff` proves
the two agree on every graph, cycles and dangling targets included. That theorem is
what lets a proof stated over `Reaches` speak about the answer the executable gives,
and the differential test (`tests/test_formal_model.py`) ties that answer to Python.
-/

namespace Throughline

abbrev Uid := String

structure Item where
  uid : Uid
  /-- `schema.is_root(item)`. -/
  root : Bool
  /-- `item.type in schema.delivery_roots`. -/
  delivery : Bool
  /-- `item.is_deleted`. -/
  deleted : Bool := false
deriving Repr, DecidableEq

/-- A link `src --kind--> tgt`, stored on `src`. -/
structure Edge where
  src : Uid
  tgt : Uid
  kind : String
deriving Repr, DecidableEq

structure Graph where
  items : List Item
  edges : List Edge
  /-- `schema.ground_link_types`. -/
  ground : List String

namespace Graph

variable (g : Graph)

def find? (u : Uid) : Option Item := g.items.find? (·.uid == u)

/-- `idx.items.get(u) is not None`. -/
def known (u : Uid) : Bool := (g.find? u).isSome

def isRoot (u : Uid) : Bool := match g.find? u with
  | some i => i.root
  | none => false

def uids : List Uid := g.items.map (·.uid)

/-- `idx.out_links(u, schema.ground_link_types)`, targets only. -/
def out (u : Uid) : List Uid :=
  (g.edges.filter fun e => e.src == u && g.ground.contains e.kind).map (·.tgt)

/-- `idx.in_links(u, schema.ground_link_types)` is non-empty. -/
def served (u : Uid) : Bool :=
  g.edges.any fun e => e.tgt == u && g.ground.contains e.kind

/-- The specification of `Index.reaches(u, schema.is_root, ground_link_types)`. -/
inductive Reaches : Uid → Prop
  | here {u} : g.known u = true → g.isRoot u = true → Reaches u
  | step {u v} : g.known u = true → v ∈ g.out u → Reaches v → Reaches u

/-- `Reaches` along a chain of at most `k` grounding links. -/
inductive ReachesIn : Nat → Uid → Prop
  | here {k u} : g.known u = true → g.isRoot u = true → ReachesIn k u
  | step {k u v} : g.known u = true → v ∈ g.out u → ReachesIn k v → ReachesIn (k + 1) u

/-- The items that reach a root along at most `k` grounding links. -/
def level : Nat → List Uid
  | 0 => g.uids.filter g.isRoot
  | k + 1 =>
    let prev := level k
    g.uids.filter fun u => g.isRoot u || (g.out u).any prev.contains

/-- The decision procedure: `level` at the number of items is the least fixpoint. -/
def reachesB (u : Uid) : Bool := (g.level g.uids.length).contains u

/-! ## Basic facts -/

variable {g}

theorem known_iff {u : Uid} : g.known u = true ↔ u ∈ g.uids := by
  simp [known, find?, uids, List.find?_isSome]

theorem ReachesIn.mono {k u} (h : g.ReachesIn k u) : ∀ {m}, k ≤ m → g.ReachesIn m u := by
  induction h with
  | here hk hr => intro m _; exact .here hk hr
  | step hk hv _ ih =>
    intro m hm
    obtain ⟨m', rfl⟩ : ∃ m', m = m' + 1 := ⟨m - 1, by omega⟩
    exact .step hk hv (ih (by omega))

theorem ReachesIn.toReaches {k u} (h : g.ReachesIn k u) : g.Reaches u := by
  induction h with
  | here hk hr => exact .here hk hr
  | step hk hv _ ih => exact .step hk hv ih

theorem Reaches.toReachesIn {u} (h : g.Reaches u) : ∃ k, g.ReachesIn k u := by
  induction h with
  | here hk hr => exact ⟨0, .here hk hr⟩
  | step hk hv _ ih => obtain ⟨k, hk'⟩ := ih; exact ⟨k + 1, .step hk hv hk'⟩

theorem mem_level {k : Nat} {u : Uid} : u ∈ g.level k ↔ g.ReachesIn k u := by
  induction k generalizing u with
  | zero =>
    simp only [level, List.mem_filter, ← known_iff]
    constructor
    · rintro ⟨hk, hr⟩; exact .here hk hr
    · intro h; cases h with
      | here hk hr => exact ⟨hk, hr⟩
  | succ k ih =>
    simp only [level, List.mem_filter, ← known_iff, Bool.or_eq_true, List.any_eq_true,
      List.contains_iff_mem, ih]
    constructor
    · rintro ⟨hk, hr | ⟨v, hv, hr⟩⟩
      · exact .here hk hr
      · exact .step hk hv hr
    · intro h; cases h with
      | here hk hr => exact ⟨hk, .inl hr⟩
      | step hk hv hr => exact ⟨hk, .inr ⟨_, hv, hr⟩⟩

theorem level_mono {k m : Nat} (h : k ≤ m) {u : Uid} (hu : u ∈ g.level k) : u ∈ g.level m :=
  mem_level.2 ((mem_level.1 hu).mono h)

theorem level_sub_uids {k : Nat} {u : Uid} (hu : u ∈ g.level k) : u ∈ g.uids := by
  cases k <;> simp only [level, List.mem_filter] at hu <;> exact hu.1

/-! ## The fixpoint is reached within as many rounds as there are items -/

/-- No round after `j` adds anything. -/
def Stable (g : Graph) (j : Nat) : Prop := ∀ u, u ∈ g.level (j + 1) → u ∈ g.level j

private theorem Stable.forever_add {j : Nat} (hs : g.Stable j) :
    ∀ d u, u ∈ g.level (j + d) → u ∈ g.level j := by
  intro d
  induction d with
  | zero => exact fun _ h => h
  | succ d ih =>
    intro u hu
    apply hs u
    rw [← Nat.add_assoc] at hu
    simp only [level, List.mem_filter, Bool.or_eq_true, List.any_eq_true,
      List.contains_iff_mem] at hu ⊢
    obtain ⟨hk, hr | ⟨v, hv, hl⟩⟩ := hu
    · exact ⟨hk, .inl hr⟩
    · exact ⟨hk, .inr ⟨v, hv, ih v hl⟩⟩

theorem Stable.forever {j : Nat} (hs : g.Stable j) :
    ∀ m, j ≤ m → ∀ u, u ∈ g.level m → u ∈ g.level j := by
  intro m hm u hu
  obtain ⟨d, rfl⟩ : ∃ d, m = j + d := ⟨m - j, by omega⟩
  exact hs.forever_add d u hu

private theorem length_filter_le_of_imp {α} {l : List α} {p q : α → Bool}
    (h : ∀ x ∈ l, p x = true → q x = true) : (l.filter p).length ≤ (l.filter q).length := by
  induction l with
  | nil => simp
  | cons a l ih =>
    have ih := ih fun x hx => h x (List.mem_cons_of_mem a hx)
    have ha := h a List.mem_cons_self
    cases hp : p a <;> cases hq : q a <;> simp_all <;> omega

private theorem length_filter_lt {α} {l : List α} {p q : α → Bool}
    (h : ∀ x ∈ l, p x = true → q x = true) (hx : ∃ x ∈ l, q x = true ∧ p x = false) :
    (l.filter p).length < (l.filter q).length := by
  induction l with
  | nil => simp at hx
  | cons a l ih =>
    have hle := length_filter_le_of_imp fun x hx => h x (List.mem_cons_of_mem a hx)
    have ha := h a List.mem_cons_self
    obtain ⟨x, hxl, hqx, hpx⟩ := hx
    rcases List.mem_cons.1 hxl with rfl | hxl
    · simp [hqx, hpx]; omega
    · have := ih (fun y hy => h y (List.mem_cons_of_mem a hy)) ⟨x, hxl, hqx, hpx⟩
      cases hp : p a <;> cases hq : q a <;> simp_all <;> omega

/-- How many items are in round `k`. -/
private def count (g : Graph) (k : Nat) : Nat := (g.uids.filter (g.level k).contains).length

private theorem count_grows {k : Nat} (hns : ¬ g.Stable k) : count g k < count g (k + 1) := by
  unfold Stable at hns
  obtain ⟨u, hu⟩ := Classical.not_forall.1 hns
  have ⟨hin, hout⟩ : u ∈ g.level (k + 1) ∧ u ∉ g.level k := by
    by_cases h : u ∈ g.level (k + 1)
    · exact ⟨h, fun h' => hu fun _ => h'⟩
    · exact absurd (fun h' => absurd h' h) hu
  apply length_filter_lt
  · intro x _ hx
    simp only [List.contains_iff_mem] at hx ⊢
    exact level_mono (Nat.le_succ k) hx
  · exact ⟨u, level_sub_uids hin, by simpa using hin, by simpa using hout⟩

private theorem stable_or_count (k : Nat) : (∃ j < k, g.Stable j) ∨ k ≤ count g k := by
  induction k with
  | zero => exact .inr (Nat.zero_le _)
  | succ k ih =>
    rcases ih with ⟨j, hj, hs⟩ | hc
    · exact .inl ⟨j, by omega, hs⟩
    · by_cases hs : g.Stable k
      · exact .inl ⟨k, by omega, hs⟩
      · exact .inr (by have := count_grows hs; omega)

theorem exists_stable : ∃ j ≤ g.uids.length, g.Stable j := by
  rcases stable_or_count (g := g) (g.uids.length + 1) with ⟨j, hj, hs⟩ | hc
  · exact ⟨j, by omega, hs⟩
  · have : count g (g.uids.length + 1) ≤ g.uids.length := List.length_filter_le _ _
    omega

/-- The decision procedure is exact: on every graph, cycles and links to unknown
targets included, it answers `true` precisely for the items that reach a root. -/
theorem reachesB_iff {u : Uid} : g.reachesB u = true ↔ g.Reaches u := by
  simp only [reachesB, List.contains_iff_mem]
  constructor
  · intro h; exact (mem_level.1 h).toReaches
  · intro h
    obtain ⟨k, hk⟩ := h.toReachesIn
    have hu := mem_level.2 hk
    by_cases hkn : k ≤ g.uids.length
    · exact level_mono hkn hu
    · obtain ⟨j, hj, hs⟩ := exists_stable (g := g)
      exact level_mono hj (hs.forever k (by omega) u hu)

end Graph

end Throughline
