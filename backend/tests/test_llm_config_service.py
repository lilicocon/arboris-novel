import unittest
from unittest.mock import AsyncMock, patch

from backend.app.core.config import settings
from backend.app.schemas.llm_config import LLMConfigRead
from backend.app.services.llm_config_service import LLMConfigService


class LLMConfigServiceRoleRoutingTest(unittest.IsolatedAsyncioTestCase):
    async def test_get_config_for_role_uses_reviewer_override_model(self) -> None:
        service = object.__new__(LLMConfigService)
        service.get_config = AsyncMock(
            return_value=LLMConfigRead(
                user_id=1,
                llm_provider_url=None,
                llm_provider_api_key=None,
                llm_provider_model="writer-model",
                embedding_provider_url=None,
                embedding_provider_api_key=None,
                embedding_provider_model="embed-model",
                embedding_provider_format="openai",
            )
        )

        with (
            patch.object(settings, "reviewer_llm_model_name", "reviewer-model"),
            patch.object(settings, "reviewer_llm_api_key", None),
            patch.object(settings, "reviewer_llm_base_url", None),
        ):
            routed = await LLMConfigService.get_config_for_role(service, user_id=1, role="reviewer")

        self.assertEqual("reviewer-model", routed.llm_provider_model)

    async def test_get_config_for_role_uses_explicit_writer_profile_when_available(self) -> None:
        service = object.__new__(LLMConfigService)
        service.get_config = AsyncMock(
            return_value=LLMConfigRead(
                user_id=1,
                llm_provider_url="https://default.example/v1",
                llm_provider_api_key="default-key",
                llm_provider_model="writer-model",
                embedding_provider_url=None,
                embedding_provider_api_key=None,
                embedding_provider_model="embed-model",
                embedding_provider_format="openai",
            )
        )

        with (
            patch.object(settings, "explicit_llm_model_name", "explicit-model"),
            patch.object(settings, "explicit_llm_api_key", "explicit-key"),
            patch.object(settings, "explicit_llm_base_url", "https://explicit.example/v1"),
        ):
            routed = await LLMConfigService.get_config_for_role(
                service,
                user_id=1,
                role="writer",
                content_rating="explicit",
            )

        self.assertEqual("explicit-model", routed.llm_provider_model)
        self.assertEqual("explicit-key", routed.llm_provider_api_key)
        self.assertEqual("https://explicit.example/v1", str(routed.llm_provider_url))


if __name__ == "__main__":
    unittest.main()
