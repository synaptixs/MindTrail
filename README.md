# MindTrail Lab

A local dashboard for your own AWEAR EEG recordings. It polls the documented REST API, stores raw one-second waveform records in SQLite, and lets you label live activities or past time intervals. The dashboard charts centered signal amplitude and compares labeled periods. It is exploratory signal analysis, not a measure of focus, mood, or health.


## Desktop app and installers (Mac and Windows)

The desktop app includes its Python runtime and dependencies. The Windows setup installs automatically and opens the dashboard; the universal Mac DMG contains **Install MindTrail Lab**, which installs to your Applications folder and opens it. First run is **enter API key → Connect**. The main setup screen includes a five-step beginner guide to getting an API key, with AWEAR’s email/Continue sign-in steps, key creation, copy-and-return instructions, and a suggested support request for personal-data access. It opens automatically when no key is configured and stays available as an expandable guide after a key is saved. The app selects the account and finds the earliest recording date automatically. Participant/date controls, Bluetooth and archive restore are available under **Advanced settings**.

The key is saved in macOS Keychain or Windows Credential Manager. If secure storage is unavailable or denied, the key works for the running session and the dashboard explains that it must be entered again after quitting. A key is never saved to plaintext configuration by the desktop app. The source CLI still supports its existing `.env` and hidden key prompt; the desktop launcher does not automatically read the project `.env`.

Desktop data is separate from installed files. New installations use: `~/Library/Application Support/MindTrail Lab/` on Mac, `%LOCALAPPDATA%\MindTrail Lab\` on Windows. Existing installations automatically reuse their original `AWEAR Personal Lab` profile when no MindTrail Lab profile exists; the vault identifiers stay stable so saved keys remain accessible. The database is `awear_data.sqlite3`; session files, exports and reports live under `data/`. Existing source data is not moved automatically. To restore it, stop the old dashboard and use **Advanced settings → Restore an existing archive** before connecting in an empty desktop profile. Copy the old `data/` directory separately if you also want Bluetooth sessions, saved exports and pattern reports.

**Settings & quit** in Overview changes setup or stops MindTrail Lab. Closing the browser tab keeps sync running. Open the app again to return to the same workspace. Each desktop profile has one running instance and an automatically chosen local port. Upgrades stop the old instance gracefully and preserve data; Windows uninstall also preserves the archive. Existing offline pattern reports are readable, but the optional clustering dependencies are not bundled.

[Installer build, signing and verification instructions](installer/README.md) describe both platforms and the simple end-user flow. Files containing **unsigned** in the name are development builds; production distribution requires signing/notarization and native Windows validation. A universal Mac binary contains both Intel and Apple Silicon code; Intel execution still needs a target-machine check. Automatic update delivery remains a future feature.

For source development, create `.venv-desktop`, install `requirements-desktop.txt`, then run `desktop.py`. The existing `Launch AWEAR.command` and `Launch AWEAR.cmd` shortcuts use that environment. These source shortcuts are developer conveniences; end users use the self-contained installers. Use `desktop.py --data-dir PATH --no-key-store --no-browser` for isolated tests. `AWEAR_DATA_DIR` sets the writable profile for command-line exports and analysis; explicit desktop `--data-dir` takes precedence.


## Start

Requires Python 3.10 or newer. No packages or build step are needed.

```bash
cd MindTrail
python3 app.py
```

First, open the local `.env` file in an editor and paste your key after `AWEAR_API_KEY=`. The file is ignored by Git and created with owner-only permissions. Restart the server after changing the key. Open **http://127.0.0.1:8765**. The key is read by the local Python process and is never sent to the browser. A nonempty `AWEAR_API_KEY` already set in your shell takes precedence over `.env`.

You can still use `python3 app.py --key-prompt` to enter the key at a hidden prompt instead of storing it in `.env`. Run that command with **nothing after `--key-prompt`**; the prompt overrides the environment and `.env`. For the source CLI, enter the key only at its prompt or in your private `.env`. Do not paste keys into chat, a repository, or a command-line argument. The desktop app provides its own API key field.

Choose the participant ID returned by your key and set the earliest time to sync. The initial backfill may take time for long histories. The server polls every 30 seconds while it is running; the page refreshes every 10 seconds. This is near-real-time polling of uploaded data, not a direct Bluetooth stream. The age of the latest record is visible on the dashboard.

Use **Source** to select another participant ID or move the sync start earlier. Previously stored records and labels remain in the local database.

After Python code changes, stop the running server with Ctrl+C and restart it on the same port (for example, `python3 app.py --port 8765`). Refreshing the browser alone updates the interface but leaves the running Python code unchanged. Each dashboard port has its own server process; restart the port you actually use.

For a clearly marked sample view without an API key, run:

```bash
python3 app.py --demo
```

Demo data is stored separately in `awear_demo.sqlite3`. Real recordings and labels are stored in `awear_data.sqlite3` next to this file. Back up that database if you want to retain your archive. Both database files are excluded by `.gitignore`.

## Labels and exports

- **Amplitude over time:** opens on the latest seven days. Drag the vertical left and right handles overlaid directly on the amplitude plot. The selection is highlighted on that same plot while dragging. Release to zoom the plot and load finer detail; the handles then sit at the edges of the zoomed view. Use the reset button to return to seven days. Expand **Enter exact start and end times** for precise local time entry. The handles also support arrow keys (one second), Shift + arrow (one minute), Page Up/Down (one hour), and Home/End. The selected range stays pinned during refresh; summary cards, activity comparisons and the raw export follow it. **Show full 7 days** returns to the moving seven-day view. Narrow ranges are re-aggregated down to one-second buckets rather than stretching the week-level averages. Hover a point to see its time span, AC RMS and record count; use **Inspect EEG at start** to examine raw samples.
- **Live activity:** enter an activity name, press **Start activity**, then **Stop activity** when done.
- **Past interval:** enter start and end times in your computer's local timezone. Confirmed activities cannot overlap. A new or edited activity can replace an unreviewed schedule suggestion; the suggested work periods before and after it are retained. This lets you add a five-minute relaxing break without deleting a whole work period. Click **Edit** directly beside any label in **Recorded 30-minute periods**, adjust its name, note or exact times, then **Save changes**. Saving a suggestion marks it reviewed; **Cancel editing** leaves it unchanged. **Delete activity** is available while editing and asks for confirmation.
- **Daily recordings & activities:** one amplitude graph and recording coverage strip per New York calendar day, from the first stored second through the end of the last stored second. The graph includes the entire day even when a half-hour pagination boundary falls in its middle. Every dashboard refresh (10 seconds while open) rechecks the day: newly uploaded later records extend the end, and delayed earlier records move the start back. No 08:00–16:00 or whole-half-hour bounds are imposed. Gaps and device changes stay separate. AC RMS averages use adaptive multiples of ten seconds, shown below each graph, and each day displays its own amplitude scale. Drag the vertical start/end handles overlaid on each graph, then release to zoom; the selected time range is shown exactly beneath it. Zooms are re-aggregated from stored seconds down to one-second detail, rather than enlarging the daily averages. Each day keeps its own selected range through refresh and pagination. **Show entire day** clears its zoom and resumes following newly uploaded records. Arrow keys move a handle by one second, Shift + arrow by one minute, Page Up/Down by one hour, and Home/End move to its bounds. **Assign activity to selection** prefills the past-interval form with the exact range; enter a name and save. Unreviewed work suggestions are split around it, while overlapping confirmed activities remain protected. Hover for exact time, amplitude and record count; click a recorded point to inspect EEG. Activity shading remains independent of raw recordings. Expand a day's **half-hour activity periods** to assign, edit or delete activities; open groups remain open on refresh. Counts identify loaded half-hour rows, and **Load older periods** reveals more rows/days. Daily graphs always include that day's full archive, not just loaded activity rows. The coverage strip assumes each timestamp starts its one-second record. Its final edge includes the complete last second, rather than extending to the next half-hour or to the present clock time.
- **EEG signal:** choose **View EEG** from a recorded period, or open **EEG signal** in the sidebar. Inspect 10-, 30-, or 60-second windows, step forward or backward, or follow the latest recording. The page shows a centered waveform, a 1–40 Hz spectrogram, average power spectrum, relative band shares, activity labels, and recording gaps. Spectral estimates use 4-second Hann windows with 2-second steps and never join records across a gap or device change. The latest view refreshes every 10 seconds, following the same uploaded recordings as the dashboard rather than a direct Bluetooth stream.
- **Mobile scores:** manually enter the AWEAR mobile app's score (0–100), displayed state (Alert, Engaged, Relaxed, or Calm), and observation time. **Timestamp at Save** captures a live reading's time when you save; uncheck it to enter a past time. Editing preserves the original timestamp unless you deliberately change it. Time entry uses your computer's timezone and is saved as UTC, separately from the time you entered or edited the observation. Optional timing uncertainty, app version, and notes preserve context. States are transcribed, never inferred from score ranges. Browse, filter, edit, delete, and export every observation in the selected range as CSV or JSON. Each observation shows its current activity label and whether a stored one-second EEG record covers that time; gaps remain unmatched. **View aligned window** opens the EEG viewer with a purple observation marker, activity shading, and raw EEG. You can also prefill an observation time from the EEG viewer. The app's score averaging window and processing delay are unknown, so matching timestamps does not establish sample-level or physiological equivalence. A point observation does not label an entire surrounding period. These manual entries stay in SQLite and remain independent of API exports and raw waveforms.
- **Signal patterns:** run the optional local analysis below, then open **Signal patterns** in the sidebar. It shows relative quality flags, an exploratory two-group Gaussian mixture clustering, a K-means comparison, pattern mixes by 30-minute period, and every analyzed four-second window for a selected period. Use **Assign activity** to prefill the existing interval-label form; review the EEG before saving a label. The pattern report is derived data and never changes raw recordings.
- **Data tables:** open **Data tables** to inspect paged raw EEG recordings with timestamp, device ID, your activity label for that second, and all 256 sample columns. Add or edit activity periods in **Activity log**; the Activity column updates from those saved intervals. Filter raw records by local start and end times, or use **View raw records** from a recorded 30-minute period. Separate Sessions and Stats tables show every field saved in the latest complete API export, with its export time visible. Raw records come from the local SQLite archive and may be newer than the saved Sessions and Stats export.
- **Exports:** the selected chart window downloads as raw JSONL; labels export as CSV. Timestamps in exports are UTC. Activity labels remain separate from the raw waveform records so you can revise context without changing the signal.

The [AWEAR API reference](https://awearlabs.com/docs#participant-data) describes one `RIGHT_TEMP` / TP10 channel with 256 samples in each one-second record, and the [device specifications](https://aweartech.com/pages/shop#specs) confirm one channel behind the right ear at 256 Hz. The app stores the waveform as returned. The overview's **AC RMS** subtracts each second's mean before summarizing oscillatory amplitude, avoiding the large baseline offset present in raw values. The EEG page centers only its displayed waveform; the raw table and exports retain exact samples. The public specifications do not define a voltage conversion, electrode reference, onboard filters, or whether each timestamp marks the first sample. All amplitudes therefore remain in **device units**, and sample positions within a second are displayed using an explicit timestamp-as-start assumption. Spectral band shares are descriptive and cannot identify focus, mood, or a clinical condition. Gaps remain gaps in the charts.

## Historical work suggestions

All amplitude timelines and daily recording graphs include every recorded hour, including mornings, evenings and weekends. The activity comparison includes an explicit **No activity assigned** group for recordings without a label. This is a display grouping, not a saved activity or an inference that outside work hours are relaxing. The work schedule only controls suggested labels; it never clips graphs, raw records, coverage or exports. Your selected graph range still controls the overview and comparison; the half-hour list pages through all recorded history.

`python3 backfill_activity_labels.py` previews labels for unassigned recorded time from 08:00 to 16:00 Monday–Friday in `America/New_York`. Add `--apply` to save them as **Work (Misc)**, or `--all-days` only when your schedule includes weekends. Suggestions are in at most 30-minute pieces, clipped to recorded endpoints, and never overlap an existing activity. Small gaps within a piece remain schedule-inferred time. Five-minute breaks are not placed without actual times.

Each suggestion has `source=schedule`, `reviewed=false`, and a batch ID. Existing annotations migrate as manual/reviewed and remain protected. Applying a batch saves the before-label snapshot, settings, proposed intervals, coverage counts and created IDs under `data/activity-backfill/<batch-id>/`. Repeating the backfill adds nothing where activities already exist. Raw EEG is never rewritten. Review suggestions through the **Edit** button beside each suggested label in **Recorded periods**; inferred activities should not be treated as observed ground truth in analysis.

## Explore signal patterns

The live app needs no packages. The optional offline clustering command requires NumPy and scikit-learn in the Python environment used to run it:

```bash
cd MindTrail
python analyze_patterns.py
```

Use `--demo` to analyze the separate demo archive, or `--participant P-XXXXXX` to select a particular participant. The command reads the local SQLite archive and saves `data/patterns/<id>.json`; it does not call the AWEAR API or edit raw records. Rerun it after new recordings arrive. The page indicates when the archive's record count or endpoints have changed since the analysis.

The analysis builds **nonoverlapping four-second windows** of 256 Hz data and does not join gaps, device changes, or 30-minute labeling boundaries. It linearly detrends each window and computes 1–40 Hz power and relative delta, theta, alpha, beta, and 30–40 Hz shares. Windows above the archive's 95th percentile of centered RMS or peak-to-peak amplitude, above its 99th percentile of single-sample jumps, or nearly flat are **flagged and excluded from clustering**. These are relative screening thresholds, not validated artifact labels. A two-component diagonal Gaussian mixture assigns exploratory Pattern 1 or 2 with a fit probability; K-means supplies a second assignment for comparison. The page also compares two- and three-group fits using silhouette, BIC, and adjusted Rand agreement. Model inputs and seed are saved in the report. Pattern numbers have no inherent physiological meaning, and a high fit probability does not imply certainty about an activity. Compare patterns across days and with your own labels before interpreting them.

## Direct Bluetooth

**Bluetooth** in the sidebar connects a right-ear AWEAR device directly to this Mac. The optional collector uses [Bleak](https://bleak.readthedocs.io/) in its own environment; the dashboard itself remains dependency free. To set up that environment if needed:

```bash
python3 -m venv .venv-bluetooth
.venv-bluetooth/bin/python -m pip install -r requirements-bluetooth.txt
```

Restart the dashboard after updating its Python code. Disconnect the device from its mobile app, select your participant in Overview, open **Bluetooth**, scan, and explicitly select your device. Choose the actual **Placement** and a duration, then press **Connect & record**. Placement is metadata only: selecting left does not modify the device, establish a left-ear montage, or validate left-side signal interpretation. Unspecified placement is saved as `unknown`. **Stop recording** preserves what has arrived so far; reconnecting starts a new session. Bluetooth needs no cloud API key. macOS may ask for Bluetooth permission for Codex or the terminal running Python. Grant it in System Settings → Privacy & Security → Bluetooth if prompted or denied. A scan only lists AWEAR candidates and does not choose or connect to a device automatically.

If scanning times out while waiting for macOS and no permission prompt appears, try the scan command below from the Mac's Terminal app and allow its Bluetooth prompt. Launching the dashboard from that same terminal lets its collector use the terminal's Bluetooth access. Close any other process using the chosen dashboard port first.

Each session is saved under `data/bluetooth/session-<UTC time>-<id>/`, independently of `awear_data.sqlite3`:

- `raw-notifications.jsonl`: every received notification as base64, with Mac Unix and monotonic arrival timestamps.
- `eeg.jsonl` and `eeg.csv`: complete 256-sample blocks in unmodified ADC device units, device timestamp/ID, counter, format, replay flag and detected counter gaps. No baseline subtraction or voltage calibration is applied to saved samples. The preview alone is centered.
- `events.jsonl`, `session.json` and `collector.log`: connection events, firmware information returned during authentication, totals, errors, and protocol revision. `status.json` supplies the live dashboard preview.

Stop recording before downloading finalized files. Partial files are retained after disconnection, authentication failure or malformed traffic; the status reports the error. Sequence gaps, duplicates and decode warnings remain explicit. Unsupported firmware formats are retained as raw notifications rather than guessed. A session with no valid EEG blocks fails visibly. Each bounded message has strict length and sample-count checks; notification queue overflow stops the capture. Samples received can include duplicate blocks, which are marked rather than silently removed.

The collector follows the community-documented [AWEAR LUCA protocol](https://github.com/eugenehp/awear-rs/tree/df7890471ef1dadc0d47fe0164adc8f702a2b3b6), pinned at `df7890471ef1dadc0d47fe0164adc8f702a2b3b6`. This is an independent Python implementation of the described wire format; compatibility with every device firmware is unverified. It supports framed hex, raw int16, and delta EEG plus framed or unframed handshake text. Its only outgoing messages are the authentication reply, `START`, and `STOP`. It does not transmit commands to set the device clock, request or erase saved recordings, update firmware, or change persistent device settings. **The device firmware may automatically replay saved EEG and clear its saved buffer on connection.** This was observed on firmware 0.1.383: the device reported replay completion followed by deletion. Received packets are retained locally, including the replay flag and device messages; the collector does not promise that the device retains its buffer. Live and replay blocks share a transmission counter. Placement is recorded on live blocks; replayed blocks have `unknown` placement because they may predate the current wear location. Device clocks can be unset or inaccurate; both device time and Mac arrival are saved without treating arrival as a sample timestamp. Bluetooth recordings are not merged into the API archive until their samples, units, timing and device IDs have been validated. No left-ear montage or mobile Calm Score is inferred.

The same collector is available from the terminal:

```bash
.venv-bluetooth/bin/python bluetooth_collector.py scan
.venv-bluetooth/bin/python bluetooth_collector.py record --device DEVICE_ID_FROM_SCAN --seconds 60 --placement right --output data/bluetooth/my-new-session
```

Use a new output directory each time; existing recording files are never overwritten. The dashboard keeps its session browser to the latest 20 sessions and reports the total.

## Export the AWEAR API

Run a separate export from the documented API endpoints:

```bash
cd MindTrail
python3 export_data.py
```

The command reads the same `.env` key, or use `--key-prompt` for a hidden prompt. It creates `data/` and saves each successful run in a new `data/export-<UTC timestamp>/` folder. It exports members and usage, and each participant's stats, sessions, and raw EEG in **both JSON and CSV**. With 2–10 participants it also writes a comparison JSON and separate comparison stats/timeline CSV files. Raw CSV has one row per second and 256 sample columns. A `manifest.json` records each participant's exported time window and counts. `data/` is excluded from Git.

By default, the raw and session export starts at each participant's `firstRecord` (or `firstSeen`, if returned) from `/stats` and ends now. For a shorter export, use `--start 2026-10-15T00:00:00Z`; use `--participant P-XXXXXX` to export only one participant. An all-history export can take a long time because the API limits raw data calls to 60 per minute. The exporter splits dense windows and does not rely on a pagination cursor. It preserves distinct raw records with the same timestamp and checks a full-history raw count against `/stats` before publishing a snapshot. If it fails, no partial snapshot is published; rerun the command.

These endpoints do **not** provide the consumer app's calm/focus scores or user-entered activity tags. The dashboard's own labels remain in SQLite and are available from its **Activity labels · CSV** download.

To merge raw EEG from the newest saved API snapshot into the dashboard's local archive, run `python3 import_snapshot.py`. It inserts missing timestamps, keeps existing recordings and labels, and does not need an API key. Pass a snapshot directory to import a particular export.

The raw table's `device_id` is retained from AWEAR raw records when supplied. For archived rows that predate this field and are newer than the saved export, fill missing IDs directly from AWEAR:

```bash
python3 backfill_device_ids.py
```

This command also reads `.env`, or accepts `--key-prompt`. It requests only one-hour windows that contain missing IDs and updates a row only when the returned timestamp and waveform both match and the device ID is unambiguous. It reports how many rows remain unresolved. It does not save your key or guess an ID from session summaries.

## Privacy and limits

The server binds to `127.0.0.1` and does not serve on your network. Its API collector makes authenticated HTTPS requests only to `awearlabs.com`; the optional Bluetooth collector communicates locally with the device you select. It does not use external fonts, scripts, analytics, or cloud storage. Other software running under your local account could still read the SQLite file and Bluetooth recordings, so use your computer's normal disk encryption and account controls.

The collector divides backfills into bounded time windows, splits any window the API still marks incomplete, deduplicates repeated records, and saves a checkpoint only after a complete window is stored. This avoids relying on the API's `nextCursor`, which can be missing or repeated. It paces raw-data requests below the documented 60-per-minute limit and surfaces AWEAR errors in the dashboard. It re-reads the most recent five minutes to catch delayed uploads. Very late uploads older than that overlap require a fresh backfill from an earlier time; use **Source** to choose an earlier sync start. Existing records are kept and deduplicated.

The research portal's [Terms](https://awearlabs.com/terms) and [Data Use Agreement](https://awearlabs.com/dua) are written around de-identified research participants and do not clearly explain personal self-tracking through the API. AWEAR's [consumer terms](https://aweartech.com/policies/terms-of-service) say you retain ownership and export rights for your raw EEG. Clarify the personal API use case with AWEAR if you rely on those terms.
