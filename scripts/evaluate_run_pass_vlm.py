"""Experiment 0 - can a vision model read run/pass off All-22 contact sheets?

Zero-shot, no training, no folds: the model sees one tiled contact sheet per
play and answers run or pass. This measures whether the signal is legible at
this resolution at all. It is a ceiling probe, not a candidate model.

Two backends:
  --backend gemini   cloud; needs GEMINI_API_KEY in the environment
  --backend ollama   local; default model gemma4:12b-it-q4_K_M

Reported beside every number: the majority-class reference, per-class recall,
and per-game accuracy. Accuracy alone once hid a model here that answered
"pass" to all thirteen runs it was given.

The key is read from the environment and never written to any output file.
"""

from __future__ import annotations

import argparse
import base64
import collections
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tapesift.research.run_pass_baseline import classification_metrics  # noqa: E402
from tapesift.research.run_pass_features import (  # noqa: E402
    reject_quarantined_input,
)

_SHARED_OPENING = (
    "This is a contact sheet of frames from ONE American football play, "
    "sampled in time order (left to right, top to bottom) from coaches' "
    "All-22 film. The play may be shown from more than one camera angle; "
    "every angle shows the same single play.\n\n"
    "Decide whether the offense ran a RUN play or a PASS play.\n\n"
)
_SHARED_CLOSING = (
    "Answer with exactly one word: RUN or PASS. No punctuation, no explanation."
)

# v1 - scored 70.3% accuracy / 65.3% balanced on 195 plays, but with 89.0%
# pass recall against 41.6% run recall. Kept verbatim so v2 can be compared
# against it rather than against a remembered number. It has two defects that
# both push toward PASS: pass cues are described first and at greater length,
# and the scramble rule contradicts docs/RUN_PASS_LABELING.md, which makes a
# scramble a RUN.
PROMPT_V1 = _SHARED_OPENING + (
    "Useful cues: on a pass the offensive linemen retreat and form a pocket "
    "and receivers release downfield; on a run the linemen fire forward past "
    "the line of scrimmage and the players collapse into a pile near the "
    "line. A sack or a scramble still counts as PASS if the quarterback "
    "dropped back to throw. A designed quarterback run counts as RUN.\n\n"
) + _SHARED_CLOSING

# v2 - run cues first and at equal length, the labeling taxonomy stated in
# full and symmetrically, and an explicit instruction not to treat PASS as the
# safe answer.
PROMPT_V2 = _SHARED_OPENING + (
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
) + _SHARED_CLOSING

PROMPT_PROXY = (
    "This is a contact sheet of frames from ONE American football play, "
    "sampled in time order from coaches' All-22 film. Did any offensive "
    "lineman cross the line of scrimmage after the snap? Answer with exactly "
    "one word: YES or NO."
)

PROMPTS = {"v1": PROMPT_V1, "v2": PROMPT_V2, "proxy": PROMPT_PROXY}
# Rebound by --prompt-version so the request builders stay simple.
ACTIVE_PROMPT = {"text": PROMPT_V2, "version": "v2"}

# Asking for a written reason made both backends generate long replies - Gemini
# ~80s per play and Gemma ~90s, against 8s and 26s for a one-word answer. The
# reason text was never scored, so it cost hours to collect and bought nothing.


# thinkingBudget 0 skips the hidden reasoning pass, which dominated latency on
# a one-word visual call. Not every model accepts the field - flash-lite
# answers HTTP 400 - so the first 400 turns it off for the rest of the run.
_SUPPORTS_THINKING_CONFIG = {"value": True}
_SUPPORTS_OPENROUTER_LOGPROBS = {"value": True}

_OPENROUTER_OUTPUT_CONSTRAINT = (
    "Your entire response must be exactly one uppercase word: RUN or PASS. "
    "Do not add punctuation, explanation, or any other text."
)


class KeyPool:
    """Round-robin over several Gemini keys.

    Each key carries its own free-tier request cap, so rotating multiplies the
    quota. A key that answers 429 is parked until its cooldown expires rather
    than retried in place, which is what makes the pool worth having.
    """

    def __init__(self, keys: list[str], cooldown: float = 65.0) -> None:
        self.keys = keys
        self.cooldown = cooldown
        self.available_at = [0.0] * len(keys)
        self.index = 0
        self.rate_limited = [0] * len(keys)

    def acquire(self) -> tuple[int, str]:
        """Return the next usable key, waiting only if every key is parked."""
        for _ in range(len(self.keys)):
            slot = self.index
            self.index = (self.index + 1) % len(self.keys)
            if time.monotonic() >= self.available_at[slot]:
                return slot, self.keys[slot]
        soonest = min(self.available_at)
        delay = max(0.0, soonest - time.monotonic())
        if delay:
            print(f"  (all {len(self.keys)} keys rate-limited; waiting {delay:.0f}s)")
            time.sleep(delay)
        slot = self.available_at.index(soonest)
        return slot, self.keys[slot]

    def park(self, slot: int, seconds: float | None = None) -> None:
        self.available_at[slot] = time.monotonic() + (seconds or self.cooldown)
        self.rate_limited[slot] += 1


def retry_delay_from(message: str) -> float | None:
    """Honour the server's own 'Please retry in 26.3s' when it offers one."""
    match = re.search(r"retry in ([0-9.]+)s", message)
    return float(match.group(1)) + 2.0 if match else None


def ask_gemini(image: bytes, model: str, api_key: str, timeout: int) -> dict:
    def call(with_thinking: bool) -> dict:
        config: dict = {"temperature": 0, "maxOutputTokens": 2048}
        if with_thinking:
            config["thinkingConfig"] = {"thinkingBudget": 0}
        body = json.dumps({
            "contents": [{"parts": [
                {"text": ACTIVE_PROMPT["text"]},
                {"inline_data": {"mime_type": "image/jpeg",
                                 "data": base64.b64encode(image).decode()}},
            ]}],
            "generationConfig": config,
        }).encode()
        request = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            data=body,
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
        parts = payload["candidates"][0].get("content", {}).get("parts", [])
        return {"text": "".join(p.get("text", "") for p in parts)}

    if _SUPPORTS_THINKING_CONFIG["value"]:
        try:
            return call(True)
        except urllib.error.HTTPError as exc:
            if exc.code != 400:
                raise
            _SUPPORTS_THINKING_CONFIG["value"] = False
            print("  (model rejects thinkingConfig; continuing without it)")
    return call(False)


def run_pass_logprob_evidence(logprobs: dict | None,
                              answer: str | None) -> dict:
    """Read RUN/PASS alternatives at the token that contains the answer."""
    content = (logprobs or {}).get("content") or []
    for index, generated in enumerate(content):
        generated_label = str(generated.get("token", "")).strip().casefold()
        if generated_label not in ("run", "pass"):
            continue
        if answer in ("run", "pass") and generated_label != answer:
            continue

        candidates = [generated, *(generated.get("top_logprobs") or [])]
        variants: dict[str, dict[tuple, tuple[str, float]]] = {
            "run": {}, "pass": {}}
        for entry in candidates:
            token = str(entry.get("token", "")).strip().casefold()
            if token in ("run", "pass") and entry.get("logprob") is not None:
                raw_bytes = entry.get("bytes")
                variant = (("bytes", *raw_bytes) if raw_bytes is not None
                           else ("token", str(entry.get("token", ""))))
                value = float(entry["logprob"])
                previous = variants[token].get(variant)
                if previous is None or value > previous[1]:
                    variants[token][variant] = (
                        str(entry.get("token", "")), value)
        if not variants["run"] or not variants["pass"]:
            continue

        class_logprobs = {}
        for label, token_variants in variants.items():
            token_logprobs = [value for _, value in token_variants.values()]
            peak = max(token_logprobs)
            class_logprobs[label] = peak + math.log(sum(
                math.exp(value - peak) for value in token_logprobs
            ))
        chosen = class_logprobs[generated_label]
        other_label = "pass" if generated_label == "run" else "run"
        other = class_logprobs[other_label]
        if chosen < other:
            raise AssertionError(
                "answer-token logprob extraction failed: "
                f"chosen={generated_label} {chosen:.9f} < "
                f"{other_label} {other:.9f} at token index {index}; "
                f"variants={{{', '.join(f'{label}: {list(items.values())}' for label, items in variants.items())}}}"
            )

        tied = class_logprobs["run"] == class_logprobs["pass"]
        if tied:
            p_run = 0.5
        else:
            peak = max(class_logprobs.values())
            run_weight = math.exp(class_logprobs["run"] - peak)
            pass_weight = math.exp(class_logprobs["pass"] - peak)
            p_run = run_weight / (run_weight + pass_weight)
        return {
            "p_run": p_run,
            "p_run_tie": tied,
            "p_run_token_index": index,
            "p_run_token": str(generated.get("token", "")),
            "run_logprob": class_logprobs["run"],
            "pass_logprob": class_logprobs["pass"],
            "run_token_variants": list(variants["run"].values()),
            "pass_token_variants": list(variants["pass"].values()),
        }
    return {
        "p_run": None,
        "p_run_tie": None,
        "p_run_token_index": None,
        "p_run_token": None,
        "run_logprob": None,
        "pass_logprob": None,
        "run_token_variants": [],
        "pass_token_variants": [],
    }


def openrouter_provider_metadata(model: str, provider: str,
                                 timeout: int) -> dict:
    request = urllib.request.Request(
        f"https://openrouter.ai/api/v1/models/{model}/endpoints",
        headers={"Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    endpoints = (payload.get("data") or {}).get("endpoints") or []
    matches = [
        endpoint for endpoint in endpoints
        if str(endpoint.get("provider_name", "")).casefold() == provider.casefold()
    ]
    if not matches:
        available = sorted({
            str(endpoint.get("provider_name", "")) for endpoint in endpoints
            if endpoint.get("provider_name")
        })
        raise SystemExit(
            f"OpenRouter provider {provider!r} is unavailable for {model}; "
            f"available={available}"
        )
    quantizations = sorted({
        str(endpoint.get("quantization") or "unknown") for endpoint in matches
    })
    return {
        "provider": str(matches[0].get("provider_name")),
        "quantization": ",".join(quantizations),
        "endpoint_names": [str(endpoint.get("name", "")) for endpoint in matches],
    }


def ask_openrouter(image: bytes, model: str, api_key: str, timeout: int,
                   max_tokens: int = 16,
                   reasoning_effort: str | None = None,
                   few_shot: list[tuple[bytes, str]] | None = None,
                   samples: int = 1,
                   temperature: float = 0.0,
                   require_logprobs: bool = False,
                   request_logprobs: bool = True,
                   system_prompt: str | None = None,
                   provider: str | None = None,
                   provider_quantization: str | None = None,
                   seed: int | None = 0) -> dict:
    """One OpenAI-shaped chat call, with token confidence when supported."""
    def call(with_logprobs: bool) -> tuple[list[dict], dict, dict]:
        messages = []
        if system_prompt is not None:
            system_text = system_prompt
            if few_shot:
                system_text += " Use the labeled film examples before classifying the final play."
            messages.append({"role": "system", "content": system_text})
        if few_shot:
            for example_image, label in few_shot:
                messages.extend([
                    {"role": "user", "content": [
                        {"type": "text", "text": "Labeled example:"},
                        {"type": "image_url", "image_url": {"url":
                            "data:image/jpeg;base64," +
                            base64.b64encode(example_image).decode()}},
                    ]},
                    {"role": "assistant", "content": label.upper()},
                ])
        messages.append({"role": "user", "content": [
            {"type": "text", "text": ACTIVE_PROMPT["text"]},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,"
                                                   + base64.b64encode(image).decode()}},
        ]})
        request_body = {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if temperature == 0:
            # OpenRouter advertises top_k=20 for this route and has returned a
            # lower-probability label at temperature zero. top_k=1 makes the
            # emitted label a direct check on the answer-token argmax.
            request_body["top_k"] = 1
        if seed is not None:
            request_body["seed"] = seed
        if provider:
            request_body["provider"] = {
                "order": [provider],
                "allow_fallbacks": False,
            }
        if reasoning_effort == "off":
            request_body["reasoning"] = {"enabled": False}
        elif reasoning_effort:
            request_body["reasoning"] = {"effort": reasoning_effort,
                                          "exclude": True}
        if with_logprobs:
            request_body.update({"logprobs": True, "top_logprobs": 5})
        request = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(request_body).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}",
                     "HTTP-Referer": "https://tapesift.local",
                     "X-Title": "TapeSift run/pass probe"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
        serving_provider = payload.get("provider")
        if provider and (
            not serving_provider
            or str(serving_provider).casefold() != provider.casefold()
        ):
            raise AssertionError(
                f"provider pin failed: requested={provider!r}, "
                f"served={serving_provider!r}"
            )
        response_metadata = {
            "serving_provider": serving_provider,
            "provider_quantization": provider_quantization,
            "served_model": payload.get("model"),
            "generation_id": payload.get("id"),
            "system_fingerprint": payload.get("system_fingerprint"),
        }
        return (payload.get("choices") or [{}], payload.get("usage") or {},
                response_metadata)

    with_logprobs = (request_logprobs
                     and _SUPPORTS_OPENROUTER_LOGPROBS["value"]
                     and samples == 1)
    if samples > 1:
        choices, costs, response_metadata = [], [], {}
        for _ in range(samples):
            batch, batch_usage, response_metadata = call(False)
            choices.append(batch[0])
            if batch_usage.get("cost") is not None:
                costs.append(float(batch_usage["cost"]))
        usage = {"cost": sum(costs) if len(costs) == samples else None}
    else:
        try:
            choices, usage, response_metadata = call(with_logprobs)
        except urllib.error.HTTPError as exc:
            if not with_logprobs or exc.code != 400:
                raise
            if require_logprobs:
                detail = exc.read()[:300].decode(errors="replace")
                raise AssertionError(
                    f"required token logprobs rejected: HTTP 400 {detail}") from exc
            _SUPPORTS_OPENROUTER_LOGPROBS["value"] = False
            print("  (model rejects token logprobs; use the two-view confidence path)")
            choices, usage, response_metadata = call(False)

    choice = choices[0]
    logprobs = choice.get("logprobs")
    if with_logprobs and logprobs is None:
        if require_logprobs:
            raise AssertionError("required token logprobs missing from response")
        _SUPPORTS_OPENROUTER_LOGPROBS["value"] = False
        print("  (model omits token logprobs; use the two-view confidence path)")
    if samples > 1:
        votes = [
            parse_answer((item.get("message") or {}).get("content") or "")[0]
            for item in choices
        ]
        valid = [vote for vote in votes if vote in ("run", "pass")]
        if valid:
            counts = collections.Counter(valid)
            winner, count = counts.most_common(1)[0]
            return {
                "text": winner.upper(),
                "p_run": counts["run"] / len(valid),
                "confidence": count / len(valid),
                "reason": "votes=" + ",".join(str(vote) for vote in votes),
                "cost_usd": usage.get("cost"),
                **response_metadata,
            }
    text = (choice.get("message") or {}).get("content") or ""
    answer = parse_answer(text)[0]
    evidence = run_pass_logprob_evidence(logprobs, answer)
    return {
        "text": text,
        **evidence,
        "cost_usd": usage.get("cost"),
        **response_metadata,
    }


def ask_ollama(image: bytes, model: str, timeout: int) -> dict:
    body = json.dumps({
        "model": model,
        "prompt": ACTIVE_PROMPT["text"],
        "images": [base64.b64encode(image).decode()],
        "stream": False,
        # Gemma 4 reasons before answering and Ollama does not surface those
        # tokens, so the reply comes back an empty string with
        # done_reason="length" - a silent total failure, not a slow answer.
        # A one-word visual verdict does not need a reasoning pass anyway.
        "think": False,
        "options": {"temperature": 0},
    }).encode()
    request = urllib.request.Request(
        "http://localhost:11434/api/generate", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return {"text": json.load(response).get("response", "")}


def parse_answer(text: str) -> tuple[str | None, float, str]:
    """Pull the verdict out of the reply, tolerating fences and stray prose."""
    cleaned = text.strip()
    if ACTIVE_PROMPT["version"] == "proxy":
        words = set(re.findall(r"[a-z]+", cleaned.casefold()))
        if ("yes" in words) != ("no" in words):
            return ("run" if "yes" in words else "pass"), 0.0, "proxy mapped"
    if "```" in cleaned:
        cleaned = cleaned.split("```")[1].removeprefix("json").strip()
    if cleaned.casefold() in ("run", "pass"):
        return cleaned.casefold(), 0.0, "bare word"
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(cleaned[start:end + 1])
            answer = str(parsed.get("answer", "")).strip().casefold()
            if answer in ("run", "pass"):
                return answer, float(parsed.get("confidence", 0.0)), str(parsed.get("reason", ""))
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    # Fall back to prose, but only when exactly one of the two appears,
    # so "not a run, it is a pass" cannot be scored as a run.
    labels = set(re.findall(r"\b(?:run|pass)\b", text.casefold()))
    if len(labels) == 1:
        return labels.pop(), 0.0, "recovered from prose"
    return None, 0.0, "unparsed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strips-dir", type=Path,
                        default=ROOT / "research" / "run_pass_working" / "experiment-0" / "strips")
    parser.add_argument("--backend", choices=("gemini", "ollama", "openrouter"),
                        default="gemini")
    parser.add_argument("--model", default=None)
    parser.add_argument("--games", nargs="*", default=None)
    parser.add_argument("--limit", type=int, default=0, help="plays per game (0 = all)")
    parser.add_argument("--sleep", type=float, default=0.0,
                        help="seconds between calls; use for free-tier rate limits")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--max-tokens", type=int, default=16)
    parser.add_argument("--samples", type=int, default=1,
                        help="OpenRouter choices per play; majority vote when >1")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--reasoning-effort",
                        choices=("off", "none", "minimal", "low", "medium",
                                 "high", "xhigh", "max"), default=None,
                        help="OpenRouter reasoning control; off sends enabled=false")
    parser.add_argument("--require-logprobs", action="store_true",
                        help="abort instead of saving a run without token logprobs")
    parser.add_argument("--no-logprobs", action="store_true",
                        help="omit logprobs for a provider-pinned baseline run")
    parser.add_argument("--force-one-word-system-prompt", action="store_true",
                        help="add a system message forcing a one-word reply")
    parser.add_argument("--openrouter-provider", default=None,
                        help="pin one OpenRouter provider with fallbacks disabled")
    parser.add_argument("--seed", type=int, default=0,
                        help="OpenRouter sampling seed; use the same value for replicas")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--prompt-version", choices=tuple(PROMPTS), default="v2",
                        help="v1 reproduces the first sweep; v2 rebalances the "
                             "run/pass cues and fixes the scramble rule")
    parser.add_argument("--resume-from-log", type=Path, default=None,
                        help="reuse answers already recorded in a run log, and "
                             "only call the model for the plays still missing")
    parser.add_argument("--few-shot-dir", type=Path, default=None,
                        help="manifest directory containing 4-8 labeled examples")
    args = parser.parse_args()
    args.strips_dir = reject_quarantined_input(args.strips_dir)
    ACTIVE_PROMPT["text"] = PROMPTS[args.prompt_version]
    ACTIVE_PROMPT["version"] = args.prompt_version

    # 3.6/3.7-flash return 503 "high demand" on the free tier as of 2026-08-30;
    # 3.5-flash is the newest that answers reliably there.
    default_model = {"gemini": "gemini-3.5-flash",
                     "ollama": "gemma4:12b-it-q4_K_M",
                     "openrouter": "qwen/qwen3-vl-32b-instruct"}
    model = args.model or default_model[args.backend]
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    if args.backend == "openrouter" and not openrouter_key:
        raise SystemExit("set OPENROUTER_API_KEY in the environment")
    if args.require_logprobs and not args.openrouter_provider:
        raise SystemExit("--require-logprobs requires --openrouter-provider")
    if args.require_logprobs and args.no_logprobs:
        raise SystemExit("--require-logprobs and --no-logprobs are incompatible")
    if args.resume_from_log and args.openrouter_provider:
        raise SystemExit(
            "provider-pinned runs cannot resume answer-only logs; rerun from scratch")
    # GEMINI_API_KEYS may hold several comma-separated keys; each has its own
    # free-tier cap, so the pool rotates through them.
    raw_keys = os.environ.get("GEMINI_API_KEYS") or os.environ.get("GEMINI_API_KEY", "")
    keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
    if args.backend == "gemini" and not keys:
        raise SystemExit("set GEMINI_API_KEY (or GEMINI_API_KEYS) in the environment")
    pool = KeyPool(keys) if keys else None
    provider_metadata = {}
    if args.backend == "openrouter" and args.openrouter_provider:
        provider_metadata = openrouter_provider_metadata(
            model, args.openrouter_provider, args.timeout)
        print("pinned OpenRouter provider="
              f"{provider_metadata['provider']} "
              f"quantization={provider_metadata['quantization']} "
              "allow_fallbacks=false")

    manifest = json.loads((args.strips_dir / "strips-manifest.json").read_text())
    records = manifest["records"]
    if args.games:
        records = [r for r in records if r["game_group"] in args.games]
    if args.limit:
        kept, seen = [], collections.Counter()
        for r in records:
            if seen[r["game_group"]] < args.limit:
                kept.append(r)
                seen[r["game_group"]] += 1
        records = kept

    few_shot: list[tuple[bytes, str]] = []
    few_shot_records = []
    if args.few_shot_dir:
        if args.backend != "openrouter":
            raise SystemExit("--few-shot-dir currently requires --backend openrouter")
        args.few_shot_dir = reject_quarantined_input(args.few_shot_dir)
        few_shot_manifest = json.loads(
            (args.few_shot_dir / "strips-manifest.json").read_text()
        )
        few_shot_records = few_shot_manifest["records"]
        labels = collections.Counter(r["label"] for r in few_shot_records)
        evaluation_ids = {str(r.get("clip_id")) for r in records}
        example_ids = {str(r.get("clip_id")) for r in few_shot_records}
        if not 4 <= len(few_shot_records) <= 8:
            raise SystemExit("few-shot manifest must contain 4-8 examples")
        if labels["run"] < 2 or labels["pass"] < 2:
            raise SystemExit("few-shot manifest needs at least two runs and two passes")
        if evaluation_ids & example_ids:
            raise SystemExit("few-shot examples overlap the evaluation cohort")
        if any(marker in str(r.get("game_group", "")).casefold()
               for marker in ("ohio state", "oklahoma")
               for r in few_shot_records):
            raise SystemExit("ABORT - sealed film referenced by few-shot manifest")
        few_shot = [
            ((args.few_shot_dir / r["strip"]).read_bytes(), r["label"])
            for r in few_shot_records
        ]
        print(f"few-shot examples={len(few_shot)} "
              f"(run={labels['run']}, pass={labels['pass']})")

    # Every progress line carries the model's answer, so a killed run is fully
    # recoverable from its log - no call is ever paid for twice.
    already: dict[tuple[str, int], tuple[str, float | None]] = {}
    if args.resume_from_log and args.resume_from_log.exists():
        pattern = re.compile(
            r"\]\s+(\S+)\s+clip\s+(\d+)\s+truth=(\w+)\s*model=(\w+)")
        # PowerShell's Tee-Object writes UTF-16LE with a BOM, so a run log
        # captured from a console window is not the UTF-8 the name implies.
        blob = args.resume_from_log.read_bytes()
        if blob[:2] in (b"\xff\xfe", b"\xfe\xff"):
            text = blob.decode("utf-16", errors="replace")
        else:
            text = blob.decode("utf-8", errors="replace")
        for line in text.splitlines():
            match = pattern.search(line)
            if match and match.group(4) in ("run", "pass"):
                probability = re.search(r"\bp_run=([0-9.]+)", line)
                already[(match.group(1), int(match.group(2)))] = (
                    match.group(4), float(probability.group(1)) if probability else None)
        print(f"resuming: {len(already)} answers recovered from "
              f"{args.resume_from_log.name}")

    print(f"backend={args.backend} model={model} plays={len(records)}\n")

    results, errors = [], []
    for index, record in enumerate(records, 1):
        cached = already.get((record["game_group"], record["clip_number"]))
        if cached:
            cached_answer, p_run = cached
            mark = "ok " if cached_answer == record["label"] else "MISS"
            probability = "" if p_run is None else f" p_run={p_run:.6f}"
            print(f"[{index:>3}/{len(records)}] {record["game_group"]:<34}"
                  f"clip {record['clip_number']:>3}  truth={record['label']:<5}"
                  f"model={cached_answer:<5} {mark}{probability} (cached)")
            results.append({**record, "predicted": cached_answer,
                            "confidence": 0.0, "p_run": p_run,
                            "reason": "recovered from log", "raw": ""})
            continue

        image = (args.strips_dir / record["strip"]).read_bytes()
        image_sha256 = hashlib.sha256(image).hexdigest()
        answer, confidence, p_run, p_run_tie, p_run_token_index, p_run_token = (
            None, 0.0, None, None, None, None)
        run_logprob, pass_logprob = None, None
        run_token_variants, pass_token_variants = [], []
        serving_provider = provider_quantization = served_model = None
        generation_id = system_fingerprint = None
        cost_usd, reason, raw = None, "", ""
        for attempt in range(args.retries):
            slot = -1
            try:
                if args.backend == "gemini":
                    slot, key = pool.acquire()
                    reply = ask_gemini(image, model, key, args.timeout)
                elif args.backend == "openrouter":
                    reply = ask_openrouter(
                        image=image,
                        model=model,
                        api_key=openrouter_key,
                        timeout=args.timeout,
                        max_tokens=args.max_tokens,
                        reasoning_effort=args.reasoning_effort,
                        few_shot=few_shot,
                        samples=args.samples,
                        temperature=args.temperature,
                        require_logprobs=args.require_logprobs,
                        request_logprobs=not args.no_logprobs,
                        system_prompt=(
                            _OPENROUTER_OUTPUT_CONSTRAINT
                            if args.force_one_word_system_prompt else None),
                        provider=args.openrouter_provider,
                        provider_quantization=provider_metadata.get("quantization"),
                        seed=args.seed,
                    )
                else:
                    reply = ask_ollama(image, model, args.timeout)
                raw = reply["text"]
                p_run = reply.get("p_run")
                p_run_tie = reply.get("p_run_tie")
                p_run_token_index = reply.get("p_run_token_index")
                p_run_token = reply.get("p_run_token")
                run_logprob = reply.get("run_logprob")
                pass_logprob = reply.get("pass_logprob")
                run_token_variants = reply.get("run_token_variants", [])
                pass_token_variants = reply.get("pass_token_variants", [])
                serving_provider = reply.get("serving_provider")
                provider_quantization = reply.get("provider_quantization")
                served_model = reply.get("served_model")
                generation_id = reply.get("generation_id")
                system_fingerprint = reply.get("system_fingerprint")
                cost_usd = reply.get("cost_usd")
                answer, confidence, reason = parse_answer(raw)
                confidence = float(reply.get("confidence", confidence))
                reason = str(reply.get("reason", reason))
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read()[:300].decode(errors="replace")
                if exc.code == 429 and slot >= 0:
                    # Park this key and let the next one take the call; only
                    # sleep if every key in the pool is exhausted.
                    pool.park(slot, retry_delay_from(detail))
                    if attempt < args.retries - 1:
                        continue
                elif exc.code in (500, 502, 503) and attempt < args.retries - 1:
                    time.sleep(3 * (attempt + 1))
                    continue
                errors.append((record["game_group"], record["clip_number"],
                               f"HTTP {exc.code} {detail[:120]}"))
                break
            except AssertionError:
                raise
            except Exception as exc:  # noqa: BLE001 - report, do not abort the sweep
                if attempt < args.retries - 1:
                    time.sleep(3)
                    continue
                errors.append((record["game_group"], record["clip_number"],
                               f"{type(exc).__name__}: {exc}"))
                break

        mark = "?" if answer is None else ("ok " if answer == record["label"] else "MISS")
        # Show the failure on the line it happened on. Collecting errors only
        # for the final summary hid a rate-limit wall behind 195 "model=None".
        note = ""
        if answer is None:
            note = f"  <- {errors[-1][2][:90]}" if errors else f"  <- unparsed: {raw[:60]!r}"
        probability = "" if p_run is None else f" p_run={p_run:.6f}"
        cost = "" if cost_usd is None else f" cost_usd={float(cost_usd):.9f}"
        print(f"[{index:>3}/{len(records)}] {record["game_group"]:<34}"
              f"clip {record['clip_number']:>3}  truth={record['label']:<5}"
              f"model={str(answer):<5} {mark}{probability}{cost}{note}")
        results.append({**record, "predicted": answer, "confidence": confidence,
                        "image_sha256": image_sha256,
                        "p_run": p_run,
                        "p_run_tie": p_run_tie,
                        "p_run_token_index": p_run_token_index,
                        "p_run_token": p_run_token,
                        "run_logprob": run_logprob,
                        "pass_logprob": pass_logprob,
                        "run_token_variants": run_token_variants,
                        "pass_token_variants": pass_token_variants,
                        "serving_provider": serving_provider,
                        "provider_quantization": provider_quantization,
                        "served_model": served_model,
                        "generation_id": generation_id,
                        "system_fingerprint": system_fingerprint,
                        "bare_answer": raw.strip().casefold() in ("run", "pass"),
                        "cost_usd": cost_usd,
                        "reason": reason, "raw": raw[:400]})
        if args.sleep:
            time.sleep(args.sleep)

    scored = [r for r in results if r["predicted"] in ("run", "pass")]
    coverage = len(scored) / len(results) if results else 0.0
    print("\n" + "=" * 78)
    print(f"coverage: {len(scored)}/{len(results)} ({coverage:.1%})")
    if errors:
        print(f"errors: {len(errors)}")
        for group, clip, why in errors[:10]:
            print(f"  {group} clip {clip}: {why}")
    if not scored:
        print("nothing scored")
        return 1

    actual = [r["label"] for r in scored]
    predicted = [r["predicted"] for r in scored]
    metrics = classification_metrics(actual, predicted)
    majority = collections.Counter(actual).most_common(1)[0][0]
    majority_metrics = classification_metrics(actual, [majority] * len(actual))

    print(f"\n{'game group':<20}{'n':>5}{'acc':>8}{'bal acc':>9}{'run rec':>9}{'pass rec':>10}{'majority':>10}")
    print("-" * 71)
    per_game = []
    for group in sorted({r["game_group"] for r in scored}):
        rows = [r for r in scored if r["game_group"] == group]
        truth = [r["label"] for r in rows]
        guess = [r["predicted"] for r in rows]
        if len(set(truth)) < 2:
            print(f"{group:<20}{len(rows):>5}   single-class fold, not scored")
            continue
        game_metrics = classification_metrics(truth, guess)
        game_majority = collections.Counter(truth).most_common(1)[0][0]
        game_majority_accuracy = truth.count(game_majority) / len(truth)
        print(f"{group:<20}{len(rows):>5}{game_metrics['accuracy']:>8.1%}"
              f"{game_metrics['balanced_accuracy']:>9.1%}"
              f"{game_metrics['recall']['run']:>9.1%}"
              f"{game_metrics['recall']['pass']:>10.1%}{game_majority_accuracy:>10.1%}")
        per_game.append({"game_group": group, "plays": len(rows),
                         "accuracy": game_metrics["accuracy"],
                         "balanced_accuracy": game_metrics["balanced_accuracy"],
                         "run_recall": game_metrics["recall"]["run"],
                         "pass_recall": game_metrics["recall"]["pass"],
                         "majority_accuracy": game_majority_accuracy,
                         "beats_majority": game_metrics["accuracy"] > game_majority_accuracy})

    print("-" * 71)
    print(f"{'POOLED':<20}{len(scored):>5}{metrics['accuracy']:>8.1%}"
          f"{metrics['balanced_accuracy']:>9.1%}{metrics['recall']['run']:>9.1%}"
          f"{metrics['recall']['pass']:>10.1%}{majority_metrics['accuracy']:>10.1%}")
    print(f"\nmacro F1: {metrics['macro_f1']:.3f}")
    print(f"confusion (actual -> predicted): {json.dumps(metrics['confusion'])}")
    beat = sum(1 for g in per_game if g["beats_majority"])
    print(f"games beating their majority reference: {beat}/{len(per_game)}")

    probability_rows = [
        r for r in scored if r.get("p_run") is not None
    ]
    tied_rows = [r for r in probability_rows if r.get("p_run_tie")]
    non_tied_rows = [r for r in probability_rows if not r.get("p_run_tie")]
    agreements = sum(
        ("run" if r["p_run"] >= 0.5 else "pass") == r["predicted"]
        for r in non_tied_rows
    )
    tie_correct = sum(r["predicted"] == r["label"] for r in tied_rows)
    bare_answers = sum(bool(r.get("bare_answer")) for r in results)
    if probability_rows:
        if non_tied_rows:
            print(f"non-tie p_run/answer agreement: "
                  f"{agreements}/{len(non_tied_rows)} "
                  f"({agreements / len(non_tied_rows):.1%})")
        else:
            print("non-tie p_run/answer agreement: 0/0")
        if tied_rows:
            print(f"exact p_run ties: {len(tied_rows)}/{len(probability_rows)}; "
                  f"emitted-word accuracy {tie_correct}/{len(tied_rows)} "
                  f"({tie_correct / len(tied_rows):.1%})")
    print(f"bare one-word answers: {bare_answers}/{len(results)} "
          f"({bare_answers / len(results):.1%})")

    output = args.output or (args.strips_dir.parent /
                             f"experiment-0-{args.backend}-{model.replace(':', '_')}.json")
    output.write_text(json.dumps({
        "schema_version": "1.0",
        "experiment_id": "run-pass-experiment-0",
        "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "manifest_sha256": hashlib.sha256(
            (args.strips_dir / "strips-manifest.json").read_bytes()).hexdigest(),
        "backend": args.backend,
        "model": model,
        "prompt_version": ACTIVE_PROMPT["version"],
        "prompt": ACTIVE_PROMPT["text"],
        "system_prompt": (
            _OPENROUTER_OUTPUT_CONSTRAINT
            if args.backend == "openrouter"
            and args.force_one_word_system_prompt else None),
        "samples": args.samples,
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
        "seed": args.seed if args.backend == "openrouter" else None,
        "top_k": 1 if args.backend == "openrouter" and args.temperature == 0 else None,
        "top_logprobs": 5 if args.require_logprobs else None,
        "reasoning_effort": args.reasoning_effort,
        "require_logprobs": args.require_logprobs,
        "request_logprobs": (
            args.backend == "openrouter" and not args.no_logprobs
            and args.samples == 1),
        "openrouter_provider": args.openrouter_provider,
        "openrouter_allow_fallbacks": (
            False if args.openrouter_provider else None),
        "openrouter_provider_metadata": provider_metadata,
        "few_shot": [
            {"game_group": r["game_group"], "clip_number": r["clip_number"],
             "clip_id": r.get("clip_id"), "label": r["label"]}
            for r in few_shot_records
        ],
        "plays_attempted": len(results),
        "plays_scored": len(scored),
        "complete": not errors,
        "coverage": coverage,
        "logprob_validation": {
            "plays_with_p_run": len(probability_rows),
            "answer_agreements": agreements,
            "answer_agreement_rate": (
                agreements / len(non_tied_rows) if non_tied_rows else None),
            "exact_ties": len(tied_rows),
            "exact_tie_rate": (
                len(tied_rows) / len(probability_rows) if probability_rows else None),
            "exact_tie_correct": tie_correct,
            "exact_tie_accuracy": (
                tie_correct / len(tied_rows) if tied_rows else None),
            "bare_answers": bare_answers,
            "bare_answer_rate": bare_answers / len(results) if results else None,
        },
        "cost_usd": sum(float(r["cost_usd"]) for r in results
                        if r.get("cost_usd") is not None),
        "pooled": {
            "accuracy": metrics["accuracy"],
            "balanced_accuracy": metrics["balanced_accuracy"],
            "macro_f1": metrics["macro_f1"],
            "run_recall": metrics["recall"]["run"],
            "pass_recall": metrics["recall"]["pass"],
            "confusion": metrics["confusion"],
        },
        "majority_reference": {
            "label": majority,
            "accuracy": majority_metrics["accuracy"],
            "balanced_accuracy": majority_metrics["balanced_accuracy"],
        },
        "per_game": per_game,
        "games_beating_majority": beat,
        "errors": [{"game_group": g, "clip_number": c, "error": e} for g, c, e in errors],
        "predictions": [{k: v for k, v in r.items() if k != "raw"} for r in results],
    }, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {output}")
    return 1 if args.require_logprobs and errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
