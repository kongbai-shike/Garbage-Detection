import csv
import re
from pathlib import Path
from typing import List


def parse_queries_text(text: str) -> List[str]:
    # Support one-per-line and common separators for quick paste.
    raw_parts = re.split(r"[\r\n,;]+", text)
    queries = []
    seen = set()
    for part in raw_parts:
        query = part.strip()
        if not query:
            continue
        key = query.lower()
        if key in seen:
            continue
        seen.add(key)
        queries.append(query)
    return queries


def read_queries_from_file(file_path: str) -> List[str]:
    path = Path(file_path).expanduser().resolve()
    suffix = path.suffix.lower()

    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as fp:
            reader = csv.reader(fp)
            lines = []
            for idx, row in enumerate(reader):
                if not row:
                    continue
                cell = row[0].strip()
                if idx == 0 and cell.lower() in {"query", "item", "name", "keyword"}:
                    continue
                lines.append(cell)
        return parse_queries_text("\n".join(lines))

    text = path.read_text(encoding="utf-8-sig")
    return parse_queries_text(text)

