"""Artha advisor evaluation harness.

Slice 1 ships only the deterministic number-verifier (number_check) — Artha's unfair
advantage: because the app OWNS the numbers, every ₹ figure an answer cites can be
recomputed from the DB and any hallucination (or gross-pool-as-free claim) fails a gate.
The full harness (runner/gates/cli/judge/cases) lands in Slice 3.
"""
