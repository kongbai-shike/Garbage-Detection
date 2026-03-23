import json
import threading
import time
from html import unescape
from html.parser import HTMLParser
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Set, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from wfm_client import MarketItem, WFMClient

CATALOG_FILE = Path(__file__).with_name("items_catalog.json")
HUIJI_PRIME_TABLE_URL = "https://warframe.huijiwiki.com/index.php?curid=33551"
HUIJI_ALIAS_CACHE_FILE = Path(__file__).with_name("huiji_alias_cache.json")
MANUAL_ALIAS_FILE = Path(__file__).with_name("manual_aliases_zh.json")
AUTO_REFRESH_MAX_AGE_SECONDS = 12 * 60 * 60
GENERIC_TOKENS = {
    "prime",
    "blueprint",
    "set",
    "chassis",
    "systems",
    "neuroptics",
    "机体",
    "系统",
    "头",
    "头部",
    "神经光元",
    "蓝图",
    "套",
}
GENERIC_CHINESE_NAME_LABELS = {
    "组合mod",
    "組合mod",
    "组合模组",
    "組合模組",
    "mod",
}
TRADITIONAL_HINT_CHARS = set("體鬥獵總裝組模槍託護連擊傷異觸發範圍")
COMPONENT_MARKER_MAP = {
    "机体": "chassis",
    "chassis": "chassis",
    "系统": "systems",
    "systems": "systems",
    "头部神经光元": "neuroptics",
    "神经光元": "neuroptics",
    "头部": "neuroptics",
    "头": "neuroptics",
    "neuroptics": "neuroptics",
    "蓝图": "blueprint",
    "blueprint": "blueprint",
}


@dataclass(frozen=True)
class CatalogItem:
    item_name: str
    url_name: str
    aliases: Tuple[str, ...]
    item_chinese_name: str = ""


class _SimpleTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: List[List[str]] = []
        self.tables: List[List[Tuple[List[str], bool]]] = []
        self._in_table = False
        self._current_table: List[Tuple[List[str], bool]] = []
        self._in_tr = False
        self._in_cell = False
        self._current_row_is_header = False
        self._current_row: List[str] = []
        self._cell_parts: List[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        lower_tag = tag.lower()
        if lower_tag == "table":
            self._in_table = True
            self._current_table = []
        elif lower_tag == "tr":
            self._in_tr = True
            self._current_row = []
            self._current_row_is_header = False
        elif self._in_tr and lower_tag in {"td", "th"}:
            self._in_cell = True
            self._cell_parts = []
            if lower_tag == "th":
                self._current_row_is_header = True
        elif self._in_cell and lower_tag == "br":
            self._cell_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        lower_tag = tag.lower()
        if self._in_cell and lower_tag in {"td", "th"}:
            cell = "".join(self._cell_parts).strip()
            self._current_row.append(unescape(cell))
            self._in_cell = False
            self._cell_parts = []
        elif self._in_tr and lower_tag == "tr":
            if any(cell.strip() for cell in self._current_row):
                self.rows.append(self._current_row)
                if self._in_table:
                    self._current_table.append((self._current_row, self._current_row_is_header))
            self._in_tr = False
            self._current_row = []
            self._current_row_is_header = False
        elif lower_tag == "table":
            if self._current_table:
                self.tables.append(self._current_table)
            self._in_table = False
            self._current_table = []

    def handle_data(self, data: str) -> None:  # type: ignore[override]
        if self._in_cell:
            self._cell_parts.append(data)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_text(text: str) -> str:
    normalized = WFMClient._normalize_query_text(text)
    return normalized.replace("_", " ")


def _is_english_item_text(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    if "prime" in normalized:
        return True
    alpha_count = sum(1 for c in normalized if "a" <= c <= "z")
    return alpha_count >= max(4, len(normalized) // 3)


def _split_alias_candidates(text: str) -> List[str]:
    normalized = text.replace("\r", "\n")
    for sep in ["/", "|", "；", ";", "，", ",", "、"]:
        normalized = normalized.replace(sep, "\n")
    parts = [" ".join(p.split()) for p in normalized.split("\n")]
    return [p for p in parts if p]


def _normalize_header_text(text: str) -> str:
    return "".join(text.strip().lower().split())


def _header_has_english_name(header_text: str) -> bool:
    normalized = _normalize_header_text(header_text)
    return "英文" in normalized or normalized in {"english", "en"}


def _header_has_chinese_name(header_text: str) -> bool:
    normalized = _normalize_header_text(header_text)
    if "分類" in normalized or "分类" in normalized:
        return False
    if "简体" in normalized or "簡體" in normalized:
        return True
    if "繁体" in normalized or "繁體" in normalized:
        return True
    if "中文" in normalized or "名稱" in normalized or "名称" in normalized:
        return True
    return normalized in {"zh", "chinese"}


def _is_huiji_category_label(text: str) -> bool:
    compact = "".join(text.strip().lower().split())
    if not compact:
        return True
    if "分类" in compact or "分類" in compact:
        return True
    if compact.endswith("mod"):
        return True
    return compact in {
        "夜靈平原",
        "夜灵平原",
        "奧布山谷",
        "奥布山谷",
        "魔裔禁地",
        "亡骸機甲mod",
        "亡骸机甲mod",
        "主武器mod",
        "副武器mod",
        "近戰mod",
        "近战mod",
        "空戰mod",
        "空战mod",
    }


def _extract_huiji_mappings_from_html(html_text: str) -> Dict[str, Set[str]]:
    parser = _SimpleTableParser()
    parser.feed(html_text)

    mappings: Dict[str, Set[str]] = {}
    tables = parser.tables or [[(row, False) for row in parser.rows]]

    for table in tables:
        english_col = -1
        chinese_cols: List[int] = []
        for row, is_header in table:
            if not is_header:
                continue
            for idx, header in enumerate(row):
                if _header_has_english_name(header) and english_col < 0:
                    english_col = idx
                if _header_has_chinese_name(header):
                    chinese_cols.append(idx)
            if english_col >= 0 and chinese_cols:
                break

        for row, is_header in table:
            if is_header or len(row) < 2:
                continue

            english_candidates: List[str] = []
            chinese_candidates: List[str] = []

            if 0 <= english_col < len(row):
                for part in _split_alias_candidates(row[english_col]):
                    if _is_english_item_text(part):
                        english_candidates.append(part)
            else:
                for cell in row:
                    for part in _split_alias_candidates(cell):
                        if _is_english_item_text(part):
                            english_candidates.append(part)

            source_zh_cols = [idx for idx in chinese_cols if 0 <= idx < len(row)]
            if not source_zh_cols:
                # Fallback for no-header tables: take the first two CJK cells after English.
                start_idx = english_col + 1 if english_col >= 0 else 0
                for idx in range(max(0, start_idx), len(row)):
                    cell = row[idx]
                    if any("\u4e00" <= ch <= "\u9fff" for ch in cell):
                        source_zh_cols.append(idx)
                    if len(source_zh_cols) >= 2:
                        break

            for idx in source_zh_cols:
                for part in _split_alias_candidates(row[idx]):
                    if any("\u4e00" <= ch <= "\u9fff" for ch in part):
                        chinese_candidates.append(part)

            if not english_candidates or not chinese_candidates:
                continue

            for en_name in english_candidates:
                en_key = _normalize_text(en_name)
                if not en_key:
                    continue
                bucket = mappings.setdefault(en_key, set())
                for zh_name in chinese_candidates:
                    cleaned = " ".join(zh_name.split())
                    if cleaned and not _is_huiji_category_label(cleaned):
                        bucket.add(cleaned)

    return mappings


def _contains_chinese_text(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _is_generic_chinese_label(text: str) -> bool:
    compact = "".join(text.strip().lower().split())
    if compact in GENERIC_CHINESE_NAME_LABELS:
        return True
    if "mod" in compact and len([ch for ch in compact if "\u4e00" <= ch <= "\u9fff"]) <= 4:
        return True
    return False


def _looks_like_traditional_text(text: str) -> bool:
    return any(ch in TRADITIONAL_HINT_CHARS for ch in text)


def _pick_primary_chinese_name(huiji_aliases: Set[str], fallback_aliases: Iterable[str]) -> str:
    candidates: List[Tuple[int, str]] = []
    seen: set[str] = set()

    # Prefer warframe.market aliases first because zh-hans is already prioritized there.
    for alias in fallback_aliases:
        cleaned = " ".join(alias.split())
        key = cleaned.lower()
        if (
            not cleaned
            or key in seen
            or not _contains_chinese_text(cleaned)
            or _is_huiji_category_label(cleaned)
        ):
            continue
        seen.add(key)
        candidates.append((0, cleaned))

    for alias in sorted(huiji_aliases):
        cleaned = " ".join(alias.split())
        key = cleaned.lower()
        if (
            not cleaned
            or key in seen
            or not _contains_chinese_text(cleaned)
            or _is_huiji_category_label(cleaned)
        ):
            continue
        seen.add(key)
        candidates.append((1, cleaned))

    if not candidates:
        return ""

    scored = [
        (
            1 if _is_generic_chinese_label(value) else 0,
            source_rank,
            1 if _looks_like_traditional_text(value) else 0,
            0 if "prime" in value.lower() else 1,
            len(value),
            value.lower(),
            value,
        )
        for source_rank, value in candidates
    ]
    scored.sort()
    return scored[0][-1]


def fetch_huiji_alias_mappings(timeout: int = 15) -> Dict[str, Set[str]]:
    retries = 3
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        request = Request(
            HUIJI_PRIME_TABLE_URL,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": "https://warframe.huijiwiki.com/",
                "Connection": "keep-alive",
            },
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                html_text = response.read().decode("utf-8", errors="ignore")
            mappings = _extract_huiji_mappings_from_html(html_text)
            if mappings:
                _save_huiji_alias_cache(mappings)
            return mappings
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(0.8 * (2 ** (attempt - 1)))

    cached = _load_huiji_alias_cache()
    if cached:
        return cached

    raise RuntimeError(f"huiji wiki request failed: {last_exc}") from last_exc


def _save_huiji_alias_cache(mappings: Dict[str, Set[str]]) -> None:
    payload = {
        "updated_at": _now_iso(),
        "mappings": {key: sorted(values) for key, values in mappings.items()},
    }
    try:
        HUIJI_ALIAS_CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return


def _load_huiji_alias_cache() -> Dict[str, Set[str]]:
    if not HUIJI_ALIAS_CACHE_FILE.exists():
        return {}

    try:
        payload = json.loads(HUIJI_ALIAS_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    raw = payload.get("mappings", {})
    if not isinstance(raw, dict):
        return {}

    parsed: Dict[str, Set[str]] = {}
    for key, values in raw.items():
        if not isinstance(key, str) or not isinstance(values, list):
            continue
        cleaned = {v for v in values if isinstance(v, str) and v.strip()}
        if cleaned:
            parsed[key] = cleaned
    return parsed


def _load_manual_alias_mappings() -> Dict[str, Set[str]]:
    if not MANUAL_ALIAS_FILE.exists():
        return {}

    try:
        payload = json.loads(MANUAL_ALIAS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(payload, dict):
        return {}

    parsed: Dict[str, Set[str]] = {}
    for _category_name, value in payload.items():
        if not isinstance(value, dict):
            continue
        for zh_name, en_name in value.items():
            if not isinstance(zh_name, str) or not isinstance(en_name, str):
                continue
            zh_clean = " ".join(zh_name.split())
            en_key = _normalize_text(en_name)
            if not zh_clean or not en_key or not _contains_chinese_text(zh_clean):
                continue
            parsed.setdefault(en_key, set()).add(zh_clean)

    return parsed


def _merge_mapping_sources(*sources: Dict[str, Set[str]]) -> Dict[str, Set[str]]:
    merged: Dict[str, Set[str]] = {}
    for source in sources:
        for key, values in source.items():
            if not key or not values:
                continue
            merged.setdefault(key, set()).update(values)
    return merged


def _merge_huiji_aliases(catalog_items: List[CatalogItem], huiji_aliases: Dict[str, Set[str]]) -> List[CatalogItem]:
    merged: List[CatalogItem] = []
    for item in catalog_items:
        key = _normalize_text(item.item_name)
        mapped_aliases = huiji_aliases.get(key, set())
        alias_ordered = list(item.aliases)
        seen = {" ".join(alias.split()).lower() for alias in alias_ordered}
        for alias in sorted(mapped_aliases):
            if _is_huiji_category_label(alias):
                continue
            surface_key = " ".join(alias.split()).lower()
            if not surface_key or surface_key in seen:
                continue
            alias_ordered.append(alias)
            seen.add(surface_key)

        primary_name = _pick_primary_chinese_name(mapped_aliases, alias_ordered)
        if not primary_name:
            primary_name = item.item_chinese_name

        merged.append(
            CatalogItem(
                item_name=item.item_name,
                url_name=item.url_name,
                aliases=tuple(alias_ordered),
                item_chinese_name=primary_name,
            )
        )

    return merged


def _catalog_keys(item: CatalogItem) -> Tuple[str, ...]:
    keys = {
        _normalize_text(item.item_name),
        _normalize_text(item.url_name),
        _normalize_text(item.url_name.replace("_", " ")),
    }
    for alias in item.aliases:
        keys.add(_normalize_text(alias))
    if item.item_chinese_name:
        keys.add(_normalize_text(item.item_chinese_name))
    return tuple(k for k in keys if k)


def _contains_all_tokens(needle: str, haystack: str) -> bool:
    tokens = [t for t in needle.split(" ") if t]
    return bool(tokens) and all(token in haystack for token in tokens)


def _has_set_intent(text: str) -> bool:
    normalized = _normalize_text(text)
    return "套" in text or "set" in normalized.split(" ")


def _has_component_intent(text: str) -> bool:
    normalized = _normalize_text(text)
    markers = ("蓝图", "机体", "系统", "头", "神经光元", "blueprint", "chassis", "systems", "neuroptics")
    return any(marker in normalized for marker in markers)


def _extract_component_markers(text: str) -> set[str]:
    normalized = _normalize_text(text)
    markers: set[str] = set()
    for source, target in COMPONENT_MARKER_MAP.items():
        if source in normalized:
            markers.add(target)
    return markers


def _item_component_markers(item: CatalogItem) -> set[str]:
    text = " ".join(_catalog_keys(item))
    markers: set[str] = set()
    for source, target in COMPONENT_MARKER_MAP.items():
        if source in text:
            markers.add(target)
    return markers


def _is_set_item(item: CatalogItem) -> bool:
    for key in _catalog_keys(item):
        if key.endswith(" set") or " set " in f" {key} ":
            return True
    return False


def save_catalog(items: Iterable[CatalogItem]) -> Path:
    payload = {
        "updated_at": _now_iso(),
        "items": [asdict(item) for item in items],
    }
    CATALOG_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return CATALOG_FILE


def load_catalog() -> Tuple[List[CatalogItem], str]:
    if not CATALOG_FILE.exists():
        return [], ""

    try:
        payload = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], ""

    if isinstance(payload, list):
        raw_items = payload
        updated_at = ""
    elif isinstance(payload, dict):
        raw_items = payload.get("items", [])
        raw_updated_at = payload.get("updated_at")
        updated_at = raw_updated_at if isinstance(raw_updated_at, str) else ""
    else:
        return [], ""

    items: List[CatalogItem] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        item_name = raw.get("item_name")
        url_name = raw.get("url_name")
        aliases = raw.get("aliases", [])
        item_chinese_name = raw.get("item_chinese_name", "")
        if not isinstance(item_name, str) or not isinstance(url_name, str):
            continue
        if not isinstance(aliases, list):
            aliases = []
        if not isinstance(item_chinese_name, str):
            item_chinese_name = ""
        alias_list = tuple(a for a in aliases if isinstance(a, str))
        items.append(
            CatalogItem(
                item_name=item_name,
                url_name=url_name,
                aliases=alias_list,
                item_chinese_name=item_chinese_name,
            )
        )

    return items, updated_at


def refresh_catalog(debug_log: Callable[[str], None] | None = None) -> Tuple[int, Path]:
    def log(msg: str) -> None:
        if debug_log is not None:
            debug_log(msg)

    huiji_aliases: Dict[str, Set[str]] = {}
    try:
        huiji_aliases = fetch_huiji_alias_mappings(timeout=15)
        log(f"Huiji 别名映射加载完成：{len(huiji_aliases)} 条英文名")
    except RuntimeError as exc:
        log(f"Huiji 映射加载失败（先使用 warframe.market 数据）: {exc}")

    manual_aliases = _load_manual_alias_mappings()
    if manual_aliases:
        log(f"手工别名映射加载完成：{len(manual_aliases)} 条英文名")

    all_aliases = _merge_mapping_sources(huiji_aliases, manual_aliases)

    client = WFMClient()
    market_items = client.list_items()
    catalog_items: List[CatalogItem] = []
    for item in market_items:
        huiji_names = set()
        item_keys = {
            _normalize_text(item.item_name),
            _normalize_text(item.url_name),
            _normalize_text(item.url_name.replace("_", " ")),
        }
        for candidate_key in item_keys:
            if candidate_key:
                huiji_names.update(all_aliases.get(candidate_key, set()))

        # Manual mappings are often base names (e.g. "Odonata Prime"),
        # so allow token-based match to related tradable entries.
        for manual_key, aliases in manual_aliases.items():
            if any(_contains_all_tokens(manual_key, key) for key in item_keys if key):
                huiji_names.update(aliases)

        alias_ordered = list(item.aliases)
        seen = {" ".join(alias.split()).lower() for alias in alias_ordered}
        for alias in sorted(huiji_names):
            if _is_huiji_category_label(alias):
                continue
            surface_key = " ".join(alias.split()).lower()
            if not surface_key or surface_key in seen:
                continue
            alias_ordered.append(alias)
            seen.add(surface_key)

        catalog_items.append(
            CatalogItem(
                item_name=item.item_name,
                url_name=item.url_name,
                aliases=tuple(alias_ordered),
                item_chinese_name=_pick_primary_chinese_name(huiji_names, alias_ordered),
            )
        )

    catalog_items = _merge_huiji_aliases(catalog_items, all_aliases)

    path = save_catalog(catalog_items)
    log(f"物品库更新完成：{len(catalog_items)} 项 -> {path}")
    return len(catalog_items), path


def _catalog_needs_refresh(max_age_seconds: int = AUTO_REFRESH_MAX_AGE_SECONDS) -> bool:
    if not CATALOG_FILE.exists():
        return True

    try:
        mtime = CATALOG_FILE.stat().st_mtime
    except OSError:
        return True

    age_seconds = max(0.0, time.time() - mtime)
    return age_seconds > max_age_seconds


def start_catalog_auto_refresh(
    debug_log: Callable[[str], None] | None = None,
    force: bool = False,
) -> threading.Thread:
    def worker() -> None:
        try:
            if not force and not _catalog_needs_refresh():
                if debug_log is not None:
                    debug_log("物品库在12小时内已更新，跳过后台刷新")
                return
            refresh_catalog(debug_log=debug_log)
        except RuntimeError as exc:
            if debug_log is not None:
                debug_log(f"物品库更新失败（使用本地缓存）: {exc}")
        except OSError as exc:
            if debug_log is not None:
                debug_log(f"物品库保存失败（使用内存数据）: {exc}")

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


def resolve_catalog_item(query: str, catalog_items: List[CatalogItem]) -> CatalogItem | None:
    if not query or not catalog_items:
        return None

    query_variants = WFMClient._expand_query_variants(query)
    query_has_set_intent = any(_has_set_intent(v) for v in query_variants)
    query_markers = set().union(*(_extract_component_markers(v) for v in query_variants))
    query_has_component_intent = any(_has_component_intent(v) for v in query_variants)
    query_specific_components = {m for m in query_markers if m in {"chassis", "systems", "neuroptics"}}
    query_blueprint_only = "blueprint" in query_markers and not query_specific_components and not query_has_set_intent

    # Build quick exact index first.
    exact_index: Dict[str, CatalogItem] = {}
    keys_by_slug: Dict[str, Tuple[str, ...]] = {}
    item_type_by_slug: Dict[str, bool] = {}
    item_markers_by_slug: Dict[str, set[str]] = {}
    for item in catalog_items:
        keys = _catalog_keys(item)
        keys_by_slug[item.url_name] = keys
        item_type_by_slug[item.url_name] = _is_set_item(item)
        item_markers_by_slug[item.url_name] = _item_component_markers(item)
        for key in keys:
            if key and key not in exact_index:
                exact_index[key] = item

    for variant in query_variants:
        matched = exact_index.get(variant)
        if matched is not None:
            matched_is_set = item_type_by_slug.get(matched.url_name, False)
            matched_markers = item_markers_by_slug.get(matched.url_name, set())
            if query_has_set_intent and not matched_is_set:
                continue
            if query_has_component_intent and not query_has_set_intent and matched_is_set:
                continue
            if query_specific_components and not (matched_markers & query_specific_components):
                continue
            if query_blueprint_only and matched_is_set:
                continue
            return matched

    keyword_pool: set[str] = set()
    for variant in query_variants:
        for token in variant.split(" "):
            t = token.strip()
            if not t:
                continue
            if len(t) == 1 and t not in {"蓝", "图", "机", "系", "头"}:
                continue
            keyword_pool.add(t)

    keywords = sorted(keyword_pool, key=len, reverse=True)
    if not keywords:
        return None
    non_generic_keywords = [token for token in keywords if token not in GENERIC_TOKENS]

    best_item: CatalogItem | None = None
    best_score = 0

    for item in catalog_items:
        item_is_set = item_type_by_slug.get(item.url_name, False)
        item_markers = item_markers_by_slug.get(item.url_name, set())
        if query_has_set_intent and not item_is_set:
            continue
        if query_has_component_intent and not query_has_set_intent and item_is_set:
            continue
        if query_specific_components and not (item_markers & query_specific_components):
            continue
        if query_blueprint_only and item_is_set:
            continue

        keys = keys_by_slug.get(item.url_name, ())
        if non_generic_keywords and not any(
            token in key for token in non_generic_keywords for key in keys if key
        ):
            continue

        item_score = 0
        for key in keys:
            if not key:
                continue

            hit_count = sum(1 for token in keywords if token in key)
            if hit_count == 0:
                continue

            longest_hit = max((len(token) for token in keywords if token in key), default=0)
            coverage = int(100 * hit_count / max(1, len(keywords)))

            score = coverage + longest_hit
            if "prime" in key and any("prime" in token for token in keywords):
                score += 10
            if _contains_all_tokens(" ".join(keywords[: min(4, len(keywords))]), key):
                score += 15
            if query_specific_components and any(component in key for component in query_specific_components):
                score += 30
            if query_blueprint_only:
                if "prime blueprint" in key and not any(marker in key for marker in ("chassis", "systems", "neuroptics")):
                    score += 25
                if any(marker in key for marker in ("chassis", "systems", "neuroptics")):
                    score -= 10

            if score > item_score:
                item_score = score

        if item_score > best_score:
            best_score = item_score
            best_item = item

    return best_item if best_score >= 35 else None

