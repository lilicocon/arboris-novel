import unittest

from backend.app.services.structured_llm_service import StructuredLLMService


class StructuredLLMServiceParseJsonTest(unittest.TestCase):
    def test_parse_json_handles_markdown_fence(self) -> None:
        payload = StructuredLLMService.parse_json(
            """```json
            {"ok": true, "value": 1}
            ```"""
        )
        self.assertEqual({"ok": True, "value": 1}, payload)

    def test_parse_json_handles_json_like_text(self) -> None:
        payload = StructuredLLMService.parse_json(
            '结果如下：{"name":"测试","desc":"第一行\\n第二行"}'
        )
        self.assertEqual("测试", payload["name"])
        self.assertEqual("第一行\n第二行", payload["desc"])


if __name__ == "__main__":
    unittest.main()
