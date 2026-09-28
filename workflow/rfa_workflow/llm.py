"""LLM 계층. 대응 에이전트가 LLM 을 쓰는 곳은 초안 작성 하나뿐이다.

- OpenAICompatLLM : OpenAI chat/completions 호환 서버 공용 (openrouter | nvidia | gemini).
                    기본은 OpenRouter 의 무료 nemotron-3.5-lightning.
- AnthropicLLM    : Anthropic SDK. 모델이 안전 분류기로 요청을 거절하면 서버가 권장 모델로
                    다시 돌리도록 fallbacks="default" 를 켠다 (server-side-fallback beta).
- RuleLLM         : API 키 없이 흐름을 돌려 보는 규칙 기반 흉내 (RFA_LLM_MODE=mock).

호출은 (name, system, user, context) 를 받는다. context 는 RuleLLM 이 판단 재료로 쓰고,
나머지는 무시한다 (모델은 system·user 프롬프트만 본다).
"""

from __future__ import annotations

from collections.abc import Mapping
from importlib import resources
from typing import Any, Protocol

import anthropic
import httpx

DEFAULT_MODEL = "claude-sonnet-4-6"
ANTHROPIC_API_URL = "https://api.anthropic.com"
MAX_TOKENS = 16000
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# OpenAI 호환 provider 별 (기본 주소, 키 환경변수, 기본 모델, 요청에 얹는 추가 필드).
# RFA_MODEL 로 모델만 덮어쓴다. nemotron 은 reasoning 을 꺼야 응답이 온다 —
# 켜 두면 생각 토큰을 수천 자 생성하느라 실제 writer 프롬프트에서 타임아웃이 났다.
OPENAI_COMPAT_PROVIDERS: dict[str, tuple[str, str, str, dict[str, Any]]] = {
    "openrouter": (
        "https://openrouter.ai/api/v1",
        "OPENROUTER_API_KEY",
        "nvidia/nemotron-3.5-lightning:free",
        {"reasoning": {"enabled": False}},
    ),
    "nvidia": (
        "https://integrate.api.nvidia.com/v1",
        "NVIDIA_API_KEY",
        "nvidia/nemotron-3.5-lightning-30b-a3b",
        {"chat_template_kwargs": {"enable_thinking": False}},
    ),
    "gemini": (
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "GEMINI_API_KEY",
        "gemini-3.8-flash",  # 2.5-flash 는 신규 사용자에게 404 (2026-09)
        {},
    ),
}


class LLMError(Exception):
    """LLM 이 거절했거나, 잘렸거나, 호출이 실패했다. 이번 실행은 failed 로 끝난다."""


class LLM(Protocol):
    def text(self, *, name: str, system: str, user: str, context: Mapping[str, Any]) -> str: ...


def prompt(name: str) -> str:
    """rfa_workflow/prompts/<name>.md"""
    return resources.files("rfa_workflow.prompts").joinpath(f"{name}.md").read_text("utf-8")


class AnthropicLLM:
    def __init__(self, client: Any, model: str = DEFAULT_MODEL) -> None:
        self._client = client
        self._model = model

    def text(self, *, name: str, system: str, user: str, context: Mapping[str, Any]) -> str:
        """SDK 가 429/5xx/연결 오류를 이미 재시도한다. 그래도 실패하면 LLMError."""
        try:
            response = self._client.beta.messages.create(
                model=self._model,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user}],
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.APIStatusError as exc:
            # 원인이 보이게 (예: 크레딧 부족, 모델 없음)
            body = exc.body if isinstance(exc.body, dict) else {}
            detail = str(body.get("error", {}).get("message", ""))
            raise LLMError(f"{name}: API {exc.status_code} {detail[:160]}".rstrip()) from exc
        except anthropic.APIError as exc:
            raise LLMError(f"{name}: API error {type(exc).__name__}") from exc

        if response.stop_reason == "refusal":  # fallback 모델까지 모두 거절
            raise LLMError(f"{name}: model refused")
        if response.stop_reason == "max_tokens":
            raise LLMError(f"{name}: output truncated")
        # 응답에는 thinking·fallback 블록도 섞여 온다. 글은 text 블록만.
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        if not text:
            raise LLMError(f"{name}: empty response")
        return text


class OpenAICompatLLM:
    """OpenAI chat/completions 호환 API. OpenRouter·NVIDIA·Gemini 가 모두 이 형태를 받는다."""

    def __init__(
        self, base_url: str, api_key: str, model: str, extra: Mapping[str, Any] | None = None
    ) -> None:
        self._model = model
        self._base_url = base_url
        self._extra = dict(extra or {})
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=300.0,
            transport=httpx.HTTPTransport(retries=2),  # 연결 실패만 재시도
        )

    def text(self, *, name: str, system: str, user: str, context: Mapping[str, Any]) -> str:
        try:
            response = self._client.post(
                "/chat/completions",
                json={
                    "model": self._model,
                    "max_tokens": MAX_TOKENS,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    **self._extra,
                },
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"{name}: API error {type(exc).__name__}") from exc

        if response.status_code >= 400:
            # 원인이 보이게 (예: 401 키 없음, 429 무료 한도 초과, 모델 없음).
            # 보통 {"error": {...}} 지만 Gemini 는 [{"error": {...}}] 리스트로 준다.
            try:
                body = response.json()
            except ValueError:
                body = None
            if isinstance(body, list) and body:
                body = body[0]
            error = body.get("error") if isinstance(body, dict) else None
            if isinstance(error, dict):
                detail = str(error.get("message", ""))
            else:
                detail = response.text
            raise LLMError(f"{name}: API {response.status_code} {detail[:160]}".rstrip())

        choice = response.json().get("choices") or [{}]
        finish = choice[0].get("finish_reason")
        if finish == "content_filter":
            raise LLMError(f"{name}: model refused")
        if finish == "length":
            raise LLMError(f"{name}: output truncated")
        text = (choice[0].get("message") or {}).get("content") or ""
        if not text.strip():
            raise LLMError(f"{name}: empty response")
        return text.strip()


class RuleLLM:
    """API 키 없이 흐름을 끝까지 돌려 보기 위한 결정적 흉내. 품질은 기대하지 말 것.

    head agent 가 준 지식을 그대로 옮긴다 → stub 지식의 기밀이 초안에 흘러 사람이 거절하는
    장면이 드러난다. 거절 사유는 head 가 이미 지식에서 걸러 오므로 여기서는 보지 않는다.
    """

    def text(self, *, name: str, system: str, user: str, context: Mapping[str, Any]) -> str:
        if name != "writer":
            raise LLMError(f"RuleLLM has no rule for {name}")
        ask = context["ask"]
        if ask.refusal:
            return "안녕하세요. 문의하신 내용은 지금 답변드리기 어려워요. 확인 후 알려드릴게요."
        return f"안녕하세요. {ask.knowledge}"


def make_llm(env: Mapping[str, str]) -> LLM:
    mode = env.get("RFA_LLM_MODE") or ""
    if not mode:
        # 값이 빠졌다고 규칙 기반 흉내로 조용히 바꾸면 가짜 초안이 결재함에 올라간다
        raise RuntimeError(
            "RFA_LLM_MODE is not set (mock | openrouter | nvidia | gemini | anthropic); "
            "set RFA_LLM_MODE=mock explicitly to run without an LLM"
        )
    if mode == "mock":
        return RuleLLM()
    if mode == "anthropic":
        # 빈 값이면 기본 주소를 명시한다. None 을 넘기면 SDK 가 환경변수(빈 문자열)를 다시 읽는다.
        client = anthropic.Anthropic(
            base_url=env.get("ANTHROPIC_BASE_URL") or ANTHROPIC_API_URL,
            api_key=env.get("ANTHROPIC_API_KEY") or None,
        )
        return AnthropicLLM(client, env.get("RFA_MODEL") or DEFAULT_MODEL)
    if mode in OPENAI_COMPAT_PROVIDERS:
        base_url, key_var, default_model, extra = OPENAI_COMPAT_PROVIDERS[mode]
        return OpenAICompatLLM(
            base_url=base_url,
            api_key=env.get(key_var) or "",
            model=env.get("RFA_MODEL") or default_model,
            extra=extra,
        )
    raise RuntimeError(
        f"unknown RFA_LLM_MODE: {mode!r} (mock | openrouter | nvidia | gemini | anthropic)"
    )
