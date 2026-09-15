# Personal shared projects

Shared Projects lets you switch between Windows and Linux, editing on one computer at a time. TapeSift saves versions into a folder already managed by your private drive application. No TapeSift account or server is required.

## Set up each computer

1. Update TapeSift on both computers. Use the same application version.
2. Create a dedicated folder in your private cloud drive and make it available locally on both computers. Finish downloading its contents before opening a project. The local folder paths can differ.
3. In TapeSift, open **File → Shared Projects… → Choose folder…** and select that folder. Keep your normal editable project folder outside the shared folder.
4. On the first computer, open your existing project and click **Save current to shared folder**. This also saves pending valid Clip Details edits.

TapeSift does not install or configure a cloud client. A folder only transfers between computers when your drive application is configured to sync it.

## Switch computers

1. Save your edits. After the first share, saved changes are published automatically after a short pause; **Save current to shared folder** publishes immediately. Typing in an unsaved form is not itself a shared save.
2. Wait for **Saved to shared folder**, then wait for your drive application to finish uploading. Close the project before switching computers. TapeSift attempts a final share on close and reports a failure before you leave.
3. On the other computer, wait for the drive download to complete. Open **File → Shared Projects…**, click **Refresh**, select the project row, and click **Open saved version**. Selecting the project row opens the latest complete version.
4. Relink the source film if prompted. You can keep the film in a different location on each computer; TapeSift remembers known local film paths and uses the current computer's export folder.

Opening a shared version creates a new local working copy. Use Shared Projects when returning to another computer so you open the latest version; an older entry in Home still refers to that older local copy. The shared snapshot files should be opened through this dialog.

## What travels

The project database includes clips, players and saved assignments, notes, tags, game details, confirmed snap/release marks, and embedded images or voiceovers. Source film, exports, application preferences, API credentials, external roster CSV files, and heatmap sidecar settings are separate. Copy or sync footage separately if it is not already available on both computers.

Each saved version contains a complete database, so embedded media increases storage use. History is retained; this version does not automatically delete older snapshots. Do not delete individual history files, because later versions refer to earlier ones.

## Interrupted transfers and overlapping edits

**Saved to shared folder** confirms a local write, not cloud upload completion. If the folder is unavailable or a transfer is incomplete, your editable project stays on this computer. Retry after the drive reconnects and finishes syncing. Older complete versions remain browsable while another version is incomplete.

If another computer has a newer version, TapeSift keeps your local work and stops publishing over it. If both computers save before seeing each other's changes, both branches remain available. Open the version you want, then use **Share current as a new project** to continue from it. Changes are not automatically merged. To preserve your current local edits as a separate project, use that same button before opening another version.

Only one TapeSift window may edit a linked local working file at a time. This local lock does not lock another computer: finish syncing and hand off manually.

## Verification

Focused tests cover consistent snapshots of open projects, import validation, incomplete transfers, overlapping revisions, local file locks, pending saves, failed preference writes, and project renaming. Native Windows and Linux XCB checks exercise the actual dialog with generated film, saved notes and snap marks, automatic publication, reopening, and export. They simulate a shared folder; a real cloud provider's network transfer is a separate setup check. See [release verification](RELEASE.md).
