# Consumer installers

End users install the packaged app, enter their API key, and click **Connect**. They do not install Python, use a terminal, choose a participant, or select a sync date. Normal operating-system download/permission prompts can still appear. The instructions below are for the publisher and testers, not end users.

## Packages

- **Windows x64:** open `MindTrail-Lab-0.2.4-Windows-x64-Setup.exe`. It installs privately under `%LOCALAPPDATA%\Programs\MindTrail Lab`, creates Start menu/Desktop shortcuts, and opens the dashboard automatically. It needs no administrator elevation. The installer contains the official embedded Python runtime and all dependencies; installation downloads nothing.
- **Mac universal:** open the DMG, then double-click **Install MindTrail Lab**. It installs under `~/Applications/MindTrail Lab.app` and opens it automatically. There is no drag-and-drop step, configuration wizard, or administrator password. The application includes Intel and Apple Silicon code. Intel execution must still be verified on an Intel Mac or with Rosetta.

First run chooses the previously selected participant if accessible, otherwise the first accessible participant in sorted ID order. Personal keys usually have one participant. Keys with several participants show the selected ID and an explanatory notice; **Advanced settings** offers a source selector. The app uses the API's `firstRecord`/`firstSeen` date to sync history automatically. If the earliest date cannot be read, a visible notice explains the seven-day fallback and how to expand it. Reconnecting preserves checkpoints when the source and sync start are unchanged. Raw records and labels are not rewritten.

The key is saved in the native credential store by default. If saving is denied or unavailable, connection still succeeds using an in-memory key, with a visible session-only notice. Secure-storage preferences, Bluetooth and archive restore remain under **Advanced settings**. Keys never enter installer files or manifests.

User data remains outside installed files. New profiles use the MindTrail Lab name; existing AWEAR Personal Lab profiles and credential-store identifiers remain usable without copying recordings or labels. On Mac, an existing old-name app is stopped and replaced, with rollback to its original name on launch failure. On Windows, upgrades reuse the old installation directory and uninstall registry identifier, replace old shortcuts, and show MindTrail Lab in installed apps. First installs and upgrades request graceful shutdown of any session using the same profile, including a copy launched outside the installation folder, before opening the installed app; the Mac installer rolls back an app replacement if launching fails. Windows uninstall preserves the data profile and key. Mac uninstall means removing the app from `~/Applications`; it likewise leaves the data profile. Automatic update delivery is a separate future feature; these installers support manual upgrades.

## Build

Convenience commands from the repository root are `./scripts/build-mac.sh` on macOS and `./scripts/build-windows.ps1` on Windows. Both forward flags such as `--release` to the existing Python builder and write to `dist/`. Install `requirements-build.txt` in an isolated environment first; Windows builds also need NSIS on PATH. Set `MINDTRAIL_BUILD_PYTHON` to select that environment's interpreter.

On a builder with `requirements-build.txt`, `python src/installer_build.py windows` builds the Windows setup EXE on Mac or Windows using NSIS (`brew install makensis` on Mac; the official NSIS package on Windows). `installer/windows-runtime.lock.json` pins the official Windows Python ZIP and every wheel by SHA-256. Wheels are checked against **Windows** environment markers and CPython ABI tags even when the builder is a Mac. They are vendored offline into an isolated `._pth` runtime; global Python, `PYTHONPATH` and user site packages are not used.

For the universal Mac builder:

```sh
python3 installer/prepare_mac_runtime.py
AWEAR_MAC_ARCH=universal2 .venv-desktop-universal/bin/python src/installer_build.py mac
```

The preparation script checksum-verifies Python's official universal package, extracts and relocates it under `build/`, and creates `.venv-desktop-universal`. It does not install or replace system Python. An existing universal Python environment can also build directly. `src/installer_build.py all` creates both artifacts. The default build names contain **unsigned**. They are development artifacts until signed/notarized and verified on the target operating systems. When a Developer ID identity is ready but notarization credentials are not, `src/installer_build.py mac --sign-only` signs the app, installer app and disk image and verifies hardened runtime and timestamps. Its output is explicitly named **signed-unnotarized**; use `--release` after configuring notarization to produce the distributable release.

The package source allowlist excludes `.env`, SQLite files, `data/`, tests, development environments and working notes. Runtime and wheel license notices remain in the package. `package-manifest.json` records the Windows payload hashes, versions and inputs; `.sha256` files identify completed installer artifacts. No source repository or publishing destination is inferred from the enclosing checkout. The supplied Windows CI workflow is an example to copy into MindTrail Lab's own repository when one is selected.

## Release signing: publisher setup

### Mac — start here

1. Open **Xcode → Settings → Accounts / Apple Accounts** and sign into the Apple Developer account for your team.
2. Select your team and click **Manage Certificates**. Look for **Developer ID Application**. If it is missing, use **+ → Developer ID Application**, or follow Apple's [Developer ID certificate instructions](https://developer.apple.com/help/account/certificates/create-developer-id-certificates/). The certificate must include its private key on this Mac. Do not export or share that key. This DMG installer uses Developer ID Application; a separate Developer ID Installer certificate is not required.
3. Tell the build operator the displayed **certificate name** and **Team ID**. These identify the certificate; they are not secret. The operator sets `AWEAR_MAC_SIGN_IDENTITY` to that name.
4. Set up a **notarytool Keychain profile** locally. The operator can run `xcrun notarytool store-credentials AWEAR-notary` with interactive prompts. Run or copy the command **before** copying the app-specific password: copying the command afterward replaces the password on the clipboard. At the password prompt, copy the actual generated value from Apple, including its hyphens, and paste it directly into the hidden prompt. The chosen password label is not the generated secret. Keep secrets out of chat and command-line arguments. An App Store Connect API key is an alternative if your team uses one. Set `AWEAR_NOTARY_PROFILE=AWEAR-notary` afterward. If credentials were saved with `--keychain "$HOME/Library/Keychains/login.keychain-db"`, also set `AWEAR_NOTARY_KEYCHAIN` to that path for builds and use the same `--keychain` argument when verifying credentials. Apple otherwise uses a different, data-protection Keychain by default.
5. Run the universal Mac build with `--release`. It signs and notarizes the inner app, staples its ticket, builds the installer app/DMG, then signs and notarizes the DMG. Failed signing/notarization stops the build. Apple must explicitly return `Accepted`; a successful process exit alone is not sufficient. Submission IDs and statuses are retained in `.notary.json` receipts beside submitted artifacts. Keep any artifact from a failed release attempt out of distribution.

Apple's [notarization documentation](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution) describes the required publisher setup. Creating/signing with certificates is a publisher task performed once; none of these steps apply to end users.

### Windows

1. On the Windows test/signing machine, identify your code-signing provider: a certificate authority/hardware token, managed signing service, or Microsoft Artifact Signing. A Microsoft account alone is not a code-signing identity. See Microsoft's [code-signing guide](https://learn.microsoft.com/en-us/windows/msix/package/sign-msix-package-guide).
2. Make the certificate available to **SignTool** through the provider's supported tools and install the Windows SDK signing tools. Do not send a private key or password to chat. If using a local certificate, use Windows' certificate import UI or your token provider's software.
3. For a local certificate, give the build operator the certificate's **thumbprint** and provider name. Set `AWEAR_WINDOWS_CERT_THUMBPRINT` in the signing environment and ensure `signtool.exe` is on PATH. For Microsoft Artifact Signing, use the GitHub setup below instead. `AWEAR_WINDOWS_SIGNTOOL` can explicitly select the Windows SDK executable.
4. On that Windows machine, run `python src/installer_build.py windows --release`. NSIS signs and timestamps the embedded uninstaller before packaging. The build then signs the setup EXE and verifies Authenticode and its timestamp. A failed compile, signing operation, or verification leaves no new unsigned file under the release filename. Missing signing configuration is rejected before packaging. The Mac's Homebrew `signtool` belongs to Mozilla NSS and cannot substitute for the Windows SDK tool. Signing establishes a publisher identity; Windows reputation checks may still display a prompt for a new app.
5. On a clean Windows test account or disposable runner, run `python installer/smoke_windows.py --installer dist/MindTrail-Lab-0.2.4-Windows-x64-Setup.exe --require-signed --report dist/windows-native-validation.json`. This requires the Windows SDK's SignTool on PATH and checks both setup and uninstaller signatures. Keep the signed setup, checksum, and native validation report together for review. A successful local Mac build or mocked signing test does not establish native Windows behavior.

### GitHub and Microsoft Artifact Signing

This public repository has no configured Azure signing access. The account names and federation payload below document the original private publisher setup; its trust accepts only the named private repository, not `synaptixs/MindTrail`. Do not reuse that connection for a fork. Public-source builds are unsigned unless a publisher configures their own approved signing identity.

`.github/workflows/windows-sign.yml` is a manual publisher workflow for `ssmith-synaptixs/mindtrail-lab`. It authenticates with GitHub OIDC through a dedicated, single-tenant Microsoft Entra application. It needs no client secret, PFX file or certificate password. The federation payload in `installer/github-federation.json` accepts only `repo:ssmith-synaptixs/mindtrail-lab:ref:refs/heads/main`, with issuer `https://token.actions.githubusercontent.com` and audience `api://AzureADTokenExchange`. Pull-request and feature-branch tokens do not match that trust. Keep the app's sign-in audience `AzureADMyOrg` and add no API permissions or passwords.

The owner must approve creation of the application, service principal and federated trust. After Microsoft approves the existing company's **public** identity validation, create the Public Trust profile `mindtrail-lab-public` under `mindtraillabfibonacci`. Assign the service principal **Artifact Signing Certificate Profile Signer** only on this profile's ARM resource ID, not on the account, resource group or subscription. This identity does not need Contributor, Owner, identity-verification or directory API permissions. [Microsoft's profile-scoped role instructions](https://learn.microsoft.com/en-us/azure/artifact-signing/tutorial-assign-roles) and [OIDC setup](https://github.com/Azure/artifact-signing-action/blob/main/docs/OIDC.md) describe the service requirements.

Configure these non-secret GitHub repository variables:

| Variable | Value |
| --- | --- |
| `AZURE_CLIENT_ID` | Dedicated application's client ID, after owner-approved creation |
| `AZURE_TENANT_ID` | Existing Fibonacci Solutions, Inc. tenant ID |
| `AZURE_SUBSCRIPTION_ID` | Existing Azure subscription ID |
| `AWEAR_SIGNING_RESOURCE_GROUP` | `rg-mindtrail-signing` |
| `AWEAR_SIGNING_ACCOUNT` | `mindtraillabfibonacci` |
| `AWEAR_SIGNING_PROFILE` | `mindtrail-lab-public` |
| `AWEAR_SIGNING_ENDPOINT` | `https://eus.codesigning.azure.net/` |
| `MINDTRAIL_WINDOWS_SIGNING_READY` | Keep `false` until the active Public Trust profile and scoped access are verified |

The workflow must be merged into `main` before it is available under Actions. The owner enables readiness only after publisher verification and profile provisioning succeed. Then manually choose **Sign Windows installer**, select **main**, and confirm signing. There are no scheduled, push or pull-request signing triggers. The job has read-only repository access plus `id-token: write`, a ten-minute timeout, and refuses profiles that are not active, provisioned and Public Trust. Azure login uses the OIDC identity without requiring subscription-wide access; profile reads use its explicit ARM ID.

`installer/prepare_windows_signing.ps1` verifies the runner's x64 SDK SignTool, .NET 8 and Visual C++ runtime, and downloads the SHA-256-pinned Microsoft client in `artifact-signing-client.lock.json`. The unsigned validation workflow runs this preparation without authenticating to Azure or signing anything. These are builder dependencies and are not added to the installed app.

For Artifact Signing release builds, `AWEAR_WINDOWS_ARTIFACT_DLIB` selects the client's x64 `Azure.CodeSigning.Dlib.dll` and `AWEAR_WINDOWS_ARTIFACT_METADATA` selects the generated non-secret account/profile JSON. Leave `AWEAR_WINDOWS_CERT_THUMBPRINT` unset. The builder rejects incomplete or ambiguous provider settings and endpoints outside the regional Microsoft HTTPS service. Both setup and the embedded uninstaller use the same profile and Microsoft's RFC3161 timestamp endpoint. SignTool trust/timestamp checks and the full native install/upgrade/uninstall check must pass before a seven-day **signed** artifact is uploaded. It is not automatically published as a GitHub Release. A skipped signing job or unsigned validation success is not evidence of a signed release.

## Verify before distribution

- Mac package check, without real credentials or Bluetooth: run the bundled executable with `--self-check /tmp/report.json`. Run **Install MindTrail Lab** with developer-only `--target-dir /tmp/awear-install-test --no-launch --report /tmp/install-report.json` to exercise installation outside the real Applications folder. Test a second installation with `AWEAR_DATA_DIR` pointing at a disposable profile to exercise upgrade shutdown. Confirm `codesign --verify --deep --strict` on both app bundles and `hdiutil verify` on the DMG. Signed releases also need Gatekeeper/notarization verification.
- Windows native check: `python installer/smoke_windows.py --installer PATH_TO_SETUP.exe --report dist/windows-native-validation.json` exercises install, bundled imports/certificates/timezones, setup-page delivery, duplicate launch, an upgrade while running, shutdown and uninstall. A synthetic activity label must survive upgrade and uninstall. The installer, app and uninstaller all receive an isolated `AWEAR_DATA_DIR`; the test refuses to run over an existing MindTrail Lab registration. Run only in a clean Windows test account or disposable runner because the real installer creates Start menu/Desktop shortcuts and an uninstall entry for that user. The credential check saves, reads and deletes only a synthetic value in the temporary profile's unique vault namespace, never a real API key. It performs no Bluetooth scan. Add `--require-signed` for release verification of setup and uninstaller signatures; unsigned CI results are development validation only. Real API connectivity and the normal interactive installer/download experience still need owner testing before public distribution.
- `installer/windows-ci.yml.example` supplies a manual Windows CI build and smoke test. Copy it into a chosen repository; no CI run or public upload is started by these local scripts.
- On clean Windows and Mac machines, verify genuine API key save/reopen/forget and connection; these require the owner's input. Run an explicit Bluetooth scan/record only when authorized. Automated tests use fakes and cannot establish real device or credential-store permission behavior.

Run `python -m unittest discover` in the development test environment (the clustering tests require NumPy and scikit-learn), and `node --check src/static/welcome.js` for frontend syntax. Installer-specific and desktop tests can run independently using `python -m unittest test.test_desktop test.test_bluetooth test.test_installer_build`.
