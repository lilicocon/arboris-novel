# AIMETA P=LLM配置模式_模型配置请求响应|R=LLM配置结构|NR=不含业务逻辑|E=LLMConfigSchema|X=internal|A=Pydantic模式|D=pydantic|S=none|RD=./README.ai
from typing import Literal, Optional

from pydantic import BaseModel, HttpUrl, Field


class LLMConfigBase(BaseModel):
    llm_provider_url: Optional[HttpUrl] = Field(default=None, description="自定义 LLM 服务地址")
    llm_provider_api_key: Optional[str] = Field(default=None, description="自定义 LLM API Key")
    llm_provider_model: Optional[str] = Field(default=None, description="自定义模型名称")
    embedding_provider_url: Optional[HttpUrl] = Field(
        default=None,
        description="自定义向量模型服务地址，留空则复用主模型地址",
    )
    embedding_provider_api_key: Optional[str] = Field(
        default=None,
        description="自定义向量模型 API Key，留空则复用主模型 API Key",
    )
    embedding_provider_model: Optional[str] = Field(default=None, description="自定义向量模型名称")
    embedding_provider_format: Optional[Literal["openai", "ollama"]] = Field(
        default=None,
        description="向量请求协议格式：openai 或 ollama；留空时使用系统默认配置。",
    )


class LLMConfigCreate(LLMConfigBase):
    pass


class LLMConfigRead(LLMConfigBase):
    user_id: int

    class Config:
        from_attributes = True


class ModelListRequest(BaseModel):
    llm_provider_url: Optional[str] = Field(default=None, description="LLM 服务地址")
    llm_provider_api_key: Optional[str] = Field(default=None, description="LLM API Key，可为空")
