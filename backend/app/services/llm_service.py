# AIMETA P=LLM服务_大模型调用封装|R=API调用_流式生成|NR=不含业务逻辑|E=LLMService|X=internal|A=服务类|D=openai,httpx|S=net|RD=./README.ai
import logging
import os
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, InternalServerError, PermissionDeniedError

from ..core.config import settings
from ..repositories.llm_config_repository import LLMConfigRepository
from ..repositories.system_config_repository import SystemConfigRepository
from ..services.llm_config_service import resolve_llm_override
from ..services.prompt_service import PromptService
from ..services.usage_service import UsageService
from ..utils.llm_tool import ChatMessage, LLMClient

logger = logging.getLogger(__name__)

try:  # pragma: no cover - 运行环境未安装时兼容
    from ollama import AsyncClient as OllamaAsyncClient
except ImportError:  # pragma: no cover - Ollama 为可选依赖
    OllamaAsyncClient = None


class LLMService:
    """封装与大模型交互的所有逻辑，包括模型配置解析与调用。"""

    def __init__(self, session):
        self.session = session
        self.llm_repo = LLMConfigRepository(session)
        self.system_config_repo = SystemConfigRepository(session)
        self.usage_service = UsageService(session)
        self._embedding_dimensions: Dict[str, int] = {}
        self._config_cache: Dict[str, Optional[str]] = {}
        self._user_llm_config_cache: Dict[int, tuple] = {}  # user_id -> (config, timestamp)

    async def get_llm_response(
        self,
        system_prompt: str,
        conversation_history: List[Dict[str, str]],
        *,
        temperature: float = 0.7,
        user_id: Optional[int] = None,
        role: str = "writer",
        content_rating: Optional[str] = None,
        timeout: float = 300.0,
        response_format: Optional[str] = "json_object",
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
    ) -> str:
        messages = [{"role": "system", "content": system_prompt}, *conversation_history]
        return await self._stream_and_collect(
            messages,
            temperature=temperature,
            user_id=user_id,
            role=role,
            content_rating=content_rating,
            timeout=timeout,
            response_format=response_format,
            max_tokens=max_tokens,
            top_p=top_p,
        )

    async def generate(
        self,
        prompt: str,
        *,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        user_id: Optional[int] = None,
        role: str = "writer",
        content_rating: Optional[str] = None,
        timeout: float = 300.0,
        max_tokens: Optional[int] = None,
        response_format: Optional[str] = None,
        top_p: Optional[float] = None,
    ) -> str:
        """兼容旧版接口的文本生成入口，统一走 get_llm_response。"""
        return await self.get_llm_response(
            system_prompt=system_prompt or "你是一位专业写作助手。",
            conversation_history=[{"role": "user", "content": prompt}],
            temperature=temperature,
            user_id=user_id,
            role=role,
            content_rating=content_rating,
            timeout=timeout,
            response_format=response_format,
            max_tokens=max_tokens,
            top_p=top_p,
        )

    async def get_summary(
        self,
        chapter_content: str,
        *,
        temperature: float = 0.2,
        user_id: Optional[int] = None,
        role: str = "summarizer",
        content_rating: Optional[str] = None,
        timeout: float = 180.0,
        system_prompt: Optional[str] = None,
    ) -> str:
        if not system_prompt:
            prompt_service = PromptService(self.session)
            system_prompt = await prompt_service.get_prompt("extraction")
        if not system_prompt:
            logger.error("未配置名为 'extraction' 的摘要提示词，无法生成章节摘要")
            raise HTTPException(status_code=500, detail="未配置摘要提示词，请联系管理员配置 'extraction' 提示词")
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": chapter_content},
        ]
        return await self._stream_and_collect(
            messages,
            temperature=temperature,
            user_id=user_id,
            role=role,
            content_rating=content_rating,
            timeout=timeout,
        )

    async def _stream_and_collect(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: float,
        user_id: Optional[int],
        role: str,
        content_rating: Optional[str],
        timeout: float,
        response_format: Optional[str] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
    ) -> str:
        config = await self._resolve_llm_config(user_id, role=role, content_rating=content_rating)
        client = LLMClient(api_key=config["api_key"], base_url=config.get("base_url"))

        chat_messages = [ChatMessage(role=msg["role"], content=msg["content"]) for msg in messages]

        full_response = ""
        finish_reason = None

        logger.info(
            "Streaming LLM response: model=%s user_id=%s messages=%d",
            config.get("model"),
            user_id,
            len(messages),
        )

        try:
            async for part in client.stream_chat(
                messages=chat_messages,
                model=config.get("model"),
                temperature=temperature,
                timeout=int(timeout),
                response_format=response_format,
                max_tokens=max_tokens,
                top_p=top_p,
            ):
                if part.get("content"):
                    full_response += part["content"]
                if part.get("finish_reason"):
                    finish_reason = part["finish_reason"]
        except InternalServerError as exc:
            detail = "AI 服务内部错误，请稍后重试"
            response = getattr(exc, "response", None)
            if response is not None:
                try:
                    payload = response.json()
                    error_data = payload.get("error", {}) if isinstance(payload, dict) else {}
                    detail = error_data.get("message_zh") or error_data.get("message") or detail
                except Exception:
                    detail = str(exc) or detail
            else:
                detail = str(exc) or detail
            logger.error(
                "LLM stream internal error: model=%s user_id=%s detail=%s",
                config.get("model"),
                user_id,
                detail,
                exc_info=exc,
            )
            raise HTTPException(status_code=503, detail=detail)
        except (httpx.RemoteProtocolError, httpx.ReadTimeout, APIConnectionError, APITimeoutError) as exc:
            if isinstance(exc, httpx.RemoteProtocolError):
                detail = "AI 服务连接被意外中断，请稍后重试"
            elif isinstance(exc, (httpx.ReadTimeout, APITimeoutError)):
                detail = "AI 服务响应超时，请稍后重试"
            else:
                detail = "无法连接到 AI 服务，请稍后重试"
            logger.error(
                "LLM stream failed: model=%s user_id=%s detail=%s",
                config.get("model"),
                user_id,
                detail,
                exc_info=exc,
            )
            raise HTTPException(status_code=503, detail=detail) from exc
        except PermissionDeniedError as exc:
            detail = "AI 服务拒绝访问（可能被上游安全策略拦截），请稍后重试或更换可用 API 地址"
            logger.error(
                "LLM stream permission denied: model=%s user_id=%s detail=%s",
                config.get("model"),
                user_id,
                detail,
                exc_info=exc,
            )
            raise HTTPException(status_code=503, detail=detail) from exc

        # Some models (e.g. grok-4.20-auto) silently return empty content when
        # response_format=json_object is set in streaming mode. Retry without it.
        if not full_response and finish_reason == "stop" and response_format:
            logger.warning(
                "LLM empty response with response_format=%s, retrying without: model=%s user_id=%s",
                response_format,
                config.get("model"),
                user_id,
            )
            full_response = ""
            finish_reason = None
            try:
                async for part in client.stream_chat(
                    messages=chat_messages,
                    model=config.get("model"),
                    temperature=temperature,
                    timeout=int(timeout),
                    response_format=None,
                    max_tokens=max_tokens,
                    top_p=top_p,
                ):
                    if part.get("content"):
                        full_response += part["content"]
                    if part.get("finish_reason"):
                        finish_reason = part["finish_reason"]
            except Exception as exc:
                logger.error(
                    "LLM retry without response_format also failed: model=%s user_id=%s",
                    config.get("model"),
                    user_id,
                    exc_info=exc,
                )
                if isinstance(exc, InternalServerError):
                    raise HTTPException(status_code=503, detail="AI 服务内部错误，请稍后重试") from exc
                if isinstance(exc, (httpx.RemoteProtocolError, httpx.ReadTimeout, APIConnectionError, APITimeoutError)):
                    raise HTTPException(status_code=503, detail="AI 服务暂时不可用，请稍后重试") from exc
                if isinstance(exc, PermissionDeniedError):
                    raise HTTPException(status_code=503, detail="AI 服务拒绝访问，请稍后重试或更换可用 API 地址") from exc
                raise

        logger.debug(
            "LLM response collected: model=%s user_id=%s finish_reason=%s preview=%s",
            config.get("model"),
            user_id,
            finish_reason,
            full_response[:500],
        )

        if finish_reason == "length":
            logger.warning(
                "LLM response truncated: model=%s user_id=%s response_length=%d",
                config.get("model"),
                user_id,
                len(full_response),
            )
            raise HTTPException(
                status_code=500,
                detail=f"AI 响应因长度限制被截断（已生成 {len(full_response)} 字符），请缩短输入内容或调整模型参数"
            )

        if not full_response:
            logger.error(
                "LLM returned empty response: model=%s user_id=%s finish_reason=%s",
                config.get("model"),
                user_id,
                finish_reason,
            )
            raise HTTPException(
                status_code=500,
                detail=f"AI 未返回有效内容（结束原因: {finish_reason or '未知'}），请稍后重试或联系管理员"
            )

        try:
            await self.usage_service.increment("api_request_count")
        except Exception as exc:
            logger.error("记录 usage_metrics 失败: %s", exc)
            raise HTTPException(status_code=500, detail="Usage tracking failed") from exc
        logger.info(
            "LLM response success: model=%s user_id=%s chars=%d",
            config.get("model"),
            user_id,
            len(full_response),
        )
        return full_response

    async def _resolve_llm_config(
        self,
        user_id: Optional[int],
        *,
        role: str = "writer",
        content_rating: Optional[str] = None,
    ) -> Dict[str, Optional[str]]:
        return await self._resolve_llm_config_with_policy(
            user_id,
            require_api_key=True,
            role=role,
            content_rating=content_rating,
        )

    async def _resolve_llm_config_with_policy(
        self,
        user_id: Optional[int],
        *,
        require_api_key: bool,
        role: str = "writer",
        content_rating: Optional[str] = None,
    ) -> Dict[str, Optional[str]]:
        base_config: Dict[str, Optional[str]]
        if user_id:
            config = await self._get_user_llm_config(user_id)
            if config and (config.llm_provider_api_key or not require_api_key):
                base_config = {
                    "api_key": config.llm_provider_api_key,
                    "base_url": config.llm_provider_url,
                    "model": config.llm_provider_model,
                }
            else:
                api_key, base_url, model = await self._get_config_values("llm.api_key", "llm.base_url", "llm.model")
                base_config = {"api_key": api_key, "base_url": base_url, "model": model}
        else:
            api_key, base_url, model = await self._get_config_values("llm.api_key", "llm.base_url", "llm.model")
            base_config = {"api_key": api_key, "base_url": base_url, "model": model}

        overrides = resolve_llm_override(role=role, content_rating=content_rating)
        config_values = {
            "api_key": overrides["api_key"] or base_config.get("api_key"),
            "base_url": overrides["base_url"] or base_config.get("base_url"),
            "model": overrides["model"] or base_config.get("model"),
        }

        if require_api_key and not config_values["api_key"]:
            logger.error("未配置默认 LLM API Key，且用户 %s 未设置自定义 API Key", user_id)
            raise HTTPException(
                status_code=500,
                detail="未配置默认 LLM API Key，请联系管理员配置系统默认 API Key 或在个人设置中配置自定义 API Key"
            )

        return config_values

    @staticmethod
    def _normalize_provider_url(url: str, suffixes: tuple) -> str:
        """Strip any of the given path suffixes (case-insensitive) repeatedly until stable."""
        normalized = url
        changed = True
        while changed:
            changed = False
            lower = normalized.lower()
            for suffix in suffixes:
                if lower.endswith(suffix):
                    normalized = normalized[: -len(suffix)].rstrip("/")
                    changed = True
                    break
        return normalized

    @staticmethod
    def _normalize_ollama_host(host: Optional[str]) -> Optional[str]:
        """归一化 Ollama host，避免误填 OpenAI 风格路径（如 /v1）。"""
        if host is None:
            return None

        normalized = host.strip().rstrip("/")
        if not normalized:
            return None

        normalized = LLMService._normalize_provider_url(normalized, ("/v1/models", "/v1/embeddings", "/v1"))
        return normalized or None

    @staticmethod
    def _extract_ollama_embed_vector(response: Any) -> Optional[List[float]]:
        """解析 /api/embed 响应，提取第一条向量。"""
        embeddings = response.get("embeddings") if isinstance(response, dict) else getattr(response, "embeddings", None)
        if not embeddings:
            return None
        first = embeddings[0] if isinstance(embeddings, list) else None
        if first is None:
            return None
        return first if isinstance(first, list) else list(first)

    @staticmethod
    def _extract_ollama_legacy_vector(response: Any) -> Optional[List[float]]:
        """解析 /api/embeddings（旧接口）响应。"""
        embedding = response.get("embedding") if isinstance(response, dict) else getattr(response, "embedding", None)
        if not embedding:
            return None
        return embedding if isinstance(embedding, list) else list(embedding)

    @staticmethod
    def _normalize_openai_embeddings_url(base_url: Optional[str]) -> Optional[str]:
        """将 OpenAI 兼容 base_url 规整为可直接 POST 的 /embeddings 端点。"""
        if not base_url:
            return None
        trimmed = base_url.strip().rstrip("/")
        if not trimmed:
            return None

        # Strip any trailing /embeddings so we can reconstruct the canonical path.
        stripped = LLMService._normalize_provider_url(trimmed, ("/embeddings",))
        lowered = stripped.lower()
        if lowered.endswith("/v1"):
            return f"{stripped}/embeddings"

        parsed = urlparse(stripped)
        if parsed.path in {"", "/"}:
            return f"{stripped}/v1/embeddings"
        return f"{stripped}/embeddings"

    async def _request_ollama_embedding(
        self,
        client: Any,
        *,
        model: str,
        text: str,
        base_url: Optional[str],
    ) -> List[float]:
        """
        优先调用 Ollama 原生 /api/embed，若服务端较旧则回退 /api/embeddings。
        """
        if hasattr(client, "embed"):
            try:
                response = await client.embed(model=model, input=text)
                embedding = self._extract_ollama_embed_vector(response)
                if embedding:
                    return embedding
                logger.warning("Ollama /api/embed 返回空向量: model=%s base_url=%s", model, base_url)
            except Exception as exc:
                logger.warning(
                    "Ollama /api/embed 请求失败，尝试回退旧接口 /api/embeddings: model=%s base_url=%s error=%s",
                    model,
                    base_url,
                    exc,
                )

        try:
            response = await client.embeddings(model=model, prompt=text)
        except Exception as exc:  # pragma: no cover - 本地服务调用失败
            logger.error(
                "Ollama 嵌入请求失败: model=%s base_url=%s error=%s",
                model,
                base_url,
                exc,
                exc_info=True,
            )
            return []

        embedding = self._extract_ollama_legacy_vector(response)
        if not embedding:
            logger.warning("Ollama /api/embeddings 返回空向量: model=%s base_url=%s", model, base_url)
            return []
        return embedding

    async def get_embedding(
        self,
        text: str,
        *,
        user_id: Optional[int] = None,
        model: Optional[str] = None,
    ) -> List[float]:
        """生成文本向量，用于章节 RAG 检索，支持 openai 与 ollama 双提供方。"""
        user_llm_config = await self._get_user_llm_config(user_id) if user_id else None
        user_embedding_model = user_llm_config.embedding_provider_model if user_llm_config else None
        user_embedding_base_url = user_llm_config.embedding_provider_url if user_llm_config else None
        user_embedding_api_key = user_llm_config.embedding_provider_api_key if user_llm_config else None
        user_llm_base_url = user_llm_config.llm_provider_url if user_llm_config else None
        user_embedding_provider_format = (
            (user_llm_config.embedding_provider_format or "").strip().lower()
            if user_llm_config
            else ""
        )

        provider = user_embedding_provider_format if user_embedding_provider_format in {"openai", "ollama"} else "openai"

        if provider not in {"openai", "ollama"}:
            logger.error("非法 embedding.provider 配置: %s", provider)
            raise HTTPException(status_code=500, detail="embedding.provider 仅支持 openai 或 ollama")
        default_model = (
            user_embedding_model
            or await self._get_config_value("ollama.embedding_model")
            or "nomic-embed-text:latest"
            if provider == "ollama"
            else user_embedding_model
            or await self._get_config_value("embedding.model")
            or "text-embedding-3-large"
        )
        target_model = model or default_model

        if provider == "ollama":
            if OllamaAsyncClient is None:
                logger.error("未安装 ollama 依赖，无法调用本地嵌入模型。")
                raise HTTPException(status_code=500, detail="缺少 Ollama 依赖，请先安装 ollama 包。")

            raw_base_url = (
                user_embedding_base_url
                or user_llm_base_url
                or await self._get_config_value("ollama.embedding_base_url")
                or await self._get_config_value("embedding.base_url")
            )
            base_url = self._normalize_ollama_host(raw_base_url)
            if raw_base_url and raw_base_url != base_url:
                logger.warning(
                    "检测到 Ollama 地址包含 OpenAI 风格路径，已自动修正: raw=%s normalized=%s",
                    raw_base_url,
                    base_url,
                )
            client = OllamaAsyncClient(host=base_url)
            embedding = await self._request_ollama_embedding(
                client,
                model=target_model,
                text=text,
                base_url=base_url,
            )
            if not embedding:
                return []
        else:
            config = await self._resolve_llm_config_with_policy(user_id, require_api_key=False)
            api_key = user_embedding_api_key or await self._get_config_value("embedding.api_key") or config["api_key"]
            base_url = (
                user_embedding_base_url
                or user_llm_base_url
                or await self._get_config_value("embedding.base_url")
                or config.get("base_url")
            )
            if api_key:
                client = AsyncOpenAI(api_key=api_key, base_url=base_url)
                try:
                    response = await client.embeddings.create(
                        input=text,
                        model=target_model,
                    )
                except Exception as exc:  # pragma: no cover - 网络或鉴权失败
                    logger.error(
                        "OpenAI 嵌入请求失败: model=%s base_url=%s user_id=%s error=%s",
                        target_model,
                        base_url,
                        user_id,
                        exc,
                        exc_info=True,
                    )
                    return []
                if not response.data:
                    logger.warning("OpenAI 嵌入请求返回空数据: model=%s user_id=%s", target_model, user_id)
                    return []
                embedding = response.data[0].embedding
            else:
                endpoint = self._normalize_openai_embeddings_url(base_url)
                if not endpoint:
                    logger.error("OpenAI 嵌入请求失败: 未配置可用 base_url，且未提供 API Key")
                    return []
                try:
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        response = await client.post(
                            endpoint,
                            json={"input": text, "model": target_model},
                            headers={"Content-Type": "application/json"},
                        )
                        response.raise_for_status()
                        payload = response.json()
                except Exception as exc:  # pragma: no cover - 网络或协议失败
                    logger.error(
                        "OpenAI 无 Key 嵌入请求失败: model=%s endpoint=%s user_id=%s error=%s",
                        target_model,
                        endpoint,
                        user_id,
                        exc,
                        exc_info=True,
                    )
                    return []

                data = payload.get("data") if isinstance(payload, dict) else None
                if not data:
                    logger.warning("OpenAI 无 Key 嵌入请求返回空数据: model=%s endpoint=%s", target_model, endpoint)
                    return []
                first = data[0] if isinstance(data, list) else None
                if not isinstance(first, dict) or "embedding" not in first:
                    logger.warning("OpenAI 无 Key 嵌入响应结构异常: model=%s endpoint=%s", target_model, endpoint)
                    return []
                embedding = first["embedding"]

        if not isinstance(embedding, list):
            embedding = list(embedding)

        dimension = len(embedding)
        if not dimension:
            vector_size_str = await self._get_config_value("embedding.model_vector_size")
            if vector_size_str:
                dimension = int(vector_size_str)
        if dimension:
            self._embedding_dimensions[target_model] = dimension
        return embedding

    async def get_embedding_dimension(self, model: Optional[str] = None) -> Optional[int]:
        """获取嵌入向量维度，优先返回缓存结果，其次读取配置。"""
        provider = await self._get_config_value("embedding.provider") or "openai"
        default_model = (
            await self._get_config_value("ollama.embedding_model") or "nomic-embed-text:latest"
            if provider == "ollama"
            else await self._get_config_value("embedding.model") or "text-embedding-3-large"
        )
        target_model = model or default_model
        if target_model in self._embedding_dimensions:
            return self._embedding_dimensions[target_model]
        vector_size_str = await self._get_config_value("embedding.model_vector_size")
        return int(vector_size_str) if vector_size_str else None

    async def _get_user_llm_config(self, user_id: int) -> Any:
        cached = self._user_llm_config_cache.get(user_id)
        if cached is None or time.time() - cached[1] > 300:
            config = await self.llm_repo.get_by_user(user_id)
            self._user_llm_config_cache[user_id] = (config, time.time())
        return self._user_llm_config_cache[user_id][0]

    async def _get_config_values(self, *keys: str) -> List[Optional[str]]:
        missing = [k for k in keys if k not in self._config_cache]
        if missing:
            records = await self.system_config_repo.get_by_keys(missing)
            for k in missing:
                rec = records.get(k)
                self._config_cache[k] = rec.value if rec else os.getenv(k.upper().replace(".", "_"))
        return [self._config_cache.get(k) for k in keys]

    async def _get_config_value(self, key: str) -> Optional[str]:
        return (await self._get_config_values(key))[0]
