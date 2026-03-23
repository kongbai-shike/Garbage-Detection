import re
from statistics import median
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Tuple

from analyzer import RatioRow, find_high_ratio_items
from item_catalog import load_catalog, resolve_catalog_item
from wfm_client import WFMClient


@dataclass(frozen=True)
class JunkResult:
    recognized_name: str
    matched_name: str
    url_name: str
    count: int
    ducats: int
    average_price: float
    ratio: float


def _normalize_line(text: str) -> str:
    text = text.replace("\n", " ").replace("\r", " ")
    text = text.replace("|", " ").replace("/", " ")
    return " ".join(text.strip().split())


def _contains_prime_token(text: str) -> bool:
    lower_text = text.lower()
    if "prime" in lower_text:
        return True
    # Common OCR confusions: prlme / pr1me / pr|me / p r i m e
    if re.search(r"p\s*r\s*[i1l|]\s*m\s*e", lower_text):
        return True
    return False


def _normalize_prime_token(text: str) -> str:
    normalized = text
    normalized = re.sub(r"p\s*r\s*[i1l|]\s*m\s*e", "Prime", normalized, flags=re.IGNORECASE)
    return normalized


def _extract_name_count(line: str) -> Tuple[str, int] | None:
    normalized = _normalize_line(line)
    if not normalized:
        return None

    patterns = [
        r"^([0-9]{1,3})\s+(.+)$",
        r"^(.+?)\s*[xX*]\s*([0-9]{1,3})$",
        r"^(.+?)\s+([0-9]{1,3})$",
    ]

    for pattern in patterns:
        match = re.match(pattern, normalized)
        if not match:
            continue

        if pattern == patterns[0]:
            count = int(match.group(1))
            name = _normalize_line(match.group(2))
        else:
            name = _normalize_line(match.group(1))
            count = int(match.group(2))

        if count <= 0:
            continue
        return name, count

    return normalized, 1


def _looks_like_complete_item_name(name: str) -> bool:
    lower_name = name.lower()
    tail_markers = [
        "blueprint",
        "chassis",
        "systems",
        "neuroptics",
        "receiver",
        "barrel",
        "stock",
        "blade",
        "handle",
        "hilt",
        "guard",
        "link",
        "gauntlet",
        "蓝图",
    ]
    return any(marker in lower_name for marker in tail_markers)


def _has_inline_count_token(line: str) -> bool:
    normalized = _normalize_line(line)
    return bool(
        re.match(r"^[0-9]{1,3}\s+", normalized)
        or re.search(r"[xX*]\s*[0-9]{1,3}$", normalized)
        or re.search(r"\s[0-9]{1,3}$", normalized)
    )


def _normalize_item_name(name: str) -> str:
    normalized = _normalize_line(name)
    normalized = _normalize_prime_token(normalized)
    normalized = re.sub(r"([A-Za-z])([\u4e00-\u9fff])", r"\1 \2", normalized)
    normalized = re.sub(r"([\u4e00-\u9fff])([A-Za-z])", r"\1 \2", normalized)
    normalized = re.sub(r"机\s*体", "机体", normalized)
    normalized = re.sub(r"系\s*统", "系统", normalized)
    normalized = re.sub(r"神\s*经\s*光\s*元", "神经光元", normalized)

    # Fix common OCR split artifacts like "机体 蓝图" -> "机体蓝图".
    part_prefix = r"(机体|系统|头部神经光元|神经光元|枪机|枪管|枪托|刀刃|刀柄|护手|连接器)"
    normalized = re.sub(r"蓝\s*图", "蓝图", normalized)
    normalized = re.sub(part_prefix + r"\s+蓝图", r"\1蓝图", normalized, flags=re.IGNORECASE)
    normalized = re.sub(part_prefix + r"\s*蓝\b", r"\1蓝图", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"(prime)\s+蓝\b", r"\1 蓝图", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"(蓝图)(\s+\1)+", r"\1", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _pick_display_name(item_name: str, aliases: Iterable[str]) -> str:
    for alias in aliases:
        if re.search(r"[\u4e00-\u9fff]", alias):
            zh_alias = _normalize_line(alias)
            if zh_alias.lower() == item_name.lower():
                continue
            return f"{zh_alias} / {item_name}"
    return item_name


def _to_english_like_query(text: str) -> str:
    variants = WFMClient._expand_query_variants(text)
    if not variants:
        return text

    def score(candidate: str) -> Tuple[int, int]:
        lower = candidate.lower()
        alpha_count = sum(1 for ch in lower if "a" <= ch <= "z")
        prime_bonus = 100 if "prime" in lower else 0
        return prime_bonus + alpha_count, -len(candidate)

    return max(variants, key=score)


def _looks_like_item_tail(text: str) -> bool:
    return _looks_like_complete_item_name(text) or bool(re.search(r"(蓝图|图)$", text))


def _merge_ocr_lines_by_bbox(entries: List[Tuple[float, float, float, float, str]]) -> List[str]:
    if not entries:
        return []

    entries_sorted = sorted(entries, key=lambda row: (row[1], row[0]))
    merged: List[Dict[str, float | str]] = []

    def overlap_ratio(a1: float, a2: float, b1: float, b2: float) -> float:
        overlap = max(0.0, min(a2, b2) - max(a1, b1))
        base = max(1.0, min(a2 - a1, b2 - b1))
        return overlap / base

    for x1, y1, x2, y2, text in entries_sorted:
        if not text:
            continue

        best_index = -1
        best_score = -1.0
        for idx, row in enumerate(merged):
            rx1 = float(row["x1"])
            rx2 = float(row["x2"])
            ry2 = float(row["y2"])
            row_text = str(row["text"])

            if y1 < ry2 - 2:
                continue

            gap = y1 - ry2
            if gap > 42:
                continue

            x_overlap = overlap_ratio(x1, x2, rx1, rx2)
            if x_overlap < 0.35:
                continue

            # Prefer appending short continuation lines (e.g. "图") to the closest block.
            continuation_hint = 0.0
            if len(text) <= 4 or text in {"图", "蓝图"}:
                continuation_hint = 0.4
            if _contains_prime_token(row_text):
                continuation_hint += 0.2

            score = x_overlap + continuation_hint - (gap / 100.0)
            if score > best_score:
                best_score = score
                best_index = idx

        if best_index >= 0 and best_score >= 0.35:
            row = merged[best_index]
            row["text"] = _normalize_line(f"{row['text']} {text}")
            row["x1"] = min(float(row["x1"]), x1)
            row["x2"] = max(float(row["x2"]), x2)
            row["y2"] = max(float(row["y2"]), y2)
        else:
            merged.append({"x1": x1, "x2": x2, "y2": y2, "text": text})

    return [str(row["text"]) for row in merged if str(row["text"]).strip()]


def _cluster_axis(values: List[float], split_threshold: float) -> List[float]:
    if not values:
        return []

    sorted_values = sorted(values)
    clusters: List[List[float]] = [[sorted_values[0]]]
    for value in sorted_values[1:]:
        if abs(value - clusters[-1][-1]) <= split_threshold:
            clusters[-1].append(value)
        else:
            clusters.append([value])

    return [sum(cluster) / len(cluster) for cluster in clusters]


def _merge_ocr_lines_by_grid(entries: List[Tuple[float, float, float, float, str]]) -> List[str]:
    if len(entries) < 4:
        return []

    widths = [max(1.0, x2 - x1) for x1, _, x2, _, _ in entries]
    heights = [max(1.0, y2 - y1) for _, y1, _, y2, _ in entries]
    median_width = float(median(widths))
    median_height = float(median(heights))

    # Use left-top anchors (not text box centers) to avoid cross-column merging
    # when one line is much longer than another.
    col_split = max(40.0, min(85.0, median_width * 0.35))
    row_split = max(70.0, median_height * 4.0)

    x_starts = [x1 for x1, _, _, _, _ in entries]
    y_starts = [y1 for _, y1, _, _, _ in entries]
    col_anchors = _cluster_axis(x_starts, col_split)
    row_anchors = _cluster_axis(y_starts, row_split)

    # Enable grid mode only when it really looks like a slot layout.
    if len(col_anchors) < 2:
        return []

    def nearest_anchor_index(value: float, anchors: List[float]) -> int:
        return min(range(len(anchors)), key=lambda idx: abs(value - anchors[idx]))

    grouped: Dict[Tuple[int, int], List[Tuple[float, float, str]]] = {}
    for x1, y1, x2, y2, text in entries:
        if not text:
            continue
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        col_idx = nearest_anchor_index(cx, col_anchors)
        row_idx = nearest_anchor_index(cy, row_anchors)
        key = (row_idx, col_idx)
        grouped.setdefault(key, []).append((y1, x1, text))

    # Safety: one inventory slot should not contain two independent Prime headers.
    for lines in grouped.values():
        prime_line_count = sum(1 for _, _, text in lines if _contains_prime_token(text))
        if prime_line_count > 1:
            return []

    merged: List[str] = []
    for key in sorted(grouped.keys()):
        lines = sorted(grouped[key], key=lambda row: (row[0], row[1]))
        joined = _normalize_line(" ".join(text for _, _, text in lines))
        if joined:
            merged.append(joined)
    return merged


def _merge_ocr_entries(entries: List[Tuple[float, float, float, float, str]]) -> List[str]:
    grid_merged = _merge_ocr_lines_by_grid(entries)
    if grid_merged:
        return grid_merged
    return _merge_ocr_lines_by_bbox(entries)


def _box_to_rect(box: object) -> Tuple[float, float, float, float]:
    x = 0.0
    y = 0.0
    x2 = 0.0
    y2 = 0.0
    try:
        if isinstance(box, list) and box:
            xs = [float(pt[0]) for pt in box if isinstance(pt, (list, tuple)) and len(pt) >= 2]
            ys = [float(pt[1]) for pt in box if isinstance(pt, (list, tuple)) and len(pt) >= 2]
            if xs and ys:
                x = min(xs)
                y = min(ys)
                x2 = max(xs)
                y2 = max(ys)
    except (TypeError, ValueError):
        pass
    return x, y, x2, y2


def _extract_text_lines_with_rapidocr(path: Path, min_score: float) -> List[str]:
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise RuntimeError(
            "未安装 RapidOCR 依赖，请先执行: pip install rapidocr-onnxruntime pillow"
        ) from exc

    engine = RapidOCR()
    result, _ = engine(str(path))
    if not result:
        return []

    positioned_lines: List[Tuple[float, float, float, float, str]] = []
    for item in result:
        if len(item) < 3:
            continue
        box = item[0]
        text = item[1]
        score = float(item[2])
        if score < min_score:
            continue
        line_text = _normalize_line(str(text))
        if not line_text:
            continue

        x, y, x2, y2 = _box_to_rect(box)
        positioned_lines.append((x, y, x2, y2, line_text))

    return _merge_ocr_entries(positioned_lines)


def _extract_text_lines_with_paddleocr(path: Path, min_score: float) -> List[str]:
    try:
        from paddleocr import PaddleOCR
    except ImportError as exc:
        raise RuntimeError(
            "未安装 PaddleOCR 依赖，请先执行: pip install paddleocr paddlepaddle pillow"
        ) from exc

    # Use mixed Chinese+English model for Warframe item names.
    engine = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
    result = engine.ocr(str(path), cls=True)
    if not result:
        return []

    lines = result[0] if isinstance(result, list) and result else []
    positioned_lines: List[Tuple[float, float, float, float, str]] = []

    for item in lines:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue

        box = item[0]
        rec = item[1]
        if not isinstance(rec, (list, tuple)) or len(rec) < 2:
            continue

        text = str(rec[0])
        try:
            score = float(rec[1])
        except (TypeError, ValueError):
            continue

        if score < min_score:
            continue

        line_text = _normalize_line(text)
        if not line_text:
            continue

        x, y, x2, y2 = _box_to_rect(box)
        positioned_lines.append((x, y, x2, y2, line_text))

    return _merge_ocr_entries(positioned_lines)


def _extract_text_lines_auto(path: Path, min_score: float) -> Tuple[List[str], str]:
    errors: List[str] = []
    for engine_name, extractor in (
        ("paddle", _extract_text_lines_with_paddleocr),
        ("rapid", _extract_text_lines_with_rapidocr),
    ):
        try:
            return extractor(path, min_score), engine_name
        except RuntimeError as exc:
            errors.append(str(exc))

    raise RuntimeError("自动 OCR 初始化失败：" + "；".join(errors))


def parse_recognized_items(lines: Iterable[str]) -> Dict[str, int]:
    parsed: Dict[str, int] = {}
    seen_name_case: Dict[str, str] = {}
    pending_count: int | None = None

    normalized_lines = [_normalize_line(raw) for raw in lines]
    normalized_lines = [line for line in normalized_lines if line]

    def commit_item(name: str, count: int) -> None:
        name = _normalize_item_name(name)
        key = name.lower()
        if key not in seen_name_case:
            seen_name_case[key] = name
            parsed[name] = 0

        canonical = seen_name_case[key]
        parsed[canonical] += count

    def has_prime(text: str) -> bool:
        return _contains_prime_token(text)

    i = 0
    while i < len(normalized_lines):
        line = normalized_lines[i]

        if re.fullmatch(r"[0-9]{1,3}", line):
            pending_count = int(line)
            i += 1
            continue

        best_name = ""
        best_count = 0
        best_span = 0

        base_extracted = _extract_name_count(line)
        can_extend = True
        if base_extracted is not None:
            base_name, _ = base_extracted
            # Count prefix/suffix (e.g. "2 Banshee Prime") should not block continuation lines.
            if has_prime(base_name) and _looks_like_complete_item_name(base_name):
                can_extend = False

        max_span = 3 if can_extend else 1

        # OCR may wrap one item name into 2-3 lines; try the longest safe span first.
        for span in range(1, max_span + 1):
            if i + span > len(normalized_lines):
                break

            segment = normalized_lines[i : i + span]
            if any(re.fullmatch(r"[0-9]{1,3}", s) for s in segment):
                break

            prime_positions = [idx for idx, s in enumerate(segment) if has_prime(s)]
            if len(prime_positions) > 1:
                break

            if span > 1 and not (_looks_like_item_tail(segment[-1]) or _has_inline_count_token(segment[-1])):
                continue

            candidate = " ".join(segment)
            extracted = _extract_name_count(candidate)
            if extracted is None:
                continue

            name, count = extracted
            name = _normalize_prime_token(name)
            if not has_prime(name):
                continue

            best_name = name
            best_count = count
            best_span = span

        if best_span == 0:
            i += 1
            continue

        if best_count == 1 and pending_count is not None:
            best_count = pending_count
        pending_count = None
        commit_item(best_name, best_count)
        i += best_span

    return parsed


def extract_text_lines_from_image(
    image_path: str,
    min_score: float = 0.45,
    ocr_engine: str = "auto",
) -> List[str]:
    lines, _ = extract_text_lines_with_engine(image_path=image_path, min_score=min_score, ocr_engine=ocr_engine)
    return lines


def extract_text_lines_with_engine(
    image_path: str,
    min_score: float = 0.45,
    ocr_engine: str = "auto",
) -> Tuple[List[str], str]:
    path = Path(image_path).expanduser().resolve()
    if not path.exists():
        raise OSError(f"图片不存在: {path}")

    engine_key = ocr_engine.strip().lower()
    if engine_key == "auto":
        return _extract_text_lines_auto(path, min_score)
    if engine_key == "paddle":
        return _extract_text_lines_with_paddleocr(path, min_score), "paddle"
    if engine_key == "rapid":
        return _extract_text_lines_with_rapidocr(path, min_score), "rapid"
    raise ValueError(f"不支持的 OCR 引擎: {ocr_engine}")


def find_junk_from_image(
    image_path: str,
    threshold: float,
    top: int,
    sample_size: int,
    ocr_engine: str = "auto",
    debug: bool = False,
    debug_log: Callable[[str], None] | None = None,
) -> Tuple[List[JunkResult], Dict[str, object]]:
    def log(message: str) -> None:
        if debug and debug_log is not None:
            debug_log(message)

    lines, engine_used = extract_text_lines_with_engine(image_path=image_path, ocr_engine=ocr_engine)
    log(f"OCR 引擎: {engine_used}")
    log(f"OCR 行数: {len(lines)}")

    recognized = parse_recognized_items(lines)
    log(f"识别到 Prime 物品数量: {len(recognized)}")

    catalog_items, catalog_updated_at = load_catalog()
    log(f"物品库条目: {len(catalog_items)}")
    client = WFMClient()

    aggregated: Dict[str, JunkResult] = {}
    matched_queries = 0
    skipped_unknown = 0

    for query, count in recognized.items():
        resolved_query = query
        resolved_url_name = ""
        resolved_display_name = ""
        if catalog_items:
            matched_item = resolve_catalog_item(query, catalog_items)
            if matched_item is None:
                skipped_unknown += 1
                log(f"跳过未命中物品库: {query}")
                continue
            resolved_query = matched_item.item_name
            resolved_url_name = matched_item.url_name
            resolved_display_name = _pick_display_name(matched_item.item_name, matched_item.aliases)
            log(f"查询识别项: {query} x{count} -> {resolved_query}")
        else:
            resolved_query = _to_english_like_query(query)
            log(f"查询识别项: {query} x{count} -> {resolved_query}")

        rows, info = find_high_ratio_items(
            query=resolved_url_name or resolved_query,
            mode="exact" if resolved_url_name else "contains",
            threshold=threshold,
            top=max(1, top),
            sample_size=sample_size,
            debug=debug,
            debug_log=debug_log,
            client=client,
        )
        if info.get("matched_count", 0) == 0 or not rows:
            continue

        matched_queries += 1
        row: RatioRow = rows[0]

        if row.url_name in aggregated:
            prev = aggregated[row.url_name]
            aggregated[row.url_name] = JunkResult(
                recognized_name=prev.recognized_name,
                matched_name=prev.matched_name,
                url_name=prev.url_name,
                count=prev.count + count,
                ducats=prev.ducats,
                average_price=prev.average_price,
                ratio=prev.ratio,
            )
        else:
            aggregated[row.url_name] = JunkResult(
                recognized_name=query,
                matched_name=resolved_display_name or row.item_name,
                url_name=row.url_name,
                count=count,
                ducats=row.ducats,
                average_price=row.average_price,
                ratio=row.ratio,
            )

    results = sorted(aggregated.values(), key=lambda r: r.ratio, reverse=True)
    if top > 0:
        results = results[:top]

    info = {
        "ocr_engine": engine_used,
        "ocr_line_count": len(lines),
        "recognized_count": len(recognized),
        "matched_query_count": matched_queries,
        "skipped_unknown_count": skipped_unknown,
        "catalog_size": len(catalog_items),
        "catalog_updated_at": catalog_updated_at,
    }
    return results, info


