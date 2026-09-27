"""LLM 계층.

- AnthropicLLM : Anthropic SDK. 샌드박스에서는 ANTHROPIC_BASE_URL=https://inference.local
                 (키는 게이트웨이가 주입하므로 비워 둔다).
                 JSON 이 필요한 호출은 structured outputs(messages.parse) 를 쓴다.
- OpenAICompatLLM : OpenAI 호환 chat completions (NVIDIA Endpoints, Ollama 등).
                 샌드박스가 Anthropic 이 아닌 제공자로 온보딩됐으면 inference.local 이
                 /v1/messages 를 받지 않으므로 이쪽을 쓴다 (RFA_LLM_MODE=openai).
                 JSON 이 필요한 호출은 스키마를 프롬프트에 싣고 출력을 검증한다.
- RuleLLM      : API 키 없이 파이프라인을 돌려 보는 규칙 기반 흉내 (RFA_LLM_MODE=mock).

모든 호출은 (name, system, user, context) 를 받는다. context 는 RuleLLM 이 판단 재료로 쓰고,
AnthropicLLM 과 OpenAICompatLLM 은 무시한다(모델은 user 프롬프트만 본다).
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping
from importlib import resources
from typing import Any, Protocol, TypeVar

import anthropic
import httpx
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "claude-opus-5"
ANTHROPIC_API_URL = "https://api.anthropic.com"
MAX_TOKENS = 16000

OPENAI_COMPAT_API_URL = "https://integrate.api.nvidia.com/v1"
OPENAI_COMPAT_MODEL = "nvidia/nemotron-3-super-120b-a12b"
OPENAI_COMPAT_MAX_TOKENS = 4096
OPENAI_COMPAT_TIMEOUT = 120.0
OPENAI_COMPAT_RETRIES = 2  # Anthropic SDK 기본값과 같게


class LLMError(Exception):
    """LLM 이 거절했거나, 약속한 형식으로 답하지 않았다. 워크플로는 needs_human 으로 보낸다."""


class LLM(Protocol):
    def text(self, *, name: str, system: str, user: str, context: Mapping[str, Any]) -> str: ...

    def structured(
        self, *, name: str, system: str, user: str, schema: type[T], context: Mapping[str, Any]
    ) -> T: ...


def prompt(name: str) -> str:
    """rfa_workflow/prompts/<name>.md"""
    return resources.files("rfa_workflow.prompts").joinpath(f"{name}.md").read_text("utf-8")


class AnthropicLLM:
    def __init__(self, client: Any, model: str = DEFAULT_MODEL) -> None:
        self._client = client
        self._model = model

    def _check(self, response: Any, name: str) -> None:
        if response.stop_reason == "refusal":
            raise LLMError(f"{name}: model refused")
        if response.stop_reason == "max_tokens":
            raise LLMError(f"{name}: output truncated")

    def _call(self, name: str, method: str, **kwargs: Any) -> Any:
        """SDK 가 429/5xx/연결 오류를 이미 재시도한다. 그래도 실패하면 LLMError."""
        try:
            return getattr(self._client.messages, method)(
                model=self._model, max_tokens=MAX_TOKENS, **kwargs
            )
        except anthropic.APIStatusError as exc:
            # 결재 문서 사유에 남으므로 원인이 보이게 (예: 크레딧 부족, 모델 없음)
            detail = (
                str(exc.body.get("error", {}).get("message", ""))
                if isinstance(exc.body, dict)
                else ""
            )
            raise LLMError(f"{name}: API {exc.status_code} {detail[:160]}".rstrip()) from exc
        except anthropic.APIError as exc:
            raise LLMError(f"{name}: API error {type(exc).__name__}") from exc

    def text(self, *, name: str, system: str, user: str, context: Mapping[str, Any]) -> str:
        response = self._call(
            name, "create", system=system, messages=[{"role": "user", "content": user}]
        )
        self._check(response, name)
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        if not text:
            raise LLMError(f"{name}: empty response")
        return text

    def structured(
        self, *, name: str, system: str, user: str, schema: type[T], context: Mapping[str, Any]
    ) -> T:
        response = self._call(
            name,
            "parse",
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema,
        )
        self._check(response, name)
        if response.parsed_output is None:
            raise LLMError(f"{name}: no structured output")
        return response.parsed_output


_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)
_JSON_INSTRUCTION = (
    "\n\n[출력 형식]\n"
    "아래 JSON Schema 를 만족하는 JSON 객체 하나만 출력한다. "
    "설명, 코드 블록 표시, 다른 글은 쓰지 않는다.\n"
)


def _first_json_object(text: str) -> Any:
    """응답에서 첫 JSON 객체를 꺼낸다. 앞뒤에 붙은 글이나 코드 블록 표시는 무시한다."""
    start = text.find("{")
    if start < 0:
        raise ValueError("JSON 객체가 없음")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    return obj


class OpenAICompatLLM:
    """OpenAI 호환 `POST {base_url}/chat/completions`. http 는 base_url 이 잡힌 httpx.Client."""

    def __init__(
        self,
        http: httpx.Client,
        model: str = OPENAI_COMPAT_MODEL,
        max_tokens: int = OPENAI_COMPAT_MAX_TOKENS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http
        self._model = model
        self._max_tokens = max_tokens
        self._sleep = sleep

    def _post(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """429/5xx/연결 오류는 backoff 0.5s → 1s 로 다시 보낸다. 그래도 실패하면 LLMError."""
        for attempt in range(1 + OPENAI_COMPAT_RETRIES):
            last = attempt == OPENAI_COMPAT_RETRIES
            try:
                response = self._http.post("chat/completions", json=payload)
            except httpx.TransportError as exc:
                if last:
                    raise LLMError(f"{name}: API error {type(exc).__name__}") from exc
            else:
                if response.is_success:
                    return response.json()
                if last or response.status_code not in _RETRY_STATUS:
                    # 결재 문서 사유에 남으므로 원인이 보이게 (예: 한도 초과, 모델 없음)
                    detail = _error_detail(response)[:160]
                    raise LLMError(f"{name}: API {response.status_code} {detail}".rstrip())
            self._sleep(0.5 * 2**attempt)
        raise AssertionError("unreachable")

    def _complete(self, name: str, system: str, user: str) -> str:
        data = self._post(
            name,
            {
                "model": self._model,
                "max_tokens": self._max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        choice = (data.get("choices") or [{}])[0]
        if choice.get("finish_reason") == "content_filter":
            raise LLMError(f"{name}: model refused")
        if choice.get("finish_reason") == "length":
            raise LLMError(f"{name}: output truncated")
        # 생각 과정을 따로 내는 모델은 reasoning 필드나 <think> 블록에 쓴다. 본문만 읽는다.
        content = (choice.get("message") or {}).get("content") or ""
        text = _THINK_BLOCK.sub("", content).strip()
        if not text:
            raise LLMError(f"{name}: empty response")
        return text

    def text(self, *, name: str, system: str, user: str, context: Mapping[str, Any]) -> str:
        return self._complete(name, system, user)

    def structured(
        self, *, name: str, system: str, user: str, schema: type[T], context: Mapping[str, Any]
    ) -> T:
        """스키마를 강제하는 방법이 제공자마다 달라서, 프롬프트로 시키고 출력을 검증한다.

        형식이 틀리면 오류를 알려 주고 한 번 더 시킨다.
        """
        system += _JSON_INSTRUCTION + json.dumps(schema.model_json_schema(), ensure_ascii=False)
        error = ""
        for _ in range(2):
            raw = self._complete(name, system, user + error)
            try:
                return schema.model_validate(_first_json_object(raw))
            except (ValueError, ValidationError) as exc:
                reason = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else str(exc)
                error = f"\n\n[이전 출력 오류 — 형식을 고쳐 JSON 만 다시 출력]\n{reason}"
        raise LLMError(f"{name}: no structured output ({reason})")


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text
    error = body.get("error", body) if isinstance(body, dict) else body
    if isinstance(error, dict):
        return str(error.get("message") or error.get("detail") or "")
    return str(error)


_SENTENCE_END = re.compile(r"(?<=[.!?。])\s+")  # 마침표 뒤에 공백이 올 때만 (0.6B, 10.1.2.3 보존)


def _sentences(text: str) -> list[str]:
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    return [s.strip() for s in _SENTENCE_END.split(" ".join(lines)) if s.strip()]


class RuleLLM:
    """API 키 없이 그래프를 끝까지 돌려 보기 위한 결정적 흉내. 품질은 기대하지 말 것."""

    def text(self, *, name: str, system: str, user: str, context: Mapping[str, Any]) -> str:
        if name != "writer":
            raise LLMError(f"RuleLLM has no text rule for {name}")
        # 지식을 그대로 옮긴다 → 기밀이 초안에 흘러들어 검토 장면이 드러난다
        body = " ".join(_sentences(context["knowledge"].answer))
        return f"안녕하세요. {body}" if body else "안녕하세요. 확인 후 답변드릴게요."

    def structured(
        self, *, name: str, system: str, user: str, schema: type[T], context: Mapping[str, Any]
    ) -> T:
        if name == "pick_task":
            question = context["question"].lower()
            hit = next(
                (
                    t.id
                    for t in context["tasks"]
                    if t.id.lower() in question or t.name.lower() in question
                ),
                "none",
            )
            return schema.model_validate(
                {"task_id": hit, "reason": "키워드 일치" if hit != "none" else "없음"}
            )
        if name == "editor":
            return schema.model_validate({"verdict": "pass", "notes": "", "issues": []})
        if name == "censor_public":
            return schema.model_validate(_rule_censor(context["draft"], context["scan"]))
        raise LLMError(f"RuleLLM has no structured rule for {name}")


def _rule_censor(draft: str, scan: list) -> dict:
    if not scan:
        return {"verdict": "allow", "redacted_body": "", "reasons": [], "summary": "스캔 결과 없음"}
    kept = [s for s in _sentences(draft) if not any(h.match in s for h in scan)]
    reasons = [
        {"rule": f"scanner:{h.type.replace('_', '-')}", "span": h.match, "action": "remove"}
        for h in scan
    ]
    body = " ".join(kept) or "확인 후 답변드릴게요."
    return {
        "verdict": "redact",
        "redacted_body": body,
        "reasons": reasons,
        "summary": "비밀값 문장 제거",
    }


def make_llm(env: Mapping[str, str]) -> LLM:
    mode = env.get("RFA_LLM_MODE", "mock")
    if mode == "mock":
        return RuleLLM()
    if mode == "anthropic":
        # 빈 값이면 기본 주소를 명시한다. None 을 넘기면 SDK 가 환경변수(빈 문자열)를 다시 읽는다.
        base_url = env.get("ANTHROPIC_BASE_URL") or ANTHROPIC_API_URL
        api_key = env.get("ANTHROPIC_API_KEY") or None
        if base_url != ANTHROPIC_API_URL and not api_key:
            api_key = "unused"  # inference.local: 샌드박스 게이트웨이가 키를 넣는다
        client = anthropic.Anthropic(base_url=base_url, api_key=api_key)
        return AnthropicLLM(client, env.get("RFA_MODEL") or DEFAULT_MODEL)
    if mode == "openai":
        base_url = env.get("OPENAI_BASE_URL") or OPENAI_COMPAT_API_URL
        # 샌드박스(inference.local)에서는 키를 비워 둔다. 게이트웨이가 넣는다.
        api_key = env.get("OPENAI_API_KEY") or env.get("NVIDIA_INFERENCE_API_KEY")
        http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=OPENAI_COMPAT_TIMEOUT,
        )
        return OpenAICompatLLM(
            http,
            env.get("RFA_MODEL") or OPENAI_COMPAT_MODEL,
            int(env.get("RFA_MAX_TOKENS") or OPENAI_COMPAT_MAX_TOKENS),
        )
    raise RuntimeError(f"unknown RFA_LLM_MODE: {mode!r} (mock | anthropic | openai)")
