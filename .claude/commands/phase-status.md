# /phase-status

Show which CodeCortex phases are complete and their latest benchmark scores.

## What to do

1. Read `docs/results.md` and extract every benchmark entry (each `## Phases` header block).
2. Read `PHASE_1_ARCHITECTURE.md` for the planned phase list.
3. Present a table:

   | Phase | Description | Status | Composite Score | Date |
   |-------|-------------|--------|-----------------|------|
   | 1 | Core refactor | ✅ | — | — |
   | 2 | Semantic indexing | ✅ | — | — |
   | ... | ... | ... | latest score | latest run date |

4. Highlight the most recent composite score prominently.
5. If any phase shows a regression vs its previous run, flag it.

## Notes

- Phases 1–8 were all completed in the initial session (2026-05-22).
- Subsequent phases will be labeled in `docs/results.md` under their own `## Phase N` sections.
- Source of truth for planned phases: `docs/code_review_graph_rewrite_plan.md` and `PHASE_1_ARCHITECTURE.md`.
