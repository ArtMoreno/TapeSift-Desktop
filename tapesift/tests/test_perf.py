"""Phase 0.2 performance probes: verify they fire and are off by default."""

from __future__ import annotations

import logging

from tapesift.core import perf


def test_perf_disabled_by_default():
    assert perf.perf_enabled() is False


def test_perf_timer_emits_when_enabled(caplog):
    perf.set_perf_enabled(True)
    try:
        with caplog.at_level(logging.DEBUG, logger="tapesift.perf"):
            with perf.PerfTimer("probe_x"):
                pass
        assert any("probe_x_ms=" in r.message for r in caplog.records)
    finally:
        perf.set_perf_enabled(False)


def test_perf_timer_silent_when_disabled(caplog):
    perf.set_perf_enabled(False)
    with caplog.at_level(logging.DEBUG, logger="tapesift.perf"):
        with perf.PerfTimer("probe_y"):
            pass
    assert not any("probe_y_ms=" in r.message for r in caplog.records)
