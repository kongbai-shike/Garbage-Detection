import csv
import tempfile
import unittest
from pathlib import Path

from analyzer import RatioRow
from main import write_csv


class MainCsvTests(unittest.TestCase):
    def test_write_csv(self) -> None:
        rows = [
            RatioRow(
                item_name="Saryn Prime Chassis",
                url_name="saryn_prime_chassis",
                ducats=100,
                average_price=9.5,
                ratio=10.5263,
            )
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "result.csv"
            write_csv(str(output), rows)

            with output.open("r", encoding="utf-8-sig", newline="") as fp:
                parsed = list(csv.reader(fp))

        self.assertEqual(parsed[0], ["item_name", "url_name", "ducats", "avg_plat", "ratio"])
        self.assertEqual(parsed[1][0], "Saryn Prime Chassis")
        self.assertEqual(parsed[1][1], "saryn_prime_chassis")
        self.assertEqual(parsed[1][2], "100")
        self.assertEqual(parsed[1][3], "9.50")
        self.assertEqual(parsed[1][4], "10.53")

    def test_write_csv_batch(self) -> None:
        rows = [
            (
                "wisp prime 机体蓝图",
                RatioRow(
                    item_name="Wisp Prime Chassis Blueprint",
                    url_name="wisp_prime_chassis_blueprint",
                    ducats=45,
                    average_price=3.8,
                    ratio=11.8421,
                ),
            )
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "result_batch.csv"
            write_csv(str(output), rows)

            with output.open("r", encoding="utf-8-sig", newline="") as fp:
                parsed = list(csv.reader(fp))

        self.assertEqual(parsed[0], ["source_query", "item_name", "url_name", "ducats", "avg_plat", "ratio"])
        self.assertEqual(parsed[1][0], "wisp prime 机体蓝图")
        self.assertEqual(parsed[1][1], "Wisp Prime Chassis Blueprint")
        self.assertEqual(parsed[1][2], "wisp_prime_chassis_blueprint")
        self.assertEqual(parsed[1][3], "45")
        self.assertEqual(parsed[1][4], "3.80")
        self.assertEqual(parsed[1][5], "11.84")


if __name__ == "__main__":
    unittest.main()

