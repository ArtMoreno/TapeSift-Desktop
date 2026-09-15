# Private release verification

Updated September 14, 2026. This is a private development release, not a public stability certification.

## Included application

All standard launch commands open Shell V3, including the current empty and populated Home pages, approved IFI branding, centered playback controls, configurable Tag Map rows, keyboard editing, and the current playback engine. The September 14 update adds compact Clip Details with inline Gain yardage, aligned down and distance, multiple secondary-player chips, clearer notes and section dividers, lighter clip colors, optional snap/time-to-throw measurements, and Year/Opponent/Game Library filters. The research interface remains available as source but is not the default launcher.

The repository starts with new Git history and a product maintainer identity. It excludes development conversations, local captures, research datasets, personal project databases, footage, preferences, credentials, machine-specific paths, and prior commit history. Technical test fixtures and public football reference data remain. Gitleaks scanned the staged release with redaction enabled and reported zero findings; this is scan evidence, not a guarantee against every possible secret format.

## Real desktop checks

| Environment | Result |
| --- | --- |
| Windows, Python 3.12, Qt Windows backend, source install | Passed |
| Windows, clean wheel environment with runtime dependencies only | Passed |
| Ubuntu 26.04 under WSLg, Python 3.14, Qt Wayland backend | Passed |

Each smoke check generated its own film, opened the current desktop, confirmed painted video frames, ran three pause/resume cycles, stepped forward and backward, used forward and reverse shuttle, exported a six-second clip, and reopened the saved project. Forward stepping measured 33 ms on the generated 30 fps source. These checks establish basic functionality, not a performance benchmark for game footage.

The wheel includes the current Home field and IFI assets and excludes the test package. Linux-specific checks cover XDG paths, legacy profile preservation, local-file desktop actions, the desktop launcher, and credential-reference writes, clearing, locked-backend errors, and failed settings writes. Credential tests use an isolated backend; a real unlocked Secret Service session remains a desktop integration check.

## September 14 clean-clone checks

Commit `3bddd6d` was checked in a clean clone against the matching GitHub branch, using fresh virtual environments with only declared runtime dependencies first. Windows used Python 3.12 and the native Qt backend; Ubuntu 26.04 under WSL used Python 3.14 and the XCB backend under Xvfb. Both passed the standard `python -m tapesift` launch through first paint and the real desktop workflow, with process exit code zero. Windows also completed the documented FFmpeg download and checksum verification from the fresh clone. GitHub Windows and Ubuntu desktop smoke steps passed for the same commit.

The feature checks exercised inline Gain, aligned down/distance, a compact primary player, keyboard entry and removal of multiple secondary players, narrow-panel overflow, draft/save/reopen, snap and release marks, pause/reverse/clip switching, and combined Year/Opponent/Game filters including unset years. The generated 30 fps film produced a 966 ms snap mark and 3400 ms release mark, displaying 2.43 seconds on both platforms; this verifies timing behavior, not football snap-detection accuracy. Playback, frame stepping and the six-second export also passed on both platforms. The timer is optional, estimated snaps require confirmation, and the video timer does not change exported footage.

The Linux CI setup now installs the two missing XCB dependencies, `libxcb-icccm4` and `libxcb-keysyms1`, already listed in the Linux setup guide. CI artifact uploads include the generated hidden `.artifacts` directory so failures retain diagnostics.

After the runtime-only checks, both clean environments installed the declared development/research extras. All 98 feature tests passed on each platform using the repository's per-file process runner: Library service (25), Library interface (32), Clip Details (38), and snap prediction (3). The earlier combined-process Windows crash described below remains a failed run.

## Regression results and open work

Every test file was executed on both platforms for the September 13 release: 193 files per initial run. The Linux run recorded 2,779 test cases, with 19 failures, four errors, and 25 platform/environment skips. The Windows run recorded 2,779 cases, with 24 failures and 19 skips. These initial results are retained; subsequent fixes were checked with focused file runs rather than replacing the initial record.

Corrections include unavailable Windows paths on Linux, portable FFmpeg fixtures, renamed sample data, current result normalization and toolbar expectations, test cleanup, and closing a source-photo reference when its host hides. No test files were removed to obtain a passing result.

The full regression suite is **not certified clean**. The initial Windows checks identified PDF text extraction, narrow inspector overflow, backdrop timing, and intermittent Qt wrapper/native lifetime failures. The September 14 inspector passed its narrow-panel checks, but a combined Windows feature-test process encountered an access violation during Qt widget construction. That failed run is retained. Native access violations were also observed in earlier focused Windows runs. A prior single-process Linux run crashed during Qt teardown; separate file processes allowed every file to run. Crashes and timeouts remain failures.

CI runs the real desktop smoke and every test file on both Windows and Ubuntu, retains logs and exit codes, and fails on any error. See [Testing](TESTING.md) for the commands. A successful launch does not override a failing regression job.

The September 14 [candidate CI run](https://github.com/ArtMoreno/TapeSift-Desktop/actions/runs/34893484046) passed both desktop smoke steps. Ubuntu's full suite still timed out in `test_shell_v3_review.py` after 300 seconds and exposed an old drive-test layout assertion that compared the preset strip with the newly compact distance input. That assertion was corrected to check every preset against its actual container and to reject horizontal overflow; all drive editing, undo and failure-recovery assertions remain. The corrected test passed on both platforms, bringing the focused total to 99 tests per platform. GitHub rejected artifact uploads because the account's artifact-storage quota was exhausted; local verification logs and reports are retained. Full-suite status remains separate from the passing feature checks above.

## Before public distribution

Resolve the remaining regression failures and validate the native Windows build. Test Omarchy on the actual target laptop with representative local film: repeated J/K/L reversals, individual frames, pause/resume, Tag Map editing, save/reopen, and an export. WSLg verification does not establish ThinkPad T480s performance, battery behavior, GPU decoding, or Omarchy-specific desktop integration.

The previous repository and local project library are not migrated or rewritten by this release. Open existing projects explicitly and relink their footage when moving between operating systems.
