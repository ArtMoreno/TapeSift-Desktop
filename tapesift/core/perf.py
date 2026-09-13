"""Lightweight performance probes (Phase 0.2).

Additive only: these timers log how long key interactions take so we can
detect playback/UI regressions against the stable checkpoint. They never
change behavior - just emit a DEBUG record to the existing rotating log:

    PERF jkl_response_ms=3.2
    PERF clip_select_ms=1.8
    PERF apply_changes_ms=42.7
    PERF clip_list_rebuild_ms=12.4
    PERF timeline_redraw_ms=5.1

Disabled by default (PERF_ENABLED=False) so there is zero overhead in the
shipping build; flip it on to gather numbers during a tuning pass.
"""

from __future__ import annotations

import logging
import os
import time

# Keep the normal desktop build quiet.  A launch with ``TAPESIFT_PERF=1``
# enables the same lightweight probes without changing application behavior.
# This is intentionally an environment switch rather than a user preference:
# performance traces are diagnostic data, not project state.
PERF_ENABLED = os.environ.get("TAPESIFT_PERF", "").strip().lower() in {
    "1", "true", "yes", "on",
}

_log = logging.getLogger("tapesift.perf")

# Optional machine-readable sink.  ``TAPESIFT_PERF_JSON=<path>`` makes every
# span, timer and mark append to an in-memory list that is written as JSON
# when the interpreter exits.  The log lines above are for a human reading
# one run; this is for scripts/perf_bench.py comparing dozens of runs, where
# grepping rotating logs is how measurements get mis-attributed.
_JSON_PATH = os.environ.get("TAPESIFT_PERF_JSON", "").strip()
_records: list[dict] = []
# Wall-clock origin for marks.  The bench sets TAPESIFT_PERF_T0 to the
# moment it spawned the process so cold-start numbers include interpreter
# and site startup, which no in-process timer can see.
_T0 = float(os.environ.get("TAPESIFT_PERF_T0", "0") or 0.0) or None


def _emit(kind: str, label: str, ms: float, extra: dict | None = None) -> None:
    if not _JSON_PATH:
        return
    rec = {"kind": kind, "label": label, "ms": round(ms, 3), "t": time.time()}
    if extra:
        rec.update(extra)
    _records.append(rec)


def mark(label: str, **extra: object) -> None:
    """Record a point in time, in ms since process spawn when known.

    Spans measure how long a step took; marks say *when* it happened.
    "First frame at 1,840 ms after spawn" is the number a user feels, and it
    is not the sum of any spans because the interpreter, site and DLL load
    all happen before the first line of application code runs.
    """
    now = time.time()
    since_t0 = (now - _T0) * 1000.0 if _T0 else float("nan")
    _log.info("PERF mark %s at_ms=%.1f", label, since_t0)
    _emit("mark", label, since_t0, extra)


def dump_json(path: str | None = None) -> None:
    target = path or _JSON_PATH
    if not target:
        return
    import json
    try:
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(_records, fh)
    except OSError:
        _log.exception("Could not write perf JSON to %s", target)


if _JSON_PATH:
    import atexit
    atexit.register(dump_json)


class PerfTimer:
    """Context manager that logs the elapsed milliseconds under `label`.

    Usage::

        with PerfTimer("jkl_response"):
            player.shuttle_forward()
    """

    __slots__ = ("_label", "_start")

    def __init__(self, label: str) -> None:
        self._label = label
        self._start = 0.0

    def __enter__(self) -> "PerfTimer":
        if PERF_ENABLED or _JSON_PATH:
            self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if PERF_ENABLED or _JSON_PATH:
            ms = (time.perf_counter() - self._start) * 1000.0
            if PERF_ENABLED:
                _log.debug("%s_ms=%.2f", self._label, ms)
            _emit("timer", self._label, ms)
        return False  # never suppress exceptions


class PerfSpan:
    """Always-on timing for a handful of startup/open steps.

    ``PerfTimer`` is deliberately silent unless someone launches with
    ``TAPESIFT_PERF=1`` and turns the log up to DEBUG. That is the right
    default for the hot interaction probes, but it means the desktop
    shortcut - the way the app is actually started - produces no evidence
    at all when opening a project feels slow or fails. There is nothing to
    read afterwards, so a report can only ever be "it was sluggish".

    This records the same measurement at INFO on the normal logger, for the
    few spans on the launch/open path where the number is the whole point.
    One line per project open is not noise; it is the difference between a
    diagnosable report and a guess.
    """

    __slots__ = ("_label", "_start", "_extra")

    def __init__(self, label: str, **extra: object) -> None:
        self._label = label
        self._start = 0.0
        self._extra = extra

    def __enter__(self) -> "PerfSpan":
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        ms = (time.perf_counter() - self._start) * 1000.0
        detail = "".join(f" {k}={v}" for k, v in self._extra.items())
        if exc_type is not None:
            _log.info(
                "PERF %s_ms=%.1f FAILED=%s%s",
                self._label, ms, exc_type.__name__, detail)
        else:
            _log.info("PERF %s_ms=%.1f%s", self._label, ms, detail)
        _emit("span", self._label, ms, dict(self._extra))
        return False  # never suppress exceptions

    def note(self, **extra: object) -> None:
        """Attach detail discovered inside the span (clip counts, paths)."""
        self._extra.update(extra)


def perf_enabled() -> bool:
    return PERF_ENABLED


def set_perf_enabled(value: bool) -> None:
    global PERF_ENABLED
    PERF_ENABLED = bool(value)
