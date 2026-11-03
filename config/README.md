# Configuration

`requirements/` owns dependency definitions. Root `requirements-*.txt` files remain
compatible pip entry points, including the existing build scripts and CI.

For source development, copy `development.example.json` to
`development.local.json` and set `data_directory` to an absolute archive path.
The local file is ignored by Git. It accepts only `data_directory`; API keys belong
in the archive's `.env` or the existing credential store.

Run a command through `python scripts/run-local.py COMMAND [arguments]`.
Available commands are `desktop`, `app`, `export`, `patterns`, `import`,
`activity-backfill`, and `device-backfill`. Select a different archive with
`python scripts/run-local.py --profile /absolute/archive COMMAND [arguments]`.

Profile selection order is `--profile`, `AWEAR_DATA_DIR`, local configuration,
then the command's existing defaults. The direct `src/*.py` entry points retain
their existing behavior; set `AWEAR_DATA_DIR` when using them against another archive.

Packaged apps never load this source-only configuration. They retain the existing
per-user profile paths, saved key identity, and first-run setup.
