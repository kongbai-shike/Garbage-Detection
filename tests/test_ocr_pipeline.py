import unittest
from unittest.mock import patch

from analyzer import RatioRow
from item_catalog import CatalogItem
from ocr_pipeline import _merge_ocr_entries, _merge_ocr_lines_by_bbox, extract_text_lines_with_engine, parse_recognized_items


class OCRPipelineTests(unittest.TestCase):
    def test_find_junk_from_image_without_catalog_uses_english_like_query(self) -> None:
        captured = {}

        def fake_find_high_ratio_items(**kwargs):
            captured.update(kwargs)
            return (
                [
                    RatioRow(
                        item_name="Ash Prime Neuroptics Blueprint",
                        url_name="ash_prime_neuroptics_blueprint",
                        ducats=45,
                        average_price=10.0,
                        ratio=4.5,
                    )
                ],
                {"matched_count": 1},
            )

        with patch("ocr_pipeline.extract_text_lines_with_engine", return_value=(["AshPrime 头部神经光元蓝图 x1"], "rapid")), patch(
            "ocr_pipeline.load_catalog", return_value=([], "")
        ), patch("ocr_pipeline.find_high_ratio_items", side_effect=fake_find_high_ratio_items):
            from ocr_pipeline import find_junk_from_image

            find_junk_from_image("dummy.png", threshold=1, top=20, sample_size=5)

        self.assertEqual(captured["mode"], "contains")
        self.assertIn("neuroptics", captured["query"])

    def test_merge_ocr_entries_by_grid_keeps_multiline_same_cell(self) -> None:
        entries = [
            (20.0, 10.0, 180.0, 26.0, "Banshee Prime"),
            (18.0, 30.0, 210.0, 48.0, "头部神经光元 蓝"),
            (90.0, 52.0, 115.0, 66.0, "图"),
            (320.0, 10.0, 480.0, 26.0, "Khora Prime"),
            (318.0, 30.0, 500.0, 48.0, "系统 蓝图"),
        ]

        merged = _merge_ocr_entries(entries)
        self.assertIn("Banshee Prime 头部神经光元 蓝 图", merged)
        self.assertIn("Khora Prime 系统 蓝图", merged)

    def test_merge_ocr_entries_falls_back_for_single_column(self) -> None:
        entries = [
            (10.0, 10.0, 120.0, 25.0, "陨蜓 Prime 蓝"),
            (15.0, 26.0, 40.0, 40.0, "图"),
        ]

        merged = _merge_ocr_entries(entries)
        self.assertEqual(merged, _merge_ocr_lines_by_bbox(entries))

    def test_merge_ocr_entries_does_not_cross_merge_adjacent_slots(self) -> None:
        entries = [
            (10.0, 10.0, 300.0, 26.0, "Atlas Prime 头部神经光元 蓝图"),
            (320.0, 10.0, 430.0, 26.0, "Banshee Prime"),
            (318.0, 30.0, 430.0, 46.0, "机体 蓝图"),
            (12.0, 220.0, 280.0, 236.0, "Baruuk Prime 机体蓝图"),
            (320.0, 220.0, 520.0, 236.0, "Caliban Prime 头部神经光元蓝图"),
        ]

        merged = _merge_ocr_entries(entries)
        self.assertIn("Atlas Prime 头部神经光元 蓝图", merged)
        self.assertIn("Banshee Prime 机体 蓝图", merged)
        self.assertEqual(len([row for row in merged if "Prime" in row]), 4)

    def test_extract_text_lines_with_engine_auto_prefers_paddle(self) -> None:
        with patch("ocr_pipeline.Path.exists", return_value=True), patch(
            "ocr_pipeline._extract_text_lines_with_paddleocr", return_value=["A"]
        ) as paddle_mock, patch("ocr_pipeline._extract_text_lines_with_rapidocr", return_value=["B"]) as rapid_mock:
            lines, engine = extract_text_lines_with_engine("dummy.png", ocr_engine="auto")

        self.assertEqual(lines, ["A"])
        self.assertEqual(engine, "paddle")
        paddle_mock.assert_called_once()
        rapid_mock.assert_not_called()

    def test_extract_text_lines_with_engine_auto_falls_back_to_rapid(self) -> None:
        with patch("ocr_pipeline.Path.exists", return_value=True), patch(
            "ocr_pipeline._extract_text_lines_with_paddleocr", side_effect=RuntimeError("paddle missing")
        ), patch("ocr_pipeline._extract_text_lines_with_rapidocr", return_value=["B"]) as rapid_mock:
            lines, engine = extract_text_lines_with_engine("dummy.png", ocr_engine="auto")

        self.assertEqual(lines, ["B"])
        self.assertEqual(engine, "rapid")
        rapid_mock.assert_called_once()

    def test_extract_text_lines_with_engine_invalid_engine(self) -> None:
        with patch("ocr_pipeline.Path.exists", return_value=True):
            with self.assertRaises(ValueError):
                extract_text_lines_with_engine("dummy.png", ocr_engine="unknown")

    def test_find_junk_from_image_uses_exact_slug_after_catalog_match(self) -> None:
        captured = {}

        def fake_find_high_ratio_items(**kwargs):
            captured.update(kwargs)
            return (
                [
                    RatioRow(
                        item_name="Caliban Prime Neuroptics Blueprint",
                        url_name="caliban_prime_neuroptics_blueprint",
                        ducats=45,
                        average_price=10.0,
                        ratio=4.5,
                    )
                ],
                {"matched_count": 1},
            )

        with patch("ocr_pipeline.extract_text_lines_with_engine", return_value=(["CalibanPrime 头 x1"], "paddle")), patch(
            "ocr_pipeline.load_catalog",
            return_value=(
                [
                    CatalogItem(
                        item_name="Caliban Prime Neuroptics Blueprint",
                        url_name="caliban_prime_neuroptics_blueprint",
                        aliases=("Caliban Prime 头部神经光元蓝图",),
                    )
                ],
                "",
            ),
        ), patch("ocr_pipeline.resolve_catalog_item") as resolve_mock, patch(
            "ocr_pipeline.find_high_ratio_items", side_effect=fake_find_high_ratio_items
        ):
            resolve_mock.return_value = CatalogItem(
                item_name="Caliban Prime Neuroptics Blueprint",
                url_name="caliban_prime_neuroptics_blueprint",
                aliases=("Caliban Prime 头部神经光元蓝图",),
            )
            from ocr_pipeline import find_junk_from_image

            results, _ = find_junk_from_image("dummy.png", threshold=1, top=20, sample_size=5)

        self.assertEqual(captured["query"], "caliban_prime_neuroptics_blueprint")
        self.assertEqual(captured["mode"], "exact")
        self.assertEqual(results[0].url_name, "caliban_prime_neuroptics_blueprint")

    def test_find_junk_from_image_prefers_chinese_alias_for_matched_name(self) -> None:
        with patch("ocr_pipeline.extract_text_lines_with_engine", return_value=(["CalibanPrime 头 x1"], "paddle")), patch(
            "ocr_pipeline.load_catalog",
            return_value=(
                [
                    CatalogItem(
                        item_name="Caliban Prime Neuroptics Blueprint",
                        url_name="caliban_prime_neuroptics_blueprint",
                        aliases=("Caliban Prime 头部神经光元蓝图",),
                    )
                ],
                "",
            ),
        ), patch(
            "ocr_pipeline.resolve_catalog_item",
            return_value=CatalogItem(
                item_name="Caliban Prime Neuroptics Blueprint",
                url_name="caliban_prime_neuroptics_blueprint",
                aliases=("Caliban Prime 头部神经光元蓝图",),
            ),
        ), patch(
            "ocr_pipeline.find_high_ratio_items",
            return_value=(
                [
                    RatioRow(
                        item_name="Caliban Prime Neuroptics Blueprint",
                        url_name="caliban_prime_neuroptics_blueprint",
                        ducats=45,
                        average_price=10.0,
                        ratio=4.5,
                    )
                ],
                {"matched_count": 1},
            ),
        ):
            from ocr_pipeline import find_junk_from_image

            results, _ = find_junk_from_image("dummy.png", threshold=1, top=20, sample_size=5)

        self.assertEqual(
            results[0].matched_name,
            "Caliban Prime 头部神经光元蓝图 / Caliban Prime Neuroptics Blueprint",
        )

    def test_merge_ocr_lines_by_bbox_with_split_card_text(self) -> None:
        entries = [
            (10.0, 10.0, 120.0, 25.0, "陨蜓 Prime 蓝"),
            (15.0, 26.0, 40.0, 40.0, "图"),
            (200.0, 10.0, 320.0, 25.0, "Ash Prime 头部"),
            (205.0, 26.0, 320.0, 40.0, "神经光元蓝图"),
        ]

        merged = _merge_ocr_lines_by_bbox(entries)
        self.assertIn("陨蜓 Prime 蓝 图", merged)
        self.assertIn("Ash Prime 头部 神经光元蓝图", merged)

    def test_parse_recognized_items_with_inline_count(self) -> None:
        lines = [
            "3 Wisp Prime 机体蓝图",
            "Ash Prime 头部神经光元蓝图 x2",
            "random noise",
        ]

        parsed = parse_recognized_items(lines)
        self.assertEqual(parsed["Wisp Prime 机体蓝图"], 3)
        self.assertEqual(parsed["Ash Prime 头部神经光元蓝图"], 2)

    def test_parse_recognized_items_with_count_line(self) -> None:
        lines = ["4", "Baruuk Prime 机体蓝图", "2", "Ember Prime 系统 蓝图"]

        parsed = parse_recognized_items(lines)
        self.assertEqual(parsed["Baruuk Prime 机体蓝图"], 4)
        self.assertEqual(parsed["Ember Prime 系统蓝图"], 2)

    def test_parse_recognized_items_with_wrapped_name_lines(self) -> None:
        lines = [
            "2",
            "Wisp",
            "Prime 机体",
            "蓝图",
            "Ash Prime 头部神经光元蓝图 x3",
        ]

        parsed = parse_recognized_items(lines)
        self.assertEqual(parsed["Wisp Prime 机体蓝图"], 2)
        self.assertEqual(parsed["Ash Prime 头部神经光元蓝图"], 3)

    def test_parse_recognized_items_wrapped_name_not_extended_by_noise(self) -> None:
        lines = [
            "2",
            "Wisp",
            "Prime 机体",
            "蓝图",
            "random noise",
        ]

        parsed = parse_recognized_items(lines)
        self.assertEqual(parsed["Wisp Prime 机体蓝图"], 2)
        self.assertEqual(len(parsed), 1)

    def test_parse_recognized_items_does_not_merge_two_prime_items(self) -> None:
        lines = [
            "Wisp Prime 机体蓝图",
            "Ash Prime 头部神经光元蓝图",
        ]

        parsed = parse_recognized_items(lines)
        self.assertEqual(parsed["Wisp Prime 机体蓝图"], 1)
        self.assertEqual(parsed["Ash Prime 头部神经光元蓝图"], 1)
        self.assertEqual(len(parsed), 2)

    def test_parse_recognized_items_with_fuzzy_prime_token(self) -> None:
        lines = [
            "2",
            "Wisp Prlme 机体",
            "蓝图",
            "Ash p r 1 m e 头部神经光元蓝图 x2",
        ]

        parsed = parse_recognized_items(lines)
        self.assertEqual(parsed["Wisp Prime 机体蓝图"], 2)
        self.assertEqual(parsed["Ash Prime 头部神经光元蓝图"], 2)

    def test_parse_recognized_items_with_split_blueprint_character(self) -> None:
        lines = [
            "3",
            "陨蜓Prime蓝",
            "图",
        ]

        parsed = parse_recognized_items(lines)
        self.assertEqual(parsed["陨蜓 Prime 蓝图"], 3)

    def test_parse_recognized_items_with_three_line_card_text(self) -> None:
        lines = [
            "2 Banshee Prime",
            "头部神经光元 蓝",
            "图",
        ]

        parsed = parse_recognized_items(lines)
        self.assertEqual(parsed["Banshee Prime 头部神经光元蓝图"], 2)

    def test_parse_recognized_items_with_split_component_characters(self) -> None:
        lines = [
            "Lavos Prime 机 体蓝图 x1",
            "Lavos Prime 系 统蓝图 x1",
        ]

        parsed = parse_recognized_items(lines)
        self.assertEqual(parsed["Lavos Prime 机体蓝图"], 1)
        self.assertEqual(parsed["Lavos Prime 系统蓝图"], 1)


if __name__ == "__main__":
    unittest.main()

