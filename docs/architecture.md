# Application structure

`tapesift.app_v3.run` is the production entry point. It uses the shared bootstrap in `app_v2` with `ui_v3.MainWindowV3` and the V3 stylesheet. All normal launch commands select this entry point.

`ui_v3` contains Home, Library, the review workspace, Tag Map editing, and current dialog presentation. `ui_v2` and `ui_core` provide shared widgets and workflow code. They remain application dependencies; their directory names do not select the launched interface.

`models`, `database`, and `services` contain project data and media operations. FFmpeg runs as a subprocess. Experimental analysis modules under `tapesift/research` are imported on demand; research fixtures use synthetic paths. Shared scoring lives in `services/segment_scoring.py`. The existing `play_detect_service` research import remains covered by the layering test.

Resources are package data. A wheel includes the same fonts, icons, field artwork, and team catalog as a source checkout. API keys and project files are runtime data and are never package resources.
