# TapeSift UI

This package contains the primary TapeSift presentation layer. It reuses the
existing models, services, database, workers,
playback, detection, project files, Library index, and export system.

The classic interface remains in the codebase as a compatibility layer.

Launch TapeSift with either:

```powershell
python run_tapesift.py
```

or double-click `run_tapesift_v2.cmd` from the repository root.

The V2 design system lives in `theme.py`. Screen wrappers add visual hierarchy
without copying the production business logic:

- `start_screen.py` adds the local-first home hero.
- `main_window.py` adds the Detect → Review → Export workflow ribbon and
  decorates the existing workspace.
- `library_screen.py` applies the V2 search, selection, and inspector styling.
