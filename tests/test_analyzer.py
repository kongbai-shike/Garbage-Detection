import unittest
from unittest.mock import patch

from analyzer import _build_ratio_row, find_high_ratio_items
from wfm_client import MarketItem


class AnalyzerTests(unittest.TestCase):
    def test_build_ratio_row(self) -> None:
        row = _build_ratio_row(
            item_name="Saryn Prime Chassis",
            url_name="saryn_prime_chassis",
            ducats=45,
            avg_price=3.0,
        )
        self.assertEqual(row.item_name, "Saryn Prime Chassis")
        self.assertEqual(row.ducats, 45)
        self.assertAlmostEqual(row.average_price, 3.0)
        self.assertAlmostEqual(row.ratio, 15.0)

    def test_find_high_ratio_items_uses_batch_prices(self) -> None:
        class _FakeClient:
            def search_items(self, query: str, mode: str):
                return [
                    MarketItem(item_name="A", url_name="a"),
                    MarketItem(item_name="B", url_name="b"),
                ]

            def get_ducats(self, url_name: str):
                return {"a": 45, "b": 15}.get(url_name)

            def get_average_sell_price_batch(self, url_names, sample_size: int = 5, max_workers: int = 8):
                self.batch_args = (tuple(url_names), sample_size, max_workers)
                return {"a": 3.0, "b": 2.0}

        fake_client = _FakeClient()
        with patch("analyzer.WFMClient", return_value=fake_client):
            rows, info = find_high_ratio_items(
                query="x",
                mode="contains",
                threshold=10.0,
                top=10,
                sample_size=5,
            )

        self.assertEqual(info["matched_count"], 2)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].url_name, "a")
        self.assertEqual(fake_client.batch_args[0], ("a", "b"))

    def test_find_high_ratio_items_reuses_injected_client(self) -> None:
        class _FakeClient:
            def search_items(self, query: str, mode: str):
                return [MarketItem(item_name="A", url_name="a")]

            def get_ducats(self, url_name: str):
                return 45

            def get_average_sell_price_batch(self, url_names, sample_size: int = 5, max_workers: int = 8):
                return {"a": 3.0}

        fake_client = _FakeClient()
        with patch("analyzer.WFMClient") as ctor_mock:
            rows, info = find_high_ratio_items(
                query="x",
                mode="contains",
                threshold=10.0,
                top=10,
                sample_size=5,
                client=fake_client,
            )

        ctor_mock.assert_not_called()
        self.assertEqual(info["matched_count"], 1)
        self.assertEqual(len(rows), 1)

    def test_find_high_ratio_items_uses_batch_ducats_when_available(self) -> None:
        class _FakeClient:
            def search_items(self, query: str, mode: str):
                return [
                    MarketItem(item_name="A", url_name="a"),
                    MarketItem(item_name="B", url_name="b"),
                ]

            def get_ducats_batch(self, url_names, max_workers: int = 8):
                self.ducats_batch_args = (tuple(url_names), max_workers)
                return {"a": 45, "b": 15}

            def get_average_sell_price_batch(self, url_names, sample_size: int = 5, max_workers: int = 8):
                return {"a": 3.0, "b": 2.0}

        fake_client = _FakeClient()
        rows, _ = find_high_ratio_items(
            query="x",
            mode="contains",
            threshold=10.0,
            top=10,
            sample_size=5,
            client=fake_client,
        )

        self.assertEqual(fake_client.ducats_batch_args[0], ("a", "b"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].url_name, "a")


if __name__ == "__main__":
    unittest.main()

