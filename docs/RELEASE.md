# Private release verification

Verified September 13, 2026. This is a private development release, not a public stability certification.

## Included application

All standard launch commands open Shell V3, including the current empty and populated Home pages, approved IFI branding, centered playback controls, configurable Tag Map rows, keyboard editing, and the current playback engine. The research interface remains available as source but is not the default launcher.

The repository starts with new Git history and a product maintainer identity. It excludes development conversations, local captures, research datasets, personal project databases, footage, preferences, credentials, machine-specific paths, and prior commit history. Technical test fixtures and public football reference data remain. Gitleaks scanned the staged release with redaction enabled and reported zero findings; this is scan evidence, not a guarantee against every possible secret format.

## Real desktop checks

| Environment | Result |
| --- | --- |
| Windows, Python 3.12, Qt Windows backend, source install | Passed |
| Windows, clean wheel environment with runtime dependencies only | Passed |
| Ubuntu 26.04 under WSLg, Python 3.14, Qt Wayland backend | Passed |

Each smoke check generated its own film, opened the current desktop, confirmed painted video frames, ran three pause/resume cycles, stepped forward and backward, used forward and reverse shuttle, exported a six-second clip, and reopened the saved project. Forward stepping measured 33 ms on the generated 30 fps source. These checks establish basic functionality, not a performance benchmark for game footage.

The wheel includes the current Home field and IFI assets and excludes the test package. Linux-specific checks cover XDG paths, legacy profile preservation, local-file desktop actions, the desktop launcher, and credential-reference writes, clearing, locked-backend errors, and failed settings writes. Credential tests use an isolated backend; a real unlocked Secret Service session remains a desktop integration check.

## Regression results and open work

Every test file was executed on both platforms: 193 files per initial run. The Linux run recorded 2,779 test cases, with 19 failures, four errors, and 25 platform/environment skips. The Windows run recorded 2,779 cases, with 24 failures and 19 skips. These initial results are retained; subsequent fixes were checked with focused file runs rather than replacing the initial record.

Corrections include unavailable Windows paths on Linux, portable FFmpeg fixtures, renamed sample data, current result normalization and toolbar expectations, test cleanup, and closing a source-photo reference when its host hides. No test files were removed to obtain a passing result.

The full regression suite is **not certified clean**. Windows checks still need investigation for PDF text extraction, narrow inspector overflow, backdrop timing, and intermittent Qt wrapper/native lifetime failures. Native access violations were also observed in focused Windows runs. A single-process Linux run crashed during Qt teardown; separate file processes allowed every file to run. Crashes and timeouts remain failures.

CI runs the real desktop smoke and every test file on both Windows and Ubuntu, retains logs and exit codes, and fails on any error. See [Testing](TESTING.md) for the commands. A successful launch does not override a failing regression job.

## Before public distribution

Resolve the remaining regression failures and validate the native Windows build. Test Omarchy on the actual target laptop with representative local film: repeated J/K/L reversals, individual frames, pause/resume, Tag Map editing, save/reopen, and an export. WSLg verification does not establish ThinkPad T480s performance, battery behavior, GPU decoding, or Omarchy-specific desktop integration.

The previous repository and local project library are not migrated or rewritten by this release. Open existing projects explicitly and relink their footage when moving between operating systems.
