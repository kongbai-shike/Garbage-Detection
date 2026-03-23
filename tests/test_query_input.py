import tempfile
import unittest
from pathlib import Path

from query_input import parse_queries_text, read_queries_from_file


class QueryInputTests(unittest.TestCase):
    def test_parse_queries_text(self) -> None:
        queries = parse_queries_text("wisp prime 机体蓝图; saryn prime set\nWISP PRIME 机体蓝图")
        self.assertEqual(queries, ["wisp prime 机体蓝图", "saryn prime set"])

    def test_read_queries_from_txt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "queries.txt"
            path.write_text("wisp prime 机体蓝图\nrevenant prime systems blueprint", encoding="utf-8")
            queries = read_queries_from_file(str(path))

        self.assertEqual(queries, ["wisp prime 机体蓝图", "revenant prime systems blueprint"])

    def test_read_queries_from_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "queries.csv"
            path.write_text("query,note\nwisp prime 机体蓝图,a\nkhora prime chassis,b\n", encoding="utf-8")
            queries = read_queries_from_file(str(path))

        self.assertEqual(queries, ["wisp prime 机体蓝图", "khora prime chassis"])


if __name__ == "__main__":
    unittest.main()


