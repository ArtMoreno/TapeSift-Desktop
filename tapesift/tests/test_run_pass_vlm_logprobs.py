import io
import json
import math

import pytest

from scripts import evaluate_run_pass_vlm as vlm


def test_openrouter_logprobs_become_p_run_and_missing_support_falls_back(monkeypatch):
    evidence = vlm.run_pass_logprob_evidence({"content": [
        {"token": "The", "logprob": -0.1, "top_logprobs": [
            {"token": "RUN", "logprob": -0.2},
            {"token": "PASS", "logprob": -1.4},
        ]},
        {"token": " answer", "logprob": -0.1, "top_logprobs": []},
        {"token": " PASS", "logprob": -0.2, "top_logprobs": [
            {"token": " PASS", "logprob": -0.2},
            {"token": " RUN", "logprob": -1.4},
        ]},
    ]}, "pass")
    assert evidence["p_run_token_index"] == 2
    assert evidence["p_run_token"] == " PASS"
    assert evidence["run_logprob"] == -1.4
    assert evidence["pass_logprob"] == -0.2
    assert math.isclose(
        evidence["p_run"], math.exp(-1.4) / (math.exp(-0.2) + math.exp(-1.4)))

    payload = {"choices": [{"message": {"content": "RUN"}}]}
    sent = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    def urlopen(request, timeout):
        sent.append(json.loads(request.data))
        return Response(json.dumps(payload).encode())

    monkeypatch.setattr(vlm.urllib.request, "urlopen", urlopen)
    monkeypatch.setitem(vlm._SUPPORTS_OPENROUTER_LOGPROBS, "value", True)
    reply = vlm.ask_openrouter(b"jpeg", "model", "key", 1,
                               max_tokens=16, reasoning_effort="off",
                               few_shot=[(b"example", "pass")],
                               system_prompt=vlm._OPENROUTER_OUTPUT_CONSTRAINT)

    assert sent[0]["logprobs"] is True
    assert sent[0]["top_logprobs"] == 5
    assert sent[0]["max_tokens"] == 16
    assert sent[0]["top_k"] == 1
    assert sent[0]["reasoning"] == {"enabled": False}
    assert [message["role"] for message in sent[0]["messages"]] == [
        "system", "user", "assistant", "user"
    ]
    assert "exactly one uppercase word" in sent[0]["messages"][0]["content"]
    assert sent[0]["messages"][2]["content"] == "PASS"
    assert reply == {
        "text": "RUN",
        "p_run": None,
        "p_run_tie": None,
        "p_run_token_index": None,
        "p_run_token": None,
        "run_logprob": None,
        "pass_logprob": None,
        "run_token_variants": [],
        "pass_token_variants": [],
        "cost_usd": None,
        "serving_provider": None,
        "provider_quantization": None,
        "served_model": None,
        "generation_id": None,
        "system_fingerprint": None,
    }
    assert vlm._SUPPORTS_OPENROUTER_LOGPROBS["value"] is False

    sent.clear()
    reply = vlm.ask_openrouter(
        b"jpeg", "model", "key", 1, request_logprobs=False)
    assert "logprobs" not in sent[0]
    assert [message["role"] for message in sent[0]["messages"]] == ["user"]
    assert reply["text"] == "RUN"
    assert reply["p_run"] is None
    assert vlm.parse_answer("RUN") == ("run", 0.0, "bare word")

    monkeypatch.setitem(vlm._SUPPORTS_OPENROUTER_LOGPROBS, "value", True)
    with pytest.raises(AssertionError, match="required token logprobs missing"):
        vlm.ask_openrouter(
            b"jpeg", "model", "key", 1, require_logprobs=True)

    vote_answers = iter(("RUN", "PASS", "RUN", "RUN", "PASS"))
    sent.clear()

    def vote_urlopen(request, timeout):
        sent.append(json.loads(request.data))
        answer = next(vote_answers)
        return Response(json.dumps({
            "choices": [{"message": {"content": answer}}],
            "usage": {"cost": 0.01},
        }).encode())

    monkeypatch.setattr(vlm.urllib.request, "urlopen", vote_urlopen)
    reply = vlm.ask_openrouter(b"jpeg", "model", "key", 1,
                               samples=5, temperature=0.7)
    assert len(sent) == 5
    assert sent[0]["temperature"] == 0.7
    assert "top_k" not in sent[0]
    assert reply["text"] == "RUN"
    assert reply["p_run"] == reply["confidence"] == 0.6
    assert reply["cost_usd"] == 0.05

    monkeypatch.setitem(vlm.ACTIVE_PROMPT, "version", "proxy")
    assert vlm.parse_answer("YES")[:2] == ("run", 0.0)
    assert vlm.parse_answer("NO")[:2] == ("pass", 0.0)
    monkeypatch.setitem(vlm.ACTIVE_PROMPT, "version", "v2")


def test_openrouter_uses_the_spoken_answer_token_not_the_first_token(monkeypatch):
    payload = {
        "id": "generation-1",
        "model": "qwen/qwen3-vl-32b-instruct",
        "provider": "Alibaba",
        "system_fingerprint": "fp-1",
        "choices": [{
            "message": {"content": "This play is a PASS."},
            "logprobs": {"content": [
                {"token": "This", "logprob": -0.1, "top_logprobs": [
                    {"token": "RUN", "logprob": -0.1},
                    {"token": "PASS", "logprob": -2.0},
                ]},
                {"token": " play", "logprob": -0.1, "top_logprobs": []},
                {"token": " is", "logprob": -0.1, "top_logprobs": []},
                {"token": " a", "logprob": -0.1, "top_logprobs": []},
                {"token": " PASS", "logprob": -0.2, "top_logprobs": [
                    {"token": " PASS", "logprob": -0.2},
                    {"token": " RUN", "logprob": -1.4},
                ]},
            ]},
        }],
        "usage": {"cost": 0.01},
    }

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    sent = []

    def urlopen(request, timeout):
        sent.append(json.loads(request.data))
        return Response(json.dumps(payload).encode())

    monkeypatch.setattr(vlm.urllib.request, "urlopen", urlopen)
    monkeypatch.setitem(vlm._SUPPORTS_OPENROUTER_LOGPROBS, "value", True)

    reply = vlm.ask_openrouter(
        b"jpeg", "qwen/qwen3-vl-32b-instruct", "key", 1,
        provider="Alibaba", provider_quantization="unknown", seed=0)

    assert sent[0]["provider"] == {
        "order": ["Alibaba"], "allow_fallbacks": False}
    assert sent[0]["seed"] == 0
    assert reply["p_run_token_index"] == 4
    assert reply["p_run"] < 0.5
    assert reply["serving_provider"] == "Alibaba"
    assert reply["provider_quantization"] == "unknown"
    assert reply["served_model"] == "qwen/qwen3-vl-32b-instruct"
    assert reply["generation_id"] == "generation-1"
    assert reply["system_fingerprint"] == "fp-1"
    assert vlm.parse_answer(reply["text"])[0] == "pass"


def test_logprobs_sum_label_variants_without_double_counting_the_chosen_token():
    evidence = vlm.run_pass_logprob_evidence({"content": [{
        "token": "PASS",
        "bytes": [80, 65, 83, 83],
        "logprob": -0.8,
        "top_logprobs": [
            {"token": "RUN", "bytes": [82, 85, 78], "logprob": -0.5},
            {"token": "PASS", "bytes": [80, 65, 83, 83], "logprob": -0.8},
            {"token": " PASS", "bytes": [32, 80, 65, 83, 83], "logprob": -0.9},
            {"token": "Pass", "bytes": [80, 97, 115, 115], "logprob": -1.2},
            {"token": ".RUN", "bytes": [46, 82, 85, 78], "logprob": -0.55},
        ],
    }]}, "pass")

    pass_mass = math.exp(-0.8) + math.exp(-0.9) + math.exp(-1.2)
    expected = math.exp(-0.5) / (math.exp(-0.5) + pass_mass)
    assert math.isclose(evidence["p_run"], expected)
    assert evidence["p_run"] < 0.5
    assert evidence["run_token_variants"] == [("RUN", -0.5)]


def test_logprob_extraction_fails_when_chosen_class_has_lower_mass():
    with pytest.raises(AssertionError, match="chosen=pass"):
        vlm.run_pass_logprob_evidence({"content": [{
            "token": "PASS",
            "logprob": -0.8,
            "top_logprobs": [
                {"token": "RUN", "logprob": -0.2},
                {"token": "PASS", "logprob": -0.8},
            ],
        }]}, "pass")


def test_logprob_extraction_preserves_an_exact_class_tie():
    evidence = vlm.run_pass_logprob_evidence({"content": [{
        "token": "PASS",
        "logprob": -0.6932151317596436,
        "top_logprobs": [
            {"token": "RUN", "logprob": -0.6932151317596436},
            {"token": " RUN", "logprob": -10.818215370178223},
            {"token": " PASS", "logprob": -10.818215370178223},
        ],
    }]}, "pass")

    assert evidence["p_run"] == 0.5
    assert evidence["p_run_tie"] is True
