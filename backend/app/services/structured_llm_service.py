from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from .llm_service import LLMService
from ..utils.json_utils import remove_think_tags, sanitize_json_like_text, unwrap_markdown_json

logger = logging.getLogger(__name__)

_GENERATE_BACKOFFS = (1.0, 2.0, 4.0)


class StructuredLLMService:
    """统一的结构化输出入口。"""

    def __init__(self, llm_service: LLMService):
        self.llm_service = llm_service

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_content: str,
        user_id: Optional[int],
        role: str = "writer",
        content_rating: Optional[str] = None,
        temperature: float = 0.3,
        timeout: float = 300.0,
        max_tokens: Optional[int] = None,
    ) -> dict[str, Any]:
        last_exc: Exception = ValueError("未收到 LLM 响应")
        for attempt, backoff in enumerate((*_GENERATE_BACKOFFS, None), start=1):
            response = await self.llm_service.get_llm_response(
                system_prompt=system_prompt,
                conversation_history=[{"role": "user", "content": user_content}],
                temperature=temperature,
                user_id=user_id,
                role=role,
                content_rating=content_rating,
                timeout=timeout,
                response_format="json_object",
                max_tokens=max_tokens,
            )
            if response and response.strip():
                try:
                    return self.parse_json(response)
                except ValueError as exc:
                    last_exc = exc
                    logger.warning("generate_json attempt %d parse failed: %s", attempt, exc)
            else:
                logger.warning("generate_json attempt %d returned empty response", attempt)
            if backoff is not None:
                await asyncio.sleep(backoff)
        raise last_exc

    @staticmethod
    def parse_json(raw_text: str) -> dict[str, Any]:
        cleaned = remove_think_tags(raw_text or "")
        normalized = unwrap_markdown_json(cleaned)
        candidates = [normalized]
        sanitized = sanitize_json_like_text(normalized)
        if sanitized != normalized:
            candidates.append(sanitized)

        for candidate in candidates:
            try:
                payload = json.loads(candidate)
            except Exception:
                continue
            if isinstance(payload, dict):
                return payload

        # Last-resort: extract outermost {...} block
        start = normalized.find("{")
        end = normalized.rfind("}")
        if start != -1 and end > start:
            try:
                payload = json.loads(normalized[start : end + 1])
                if isinstance(payload, dict):
                    return payload
            except Exception:
                pass

        excerpt = (raw_text or "")[:100]
        raise ValueError(f"模型未返回合法 JSON 对象: {excerpt!r}")
