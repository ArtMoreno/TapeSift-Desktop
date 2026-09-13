"""Run First Read across a whole game.

The engine in ``first_read_service`` answers one play. This walks a project's
plays, stores a suggestion on each, and reports what happened. The awkward
parts are all here rather than in the engine: resuming, partial failure,
throttling, and stopping when the analyst says stop.

Nothing here writes ``run_pass``. A batch produces suggestions; the analyst
still makes every call.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from tapesift.services.first_read_service import (
    FirstRead, FirstReadCancelled, FirstReadError, FirstReadRateLimited,
    _wait_for_retry, load, read_play, store,
)

#: Rough running cost per play for both views, in US dollars. Measured at
#: about eight cents for a full game of ~70 plays. It is shown to the analyst
#: before anything leaves the machine, so it errs high rather than low.
COST_PER_PLAY_USD = 0.0012

#: Give up on a play after this many throttle waits. Without a ceiling a
#: rate-limited key turns into an overnight run that answers nothing, which
#: is exactly how a day was lost to a free-tier quota once.
MAX_RATE_LIMIT_WAITS = 3
DEFAULT_BACKOFF_S = 20.0


@dataclass(frozen=True)
class BatchPlan:
    """What a run would do, before it does any of it."""

    to_read: tuple[str, ...]
    already_read: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.to_read)

    @property
    def estimated_cost_usd(self) -> float:
        return round(self.count * COST_PER_PLAY_USD, 4)

    def describe(self) -> str:
        """One line the confirmation dialog can show verbatim."""
        if not self.count:
            return "Every play already has a First Read."
        skipped = (f", {len(self.already_read)} already done"
                   if self.already_read else "")
        return (f"First Read on {self.count} "
                f"play{'s' if self.count != 1 else ''}{skipped} - "
                f"about ${self.estimated_cost_usd:.2f}")


@dataclass
class BatchProgress:
    """Where a running batch has got to.

    ``sheet_path`` is the contact sheet just rendered for this play. It is
    what the dialog displays: the actual frames behind the call, not a
    decorative animation.
    """

    index: int
    total: int
    clip_id: str
    read: FirstRead | None = None
    message: str = ""
    sheet_path: Path | None = None
    start_ms: int = 0
    end_ms: int = 0
    sheet_bytes: bytes = b""


@dataclass
class BatchSummary:
    """What a finished - or abandoned - batch produced."""

    total: int = 0
    suggested: int = 0
    needs_analyst: int = 0
    failed: int = 0
    skipped: int = 0
    cancelled: bool = False
    stopped_reason: str = ""
    failures: list[tuple[str, str]] = field(default_factory=list)

    @property
    def answered(self) -> int:
        return self.suggested + self.needs_analyst

    def describe(self) -> str:
        if self.cancelled:
            head = f"Stopped after {self.answered} of {self.total} plays"
        elif self.stopped_reason:
            head = f"Stopped: {self.stopped_reason}"
        else:
            head = f"First Read finished on {self.total} plays"
        if not self.answered:
            return head
        share = self.suggested / self.answered
        return (f"{head}. {self.suggested} suggested ({share:.0%}), "
                f"{self.needs_analyst} need your call"
                + (f", {self.failed} failed" if self.failed else ""))


def _analysis_json(clip) -> str:
    value = getattr(clip, "analysis_json", "")
    return (value() if callable(value) else value) or ""


def plan(clips, *, redo: bool = False) -> BatchPlan:
    """Decide which plays need a call, so the cost is known up front.

    A play that already carries a suggestion is skipped. The stored
    suggestion is the resume record - there is no side-car log to keep in
    step with the project, and quitting mid-run costs nothing but the play
    that was in flight.
    """
    to_read: list[str] = []
    already: list[str] = []
    for clip in clips:
        existing = load(_analysis_json(clip))
        if existing is not None and not redo:
            already.append(clip.id)
        else:
            to_read.append(clip.id)
    return BatchPlan(tuple(to_read), tuple(already))


def run(
        clips, *, ffmpeg_path: str, source: Path, scratch_dir: Path,
        api_key: str, save, model: str = "", now=None,
        on_progress=None, should_cancel=None, redo: bool = False,
        reader=None, sleep=time.sleep,
        backoff_s: float = DEFAULT_BACKOFF_S) -> BatchSummary:
    """Read every play that needs one, and store what comes back.

    ``save(clip_id, analysis_json)`` persists one result; it is called after
    each play rather than at the end, so a run that dies keeps everything it
    had already paid for.

    A play that fails to render or comes back unreadable is recorded and the
    batch continues - one bad clip in a cut-up should not cost the other
    seventy. A provider that refuses outright stops the run, because every
    remaining play would fail the same way and cost money doing it.
    """
    selected = plan(clips, redo=redo)
    wanted = set(selected.to_read)
    by_id = {clip.id: clip for clip in clips}
    summary = BatchSummary(total=len(wanted), skipped=len(selected.already_read))
    do_read = reader or read_play
    stamp = now or (lambda: "")

    for index, clip_id in enumerate(selected.to_read, start=1):
        if should_cancel is not None and should_cancel():
            summary.cancelled = True
            break
        clip = by_id[clip_id]
        # The first sheet rendered is the one the dialog shows; the second
        # view is the same play again and would only flicker.
        shown: list[Path] = []
        try:
            result = _read_with_backoff(
                do_read, clip, ffmpeg_path=ffmpeg_path, source=source,
                scratch_dir=scratch_dir, api_key=api_key, model=model,
                now=stamp(), sleep=sleep, backoff_s=backoff_s,
                should_cancel=should_cancel,
                on_sheet=lambda name, path: shown.append(path))
        except FirstReadCancelled:
            summary.cancelled = True
            break
        except FirstReadError as exc:
            # A refusal is about the key or the account, not this play.
            summary.stopped_reason = str(exc)
            break
        if result.error:
            summary.failed += 1
            summary.failures.append((clip_id, result.error))
        elif result.needs_analyst:
            summary.needs_analyst += 1
        else:
            summary.suggested += 1
        save(clip_id, store(_analysis_json(clip), result))
        if on_progress is not None:
            # The next play reuses these paths before a queued GUI slot may run.
            try:
                sheet_bytes = shown[0].read_bytes() if shown else b""
            except OSError:
                sheet_bytes = b""
            on_progress(BatchProgress(
                index=index, total=summary.total, clip_id=clip_id,
                read=result, sheet_path=shown[0] if shown else None,
                start_ms=clip.start_ms, end_ms=clip.end_ms,
                sheet_bytes=sheet_bytes))
    return summary


def _read_with_backoff(do_read, clip, *, ffmpeg_path, source, scratch_dir,
                       api_key, model, now, sleep, backoff_s,
                       on_sheet=None, should_cancel=None) -> FirstRead:
    """One play, waiting out throttling but never forever."""
    waits = 0
    while True:
        try:
            kwargs = {"api_key": api_key, "now": now, "on_sheet": on_sheet,
                      "should_cancel": should_cancel, "sleep": sleep}
            if model:
                kwargs["model"] = model
            return do_read(
                ffmpeg_path, source, clip.start_ms, clip.end_ms,
                scratch_dir, **kwargs)
        except FirstReadRateLimited as exc:
            waits += 1
            if waits > MAX_RATE_LIMIT_WAITS:
                # Report it as this play's failure rather than raising: the
                # limit may clear, and the plays already paid for are kept.
                return FirstRead(
                    label=None, agreement=False, created_at=now,
                    error="Rate limited - try again later.")
            _wait_for_retry(exc.retry_after or backoff_s,
                            should_cancel=should_cancel, sleep=sleep)
