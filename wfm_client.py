import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from difflib import get_close_matches
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class MarketItem:
    item_name: str
    url_name: str
    aliases: Tuple[str, ...] = ()


class WFMClient:
    BASE_URL = "https://api.warframe.market/v2"
    PRICE_CACHE_TTL_SECONDS = 30
    QUERY_TERM_MAP = {
        "机体蓝图": "chassis blueprint",
        "头盔蓝图": "neuroptics blueprint",
        "头部神经光元蓝图": "neuroptics blueprint",
        "神经光元蓝图": "neuroptics blueprint",
        "系统蓝图": "systems blueprint",
        "机体": "chassis",
        "系统": "systems",
        "头部": "neuroptics",
        "枪管": "barrel",
        "枪机": "receiver",
        "枪托": "stock",
        "握柄": "handle",
        "刀刃": "blade",
        "总图": "blueprint",
        "蓝图": "blueprint",
        "套": "set",
    }
    _COMPONENT_MARKER_MAP = {
        "机体": "chassis",
        "chassis": "chassis",
        "系统": "systems",
        "systems": "systems",
        "头部神经光元": "neuroptics",
        "神经光元": "neuroptics",
        "头盔": "neuroptics",
        "头部": "neuroptics",
        "neuroptics": "neuroptics",
        "蓝图": "blueprint",
        "blueprint": "blueprint",
    }

    def __init__(self, timeout: int = 10, price_cache_ttl_seconds: int = PRICE_CACHE_TTL_SECONDS):
        self.timeout = timeout
        self._items_cache: Optional[List[MarketItem]] = None
        self._local_alias_map = self._load_local_aliases()
        self._price_cache_ttl_seconds = max(0, int(price_cache_ttl_seconds))
        self._price_cache: Dict[Tuple[str, int], Tuple[float, Optional[float]]] = {}
        self._price_cache_lock = threading.Lock()
        self._ducats_cache: Dict[str, Tuple[float, Optional[int]]] = {}
        self._ducats_cache_lock = threading.Lock()

    def _get_cached_price(self, cache_key: Tuple[str, int]) -> Optional[float] | object:
        with self._price_cache_lock:
            cached = self._price_cache.get(cache_key)
        if cached is None:
            return _CACHE_MISS

        created_at, value = cached
        if self._price_cache_ttl_seconds > 0 and (time.time() - created_at) > self._price_cache_ttl_seconds:
            with self._price_cache_lock:
                self._price_cache.pop(cache_key, None)
            return _CACHE_MISS
        return value

    def _set_cached_price(self, cache_key: Tuple[str, int], value: Optional[float]) -> None:
        with self._price_cache_lock:
            self._price_cache[cache_key] = (time.time(), value)

    def _get_cached_ducats(self, cache_key: str) -> Optional[int] | object:
        with self._ducats_cache_lock:
            cached = self._ducats_cache.get(cache_key)
        if cached is None:
            return _CACHE_MISS

        created_at, value = cached
        if self._price_cache_ttl_seconds > 0 and (time.time() - created_at) > self._price_cache_ttl_seconds:
            with self._ducats_cache_lock:
                self._ducats_cache.pop(cache_key, None)
            return _CACHE_MISS
        return value

    def _set_cached_ducats(self, cache_key: str, value: Optional[int]) -> None:
        with self._ducats_cache_lock:
            self._ducats_cache[cache_key] = (time.time(), value)

    @staticmethod
    def _load_local_aliases() -> dict:
        alias_file = Path(__file__).with_name("aliases_zh.json")
        if not alias_file.exists():
            return {}

        try:
            data = json.loads(alias_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

        aliases = {}
        for alias, slug in data.items():
            if isinstance(alias, str) and isinstance(slug, str):
                aliases[alias.strip().lower()] = slug.strip().lower()
        return aliases

    def _json_get(self, path: str) -> dict:
        request = Request(
            f"{self.BASE_URL}{path}",
            headers={
                "User-Agent": "wf-ducat-ratio-tool/1.0",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as exc:
            raise RuntimeError(f"warframe.market request failed: {exc}") from exc

    def list_items(self) -> List[MarketItem]:
        if self._items_cache is not None:
            return self._items_cache

        payload = self._json_get("/items")
        entries = payload.get("data", [])

        items = []
        for e in entries:
            en_info = e.get("i18n", {}).get("en", {})
            name = en_info.get("name")
            slug = e.get("slug")
            if name and slug:
                aliases = self._extract_aliases(e)
                items.append(MarketItem(item_name=name, url_name=slug, aliases=aliases))

        self._items_cache = items
        return self._items_cache

    @staticmethod
    def _extract_aliases(item_payload: dict) -> Tuple[str, ...]:
        aliases = []
        i18n = item_payload.get("i18n", {})
        for locale in (
            "zh-hans",
            "zh_hans",
            "zh-cn",
            "zh_cn",
            "zh",
            "zh-hant",
            "zh_hant",
            "zh-tw",
            "zh_tw",
        ):
            localized_name = i18n.get(locale, {}).get("name")
            if localized_name:
                aliases.append(localized_name)
        return tuple(dict.fromkeys(aliases))

    @staticmethod
    def _normalize_query_text(text: str) -> str:
        normalized = text.strip().lower()
        normalized = normalized.replace("|", " ").replace("/", " ")
        normalized = re.sub(r"p\s*r\s*[i1l|]\s*m\s*e", "prime", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"([a-z])([\u4e00-\u9fff])", r"\1 \2", normalized)
        normalized = re.sub(r"([\u4e00-\u9fff])([a-z])", r"\1 \2", normalized)
        normalized = re.sub(r"\b([a-z]{2,})prime\b", r"\1 prime", normalized)
        # Fix OCR character splits in common Prime part labels.
        normalized = re.sub(r"机\s*体", "机体", normalized)
        normalized = re.sub(r"系\s*统", "系统", normalized)
        normalized = re.sub(r"头\s*盔", "头盔", normalized)
        normalized = re.sub(r"神\s*经\s*光\s*元", "神经光元", normalized)
        part_prefix = r"(机体|系统|头部神经光元|神经光元|枪机|枪管|枪托|刀刃|刀柄|护手|连接器)"
        normalized = re.sub(part_prefix + r"\s+蓝图", r"\1蓝图", normalized)
        normalized = re.sub(r"蓝\s*图", "蓝图", normalized)
        normalized = re.sub(part_prefix + r"\s*蓝\b", r"\1蓝图", normalized)
        normalized = re.sub(r"(prime)\s+蓝\b", r"\1 蓝图", normalized)
        return " ".join(normalized.split())

    @classmethod
    def _expand_query_variants(cls, query: str) -> Tuple[str, ...]:
        base = cls._normalize_query_text(query)
        if not base:
            return ("",)

        variants = [base]
        seen = {base}

        # Expand repeatedly so OCR-mixed queries can be translated in combination.
        for _ in range(3):
            snapshot = list(variants)
            changed = False
            for current in snapshot:
                for src, dst in cls.QUERY_TERM_MAP.items():
                    if src in current:
                        candidate = cls._normalize_query_text(current.replace(src, dst))
                        if candidate and candidate not in seen:
                            variants.append(candidate)
                            seen.add(candidate)
                            changed = True
            if not changed:
                break

        return tuple(v for v in variants if v)

    @staticmethod
    def _has_set_intent(text: str) -> bool:
        normalized = WFMClient._normalize_query_text(text)
        return "套" in normalized or "set" in normalized.split(" ")

    @classmethod
    def _extract_component_markers(cls, text: str) -> set[str]:
        normalized = cls._normalize_query_text(text)
        markers: set[str] = set()
        for source, target in cls._COMPONENT_MARKER_MAP.items():
            if source in normalized:
                markers.add(target)
        return markers

    @staticmethod
    def _is_set_item(item: MarketItem) -> bool:
        keys = [item.item_name.lower(), item.url_name.lower(), *(alias.lower() for alias in item.aliases)]
        for key in keys:
            if key.endswith(" set") or " set " in f" {key} ":
                return True
            if "套" in key:
                return True
        return False

    @staticmethod
    def _is_component_item(item: MarketItem) -> bool:
        text = " ".join((item.item_name, item.url_name, *item.aliases)).lower()
        return any(marker in text for marker in ("chassis", "systems", "neuroptics", "机体", "系统", "神经光元"))

    @classmethod
    def _item_matches_component_intent(cls, item: MarketItem, query_components: set[str]) -> bool:
        specific = {m for m in query_components if m in {"chassis", "systems", "neuroptics"}}
        if not specific:
            return True
        item_text = " ".join((item.item_name, item.url_name, *item.aliases)).lower()
        return any(marker in item_text for marker in specific)

    @staticmethod
    def _contains_all_tokens(needle: str, haystack: str) -> bool:
        tokens = [t for t in needle.split(" ") if t]
        return bool(tokens) and all(t in haystack for t in tokens)

    def search_items(self, query: str, mode: str = "exact") -> List[MarketItem]:
        items = self.list_items()
        query_variants = self._expand_query_variants(query)
        query_norm = query_variants[0]
        query_has_set_intent = any(self._has_set_intent(q) for q in query_variants)
        query_components = set().union(*(self._extract_component_markers(q) for q in query_variants))
        query_has_component_intent = bool(query_components - {"blueprint"})
        blueprint_only_intent = "blueprint" in query_components and not query_has_component_intent and not query_has_set_intent
        slug_lookup = {i.url_name.lower(): i for i in items}

        for q in query_variants:
            direct_slug = self._local_alias_map.get(q)
            if direct_slug and direct_slug in slug_lookup:
                mapped = slug_lookup[direct_slug]
                if query_has_set_intent and not self._is_set_item(mapped):
                    continue
                if query_has_component_intent and self._is_set_item(mapped):
                    continue
                if not self._item_matches_component_intent(mapped, query_components):
                    continue
                if blueprint_only_intent and self._is_set_item(mapped):
                    continue
                return [mapped]

        def intent_filter(item: MarketItem) -> bool:
            if query_has_set_intent:
                return self._is_set_item(item)
            if query_has_component_intent and self._is_set_item(item):
                return False
            if not self._item_matches_component_intent(item, query_components):
                return False
            if blueprint_only_intent and self._is_set_item(item):
                return False
            return True

        if mode == "contains":
            direct_matches = [
                i
                for i in items
                if intent_filter(i)
                if any(
                    q in i.item_name.lower()
                    or q in i.url_name.lower()
                    or any(q in alias.lower() for alias in i.aliases)
                    or self._contains_all_tokens(q, i.item_name.lower())
                    for q in query_variants
                )
            ]

            mapped = []
            for alias, slug in self._local_alias_map.items():
                if any(q in alias for q in query_variants) and slug in slug_lookup:
                    candidate = slug_lookup[slug]
                    if intent_filter(candidate):
                        mapped.append(candidate)

            if blueprint_only_intent:
                preferred = [i for i in direct_matches + mapped if "prime blueprint" in i.item_name.lower() and not self._is_component_item(i)]
                if preferred:
                    return list(dict.fromkeys(preferred))

            return list(dict.fromkeys(direct_matches + mapped))

        exact = [
            i
            for i in items
            if intent_filter(i)
            if any(
                i.item_name.lower() == q
                or i.url_name.lower() == q
                or any(alias.lower() == q for alias in i.aliases)
                for q in query_variants
            )
        ]
        if exact:
            return exact

        names_lookup = {i.item_name.lower(): i for i in items}
        close = get_close_matches(query_norm, names_lookup.keys(), n=5, cutoff=0.75)
        return [names_lookup[name] for name in close]

    def get_ducats(self, url_name: str) -> Optional[int]:
        cache_key = url_name.strip().lower()
        cached = self._get_cached_ducats(cache_key)
        if cached is not _CACHE_MISS:
            return cached  # type: ignore[return-value]

        path = f"/items/{quote(url_name)}"
        payload = self._json_get(path)
        ducats = payload.get("data", {}).get("ducats")
        if isinstance(ducats, int) and ducats > 0:
            self._set_cached_ducats(cache_key, ducats)
            return ducats
        self._set_cached_ducats(cache_key, None)
        return None

    def get_ducats_batch(self, url_names: List[str], max_workers: int = 8) -> Dict[str, Optional[int]]:
        cleaned = [name for name in dict.fromkeys(url_names) if isinstance(name, str) and name.strip()]
        if not cleaned:
            return {}

        worker_count = max(1, min(int(max_workers), len(cleaned)))
        if worker_count == 1:
            return {name: self.get_ducats(name) for name in cleaned}

        results: Dict[str, Optional[int]] = {}
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_map = {executor.submit(self.get_ducats, name): name for name in cleaned}
            for future in as_completed(future_map):
                name = future_map[future]
                try:
                    results[name] = future.result()
                except RuntimeError:
                    results[name] = None
        return results

    def get_average_sell_price(self, url_name: str, sample_size: int = 5) -> Optional[float]:
        cache_key = (url_name.strip().lower(), int(sample_size))
        cached = self._get_cached_price(cache_key)
        if cached is not _CACHE_MISS:
            return cached  # type: ignore[return-value]

        path = f"/orders/item/{quote(url_name)}"
        payload = self._json_get(path)
        orders = payload.get("data", [])

        prices = []
        for order in orders:
            if order.get("type") != "sell":
                continue
            if not order.get("visible", True):
                continue

            user = order.get("user", {})
            if user.get("platform") and user.get("platform") != "pc":
                continue
            if user.get("status") not in {"ingame", "online"}:
                continue

            plat = order.get("platinum")
            if isinstance(plat, (int, float)) and plat > 0:
                prices.append(float(plat))

        if not prices:
            self._set_cached_price(cache_key, None)
            return None

        prices.sort()
        sample = prices[: max(1, sample_size)]
        avg = sum(sample) / len(sample)
        self._set_cached_price(cache_key, avg)
        return avg

    def get_average_sell_price_batch(
        self,
        url_names: List[str],
        sample_size: int = 5,
        max_workers: int = 8,
    ) -> Dict[str, Optional[float]]:
        cleaned = [name for name in dict.fromkeys(url_names) if isinstance(name, str) and name.strip()]
        if not cleaned:
            return {}

        worker_count = max(1, min(int(max_workers), len(cleaned)))
        if worker_count == 1:
            return {name: self.get_average_sell_price(name, sample_size=sample_size) for name in cleaned}

        results: Dict[str, Optional[float]] = {}
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_map = {
                executor.submit(self.get_average_sell_price, name, sample_size): name
                for name in cleaned
            }
            for future in as_completed(future_map):
                name = future_map[future]
                try:
                    results[name] = future.result()
                except RuntimeError:
                    results[name] = None
        return results


_CACHE_MISS = object()
