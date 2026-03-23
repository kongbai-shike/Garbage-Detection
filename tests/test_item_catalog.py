import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import item_catalog
from item_catalog import (
    CatalogItem,
    _extract_huiji_mappings_from_html,
    _load_manual_alias_mappings,
    _merge_huiji_aliases,
    _pick_primary_chinese_name,
    fetch_huiji_alias_mappings,
    load_catalog,
    refresh_catalog,
    resolve_catalog_item,
    save_catalog,
    start_catalog_auto_refresh,
)
from wfm_client import MarketItem


class ItemCatalogTests(unittest.TestCase):
    class _FakeResponse:
        def __init__(self, text: str) -> None:
            self._text = text

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def read(self) -> bytes:
            return self._text.encode("utf-8")

    def test_extract_huiji_mappings_from_html(self) -> None:
        html_text = """
        <table>
          <tr><th>英文</th><th>中文</th></tr>
          <tr><td>Atlas Prime Systems Blueprint</td><td>Atlas Prime 系统蓝图</td></tr>
          <tr><td>Titania Prime Blueprint</td><td>陨蜓 Prime 蓝图</td></tr>
        </table>
        """

        mappings = _extract_huiji_mappings_from_html(html_text)
        self.assertIn("atlas prime systems blueprint", mappings)
        self.assertIn("Atlas Prime 系统蓝图", mappings["atlas prime systems blueprint"])
        self.assertIn("陨蜓 Prime 蓝图", mappings["titania prime blueprint"])

    def test_extract_huiji_mappings_ignores_category_columns(self) -> None:
        html_text = """
        <table>
          <tr>
            <th>英文名</th><th>國際服簡體名稱</th><th>國際服繁體名稱</th><th>分類1</th><th>分類2</th>
          </tr>
          <tr>
            <td>Targeting_Subsystem</td><td>定位辅助</td><td>定位輔助</td><td>副武器MOD</td><td>通常MOD</td>
          </tr>
        </table>
        """

        mappings = _extract_huiji_mappings_from_html(html_text)
        self.assertIn("targeting subsystem", mappings)
        self.assertIn("定位辅助", mappings["targeting subsystem"])
        self.assertIn("定位輔助", mappings["targeting subsystem"])
        self.assertNotIn("副武器MOD", mappings["targeting subsystem"])
        self.assertNotIn("通常MOD", mappings["targeting subsystem"])

    def test_extract_huiji_mappings_from_html_without_header(self) -> None:
        html_text = """
        <table>
          <tr><td>Titania Prime Blueprint</td><td>陨蜓 Prime 蓝图</td></tr>
        </table>
        """

        mappings = _extract_huiji_mappings_from_html(html_text)
        self.assertIn("titania prime blueprint", mappings)
        self.assertIn("陨蜓 Prime 蓝图", mappings["titania prime blueprint"])

    def test_merge_huiji_aliases_adds_new_chinese_alias(self) -> None:
        catalog = [
            CatalogItem(
                item_name="Titania Prime Blueprint",
                url_name="titania_prime_blueprint",
                aliases=("Titania Prime 总图",),
            )
        ]
        merged = _merge_huiji_aliases(
            catalog,
            {"titania prime blueprint": {"陨蜓 Prime 蓝图", "Titania Prime 总图"}},
        )

        self.assertEqual(len(merged), 1)
        self.assertIn("Titania Prime 总图", merged[0].aliases)
        self.assertIn("陨蜓 Prime 蓝图", merged[0].aliases)

    def test_merge_huiji_aliases_filters_category_labels(self) -> None:
        catalog = [
            CatalogItem(
                item_name="Targeting Subsystem",
                url_name="targeting_subsystem",
                aliases=(),
                item_chinese_name="",
            )
        ]
        merged = _merge_huiji_aliases(
            catalog,
            {"targeting subsystem": {"定位辅助", "定位輔助", "副武器MOD", "通常MOD"}},
        )

        self.assertEqual(set(merged[0].aliases), {"定位辅助", "定位輔助"})
        self.assertIn(merged[0].item_chinese_name, {"定位辅助", "定位輔助"})

    def test_pick_primary_chinese_name_prefers_meaningful_over_generic_mod(self) -> None:
        picked = _pick_primary_chinese_name(
            huiji_aliases={"組合MOD"},
            fallback_aliases=("猎人肾上腺素", "獵者腎上腺素", "組合MOD"),
        )
        self.assertEqual(picked, "猎人肾上腺素")

    def test_pick_primary_chinese_name_prefers_simplified_over_traditional(self) -> None:
        picked = _pick_primary_chinese_name(
            huiji_aliases={"角鬥士威猛"},
            fallback_aliases=("角斗士威猛", "角鬥士威猛"),
        )
        self.assertEqual(picked, "角斗士威猛")

    def test_merge_huiji_aliases_backfills_item_chinese_name_from_aliases(self) -> None:
        catalog = [
            CatalogItem(
                item_name="Hunter Adrenaline",
                url_name="hunter_adrenaline",
                aliases=("猎人肾上腺素", "獵者腎上腺素", "組合MOD"),
                item_chinese_name="",
            )
        ]
        merged = _merge_huiji_aliases(catalog, {})
        self.assertEqual(merged[0].item_chinese_name, "猎人肾上腺素")

    def test_refresh_catalog_merges_huiji_aliases_into_saved_catalog(self) -> None:
        market_items = [
            MarketItem(
                item_name="Titania Prime Blueprint",
                url_name="titania_prime_blueprint",
                aliases=("Titania Prime 总图",),
            )
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            catalog_path = Path(temp_dir) / "items_catalog.json"
            call_order = []

            def fake_fetch_huiji(*args, **kwargs):
                call_order.append("huiji")
                return {"titania prime blueprint": {"陨蜓 Prime 蓝图"}}

            def fake_list_items(*args, **kwargs):
                call_order.append("wfm")
                return market_items

            with patch.object(item_catalog, "CATALOG_FILE", catalog_path), patch(
                "item_catalog.fetch_huiji_alias_mappings",
                side_effect=fake_fetch_huiji,
            ), patch(
                "item_catalog.WFMClient.list_items", side_effect=fake_list_items
            ):
                count, _ = refresh_catalog()
                loaded, _ = load_catalog()

        self.assertEqual(count, 1)
        self.assertEqual(call_order[:2], ["huiji", "wfm"])
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].item_chinese_name, "陨蜓 Prime 蓝图")
        self.assertIn("陨蜓 Prime 蓝图", loaded[0].aliases)

    def test_start_catalog_auto_refresh_skips_when_catalog_is_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            catalog_path = Path(temp_dir) / "items_catalog.json"
            catalog_path.write_text("[]", encoding="utf-8")
            catalog_path.touch()

            logs: list[str] = []
            with patch.object(item_catalog, "CATALOG_FILE", catalog_path), patch(
                "item_catalog.refresh_catalog"
            ) as refresh_mock:
                thread = start_catalog_auto_refresh(debug_log=logs.append)
                thread.join(timeout=1.0)

        refresh_mock.assert_not_called()
        self.assertTrue(any("跳过后台刷新" in msg for msg in logs))

    def test_start_catalog_auto_refresh_runs_when_catalog_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            catalog_path = Path(temp_dir) / "items_catalog.json"

            with patch.object(item_catalog, "CATALOG_FILE", catalog_path), patch(
                "item_catalog.refresh_catalog"
            ) as refresh_mock:
                thread = start_catalog_auto_refresh()
                thread.join(timeout=1.0)

        refresh_mock.assert_called_once()

    def test_load_manual_alias_mappings(self) -> None:
        payload = {
            "曲翼": {
                "陨蜓": "Odonata",
                "陨蜓 Prime": "Odonata Prime",
            },
            "曲翼枪械": {
                "巨浪": "Fluctus",
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            manual_path = Path(temp_dir) / "manual_aliases_zh.json"
            manual_path.write_text(item_catalog.json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with patch.object(item_catalog, "MANUAL_ALIAS_FILE", manual_path):
                mappings = _load_manual_alias_mappings()

        self.assertIn("odonata", mappings)
        self.assertIn("陨蜓", mappings["odonata"])
        self.assertIn("odonata prime", mappings)
        self.assertIn("陨蜓 Prime", mappings["odonata prime"])
        self.assertIn("fluctus", mappings)
        self.assertIn("巨浪", mappings["fluctus"])

    def test_refresh_catalog_applies_manual_aliases(self) -> None:
        market_items = [
            MarketItem(
                item_name="Odonata Prime Blueprint",
                url_name="odonata_prime_blueprint",
                aliases=(),
            )
        ]
        manual_payload = {
            "曲翼": {
                "陨蜓 Prime": "Odonata Prime",
            }
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            catalog_path = Path(temp_dir) / "items_catalog.json"
            manual_path = Path(temp_dir) / "manual_aliases_zh.json"
            manual_path.write_text(item_catalog.json.dumps(manual_payload, ensure_ascii=False), encoding="utf-8")

            with patch.object(item_catalog, "CATALOG_FILE", catalog_path), patch.object(
                item_catalog, "MANUAL_ALIAS_FILE", manual_path
            ), patch(
                "item_catalog.fetch_huiji_alias_mappings", return_value={}
            ), patch(
                "item_catalog.WFMClient.list_items", return_value=market_items
            ):
                refresh_catalog()
                loaded, _ = load_catalog()

        self.assertEqual(len(loaded), 1)
        self.assertIn("陨蜓 Prime", loaded[0].aliases)

    def test_resolve_catalog_item_component_query_does_not_match_set(self) -> None:
        catalog = [
            CatalogItem(
                item_name="Caliban Prime Set",
                url_name="caliban_prime_set",
                aliases=("Caliban Prime 套",),
            ),
            CatalogItem(
                item_name="Caliban Prime Neuroptics Blueprint",
                url_name="caliban_prime_neuroptics_blueprint",
                aliases=("Caliban Prime 头部神经光元蓝图",),
            ),
        ]

        matched = resolve_catalog_item("CalibanPrime 头", catalog)
        self.assertIsNotNone(matched)
        self.assertEqual(matched.url_name, "caliban_prime_neuroptics_blueprint")

    def test_resolve_catalog_item_set_query_prefers_set(self) -> None:
        catalog = [
            CatalogItem(
                item_name="Caliban Prime Set",
                url_name="caliban_prime_set",
                aliases=("Caliban Prime 套",),
            ),
            CatalogItem(
                item_name="Caliban Prime Neuroptics Blueprint",
                url_name="caliban_prime_neuroptics_blueprint",
                aliases=("Caliban Prime 头部神经光元蓝图",),
            ),
        ]

        matched = resolve_catalog_item("Caliban Prime 套", catalog)
        self.assertIsNotNone(matched)
        self.assertEqual(matched.url_name, "caliban_prime_set")

    def test_resolve_catalog_item_with_partial_ocr_query(self) -> None:
        catalog = [
            CatalogItem(
                item_name="Ash Prime Neuroptics Blueprint",
                url_name="ash_prime_neuroptics_blueprint",
                aliases=("Ash Prime 头部神经光元蓝图",),
            ),
            CatalogItem(
                item_name="Ash Prime Chassis Blueprint",
                url_name="ash_prime_chassis_blueprint",
                aliases=("Ash Prime 机体蓝图",),
            ),
        ]

        matched = resolve_catalog_item("AshPrime 头部", catalog)
        self.assertIsNotNone(matched)
        self.assertEqual(matched.url_name, "ash_prime_neuroptics_blueprint")

    def test_resolve_catalog_item_with_keyword_contains_query(self) -> None:
        catalog = [
            CatalogItem(
                item_name="Titania Prime Blueprint",
                url_name="titania_prime_blueprint",
                aliases=("陨蜓 Prime 蓝图",),
            ),
            CatalogItem(
                item_name="Atlas Prime Systems Blueprint",
                url_name="atlas_prime_systems_blueprint",
                aliases=("Atlas Prime 系统蓝图",),
            ),
        ]

        matched = resolve_catalog_item("陨蜓 Prime 蓝", catalog)
        self.assertIsNotNone(matched)
        self.assertEqual(matched.url_name, "titania_prime_blueprint")

    def test_resolve_catalog_item_systems_query_does_not_match_chassis(self) -> None:
        catalog = [
            CatalogItem(
                item_name="Khora Prime Chassis Blueprint",
                url_name="khora_prime_chassis_blueprint",
                aliases=("Khora Prime 机体蓝图",),
            ),
            CatalogItem(
                item_name="Khora Prime Systems Blueprint",
                url_name="khora_prime_systems_blueprint",
                aliases=("Khora Prime 系统蓝图",),
            ),
        ]

        matched = resolve_catalog_item("Khora Prime 系 统蓝图", catalog)
        self.assertIsNotNone(matched)
        self.assertEqual(matched.url_name, "khora_prime_systems_blueprint")

    def test_resolve_catalog_item_blueprint_query_avoids_set(self) -> None:
        catalog = [
            CatalogItem(
                item_name="Lavos Prime Set",
                url_name="lavos_prime_set",
                aliases=("Lavos Prime 套",),
            ),
            CatalogItem(
                item_name="Lavos Prime Blueprint",
                url_name="lavos_prime_blueprint",
                aliases=("Lavos Prime 蓝图",),
            ),
        ]

        matched = resolve_catalog_item("Lavos Prime 蓝", catalog)
        self.assertIsNotNone(matched)
        self.assertEqual(matched.url_name, "lavos_prime_blueprint")

    def test_save_and_load_catalog_roundtrip(self) -> None:
        data = [
            CatalogItem(
                item_name="Wisp Prime Chassis Blueprint",
                url_name="wisp_prime_chassis_blueprint",
                aliases=("Wisp Prime 机体蓝图",),
                item_chinese_name="Wisp Prime 机体蓝图",
            )
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            catalog_path = Path(temp_dir) / "items_catalog.json"
            with patch.object(item_catalog, "CATALOG_FILE", catalog_path):
                save_catalog(data)
                loaded, updated_at = load_catalog()

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].url_name, "wisp_prime_chassis_blueprint")
        self.assertEqual(loaded[0].item_chinese_name, "Wisp Prime 机体蓝图")
        self.assertTrue(updated_at)

    def test_load_catalog_without_item_chinese_name_is_backward_compatible(self) -> None:
        payload = {
            "updated_at": "2026-01-01T00:00:00+00:00",
            "items": [
                {
                    "item_name": "Wisp Prime Chassis Blueprint",
                    "url_name": "wisp_prime_chassis_blueprint",
                    "aliases": ["Wisp Prime 机体蓝图"],
                }
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            catalog_path = Path(temp_dir) / "items_catalog.json"
            catalog_path.write_text(item_catalog.json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with patch.object(item_catalog, "CATALOG_FILE", catalog_path):
                loaded, _ = load_catalog()

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].item_chinese_name, "")

    def test_load_catalog_from_top_level_item_list(self) -> None:
        payload = [
            {
                "item_name": "Secura Dual Cestra",
                "url_name": "secura_dual_cestra",
                "aliases": ["保障錫斯特雙槍", "保障锡斯特双枪"],
                "item_chinese_name": "保障锡斯特双枪",
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            catalog_path = Path(temp_dir) / "items_catalog.json"
            catalog_path.write_text(item_catalog.json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with patch.object(item_catalog, "CATALOG_FILE", catalog_path):
                loaded, updated_at = load_catalog()

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].url_name, "secura_dual_cestra")
        self.assertEqual(loaded[0].item_chinese_name, "保障锡斯特双枪")
        self.assertEqual(updated_at, "")

    def test_fetch_huiji_alias_mappings_retries_then_succeeds(self) -> None:
        html_text = """
        <table>
          <tr><td>Titania Prime Blueprint</td><td>陨蜓 Prime 蓝图</td></tr>
        </table>
        """
        failures = [
            HTTPError(item_catalog.HUIJI_PRIME_TABLE_URL, 403, "Forbidden", hdrs=None, fp=None),
            HTTPError(item_catalog.HUIJI_PRIME_TABLE_URL, 403, "Forbidden", hdrs=None, fp=None),
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "huiji_alias_cache.json"

            def fake_urlopen(*args, **kwargs):
                if failures:
                    raise failures.pop(0)
                return ItemCatalogTests._FakeResponse(html_text)

            with patch.object(item_catalog, "HUIJI_ALIAS_CACHE_FILE", cache_path), patch(
                "item_catalog.urlopen", side_effect=fake_urlopen
            ), patch("item_catalog.time.sleep") as sleep_mock:
                mappings = fetch_huiji_alias_mappings(timeout=1)

        self.assertIn("titania prime blueprint", mappings)
        self.assertIn("陨蜓 Prime 蓝图", mappings["titania prime blueprint"])
        self.assertEqual(sleep_mock.call_count, 2)

    def test_fetch_huiji_alias_mappings_uses_cache_when_network_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "huiji_alias_cache.json"
            cache_payload = {
                "updated_at": "2026-01-01T00:00:00+00:00",
                "mappings": {
                    "titania prime blueprint": ["陨蜓 Prime 蓝图"],
                },
            }
            cache_path.write_text(item_catalog.json.dumps(cache_payload, ensure_ascii=False), encoding="utf-8")

            with patch.object(item_catalog, "HUIJI_ALIAS_CACHE_FILE", cache_path), patch(
                "item_catalog.urlopen",
                side_effect=HTTPError(item_catalog.HUIJI_PRIME_TABLE_URL, 403, "Forbidden", hdrs=None, fp=None),
            ), patch("item_catalog.time.sleep"):
                mappings = fetch_huiji_alias_mappings(timeout=1)

        self.assertIn("titania prime blueprint", mappings)
        self.assertIn("陨蜓 Prime 蓝图", mappings["titania prime blueprint"])


if __name__ == "__main__":
    unittest.main()

