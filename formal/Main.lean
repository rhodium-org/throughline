/-
Copyright (c) 2026 Henry J Grech-Cini
SPDX-License-Identifier: Apache-2.0
-/
import Throughline

/-!
# `tl-formal`: the proved model, runnable

Reads a graph and one link change on stdin, one record per line, fields separated
by spaces:

    ground <link type>
    item <uid> <root 0|1> <delivery 0|1> <deleted 0|1>
    edge <src> <tgt> <link type>
    change <src>
    remove <tgt> <link type>
    add <tgt> <link type>

and writes the model's verdicts, one per line:

    grounded <uid>       grounding_gap is None before the change
    unserved <uid>       is_unserved before the change
    orphaned <uid>       ungrounded_by(...)[0]
    newunserved <uid>    ungrounded_by(...)[1]

`tests/test_formal_model.py` feeds the same random graphs to this and to
`throughline.grounding` and requires the answers to match.
-/

open Throughline

structure Input where
  graph : Graph := ⟨[], [], []⟩
  src : Uid := ""
  remove : List (Uid × String) := []
  add : List (Uid × String) := []

def flag : String → Except String Bool
  | "0" => pure false
  | "1" => pure true
  | s => throw s!"expected 0 or 1, got '{s}'"

def record (inp : Input) (line : String) : Except String Input :=
  let g := inp.graph
  match (line.splitOn " ").filter (· ≠ "") with
  | [] => pure inp
  | ["ground", k] => pure { inp with graph := { g with ground := g.ground ++ [k] } }
  | ["item", u, r, d, x] => do
    let i : Item := ⟨u, ← flag r, ← flag d, ← flag x⟩
    pure { inp with graph := { g with items := g.items ++ [i] } }
  | ["edge", s, t, k] => pure { inp with graph := { g with edges := g.edges ++ [⟨s, t, k⟩] } }
  | ["change", s] => pure { inp with src := s }
  | ["remove", t, k] => pure { inp with remove := inp.remove ++ [(t, k)] }
  | ["add", t, k] => pure { inp with add := inp.add ++ [(t, k)] }
  | _ => throw s!"unreadable line: '{line}'"

def main : IO UInt32 := do
  let text ← (← IO.getStdin).readToEnd
  match (text.splitOn "\n").foldlM record {} with
  | .error msg =>
    IO.eprintln s!"tl-formal: {msg}"
    return 2
  | .ok inp =>
    let before := inp.graph
    let after := before.withEdges inp.src inp.remove inp.add
    let out ← IO.getStdout
    for i in before.items do
      if before.gapFree i.uid then out.putStrLn s!"grounded {i.uid}"
      if before.isUnserved i then out.putStrLn s!"unserved {i.uid}"
    for u in orphaned before after do out.putStrLn s!"orphaned {u}"
    for u in newlyUnserved before after do out.putStrLn s!"newunserved {u}"
    return 0
