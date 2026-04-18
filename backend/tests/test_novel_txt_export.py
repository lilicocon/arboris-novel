import unittest

from backend.app.services.novel_service import build_confirmed_chapters_txt


class NovelTxtExportTest(unittest.TestCase):
    def test_build_confirmed_chapters_txt_formats_chapters_for_reader_catalog(self) -> None:
        content = build_confirmed_chapters_txt(
            [
                {
                    "chapter_number": 1,
                    "title": "雾港来信",
                    "content": "第一章正文",
                },
                {
                    "chapter_number": 2,
                    "title": "旧站台",
                    "content": "第二章正文",
                },
            ]
        )

        self.assertEqual(
            "第1章 雾港来信\n\n第一章正文\n\n\n第2章 旧站台\n\n第二章正文",
            content,
        )

    def test_build_confirmed_chapters_txt_skips_empty_content(self) -> None:
        content = build_confirmed_chapters_txt(
            [
                {
                    "chapter_number": 1,
                    "title": "雾港来信",
                    "content": "   ",
                },
                {
                    "chapter_number": 2,
                    "title": "旧站台",
                    "content": "第二章正文",
                },
            ]
        )

        self.assertEqual("第2章 旧站台\n\n第二章正文", content)


if __name__ == "__main__":
    unittest.main()
