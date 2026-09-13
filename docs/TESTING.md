# Testing

Install `.[dev,research]`; the research extra is required to collect the complete test suite.

```sh
python scripts/run_tests.py --output .artifacts/tests --jobs 2
```

The runner executes every test file in a separate process, without retries, and retains logs, JUnit reports, and exit codes. Any assertion failure, native crash, or timeout fails the run. Tests isolate application data and use temporary projects.

Native Qt teardown crashes have occurred when many multimedia windows share one process, on Windows and during a Linux/Python 3.14 full-suite run. Process isolation lets the remaining files finish; it does not turn a crash into a pass. A crash is an incomplete run, not a pass. Keep logs, exit codes, and JUnit results. Do not discard failing tests or retry assertion failures into a green result. Native window-frame tests require a headed Windows session and skip on Linux by design.

CI runs the full Linux suite and platform-specific checks. The release smoke check generates its own test footage, opens the current interface, exercises playback and clip persistence, and exports from that footage. Synthetic smoke results do not establish performance on long or high-bitrate game film.

The Linux port adds checks for XDG storage, OS file opening, secure credential references, and failed keyring/settings writes. Project, keyboard, transport, and export tests remain part of the suite.
