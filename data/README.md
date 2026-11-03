# Archive layout

This checkout tracks only this guide and empty layout placeholders. Real recordings,
derived files, databases, keys, and exports are ignored and never bundled.

The selected archive is the **profile root**, containing `awear_data.sqlite3`, optional
`.env`, and profile state. The database still contains both recordings and human
annotations; this structural cleanup does not split or move it.

Within the selected profile:

```text
data/
  raw/
    cloud/export-*/         New API snapshots (JSON and CSV)
    bluetooth/session-*/    New original Bluetooth captures
  processed/
    patterns/              Derived pattern reports
    activity-backfill/     Backfill previews/backups/reports
```

Existing `data/export-*`, `data/bluetooth/session-*`, and `data/patterns/*` remain
readable in place. Readers search both layouts. New files go to the paths above;
explicit export `--data-dir` values still take precedence. No automatic move,
deletion, or rewriting of historical files occurs.

Do not treat the database, labels, or activity-backfill backups as disposable caches.
Delete derived output only when you understand how it can be reproduced. A future
schema migration may separate state, but that is not part of this cleanup.
