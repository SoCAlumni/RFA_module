"""LLM 계층. 대응 에이전트가 LLM 을 쓰는 곳은 초안 작성 하나뿐이다.

- AnthropicLLM : Anthropic SDK. 모델이 안전 분류기로 요청을 거절하면 서버가 권장 모델로
                 다시 돌리도록 fallbacks="default" 를 켠다 (beta server-side-fallback-2026-07-01).
- RuleLLM      : API 키 없이 흐름을 돌려 보는 규칙 기반 흉내 (RFA_LLM_MODE=mock).

호출은 (name, system, user, context) 를 받는다. context 는 RuleLLM 이 판단 재료로 쓰고,
AnthropicLLM 은 무시한다 (모델은 user 프롬프트만 본다).
"""

from __future__ import annotations

from collections.abc import Mapping
from importlib import resources
from typing import Any, Protocol

import anthropic

DEFAULT_MODEL = "claude-opus-5"
ANTHROPIC_API_URL = "https://api.anthropic.com"
MAX_TOKENS = 16000
FALLBACK_BETA = "server-side-fallback-2026-07-01"


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
    mode = env.get("RFA_LLM_MODE", "mock")
    if mode == "mock":
        return RuleLLM()
    if mode == "anthropic":
        # 빈 값이면 기본 주소를 명시한다. None 을 넘기면 SDK 가 환경변수(빈 문자열)를 다시 읽는다.
        client = anthropic.Anthropic(
            base_url=env.get("ANTHROPIC_BASE_URL") or ANTHROPIC_API_URL,
            api_key=env.get("ANTHROPIC_API_KEY") or None,
        )
        return AnthropicLLM(client, env.get("RFA_MODEL") or DEFAULT_MODEL)
    raise RuntimeError(f"unknown RFA_LLM_MODE: {mode!r} (mock | anthropic)")
