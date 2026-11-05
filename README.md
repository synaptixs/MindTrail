# MindTrail Lab

**Explore your EEG recordings. Add context with activity labels. Keep your archive on your computer.**

MindTrail Lab is a local dashboard for data collected by your AWEAR device.
The cloud EEG recordings shown here were collected **behind the right ear**
(RIGHT_TEMP / TP10, one channel at 256 Hz). Separate Bluetooth recordings keep
their own recorded placement; this does not relabel left-side or unknown sessions. Review
amplitude trends, inspect EEG, compare activities, and export your recordings.

> **For personal exploration:** signal patterns and amplitude are not validated measures
> of focus, mood, or health. Waveforms remain in device units.

[Download installers](#download-installers) · [Get started](#get-started) · [Get an API key](#get-your-awear-api-key) ·
[Use the dashboard](#use-the-dashboard) · [Explore without labels](#explore-without-labels) · [Run from source](#run-from-source) ·
[Your data and privacy](#your-data-and-privacy) · [Development workflow](#development-workflow) ·
[Build installers](#build-installers) · [Troubleshooting](#troubleshooting)

## Download installers

**MindTrail Lab 0.2.8 preview — Recordings first, guided exploration**

Explore right-ear EEG quality and recurring patterns, add your own activity labels,
compare recording days, and retain numerical history. Analysis runs locally with
bundled dependencies. Back up and restore your workspace, or review it offline.
Mobile-score entry remains retired; saved observations stay archived.

| Your computer | Download | Signing status |
| --- | --- | --- |
| **Mac — Apple Silicon or Intel** | [Download the Mac installer (.dmg)](https://github.com/synaptixs/MindTrail/releases/download/v0.2.8/MindTrail-Lab-0.2.8-Mac-universal2.dmg) | Developer ID signed; Apple notarized |
| **Windows x64** | [Download the Windows installer — UNSIGNED (.exe)](https://github.com/synaptixs/MindTrail/releases/download/v0.2.8/MindTrail-Lab-0.2.8-Windows-x64-Setup-unsigned.exe) | **Unsigned — Windows publisher signing is not configured** |

> **Windows testers:** this preview installer is unsigned. Windows or your browser
> may show an unknown-publisher warning, and organization security policies may
> block it. If blocked, contact the publisher or your IT administrator. Do not
> disable security protections. Wait for the signed release if your computer
> requires a trusted publisher.

Open the download and follow the [three setup steps below](#get-started).
Python is included; you do not need to install it yourself.

[Release notes and verification reports](https://github.com/synaptixs/MindTrail/releases/tag/v0.2.8) ·
[SHA-256 checksums](https://github.com/synaptixs/MindTrail/releases/download/v0.2.8/SHA256SUMS.txt)

## Get started

### 1. Install MindTrail Lab

Use the [download for your computer](#download-installers), then:

| Computer | Open | Then |
| --- | --- | --- |
| **Mac** | The MindTrail Lab `.dmg` file | Double-click **Install MindTrail Lab**. It installs under `~/Applications` and opens the dashboard. |
| **Windows x64** | The MindTrail Lab `Setup.exe` file | Run the installer. It installs for your user account and opens the dashboard. |

Packaged installers include Python and dependencies. You do not need a terminal.
You can also [run from source](#run-from-source); cloning this repository alone does not install the app.

> Files named **unsigned** are development builds. Windows or macOS may show a
> security warning or block installation. If blocked, contact the publisher or your
> IT administrator. Trusted release signing is a separate publisher setup step.

### 2. Connect your recordings

1. Open **MindTrail Lab**.
2. Paste your AWEAR API key into **API key**.
3. Click **Connect**.

The app selects your accessible account and starts syncing historical recordings.
A long recording history can take time to load. New uploaded data continues to sync
while the app is running.

**No key yet?** Follow [the steps below](#get-your-awear-api-key), or expand
**How to get an AWEAR API key** on the app's welcome screen.

### 3. Review and label an activity

1. Open **Activity log** and find the day you want to review.
2. Drag the start and end handles on its amplitude graph to select a period.
3. Click **Assign activity to selection**, enter an activity name, and save.
4. To change a label later, click **Edit** beside it.

Expand **Your activities** in Activity log to manage your own
names, categories, context and favorites. Rename keeps saved periods and their original
names in history; archive hides an activity from new choices. Export the vocabulary and
history as JSON, or current assignments with IDs as CSV. When labeling a period, keep
its review state separate from timing: mark known or approximate boundaries and optional
± uncertainty in seconds. Original schedule/model origins stay visible after review. See [personal activity details](docs/PERSONAL_ACTIVITIES.md).

The dashboard also separates recording gaps, quality screening and activities into
three daily lanes. Expand **Enter precise start and end** for millisecond selection;
New York times show their UTC offset and ask for EDT/EST when an hour repeats. Quality
lanes need a current completed analysis. See [daily review details](docs/DAILY_REVIEW.md).

Use **Show entire day** to reset a daily graph. New recordings extend the day's
unzoomed graph automatically. A selected zoom stays pinned while data refreshes.

### Guided workspace

The workspace starts with **Overview**, **Recordings** and **Explore**;
specialist pages remain under **More tools**. Daily recordings appear first. Activity
editing and signal details expand only when needed. First run is still API key →
**Connect**, with **Need an API key?** help beside the form.

Once recordings arrive, **Explore my recordings** prepares local analysis with one
click. No labels, question or algorithm selection is required. A current readiness
report enables **Explore my day** and a summary; comparing dates is offered when
multiple dates are present in the same recorded context. If no unflagged windows
are available, the app directs you to recording quality instead. No insight job
starts automatically from this screen. Existing analysis options are preserved.
Archived pattern reports remain under More tools; their empty state leads back
to Explore instead of asking users to run Python.

The v0.2.8 installers include this guided layout. Earlier v0.2.7 downloads retain
the previous navigation. All existing page URLs remain usable.

## Get your AWEAR API key

An API key is a private access code that lets MindTrail Lab retrieve your recordings.

1. Open the [AWEAR portal](https://awearlabs.com/login).
2. Sign in with the account that has access to your recordings.
3. Find **API keys**, create a key, and give it a name such as **MindTrail Lab**.
4. Copy the key and return to MindTrail Lab.
5. Paste it into **API key** and click **Connect**.

If the API-key option or your recordings are unavailable, [contact AWEAR](https://aweartech.com/pages/contact).
You can ask: “I own an AWEAR device and would like API access to my own recordings.
How do I enable it and create an API key?”

See also [AWEAR's authentication instructions](https://awearlabs.com/docs#authentication).
Keep keys out of GitHub, screenshots, bug reports, and command-line arguments.

## Use the dashboard

| What you want to do | Where to go |
| --- | --- |
| See amplitude trends and compare activities | **Overview** |
| Zoom into a day and assign or edit labels | **Activity log** |
| Examine waveforms, spectra, and recording gaps | **EEG signal** |
| Inspect raw samples, sessions, and stats | **Data tables** |
| Explore readiness without activity labels; save research questions | **Discover recordings** |
| Review analysis coverage and screening cues | **Recording quality** |
| Review, edit, split and undo activity intervals | **Episode review** |
| Inspect feature distributions, PCA, representative waveforms and similar periods | **Feature explorer** |
| Compare confirmed activities across days and known sessions | **Activity comparisons** |
| Inspect recurring spectral patterns and recording-condition sensitivity | **Across days** |
| Add optional recollections without assigning activities | **Optional context** |
| Review question-linked findings and download an analysis audit | **Discovery report** |
| Compare retained numerical versions | **Recording changes** |
| Read legacy offline clustering reports | **Signal patterns** |
| Scan and record directly from a device | **Bluetooth** |

**Activity labels:** live activities use **Start activity** and **Stop activity**.
Past activities use exact start/end times. Confirmed labels cannot overlap;
unreviewed schedule suggestions can be replaced or split by your edits.

**Data freshness:** cloud data is polled every 30 seconds, and open dashboard views
refresh every 10 seconds. This follows data uploaded to AWEAR; it is not a direct
Bluetooth stream. The dashboard shows the age of the latest recording.

**Quitting:** closing the browser tab keeps syncing active. Use **Settings & quit**
to stop the desktop app. Open MindTrail Lab again to return to your workspace.

### Detailed feature guides

- [Label-free discovery readiness and optional questions](docs/DISCOVERY_READINESS.md)

Expand only the guide you need:

<details>
<summary><strong>Amplitude over time</strong></summary>

opens on the latest seven days. Drag the vertical left and right handles overlaid directly on the amplitude plot. The selection is highlighted on that same plot while dragging.

Release to zoom the plot and load finer detail; the handles then sit at the edges of the zoomed view. Use the reset button to return to seven days. Expand **Enter exact start and end times** for precise local time entry.

The handles also support arrow keys (one second), Shift + arrow (one minute), Page Up/Down (one hour), and Home/End. The selected range stays pinned during refresh; summary cards, activity comparisons and the raw export follow it. **Show full 7 days** returns to the moving seven-day view.

Narrow ranges are re-aggregated down to one-second buckets rather than stretching the week-level averages. Hover a point to see its time span, AC RMS and record count; use **Inspect EEG at start** to examine raw samples.

</details>

<details>
<summary><strong>Live activity</strong></summary>

enter an activity name, press **Start activity**, then **Stop activity** when done.

</details>

<details>
<summary><strong>Past interval</strong></summary>

enter start and end times in your computer's local timezone. Confirmed activities cannot overlap. A new or edited activity can replace an unreviewed schedule suggestion; the suggested work periods before and after it are retained.

This lets you add a five-minute relaxing break without deleting a whole work period. Click **Edit** directly beside any label in **Recorded 30-minute periods**, adjust its name, note or exact times, then **Save changes**. Saving a suggestion marks it reviewed; **Cancel editing** leaves it unchanged.

**Delete activity** is available while editing and asks for confirmation.

</details>

<details>
<summary><strong>Daily recordings & activities</strong></summary>

one amplitude graph and recording coverage strip per New York calendar day, from the first stored second through the end of the last stored second. The graph includes the entire day even when a half-hour pagination boundary falls in its middle. Every dashboard refresh (10 seconds while open) rechecks the day: newly uploaded later records extend the end, and delayed earlier records move the start back.

No 08:00–16:00 or whole-half-hour bounds are imposed. Gaps and device changes stay separate. AC RMS averages use adaptive multiples of ten seconds, shown below each graph, and each day displays its own amplitude scale.

Drag the vertical start/end handles overlaid on each graph, then release to zoom; the selected time range is shown exactly beneath it. Zooms are re-aggregated from stored seconds down to one-second detail, rather than enlarging the daily averages. Each day keeps its own selected range through refresh and pagination.

**Show entire day** clears its zoom and resumes following newly uploaded records. Arrow keys move a handle by one second, Shift + arrow by one minute, Page Up/Down by one hour, and Home/End move to its bounds. **Assign activity to selection** prefills the past-interval form with the exact range; enter a name and save.

Unreviewed work suggestions are split around it, while overlapping confirmed activities remain protected. Hover for exact time, amplitude and record count; click a recorded point to inspect EEG. Activity shading remains independent of raw recordings.

Expand a day's **half-hour activity periods** to assign, edit or delete activities; open groups remain open on refresh. Counts identify loaded half-hour rows, and **Load older periods** reveals more rows/days. Daily graphs always include that day's full archive, not just loaded activity rows.

The coverage strip assumes each timestamp starts its one-second record. Its final edge includes the complete last second, rather than extending to the next half-hour or to the present clock time.

</details>

<details>
<summary><strong>EEG signal</strong></summary>

choose **View EEG** from a recorded period, or open **EEG signal** in the sidebar. Inspect 10-, 30-, or 60-second windows, step forward or backward, or follow the latest recording. The page shows a centered waveform, a 1–40 Hz spectrogram, average power spectrum, relative band shares, activity labels, and recording gaps.

Spectral estimates use 4-second Hann windows with 2-second steps and never join records across a gap or device change. The latest view refreshes every 10 seconds, following the same uploaded recordings as the dashboard rather than a direct Bluetooth stream.

</details>


<details>
<summary><strong>Signal patterns</strong></summary>

run the optional local analysis below, then open **Signal patterns** in the sidebar. It shows relative quality flags, an exploratory two-group Gaussian mixture clustering, a K-means comparison, pattern mixes by 30-minute period, and every analyzed four-second window for a selected period. Use **Assign activity** to prefill the existing interval-label form; review the EEG before saving a label.

The pattern report is derived data and never changes raw recordings.

</details>

<details>
<summary><strong>Data tables</strong></summary>

open **Data tables** to inspect paged raw EEG recordings with timestamp, device ID, your activity label for that second, and all 256 sample columns. Add or edit activity periods in **Activity log**; the Activity column updates from those saved intervals. Filter raw records by local start and end times, or use **View raw records** from a recorded 30-minute period.

Separate Sessions and Stats tables show every field saved in the latest complete API export, with its export time visible. Raw records come from the local SQLite archive and may be newer than the saved Sessions and Stats export.

</details>

<details>
<summary><strong>Exports</strong></summary>

the selected chart window downloads as raw JSONL; labels export as CSV. Timestamps in exports are UTC. Activity labels remain separate from the raw waveform records so you can revise context without changing the signal.

</details>

## Explore without labels

The v0.2.8 installers include local numerical analysis on **Windows x64** and
**Mac Intel/Apple Silicon (macOS 12+)**. No Python installation or activity labels
are required. Source users install [analysis dependencies](config/requirements/analysis.txt)
in their development environment first.

1. Open **Recording quality** and click **Run analysis**. Review coverage, gaps,
   screening cues and exclusions before interpreting features.
2. Open **Discover recordings** to inspect what is available. A saved research
   question is optional; unlabeled data is a valid starting point.
3. Use **Across days** to inspect recurring spectral patterns and recording-condition
   sensitivity. Use **Feature explorer** for feature distributions, PCA, representative
   waveforms and explainable similar-period retrieval. Optional model groups have no
   default activity or mental-state meaning; PCA is a projection, not evidence of a group.
4. Add a recollection in **Optional context** when useful, or select a known period in
   **Activity log** and assign your own activity. **Episode review** supports edit,
   split and undo. **Activity comparisons** needs suitable confirmed labels and shows
   when day/session support is insufficient.
5. Open **Discovery report** for daily variation, change cues, cross-day candidates
   and missing context. Apply a saved question and evidence view, download the report,
   or record what to explore next—including insufficient evidence.
6. Use **Recording changes** to compare retained numerical versions as recordings
   change. A pinned pair stays selected. Optional automatic analysis runs only while
   that page is open; it is separate from cloud synchronization.

Analysis runs in a separate process with time and memory limits. Jobs can be canceled
and reuse unchanged feature windows; previous complete results remain available while
new work runs. Bluetooth captures are preserved separately and are not inputs to this
cloud-EEG analysis. Unknown sessions/contact conditions stay unknown. Similarity,
recurrence and screening cues do not establish an activity, biological cause or diagnosis.

Read the [analysis contracts](docs/ANALYSIS_QUALITY.md),
[discovery guide](docs/DISCOVERY_READINESS.md),
[feature explorer guide](docs/EXPLORATORY_VIEWS.md), and
[activity comparison guide](docs/ACTIVITY_COMPARISONS.md) for exact scope and limits.
[Phase 1 acceptance](docs/PHASE1_ACCEPTANCE.md) documents the reproducible archive
resource and preservation checks.

### Keep an analysis audit

After analyzing your recordings, open **Discovery report → Download analysis audit ZIP**.
It includes the report with offline charts, all computed feature windows in JSON/CSV,
activity/context audit histories, units and a checksum manifest. On **Recording changes**,
use **Download all retained numerical history** for every saved compact numerical version.
The downloads state their omissions; keep your complete profile separately for recovery.
They exclude raw EEG and credentials and do not provide automatic restore/import.

## Delivery status and remaining limitations

All five development phases are complete in the recorded software, discovery and
preview-release scope:

| Phase | Delivered |
| --- | --- |
| **1 — Data and quality** | Gap-safe feature extraction, provenance, quality coverage, background jobs and resource checks |
| **2 — Personal activities** | Personal vocabulary, review/timing provenance, daily lanes, precise editing, split and undo |
| **3 — Exploratory patterns** | Optional clustering diagnostics, interpretable features/PCA, representative waveforms, similar-period retrieval and activity comparisons |
| **4 — Discovery-first review** | Zero-label readiness, questions, cross-day recurrence, optional context and evidence review |
| **5 — Reporting and release** | Question-linked reports, retained numerical history, audit exports, bundled analysis, workspace recovery and Mac/Windows installers |

Completion does not establish scientific interpretation or inferential readiness.
Activity labels and clustering remain optional; no validated focus, mood or health
score is provided. Mobile-score entry is retired because the available API supplies
no mobile-score history; archived observations remain exportable.

The v0.2.8 release records source provenance, checksums and validation reports:

- **Mac:** Developer ID signed, Apple notarized and stapled; Gatekeeper accepted.
  Native analysis/recovery ran on Intel and Apple Silicon. Genuine API connection
  and Keychain save/reopen/forget were checked on Mac.
- **Windows:** explicitly **unsigned**. Automated native install, upgrade, synthetic
  credential storage, analysis, recovery and uninstall-preservation checks passed.
  First run with a real AWEAR key remains **unverified**; the owner approved this
  preview limitation because no Windows test PC was available.
- **Restore:** automated native HTTP ZIP restore passed on all three architectures.
  The interactive browser file-picker check remains **unverified** after its test
  backend stalled. Bluetooth hardware was not exercised in release validation.

Earlier manual activity-review checks were deferred by the owner; they are not
reported as passed. Automatic app updates, signed Windows distribution and Bluetooth
capture ingestion into cloud analysis remain future work. See the
[release evidence](https://github.com/synaptixs/MindTrail/releases/tag/v0.2.8) and
[publisher verification guide](installer/README.md#v028-release-evidence-and-remaining-checks).

## Your data and privacy

**Bring your own device and key.** Each person needs their own AWEAR device and,
for cloud syncing, an API key from the AWEAR account that has access to their recordings.
MindTrail Lab does not provide a shared device, account or API key. Cloud access is
limited by the permissions on the key you enter; a key with multiple participants
can access more than one person's recordings. Direct Bluetooth recording does not
require a cloud API key, but you must scan and select your device yourself.

**No personal recordings or credentials are included in this repository or the
published installers.** The repository's `data/` folder contains only documentation
and empty placeholders; test fixtures use synthetic data. Installer builds include
application code and dependencies, not the publisher's local archive, API key or
device configuration. Installing the app does not connect you to the publisher's device.

### Where your recordings and key are stored

The dashboard listens on `127.0.0.1` on your computer. Cloud syncing sends your key
to AWEAR using authenticated HTTPS requests and downloads the recordings it permits.
Analysis runs locally. Raw recordings and activity labels remain separate, so editing
a label does not change the waveform.

| How you run the app | Default archive location |
| --- | --- |
| **Mac desktop** | `~/Library/Application Support/MindTrail Lab/` |
| **Windows desktop** | `%LOCALAPPDATA%\MindTrail Lab\` |
| **Source CLI** | The repository root, unless `AWEAR_DATA_DIR` is set |

The archive is `awear_data.sqlite3`; exports, Bluetooth sessions, and reports are
under `data/`. Back up both when you want to preserve the complete workspace.
The desktop app saves your key separately in **macOS Keychain** or **Windows
Credential Manager**. If secure storage is unavailable, it explains that the key is
session-only. Uninstalling the desktop app preserves its profile and saved key.
Existing installations may reuse their original `AWEAR Personal Lab` folder as
explained below.

Keep your key, database, exports and backups private. Runtime data and credentials
are excluded from Git; do not force-add them or attach them to public issues.
Workspace backups exclude API keys, but contain personal recordings and annotations.
Exports use UTC timestamps. People using the **same OS login and workspace** can
reuse its saved data and key; separate OS accounts are recommended on shared computers.
Local workspace separation is not a separate app login or an access-control boundary.

### Multiple devices and profiles

A **workspace profile** is a local archive folder with its own database, settings,
reports and saved-key namespace. A **participant** is a recording source returned
by AWEAR. These are different from physical devices.

| Need | Current support |
| --- | --- |
| Separate archives for two devices or people | Use separate workspace folders, with the appropriate key and source in each. This requires advanced launch configuration; there is no named-profile picker in the dashboard. |
| Choose among participants accessible to one key | Use **Settings → Advanced settings → Load available sources**. One participant is selected at a time; this is not a device-to-profile mapping. |
| Record from different Bluetooth devices | Scan and select the device for each recording. Each app instance supports one Bluetooth recording at a time and retains the selected device and placement in session metadata. |
| Automatically assign device A to profile A and device B to profile B | Not implemented. Selecting a nearby Bluetooth device does not verify its ownership. |

Advanced launch options already exist: desktop `--data-dir PATH` or `AWEAR_DATA_DIR`
selects a workspace folder, and `python scripts/run-local.py --profile PATH desktop`
selects one when running from source. For source launches, the ignored
`config/development.local.json` can also select a folder using `data_directory`.
Each folder gets a separate credential-store namespace; configure its own key and
accessible participant. A local profile does not create an AWEAR account, change
API permissions, or automatically separate two devices returned under one participant.
Simultaneous two-device Bluetooth capture has not been validated as a supported workflow.

<details>
<summary><strong>Existing profiles, archive restore, and desktop behavior</strong></summary>

The key is saved in macOS Keychain or Windows Credential Manager. If secure storage is unavailable or denied, the key works for the running session and the dashboard explains that it must be entered again after quitting. A key is never saved to plaintext configuration by the desktop app. The source CLI still supports its existing `.env` and hidden key prompt; the desktop launcher does not automatically read the project `.env`.

Desktop data is separate from installed files. New installations use: `~/Library/Application Support/MindTrail Lab/` on Mac, `%LOCALAPPDATA%\MindTrail Lab\` on Windows. Existing installations automatically reuse their original `AWEAR Personal Lab` profile when no MindTrail Lab profile exists; the vault identifiers stay stable so saved keys remain accessible. The database is `awear_data.sqlite3`; session files, exports and reports live under `data/`. Existing source data is not moved automatically. In v0.2.7, use **Settings → Advanced settings → Download workspace backup** to save recordings, labels, Bluetooth files and discovery history together. Restore that ZIP through **Restore an existing archive** in a new, empty desktop workspace, then choose **Open recordings offline** or reconnect an API key. Keys are excluded; generated analyses must be run again. Older published builds accept only a SQLite file, which requires copying `data/` separately. Keep the original backup until you have checked the restored workspace.

**Settings & quit** in Overview changes setup or stops MindTrail Lab. Closing the browser tab keeps sync running. Open the app again to return to the same workspace. Each desktop profile has one running instance and an automatically chosen local port. Upgrades stop the old instance gracefully and preserve data; Windows uninstall also preserves the archive. Existing offline pattern reports remain readable. The v0.2.8 installers bundle the numerical runtime for dashboard analysis, including optional exploration; source commands use their own Python environment.

[Installer build, signing and verification instructions](installer/README.md) describe both platforms and the simple end-user flow. Files containing **unsigned** in the name are development builds; production distribution requires signing/notarization and native Windows validation. Native analysis and recovery were validated on Intel and Apple Silicon Macs. Automatic update delivery remains a future feature.

For source development, create `.venv-desktop`, install `requirements-desktop.txt`, then run `src/desktop.py`. The existing `Launch AWEAR.command` and `Launch AWEAR.cmd` shortcuts use that environment. These source shortcuts are developer conveniences; end users use the self-contained installers. Use `src/desktop.py --data-dir PATH --no-key-store --no-browser` for isolated tests. `AWEAR_DATA_DIR` sets the writable profile for command-line exports and analysis; explicit desktop `--data-dir` takes precedence.

</details>

<details>
<summary><strong>Signal interpretation and device limitations</strong></summary>

The [AWEAR API reference](https://awearlabs.com/docs#participant-data) describes one `RIGHT_TEMP` / TP10 channel with 256 samples in each one-second record, and the [device specifications](https://aweartech.com/pages/shop#specs) confirm one channel behind the right ear at 256 Hz. The app stores the waveform as returned. Archived phone observations remain in SQLite and can be exported under **Exports → Archived phone observations**; no saved data is deleted. The overview's **AC RMS** subtracts each second's mean before summarizing oscillatory amplitude, avoiding the large baseline offset present in raw values. The EEG page centers only its displayed waveform; the raw table and exports retain exact samples. The public specifications do not define a voltage conversion, electrode reference, onboard filters, or whether each timestamp marks the first sample. All amplitudes therefore remain in **device units**, and sample positions within a second are displayed using an explicit timestamp-as-start assumption. Spectral band shares are descriptive and cannot identify focus, mood, or a clinical condition. Gaps remain gaps in the charts.

</details>

<details>
<summary><strong>Collection behavior, privacy details, and AWEAR terms</strong></summary>

The server binds to `127.0.0.1` and does not serve on your network. Its API collector makes authenticated HTTPS requests only to `awearlabs.com`; the optional Bluetooth collector communicates locally with the device you select. It does not use external fonts, scripts, analytics, or cloud storage. Other software running under your local account could still read the SQLite file and Bluetooth recordings, so use your computer's normal disk encryption and account controls.

The collector divides backfills into bounded time windows, splits any window the API still marks incomplete, deduplicates repeated records, and saves a checkpoint only after a complete window is stored. This avoids relying on the API's `nextCursor`, which can be missing or repeated. It paces raw-data requests below the documented 60-per-minute limit and surfaces AWEAR errors in the dashboard. It re-reads the most recent five minutes to catch delayed uploads. Very late uploads older than that overlap require a fresh backfill from an earlier time; use **Source** to choose an earlier sync start. Existing records are kept and deduplicated.

The research portal's [Terms](https://awearlabs.com/terms) and [Data Use Agreement](https://awearlabs.com/dua) are written around de-identified research participants and do not clearly explain personal self-tracking through the API. AWEAR's [consumer terms](https://aweartech.com/policies/terms-of-service) say you retain ownership and export rights for your raw EEG. Clarify the personal API use case with AWEAR if you rely on those terms.

</details>

## Run from source

These steps are for developers or people comfortable with a terminal.
Run commands from the repository root. New clones use `develop`, the default
branch for ongoing development. For published builds, use the
[release downloads](#download-installers) or check out a specific release tag.

### Option A — try the demo without an API key

With Python 3.10 or newer installed:

```bash
git clone https://github.com/synaptixs/MindTrail.git
cd MindTrail
python3 src/app.py --demo
```

Open **http://127.0.0.1:8765**. Demo data uses a separate `awear_demo.sqlite3` archive.
On Windows, use `python` in place of `python3`; timezone support may require `tzdata`.

### Option B — run the desktop launcher

Create an isolated environment and install the desktop dependencies:

**macOS**

```bash
python3 -m venv .venv-desktop
.venv-desktop/bin/python -m pip install -r requirements-desktop.txt
.venv-desktop/bin/python src/desktop.py
```

**Windows PowerShell**

```powershell
python -m venv .venv-desktop
.\.venv-desktop\Scripts\python.exe -m pip install -r requirements-desktop.txt
.\.venv-desktop\Scripts\python.exe src/desktop.py
```

Enter your key on the welcome screen and click **Connect**. Once the environment
exists, `Launch AWEAR.command` or `Launch AWEAR.cmd` opens the same launcher.

### Option C — run the source CLI with your real data

```bash
python3 src/app.py --key-prompt
```

Enter the key at the hidden prompt, then open **http://127.0.0.1:8765**. Choose your
participant and sync start in the dashboard. Put nothing after `--key-prompt`.

Alternatively, create a private `.env` file in the repository root containing
`AWEAR_API_KEY=YOUR_KEY`. It is ignored by Git. A nonempty shell variable overrides
`.env`; the hidden prompt overrides both. The desktop launcher uses its welcome
screen and does not automatically read the project's `.env`.

## Build installers

The repository already includes Mac DMG and Windows setup builders. End users do
not need these tools; they are for the publisher.

### 1. Prepare an isolated build environment

```bash
python -m venv .venv-desktop
```

Install dependencies using that environment's interpreter:

| Platform | Command |
| --- | --- |
| **Mac** | `.venv-desktop/bin/python -m pip install -r requirements-build.txt` |
| **Windows** | `.\.venv-desktop\Scripts\python.exe -m pip install -r requirements-build.txt` |

Use `python3` for the environment-creation command on a Mac if needed.
Windows builds also require **NSIS on PATH**.

### 2. Build for your platform

**macOS — development DMG**

```bash
MINDTRAIL_BUILD_PYTHON=.venv-desktop/bin/python ./scripts/build-mac.sh
```

**Windows PowerShell — development installer**

```powershell
$env:MINDTRAIL_BUILD_PYTHON = '.\.venv-desktop\Scripts\python.exe'
.\scripts\build-windows.ps1
```

### 3. Find the output in `dist/`

Development artifacts have **unsigned** in their filenames. Pass `--release` only
after configuring the required signing credentials; Mac releases also require
Apple notarization. Universal Mac builds require the separate universal Python
preparation steps.

Read the [complete installer guide](installer/README.md) for universal builds,
signing setup, prerequisites, and native verification.

## Advanced workflows

<details>
<summary><strong>Export raw data, sessions, and stats as JSON and CSV</strong></summary>

Run a separate export from the documented API endpoints:

```bash
cd MindTrail
python3 src/export_data.py
```

The command reads the same `.env` key, or use `--key-prompt` for a hidden prompt. It creates `data/` and saves each successful run in a new `data/raw/cloud/export-<UTC timestamp>/` folder. It exports members and usage, and each participant's stats, sessions, and raw EEG in **both JSON and CSV**. With 2–10 participants it also writes a comparison JSON and separate comparison stats/timeline CSV files. Raw CSV has one row per second and 256 sample columns. A `manifest.json` records each participant's exported time window and counts. `data/` is excluded from Git.

By default, the raw and session export starts at each participant's `firstRecord` (or `firstSeen`, if returned) from `/stats` and ends now. For a shorter export, use `--start 2026-10-15T00:00:00Z`; use `--participant P-XXXXXX` to export only one participant. An all-history export can take a long time because the API limits raw data calls to 60 per minute. The exporter splits dense windows and does not rely on a pagination cursor. It preserves distinct raw records with the same timestamp and checks a full-history raw count against `/stats` before publishing a snapshot. If it fails, no partial snapshot is published; rerun the command.

These endpoints do **not** provide the consumer app's calm/focus scores or user-entered activity tags. The dashboard's own labels remain in SQLite and are available from its **Activity labels · CSV** download.

To merge raw EEG from the newest saved API snapshot into the dashboard's local archive, run `python3 src/import_snapshot.py`. It inserts missing timestamps, keeps existing recordings and labels, and does not need an API key. Pass a snapshot directory to import a particular export.

The raw table's `device_id` is retained from AWEAR raw records when supplied. For archived rows that predate this field and are newer than the saved export, fill missing IDs directly from AWEAR:

```bash
python3 src/backfill_device_ids.py
```

This command also reads `.env`, or accepts `--key-prompt`. It requests only one-hour windows that contain missing IDs and updates a row only when the returned timestamp and waveform both match and the device ID is unambiguous. It reports how many rows remain unresolved. It does not save your key or guess an ID from session summaries.

</details>

<details>
<summary><strong>Suggest historical work activities, then review them</strong></summary>

All amplitude timelines and daily recording graphs include every recorded hour, including mornings, evenings and weekends. The activity comparison includes an explicit **No activity assigned** group for recordings without a label. This is a display grouping, not a saved activity or an inference that outside work hours are relaxing. The work schedule only controls suggested labels; it never clips graphs, raw records, coverage or exports. Your selected graph range still controls the overview and comparison; the half-hour list pages through all recorded history.

`python3 src/backfill_activity_labels.py` previews labels for unassigned recorded time from 08:00 to 16:00 Monday–Friday in `America/New_York`. Add `--apply` to save them as **Work (Misc)**, or `--all-days` only when your schedule includes weekends. Suggestions are in at most 30-minute pieces, clipped to recorded endpoints, and never overlap an existing activity. Small gaps within a piece remain schedule-inferred time. Five-minute breaks are not placed without actual times.

Each suggestion has `source=schedule`, `reviewed=false`, and a batch ID. Existing annotations migrate as manual/reviewed and remain protected. Applying a batch saves the before-label snapshot, settings, proposed intervals, coverage counts and created IDs under `data/processed/activity-backfill/<batch-id>/`. Repeating the backfill adds nothing where activities already exist. Raw EEG is never rewritten. Review suggestions through the **Edit** button beside each suggested label in **Recorded periods**; inferred activities should not be treated as observed ground truth in analysis.

</details>

<details>
<summary><strong>Run optional offline signal clustering</strong></summary>

The published installers already include dashboard analysis dependencies. To generate a separate legacy Signal patterns report from source, install the analysis requirements in your chosen environment:

```bash
python -m pip install -r config/requirements/analysis.txt
python src/analyze_patterns.py
```

Use `--demo` to analyze the separate demo archive, or `--participant P-XXXXXX` to select a particular participant. The command reads the local SQLite archive and saves `data/processed/patterns/<id>.json`; it does not call the AWEAR API or edit raw records. Rerun it after new recordings arrive. The page indicates when the archive's record count or endpoints have changed since the analysis.

The analysis builds **nonoverlapping four-second windows** of 256 Hz data and does not join gaps, device changes, or 30-minute labeling boundaries. It linearly detrends each window and computes 1–40 Hz power and relative delta, theta, alpha, beta, and 30–40 Hz shares. Windows above the archive's 95th percentile of centered RMS or peak-to-peak amplitude, above its 99th percentile of single-sample jumps, or nearly flat are **flagged and excluded from clustering**. These are relative screening thresholds, not validated artifact labels. A two-component diagonal Gaussian mixture assigns exploratory Pattern 1 or 2 with a fit probability; K-means supplies a second assignment for comparison. The page also compares two- and three-group fits using silhouette, BIC, and adjusted Rand agreement. Model inputs and seed are saved in the report. Pattern numbers have no inherent physiological meaning, and a high fit probability does not imply certainty about an activity. Compare patterns across days and with your own labels before interpreting them.

</details>

<details>
<summary><strong>Record directly over Bluetooth</strong></summary>

**Bluetooth** in the sidebar connects a right-ear AWEAR device directly to this Mac. The optional collector uses [Bleak](https://bleak.readthedocs.io/) in its own environment; the dashboard itself remains dependency free. To set up that environment if needed:

```bash
python3 -m venv .venv-bluetooth
.venv-bluetooth/bin/python -m pip install -r requirements-bluetooth.txt
```

Restart the dashboard after updating its Python code. Disconnect the device from its mobile app, select your participant in Overview, open **Bluetooth**, scan, and explicitly select your device. Choose the actual **Placement** and a duration, then press **Connect & record**. Placement is metadata only: selecting left does not modify the device, establish a left-ear montage, or validate left-side signal interpretation. Unspecified placement is saved as `unknown`. **Stop recording** preserves what has arrived so far; reconnecting starts a new session. Bluetooth needs no cloud API key. macOS may ask for Bluetooth permission for Codex or the terminal running Python. Grant it in System Settings → Privacy & Security → Bluetooth if prompted or denied. A scan only lists AWEAR candidates and does not choose or connect to a device automatically.

If scanning times out while waiting for macOS and no permission prompt appears, try the scan command below from the Mac's Terminal app and allow its Bluetooth prompt. Launching the dashboard from that same terminal lets its collector use the terminal's Bluetooth access. Close any other process using the chosen dashboard port first.

Each dashboard session is saved under `data/raw/bluetooth/session-<UTC time>-<id>/`, independently of `awear_data.sqlite3`:

- `raw-notifications.jsonl`: every received notification as base64, with Mac Unix and monotonic arrival timestamps.
- `eeg.jsonl` and `eeg.csv`: complete 256-sample blocks in unmodified ADC device units, device timestamp/ID, counter, format, replay flag and detected counter gaps. No baseline subtraction or voltage calibration is applied to saved samples. The preview alone is centered.
- `events.jsonl`, `session.json` and `collector.log`: connection events, firmware information returned during authentication, totals, errors, and protocol revision. `status.json` supplies the live dashboard preview.

Stop recording before downloading finalized files. Partial files are retained after disconnection, authentication failure or malformed traffic; the status reports the error. Sequence gaps, duplicates and decode warnings remain explicit. Unsupported firmware formats are retained as raw notifications rather than guessed. A session with no valid EEG blocks fails visibly. Each bounded message has strict length and sample-count checks; notification queue overflow stops the capture. Samples received can include duplicate blocks, which are marked rather than silently removed.

The collector follows the community-documented [AWEAR LUCA protocol](https://github.com/eugenehp/awear-rs/tree/df7890471ef1dadc0d47fe0164adc8f702a2b3b6), pinned at `df7890471ef1dadc0d47fe0164adc8f702a2b3b6`. This is an independent Python implementation of the described wire format; compatibility with every device firmware is unverified. It supports framed hex, raw int16, and delta EEG plus framed or unframed handshake text. Its only outgoing messages are the authentication reply, `START`, and `STOP`. It does not transmit commands to set the device clock, request or erase saved recordings, update firmware, or change persistent device settings. **The device firmware may automatically replay saved EEG and clear its saved buffer on connection.** This was observed on firmware 0.1.383: the device reported replay completion followed by deletion. Received packets are retained locally, including the replay flag and device messages; the collector does not promise that the device retains its buffer. Live and replay blocks share a transmission counter. Placement is recorded on live blocks; replayed blocks have `unknown` placement because they may predate the current wear location. Device clocks can be unset or inaccurate; both device time and Mac arrival are saved without treating arrival as a sample timestamp. Bluetooth recordings are not merged into the API archive until their samples, units, timing and device IDs have been validated. No left-ear montage or mobile Calm Score is inferred.

The same collector is available from the terminal:

```bash
.venv-bluetooth/bin/python src/bluetooth_collector.py scan
.venv-bluetooth/bin/python src/bluetooth_collector.py record --device DEVICE_ID_FROM_SCAN --seconds 60 --placement right --output data/bluetooth/my-new-session
```

Use a new output directory each time; existing recording files are never overwritten. The dashboard keeps its session browser to the latest 20 sessions and reports the total.

</details>

## Repository layout

```text
MindTrail/
├── src/          Application modules, dashboard assets, Python build entry points
├── test/         Automated unittest cases
├── config/       Dependency definitions and source configuration templates
├── data/         Ignored runtime data; raw/ and processed/ layout guides only
├── scripts/      Source-profile launcher and Mac/Windows build commands
├── installer/    Packaging templates, runtime locks, and verification tools
└── README.md     Start here
```

### Canonical source and existing archives

Keep one Git checkout as the source of truth. An archive can stay outside it; do not
copy recordings or keys into the checkout when upgrading source code.

Copy `config/development.example.json` to the ignored
`config/development.local.json`, then set `data_directory` to your existing archive's
absolute path. Use the source launcher from any directory:

```bash
python /path/to/MindTrail/scripts/run-local.py desktop
python /path/to/MindTrail/scripts/run-local.py app --port 8765
python /path/to/MindTrail/scripts/run-local.py export
python /path/to/MindTrail/scripts/run-local.py patterns
```

Use `--profile /absolute/archive` **before** the command to override the saved path.
The existing `AWEAR_DATA_DIR` environment variable takes precedence over the local
configuration. Direct `src/*.py` commands keep their existing defaults.

New cloud snapshots and Bluetooth captures go under the archive's `data/raw/`;
new pattern/backfill reports go under `data/processed/`. Historical exports,
captures, and reports remain readable in their old locations. The database, labels,
credential-store identity and `.env` remain at their existing profile paths.
See the [archive layout](data/README.md) and [configuration guide](config/README.md).

Packaged apps keep their normal per-user storage and do not load source configuration.

## Development workflow

**Feature branches → `develop` → `main`**

| Branch | Purpose | Pull-request target |
| --- | --- | --- |
| `codex/<change>` or another feature branch | One focused feature, fix or documentation change, started from current `develop` | `develop` |
| `develop` | Default branch; integrates ongoing work for testing | `main` when preparing a release |
| `main` | Protected release branch | Release changes arrive through a `develop` → `main` PR |

### 1. Develop a change

Start from a clean checkout, update `develop`, and create a feature branch:

```bash
git switch develop
git pull --ff-only origin develop
git switch -c codex/my-change
```

Make the change and run the [tests below](#run-the-tests). Commit and push your
feature branch, then open a pull request with **base: `develop`**. Merge it after
review and successful validation. Keep personal data and API keys out of commits.

### 2. Prepare a release

After the intended changes have landed in `develop`, open a pull request with
**base: `main`** and **compare: `develop`**. Include the release version, notes and
validation evidence. Keep the PR up to date with `main` and resolve review
conversations before merging. Build and publish release installers from the merged
`main` revision, following the [installer guide](installer/README.md).

`main` requires a pull request and a successful GitHub Actions `source` check;
these rules apply to administrators too. Force pushes and branch deletion are
blocked. A second person's approval is not mandatory. The Mac and Windows installer
validation jobs also run on ready pull requests; inspect both results before a
release. Their shared `package` check name is not currently a required status check.

After a release merge, bring its merge commit back into `develop` before starting
the next change. When `develop` has not advanced, this is a fast-forward:

```bash
git fetch origin
git switch develop
git merge --ff-only origin/main
git push origin develop
```

If the branches have diverged, use a `main` → `develop` synchronization PR instead
of resetting or force-pushing. Keep the long-lived `develop` and `main` branches;
delete merged feature branches when no longer needed.

### Run the tests

In your development environment:

```bash
python -m pip install -r requirements-build.txt -r config/requirements/analysis.txt
python -m unittest discover
node --check src/static/welcome.js
node --check src/static/eeg.js
```

The JavaScript syntax check requires Node.js. Platform installer checks are described
in the [installer guide](installer/README.md#verify-before-distribution).

## Troubleshooting

| Problem | What to try |
| --- | --- |
| **No API key or no recordings in the portal** | Contact AWEAR to confirm API access for your account. |
| **Recent recordings are missing** | Check the latest-record age; the mobile app may not have uploaded them yet. For much older delayed data, choose an earlier sync start under **Source**. |
| **Python edits are not appearing** | Stop and restart the server process on the port you use, then refresh the browser. |
| **Saved key is unavailable after quitting** | Check the secure-storage notice; a session-only key must be entered again. |
| **Bluetooth scan does not find the device** | Disconnect the mobile app and check macOS Bluetooth permission. See the expanded Bluetooth guide above. |
| **A development installer is blocked** | Contact the publisher or your IT administrator; do not disable security protections. |
| **The browser closed but syncing continues** | Use **Settings & quit** to stop the desktop app. |

When reporting a problem, include your operating system, the step you tried, and
the error message. Keep API keys and personal recordings out of reports.
