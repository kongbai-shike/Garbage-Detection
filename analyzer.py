from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple

from wfm_client import WFMClient


@dataclass(frozen=True)
class RatioRow:
    item_name: str
    url_name: str
    ducats: int
    average_price: float
    ratio: float


def _build_ratio_row(item_name: str, url_name: str, ducats: int, avg_price: float) -> RatioRow:
    ratio = ducats / avg_price
    return RatioRow(
        item_name=item_name,
        url_name=url_name,
        ducats=ducats,
        average_price=avg_price,
        ratio=ratio,
    )


def find_high_ratio_items(
    query: str,
    mode: str,
    threshold: float,
    top: int,
    sample_size: int,
    debug: bool = False,
    debug_log: Callable[[str], None] | None = None,
    client: WFMClient | None = None,
) -> Tuple[List[RatioRow], Dict[str, int]]:
    def log(message: str) -> None:
        if debug and debug_log is not None:
            debug_log(message)

    log(f"开始查询：query='{query}', mode={mode}, threshold={threshold:g}, top={top}, sample_size={sample_size}")

    client = client or WFMClient()
    candidates = client.search_items(query=query, mode=mode)
    log(f"候选物品数量：{len(candidates)}")

    candidate_urls = [item.url_name for item in candidates]
    ducats_payload: Dict[str, int | None]
    if hasattr(client, "get_ducats_batch"):
        ducats_payload = client.get_ducats_batch(candidate_urls, max_workers=8)  # type: ignore[attr-defined]
    else:
        ducats_payload = {url: client.get_ducats(url) for url in candidate_urls}

    ducats_by_url: Dict[str, int] = {}
    for index, item in enumerate(candidates, start=1):
        log(f"[{index}/{len(candidates)}] 检查物品：{item.item_name} ({item.url_name})")
        ducats = ducats_payload.get(item.url_name)
        if ducats is None:
            log("  - 跳过：未获取到 ducats")
            continue
        ducats_by_url[item.url_name] = ducats

    price_by_url = client.get_average_sell_price_batch(
        list(ducats_by_url.keys()),
        sample_size=sample_size,
        max_workers=8,
    )

    rows: List[RatioRow] = []
    for item in candidates:
        ducats = ducats_by_url.get(item.url_name)
        if ducats is None:
            continue

        average_price = price_by_url.get(item.url_name)
        if average_price is None or average_price <= 0:
            log("  - 跳过：未获取到有效均价")
            continue

        row = _build_ratio_row(
            item_name=item.item_name,
            url_name=item.url_name,
            ducats=ducats,
            avg_price=average_price,
        )
        log(f"  - 数据：ducats={row.ducats}, avg={row.average_price:.2f}, ratio={row.ratio:.2f}")
        if row.ratio >= threshold:
            rows.append(row)
            log("  - 通过阈值，加入结果")
        else:
            log("  - 未通过阈值")

    rows.sort(key=lambda r: r.ratio, reverse=True)
    if top > 0:
        rows = rows[:top]

    info = {
        "matched_count": len(candidates),
    }
    log(f"查询结束：高锌价比结果数量={len(rows)}")
    return rows, info
