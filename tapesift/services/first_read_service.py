"""First Read - a suggested run/pass call the analyst confirms.

Two contact sheets of the same play go to a vision model. When both views
agree the suggestion is offered; when they disagree the play is handed to the
analyst with no suggestion at all. Measured over 352 analyst-labelled plays
across 13 games, zero-shot:

    both views agree (80% of plays)   83.6% accuracy, 81.9% balanced
    the two views disagree            56.3% and 43.7% - both are guessing

That split is why disagreement is surfaced rather than tie-broken. Answering
PASS on every disagreement scores a higher headline (80.1%) while run recall
falls to 57.5%: it buys accuracy by conceding the class that matters, which
this project has already shipped once at 76.8% with zero of thirteen runs
found.

**A First Read is never truth.** It is written to ``clips.analysis_json`` and
nothing here ever writes ``run_pass``, which belongs to the analyst alone.
``run_pass_label_service`` treats an explicit analyst choice as the only
trainable label, and every dataset export reads that field - so a suggestion
cannot leak into research data even by mistake. Accepting a suggestion is the
analyst pressing R or P like any other play.

This is the one feature in TapeSift that contacts the network, it is off
unless the user turns it on, and it uses the user's own API key so the frames
travel from their machine to their provider without passing through us.
"""

from __future__ import annotations

import base64
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from tapesift.services import ffmpeg_service

#: Where a suggestion lives inside ``clips.analysis_json``.
ANALYSIS_KEY = "first_read"
SCHEMA_VERSION = "1.0"

RUN = "run"
PASS = "pass"

#: The default engine. Capability, not tuning, decides this: measured on the
#: same 157 plays, ``qwen3-vl-8b`` answered RUN to every play and Gemma 12B
#: answered PASS to almost all of them, while the 32B scored 73.2%. The line
#: sits somewhere between 8B and 32B, and nothing below it is worth tuning.
DEFAULT_MODEL = "qwen/qwen3-vl-32b-instruct"
DEFAULT_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

# A connection may disappear after the provider has accepted a request.
# Bound retries to avoid repeated charges during a sustained outage.
MAX_CONNECTION_ATTEMPTS = 3

#: Two views of the same play. Eight tiles is the primary read; sixteen is the
#: second opinion. Both sample the front half of the clip at full source width.
#:
#: Tile width is the second-largest lever after the model itself: at 480px a
#: lineman is about fifteen pixels tall and the whole call is whether he went
#: forward or backward. 960px bought +4.5 points for one extra cent. More
#: tiles do not help - the model has a fixed attention budget, and 25 tiles
#: makes a sheet large enough that it downsamples internally, giving back the
#: resolution just paid for.
VIEW_A = {"columns": 4, "rows": 2, "tile_width": 960, "window_fraction": 0.5}
VIEW_B = {"columns": 4, "rows": 4, "tile_width": 960, "window_fraction": 0.5}

_SHARED_OPENING = (
    "This is a contact sheet of frames from ONE American football play, "
    "sampled in time order (left to right, top to bottom) from coaches' "
    "All-22 film. The play may be shown from more than one camera angle; "
    "every angle shows the same single play.\n\n"
    "Decide whether the offense ran a RUN play or a PASS play.\n\n"
)

#: Prompt v2, verbatim from the measured configuration. Wording is a weak
#: lever - v2 fixed a real defect in v1 (it called a scramble a PASS, which
#: contradicts docs/RUN_PASS_LABELING.md) but changed nothing at all on the
#: worst film. Do not expect another rewrite to pay.
PROMPT = _SHARED_OPENING + (
    "RUN looks like: the offensive linemen fire forward and cross the line of "
    "scrimmage, blockers move downfield, and the players collapse into a pile "
    "at or beyond the line.\n"
    "PASS looks like: the offensive linemen retreat and form a pocket, the "
    "linemen stay behind the line of scrimmage, and receivers release "
    "downfield into coverage.\n\n"
    "The single most reliable cue is the offensive line. Linemen moving "
    "downfield past the line of scrimmage means RUN. Linemen backing up means "
    "PASS.\n\n"
    "Classification rules:\n"
    "- Designed quarterback run, quarterback draw, or a scramble where the "
    "quarterback takes off and runs: RUN\n"
    "- Run-pass option where the ball is handed off or the quarterback keeps "
    "it: RUN\n"
    "- Sack, screen pass, spike, or run-pass option where the ball is thrown: "
    "PASS\n\n"
    "Roughly 40% of these plays are runs. Do not treat PASS as the safe "
    "answer when the play is unclear - judge the offensive line and commit.\n\n"
) + (
    "Answer with exactly one word: RUN or PASS. No punctuation, no explanation."
)

PROMPT_VERSION = "v2"


class FirstReadError(Exception):
    """A First Read could not be produced. Carries a human-facing reason."""


class FirstReadCancelled(FirstReadError):
    """The analyst stopped before another request could be sent."""


class FirstReadRateLimited(FirstReadError):
    """The provider asked us to slow down.

    Separate from a plain failure because the batch answers it differently:
    a refusal stops the run, throttling only means waiting. ``retry_after``
    is the provider's own hint in seconds, 0 when it did not give one.
    """

    def __init__(self, message: str, retry_after: float = 0.0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class FirstRead:
    """One suggestion, or one honest refusal to suggest."""

    label: str | None
    """``run``, ``pass``, or None when the two views disagree."""

    agreement: bool
    views: tuple[str | None, str | None] = (None, None)
    model: str = DEFAULT_MODEL
    prompt_version: str = PROMPT_VERSION
    created_at: str = ""
    accepted_at: str = ""
    error: str = ""

    @property
    def needs_analyst(self) -> bool:
        """True when the model cannot be trusted on this play.

        Disagreement is not a weak preference to break. On the plays where
        the views differ, one scores 56.3% and the other 43.7% - the model is
        blind here, and saying so is the whole point of the second view.
        """
        return not self.agreement or self.label is None

    def to_payload(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "label": self.label or "",
            "agreement": self.agreement,
            "views": {"a": self.views[0] or "", "b": self.views[1] or ""},
            "model": self.model,
            "prompt_version": self.prompt_version,
            "frames_a": _view_signature(VIEW_A),
            "frames_b": _view_signature(VIEW_B),
            "created_at": self.created_at,
            "accepted_at": self.accepted_at,
            "error": self.error,
        }

    @classmethod
    def from_payload(cls, payload: dict) -> "FirstRead":
        views = payload.get("views") or {}
        return cls(
            label=(payload.get("label") or "") or None,
            agreement=bool(payload.get("agreement")),
            views=((views.get("a") or "") or None,
                   (views.get("b") or "") or None),
            model=payload.get("model", ""),
            prompt_version=payload.get("prompt_version", ""),
            created_at=payload.get("created_at", ""),
            accepted_at=payload.get("accepted_at", ""),
            error=payload.get("error", ""),
        )


def _view_signature(view: dict) -> str:
    """A short, stable description of how one sheet was sampled."""
    return (f"{view['columns']}x{view['rows']}@{view['tile_width']}px"
            f"/first{int(view['window_fraction'] * 100)}%")


def build_contact_sheet(
        ffmpeg_path: str, source: Path, start_ms: int, end_ms: int,
        out_path: Path, *, columns: int, rows: int, tile_width: int,
        window_fraction: float) -> bool:
    """Render one tiled contact sheet. False when FFmpeg produced nothing.

    Sampling only the front of the clip is deliberate. Spreading the same
    frames across a whole clip cost run recall badly: 58.3% on clips under
    25s against 12.5% over 45s, while pass recall held. A run is a ~3s event,
    so at one frame every five seconds it falls between samples and the play
    reads as a pass.
    """
    duration = (end_ms - start_ms) / 1000.0 * window_fraction
    count = columns * rows
    # Nudge the rate so the final tile lands inside the clip; past its end
    # FFmpeg pads the sheet with a repeat of the last frame.
    fps = count / max(duration - 0.25, 0.5)
    command = [
        ffmpeg_path, "-hide_banner", "-loglevel", "error",
        "-ss", f"{start_ms / 1000:.3f}", "-t", f"{duration:.3f}",
        "-i", str(source),
        "-vf", f"fps={fps:.6f},scale={tile_width}:-1,tile={columns}x{rows}",
        "-frames:v", "1", "-q:v", "3", "-y", str(out_path),
    ]
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Scratch names are reused between plays. A failed or empty render
        # must never make the previous play's sheet look like fresh evidence.
        out_path.unlink(missing_ok=True)
        result = subprocess.run(
            command, check=False, capture_output=True,
            creationflags=getattr(ffmpeg_service, "CREATE_NO_WINDOW", 0))
        return result.returncode == 0 and out_path.is_file() and out_path.stat().st_size > 0
    except (OSError, subprocess.SubprocessError):
        return False


def parse_answer(text: str) -> str | None:
    """Read RUN or PASS out of a reply, refusing anything ambiguous.

    Only a reply naming exactly one of the two words counts. "not a run, it
    is a pass" names both and is discarded rather than guessed at - a wrong
    parse is indistinguishable from a wrong model, and this project has spent
    a day on that confusion before.
    """
    low = (text or "").casefold()
    has_run, has_pass = "run" in low, "pass" in low
    if has_run == has_pass:
        return None
    return RUN if has_run else PASS


def _wait_for_retry(seconds, *, should_cancel=None, sleep=None) -> None:
    """Keep Stop responsive while waiting for another network attempt."""
    if sleep is None:
        from time import sleep
    while True:
        if should_cancel is not None and should_cancel():
            raise FirstReadCancelled("First Read stopped.")
        if seconds <= 0:
            return
        step = min(seconds, 0.1) if should_cancel is not None else seconds
        sleep(step)
        seconds -= step


def ask_model(image: bytes, *, api_key: str, model: str = DEFAULT_MODEL,
              endpoint: str = DEFAULT_ENDPOINT, timeout: int = 120,
              opener=None, should_cancel=None, sleep=None) -> str:
    """Send one contact sheet and return the raw reply text.

    The key is used here and never stored in a project, a log, or an export.
    """
    if not api_key:
        raise FirstReadError("First Read needs your API key. Add it in Settings.")
    body = {
        "model": model,
        "temperature": 0,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64,"
                       + base64.b64encode(image).decode()}},
        ]}],
    }
    pinned_qwen = model == DEFAULT_MODEL and endpoint.rstrip("/") == DEFAULT_ENDPOINT
    if pinned_qwen:
        # Match the measured Qwen route; fail rather than switch providers.
        body.update(top_k=1, seed=0, max_tokens=64, provider={
            "only": ["alibaba"], "allow_fallbacks": False,
            "require_parameters": True,
        })
    # Imported here, not at module top: this module reaches the clip editor
    # and clip list through first_read_ui, so every launch was paying for
    # urllib.request (and http.client, email.parser behind it - 25 ms of
    # import time) to support an opt-in network call nobody makes at start.
    import urllib.error
    import urllib.request
    import http.client
    import math
    import ssl

    request = urllib.request.Request(
        endpoint, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"})
    open_url = opener or urllib.request.urlopen
    transient_errors = (ConnectionError, TimeoutError,
                        http.client.IncompleteRead, ssl.SSLEOFError)
    for attempt in range(MAX_CONNECTION_ATTEMPTS):
        if should_cancel is not None and should_cancel():
            raise FirstReadCancelled("First Read stopped.")
        try:
            with open_url(request, timeout=timeout) as response:
                payload = json.load(response)
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:200].decode(errors="replace")
            if exc.code == 429:
                hint = exc.headers.get("Retry-After", "") if exc.headers else ""
                try:
                    retry_after = float(hint)
                except (TypeError, ValueError):
                    retry_after = 0.0
                if not math.isfinite(retry_after) or retry_after < 0:
                    retry_after = 0.0
                raise FirstReadRateLimited(
                    "The First Read provider is rate limiting this key.",
                    retry_after) from exc
            raise FirstReadError(
                f"The First Read provider refused the request ({exc.code}). "
                f"{detail}") from exc
        except (urllib.error.URLError, *transient_errors) as exc:
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            if not isinstance(reason, transient_errors) or attempt + 1 == MAX_CONNECTION_ATTEMPTS:
                raise FirstReadError(
                    f"Could not reach the First Read provider: {reason}") from exc
            # Retry this image only: a successful first view must not be replayed.
            _wait_for_retry(attempt + 1, should_cancel=should_cancel, sleep=sleep)
    if pinned_qwen and (
            not isinstance(payload, dict)
            or payload.get("provider") != "Alibaba"
            or payload.get("model") != DEFAULT_MODEL):
        raise FirstReadError(
            "First Read could not verify the expected Qwen model on Alibaba. "
            "No suggestion was accepted.")
    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise FirstReadError("The provider sent a reply we could not read.") from exc


def read_play(
        ffmpeg_path: str, source: Path, start_ms: int, end_ms: int,
        scratch_dir: Path, *, api_key: str, model: str = DEFAULT_MODEL,
        now: str = "", asker=None, on_sheet=None,
        should_cancel=None, sleep=None) -> FirstRead:
    """Two views of one play, and a suggestion only when they agree.

    ``on_sheet(view_name, path)`` fires as each sheet is rendered, before it
    is sent. The progress dialog shows exactly the frames the model is about
    to receive, so a misread play can be understood rather than just counted.
    """
    ask = asker or (lambda img: ask_model(
        img, api_key=api_key, model=model,
        should_cancel=should_cancel, sleep=sleep))
    answers: list[str | None] = []
    for name, view in (("a", VIEW_A), ("b", VIEW_B)):
        if should_cancel is not None and should_cancel():
            raise FirstReadCancelled("First Read stopped.")
        sheet = scratch_dir / f"first-read-{name}.jpg"
        if not build_contact_sheet(
                ffmpeg_path, source, start_ms, end_ms, sheet, **view):
            return FirstRead(
                label=None, agreement=False, created_at=now,
                error="Could not render frames for this play.")
        if on_sheet is not None:
            on_sheet(name, sheet)
        if should_cancel is not None and should_cancel():
            raise FirstReadCancelled("First Read stopped.")
        answers.append(parse_answer(ask(sheet.read_bytes())))
    first, second = answers[0], answers[1]
    agree = first is not None and first == second
    return FirstRead(
        label=first if agree else None,
        agreement=agree,
        views=(first, second),
        model=model,
        created_at=now,
    )


def store(analysis_json: str, read: FirstRead) -> str:
    """Put a suggestion into a clip's analysis JSON, leaving the rest alone."""
    try:
        data = json.loads(analysis_json) if analysis_json else {}
    except (json.JSONDecodeError, TypeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data[ANALYSIS_KEY] = read.to_payload()
    return json.dumps(data)


def load(analysis_json: str) -> FirstRead | None:
    """Read back a stored suggestion, or None when there is not one."""
    try:
        data = json.loads(analysis_json) if analysis_json else {}
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    payload = data.get(ANALYSIS_KEY)
    if not isinstance(payload, dict):
        return None
    return FirstRead.from_payload(payload)
