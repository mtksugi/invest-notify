from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import requests


@dataclass(frozen=True)
class OpenAICompatConfig:
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    timeout_seconds: int = 180
    max_retries: int = 2


def load_openai_compat_config_from_env() -> OpenAICompatConfig:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required")

    base_url = os.environ.get("OPENAI_BASE_URL", "").strip() or "https://api.openai.com/v1"
    model = os.environ.get("OPENAI_MODEL", "").strip() or "gpt-4o-mini"
    timeout = int(os.environ.get("OPENAI_TIMEOUT_SECONDS", "180"))
    retries = int(os.environ.get("OPENAI_MAX_RETRIES", "2"))
    return OpenAICompatConfig(
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout_seconds=timeout,
        max_retries=retries,
    )


def chat_json(
    *,
    cfg: OpenAICompatConfig,
    system: str,
    user: str,
    temperature: float | None = None,
    max_tokens: int = 4000,
) -> dict[str, Any]:
    """
    OpenAI互換の Chat Completions に投げ、JSONとしてパースして返す。
    互換差分（モデル/プロバイダごとのパラメータ差）を、読みやすい形で吸収する。
    """

    url = cfg.base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }
    base_payload: dict[str, Any] = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if temperature is not None:
        base_payload["temperature"] = temperature

    # JSON強制（効かない/拒否される互換実装もあるためフォールバックあり）
    payloads: list[dict[str, Any]] = []
    for use_response_format in (True, False):
        for token_param in ("max_completion_tokens", "max_tokens"):
            p = dict(base_payload)
            p[token_param] = max_tokens
            if use_response_format:
                p["response_format"] = {"type": "json_object"}
            payloads.append(p)

    last_err: str | None = None
    for p in payloads:
        for attempt in range(max(0, cfg.max_retries) + 1):
            try:
                r = requests.post(url, headers=headers, json=p, timeout=cfg.timeout_seconds)
            except requests.exceptions.RequestException as e:
                last_err = f"request error: {e}"
                continue

            if r.status_code >= 400:
                # このpayloadは相性が悪いので次へ（詳細は最後のエラーに残す）
                last_err = f"{r.status_code} {r.text[:300]}"
                break

            data = r.json()
            content, meta = _extract_assistant_content_and_meta(data)
            if not content.strip():
                last_err = f"empty content (finish_reason={meta.get('finish_reason')}, refusal={meta.get('refusal')})"
                continue
            return _parse_json_lenient(content)

    raise RuntimeError(f"LLM request failed: {last_err or 'unknown'}")


def _extract_assistant_content_and_meta(resp: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    try:
        choice0 = resp["choices"][0]
    except Exception:
        raise RuntimeError("Unexpected LLM response shape (missing choices[0])")

    meta: dict[str, Any] = {}
    if isinstance(choice0, dict):
        meta["finish_reason"] = choice0.get("finish_reason")

    # Chat Completions (typical)
    if isinstance(choice0, dict) and "message" in choice0:
        msg = choice0.get("message") or {}
        if isinstance(msg, dict):
            meta["refusal"] = msg.get("refusal")
        content = msg.get("content")
        if isinstance(content, str):
            return content, meta
        # 一部互換/モデルで list 形式になるケースを吸収
        if isinstance(content, list):
            parts: list[str] = []
            for p in content:
                if isinstance(p, dict):
                    # OpenAI互換のcontent parts形式
                    if p.get("type") == "text" and isinstance(p.get("text"), str):
                        parts.append(p["text"])
                    elif isinstance(p.get("content"), str):
                        parts.append(p["content"])
            return "\n".join(parts), meta

    # Completion互換
    if isinstance(choice0, dict) and isinstance(choice0.get("text"), str):
        return choice0["text"], meta

    raise RuntimeError(
        f"Unexpected LLM response shape (keys={list(choice0.keys()) if isinstance(choice0, dict) else type(choice0)})"
    )


def _parse_json_lenient(s: str) -> dict[str, Any]:
    """
    JSON以外の前後文字が混ざっても、最初の{...}を拾ってパースする（MVP）。
    """
    s = (s or "").strip()
    if not s:
        raise RuntimeError("LLM output was empty (expected JSON)")
    try:
        obj = json.loads(s)
        if not isinstance(obj, dict):
            raise RuntimeError("LLM output is not a JSON object")
        return obj
    except Exception:
        # 最初の { と最後の } を使って切り出し
        i = s.find("{")
        j = s.rfind("}")
        if i == -1 or j == -1 or j <= i:
            excerpt = s[:300].replace("\n", "\\n")
            raise RuntimeError(f"Failed to parse JSON from LLM output. Excerpt: {excerpt}")
        obj = json.loads(s[i : j + 1])
        if not isinstance(obj, dict):
            raise RuntimeError("LLM output is not a JSON object")
        return obj

