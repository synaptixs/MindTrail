# Phase 2 acceptance: personal activities and episode review

Phase 2 covers source/cloud review and stdlib annotation workflows. It does not establish
physiological validity, activity-recognition accuracy or full packaged numerical analysis.
P1's source/cloud scope and Phase 5's numerical dependency/release work remain unchanged.

## Software evidence and owner gate

| Criterion | Software evidence | Owner acceptance |
| --- | --- | --- |
| P2-A1 Personal vocabulary | Distinct participant IDs/definitions/assignments, same text, empty vocabulary, create-and-label and archive fixtures | Confirm names/context fit the individual's activities |
| P2-A2 Identity/history | v1/v2 fixtures; backed-up rollback/retry; old table hashes unchanged; complete exports; scoped stale writes; immutable history and operation provenance | Review a rename/archive and audit export |
| P2-A3 Precise/reversible editing | Exact milliseconds, explicit DST choices, split/undo/reverse-undo, restored IDs/timing, conflict and ABA guards, imported-ledger undo | Select → label → edit → split → undo → export walkthrough |
| P2-A4 Arrival preservation | Existing pinned/unpinned refresh fixtures; stale candidates withheld; no candidate auto-writes; unknown spans protected | Confirm selected periods and saved labels stay fixed during refresh |
| P2-A5 Useful review | Bounded counts/reasons, interleaved ordinary coverage, blind entry, separate unknown/rejected states, keyboard and narrow-screen software walkthrough | Review usefulness against personally annotated examples |

The Phase 2 gate remains **pending owner walkthrough and candidate-usefulness decision**.
Passing tests or merging the PR does not substitute for that review. Candidate detection
stays opt-in/off by default; keeping it disabled is a valid outcome if it adds little value.
Legacy labels with unknown timing cannot establish boundary accuracy by themselves.

## Owner walkthrough

Use a backup or a disposable copy if experimenting with personal labels.

1. Open **Episode review** with cues off. Review an ordinary period, enter your own activity
   and check the displayed New York start/end. Save, edit, split at a known time, then undo.
   Verify the original label, timing, ID and unrelated intervals are retained.
2. Mark a period **Activity unknown**, inspect its separate count/lane/comparison, and undo.
   Reveal an existing suggestion, confirm or reject it explicitly, then inspect history.
3. Export activities/history JSON and current assignments CSV. Compare the chosen interval,
   source, timing, decisions, original names and reversible operation with the screen.
4. During a refresh/new-data arrival, verify a pinned daily range and saved human periods
   do not move. Use keyboard controls and a narrow window for the same essential actions.
5. Optionally run candidate detection and compare PELT/baseline around personally annotated
   boundaries. Record helpful/unhelpful examples and timing uncertainty. Choose whether
   to keep proposals optional/off; do not treat signal changes as known activities.

Record the owner's outcome in the external development plan. Begin Phase 3 only after
this gate is explicitly resolved, or after an explicit scope revision/defer decision.

## Preservation and performance protocol

W5 checks a copied frozen v2 archive, never the live profile. Before/after digests cover
readings, labels, activities, definition history and assignment history. Original source
file bytes are checked separately. Predeclared ceilings: 30 seconds for migration including
backup, 5 seconds for full-scope queue generation. Repeating the queue must give identical
output. Exact private evidence stays outside this public repository; aggregate results
belong in the delivery PR. Synthetic fixtures exercise mutations and conflict/restore
paths. Native Windows validation checks the bundled review assets, stdlib split/undo,
unknown decisions and ledger preservation through installation/upgrade/uninstall.
