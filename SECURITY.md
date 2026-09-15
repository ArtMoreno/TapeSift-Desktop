# Privacy and security

Editing, project storage, local play detection, and export run on the computer. There is no required TapeSift account or telemetry service.

**First Read is the single exception** to local analysis. It is **off by default** and runs only after an explicit action. It uses the **user's own API key** to send selected **still-frame contact sheets** and analysis instructions directly to the configured provider. Requests and credentials **do not pass through TapeSift** servers. Provider retention and billing terms apply.

Windows protects saved credentials with DPAPI. Linux stores them in a Secret Service keyring; settings contain an opaque reference, never the plaintext key. Linux saving fails with an explanation if the keyring is locked or unavailable. Editing does not require a keyring when First Read is unused.

Projects refer to source files and store clip metadata and optional captured images. Exported summaries can include names and notes. Review exports before sharing them.

**Shared Projects is optional.** It writes complete project snapshots to the folder you choose; your drive application handles any cloud transfer and access permissions. These snapshots contain project metadata, player names, notes, local file paths, and embedded images or voiceovers. TapeSift does not add encryption or upload the original film. API credentials and application preferences are not included. Choose a private folder and keep editable working projects outside it. See [Shared Projects](docs/SHARED_PROJECTS.md).

Do not commit projects, footage, settings, logs, crash dumps, API keys, or private screenshots. The release scanner examines tracked files without printing matched secret values. Dependencies and Windows FFmpeg downloads use their documented upstream servers.

The development-only companion server is excluded from Windows application builds and is not exposed in the normal desktop interface. Do not enable it on untrusted networks.

Report security issues privately to the repository maintainers. Do not post credentials or private film in issues or CI logs.
