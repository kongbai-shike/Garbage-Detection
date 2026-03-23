import unittest
from unittest.mock import patch

from wfm_client import MarketItem, WFMClient


class WFMClientSearchTests(unittest.TestCase):
    def test_extract_aliases_supports_underscore_locale_keys(self) -> None:
        payload = {
            "i18n": {
                "zh_hans": {"name": "萨林 Prime 机体蓝图"},
                "zh_cn": {"name": "萨林 Prime 机体蓝图"},
            }
        }

        aliases = WFMClient._extract_aliases(payload)  # pylint: disable=protected-access
        self.assertIn("萨林 Prime 机体蓝图", aliases)

    def test_exact_search_with_local_alias_map(self) -> None:
        client = WFMClient()
        client._items_cache = [  # pylint: disable=protected-access
            MarketItem(item_name="Saryn Prime Chassis", url_name="saryn_prime_chassis")
        ]
        client._local_alias_map = {"萨林 prime 机体蓝图": "saryn_prime_chassis"}  # pylint: disable=protected-access

        matched = client.search_items("萨林 Prime 机体蓝图", mode="exact")
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].url_name, "saryn_prime_chassis")

    def test_exact_search_with_chinese_alias(self) -> None:
        client = WFMClient()
        client._items_cache = [  # pylint: disable=protected-access
            MarketItem(
                item_name="Saryn Prime Chassis",
                url_name="saryn_prime_chassis",
                aliases=("萨林 Prime 机体蓝图",),
            )
        ]

        matched = client.search_items("萨林 Prime 机体蓝图", mode="exact")
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].url_name, "saryn_prime_chassis")

    def test_contains_search_with_chinese_keyword(self) -> None:
        client = WFMClient()
        client._items_cache = [  # pylint: disable=protected-access
            MarketItem(
                item_name="Saryn Prime Chassis",
                url_name="saryn_prime_chassis",
                aliases=("萨林 Prime 机体蓝图",),
            ),
            MarketItem(item_name="Paris Prime String", url_name="paris_prime_string"),
        ]

        matched = client.search_items("机体", mode="contains")
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].url_name, "saryn_prime_chassis")

    def test_contains_search_with_mixed_language_query(self) -> None:
        client = WFMClient()
        client._items_cache = [  # pylint: disable=protected-access
            MarketItem(
                item_name="Wisp Prime Chassis Blueprint",
                url_name="wisp_prime_chassis_blueprint",
            ),
            MarketItem(item_name="Wisp Prime Systems Blueprint", url_name="wisp_prime_systems_blueprint"),
        ]

        matched = client.search_items("wisp prime 机体蓝图", mode="contains")
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].url_name, "wisp_prime_chassis_blueprint")

    def test_contains_search_with_no_space_ocr_query(self) -> None:
        client = WFMClient()
        client._items_cache = [  # pylint: disable=protected-access
            MarketItem(item_name="Ash Prime Neuroptics Blueprint", url_name="ash_prime_neuroptics_blueprint"),
            MarketItem(item_name="Ash Prime Chassis Blueprint", url_name="ash_prime_chassis_blueprint"),
        ]

        matched = client.search_items("AshPrime头部神经光元蓝图", mode="contains")
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].url_name, "ash_prime_neuroptics_blueprint")

    def test_contains_search_with_fuzzy_prime_ocr_query(self) -> None:
        client = WFMClient()
        client._items_cache = [  # pylint: disable=protected-access
            MarketItem(item_name="Wisp Prime Chassis Blueprint", url_name="wisp_prime_chassis_blueprint"),
            MarketItem(item_name="Wisp Prime Systems Blueprint", url_name="wisp_prime_systems_blueprint"),
        ]

        matched = client.search_items("Wisp Pr1me机体 蓝图", mode="contains")
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].url_name, "wisp_prime_chassis_blueprint")

    def test_contains_search_split_component_text_prefers_correct_component(self) -> None:
        client = WFMClient()
        client._items_cache = [  # pylint: disable=protected-access
            MarketItem(item_name="Lavos Prime Chassis Blueprint", url_name="lavos_prime_chassis_blueprint"),
            MarketItem(item_name="Lavos Prime Systems Blueprint", url_name="lavos_prime_systems_blueprint"),
            MarketItem(item_name="Lavos Prime Blueprint", url_name="lavos_prime_blueprint"),
            MarketItem(item_name="Lavos Prime Set", url_name="lavos_prime_set"),
        ]

        matched = client.search_items("Lavos Prime 系 统蓝图", mode="contains")
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].url_name, "lavos_prime_systems_blueprint")

    def test_contains_search_blueprint_only_does_not_return_set(self) -> None:
        client = WFMClient()
        client._items_cache = [  # pylint: disable=protected-access
            MarketItem(item_name="Lavos Prime Blueprint", url_name="lavos_prime_blueprint"),
            MarketItem(item_name="Lavos Prime Set", url_name="lavos_prime_set"),
        ]

        matched = client.search_items("Lavos Prime 蓝", mode="contains")
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].url_name, "lavos_prime_blueprint")

    def test_get_average_sell_price_uses_cache(self) -> None:
        client = WFMClient(price_cache_ttl_seconds=60)
        payload = {
            "data": [
                {"type": "sell", "visible": True, "user": {"platform": "pc", "status": "ingame"}, "platinum": 10},
                {"type": "sell", "visible": True, "user": {"platform": "pc", "status": "online"}, "platinum": 12},
            ]
        }

        with patch.object(client, "_json_get", return_value=payload) as json_get_mock:
            first = client.get_average_sell_price("wisp_prime_chassis", sample_size=2)
            second = client.get_average_sell_price("wisp_prime_chassis", sample_size=2)

        self.assertEqual(first, 11.0)
        self.assertEqual(second, 11.0)
        self.assertEqual(json_get_mock.call_count, 1)

    def test_get_average_sell_price_batch_handles_single_failure(self) -> None:
        client = WFMClient()

        def fake_get(url_name: str, sample_size: int = 5):
            if url_name == "bad_item":
                raise RuntimeError("network")
            return 9.5

        with patch.object(client, "get_average_sell_price", side_effect=fake_get):
            result = client.get_average_sell_price_batch(
                ["good_item", "bad_item", "good_item"],
                sample_size=5,
                max_workers=4,
            )

        self.assertEqual(result.get("good_item"), 9.5)
        self.assertIsNone(result.get("bad_item"))

    def test_get_ducats_uses_cache(self) -> None:
        client = WFMClient(price_cache_ttl_seconds=60)
        payload = {"data": {"ducats": 45}}

        with patch.object(client, "_json_get", return_value=payload) as json_get_mock:
            first = client.get_ducats("wisp_prime_chassis")
            second = client.get_ducats("wisp_prime_chassis")

        self.assertEqual(first, 45)
        self.assertEqual(second, 45)
        self.assertEqual(json_get_mock.call_count, 1)


if __name__ == "__main__":
    unittest.main()

