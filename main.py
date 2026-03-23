import argparse
import csv
from pathlib import Path
from typing import List, Sequence, Tuple

from analyzer import find_high_ratio_items
from item_catalog import start_catalog_auto_refresh
from ocr_pipeline import find_junk_from_image
from query_input import parse_queries_text, read_queries_from_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="查询 Warframe 物品锌价比（ducats / plat）"
    )
    parser.add_argument(
        "query",
        nargs="?",
        help="要查询的物品名（根据 --mode 走精确或包含匹配）",
    )
    parser.add_argument(
        "--queries",
        default="",
        help="批量查询词，支持逗号/分号分隔，例如：a;b;c",
    )
    parser.add_argument(
        "--query-file",
        default="",
        help="批量查询文件路径（txt/csv），每行一条或 csv 第一列",
    )
    parser.add_argument(
        "--ocr-image",
        default="",
        help="从图片 OCR 识别物品并查询（例如: screenshot.png）",
    )
    parser.add_argument(
        "--ocr-engine",
        choices=["auto", "paddle", "rapid"],
        default="auto",
        help="OCR 引擎：auto=优先 PaddleOCR，失败回退 RapidOCR",
    )
    parser.add_argument(
        "--mode",
        choices=["exact", "contains"],
        default="exact",
        help="搜索模式：exact=精确匹配，contains=关键字匹配",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=10.0,
        help="最小锌价比阈值（默认: 10）",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="最多输出多少条高锌价比结果（默认: 20）",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=5,
        help="取最便宜多少个卖单做均价（默认: 5）",
    )
    parser.add_argument(
        "--export-csv",
        default="",
        help="可选：导出高锌价比结果到 CSV 路径",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="启动图形界面",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="打印调试过程日志",
    )
    return parser.parse_args()


def format_line(index: int, row) -> str:
    return (
        f"{index:>2}. {row.item_name:<35} "
        f"ducats={row.ducats:<3} "
        f"avg_plat={row.average_price:>6.2f} "
        f"ratio={row.ratio:>6.2f}"
    )


def write_csv(output_path: str, rows: Sequence) -> Path:
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    is_batch = bool(rows) and isinstance(rows[0], tuple)

    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.writer(fp)
        if is_batch:
            writer.writerow(["source_query", "item_name", "url_name", "ducats", "avg_plat", "ratio"])
            for source_query, row in rows:
                writer.writerow(
                    [
                        source_query,
                        row.item_name,
                        row.url_name,
                        row.ducats,
                        f"{row.average_price:.2f}",
                        f"{row.ratio:.2f}",
                    ]
                )
        else:
            writer.writerow(["item_name", "url_name", "ducats", "avg_plat", "ratio"])
            for row in rows:
                writer.writerow(
                    [
                        row.item_name,
                        row.url_name,
                        row.ducats,
                        f"{row.average_price:.2f}",
                        f"{row.ratio:.2f}",
                    ]
                )

    return path


def collect_queries(args: argparse.Namespace) -> List[str]:
    queries: List[str] = []

    if args.query:
        queries.extend(parse_queries_text(args.query))
    if args.queries:
        queries.extend(parse_queries_text(args.queries))
    if args.query_file:
        queries.extend(read_queries_from_file(args.query_file))

    seen = set()
    uniq = []
    for q in queries:
        key = q.lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(q)
    return uniq


def write_ocr_csv(output_path: str, rows) -> Path:
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(["name", "count", "ducats", "avg_plat", "ratio", "url_name"])
        for row in rows:
            writer.writerow(
                [
                    row.matched_name,
                    row.count,
                    row.ducats,
                    f"{row.average_price:.2f}",
                    f"{row.ratio:.2f}",
                    row.url_name,
                ]
            )

    return path


def main() -> None:
    args = parse_args()

    def catalog_log(message: str) -> None:
        if args.debug:
            print(f"[CATALOG] {message}")

    start_catalog_auto_refresh(debug_log=catalog_log)

    if args.ocr_image:
        def ocr_debug(message: str) -> None:
            print(f"[DEBUG] {message}")

        try:
            rows, info = find_junk_from_image(
                image_path=args.ocr_image,
                threshold=args.threshold,
                top=args.top,
                sample_size=args.sample_size,
                ocr_engine=args.ocr_engine,
                debug=args.debug,
                debug_log=ocr_debug,
            )
        except (RuntimeError, OSError) as exc:
            print(f"OCR 查询失败: {exc}")
            return

        print(
            f"OCR 引擎: {info.get('ocr_engine', 'unknown')}，"
            f"OCR 行数: {info['ocr_line_count']}，识别物品: {info['recognized_count']}，"
            f"有匹配结果: {info['matched_query_count']}"
        )
        if not rows:
            print("没有识别到可卖垃圾（高锌价比）物品。")
            return

        print("\n可卖垃圾（名称 x 数量）:")
        for i, row in enumerate(rows, start=1):
            print(
                f"{i:>2}. {row.matched_name:<35} x{row.count:<3} "
                f"ducats={row.ducats:<3} avg_plat={row.average_price:>6.2f} ratio={row.ratio:>6.2f}"
            )

        if args.export_csv:
            try:
                csv_path = write_ocr_csv(args.export_csv, rows)
                print(f"\nCSV 已导出: {csv_path}")
            except OSError as exc:
                print(f"\nCSV 导出失败: {exc}")
        return
    has_cli_query = bool(args.query or args.queries or args.query_file)

    if args.gui or not has_cli_query:
        try:
            from gui_app import run_gui
        except Exception as exc:
            print(f"GUI 启动失败: {exc}")
            print("可改用命令行模式，例如: python main.py \"wisp prime chassis\"")
            return
        run_gui()
        return

    def cli_debug(message: str) -> None:
        print(f"[DEBUG] {message}")

    try:
        queries = collect_queries(args)
    except OSError as exc:
        print(f"读取批量查询文件失败: {exc}")
        return

    if not queries:
        print("没有有效查询词。")
        return

    is_batch = len(queries) > 1
    all_rows: List[Tuple[str, object]] = []
    total_matched = 0

    for index, query in enumerate(queries, start=1):
        if is_batch:
            print(f"\n[{index}/{len(queries)}] 查询：{query}")

        try:
            results, info = find_high_ratio_items(
                query=query,
                mode=args.mode,
                threshold=args.threshold,
                top=args.top,
                sample_size=args.sample_size,
                debug=args.debug,
                debug_log=cli_debug,
            )
        except RuntimeError as exc:
            print(f"请求失败: {exc}")
            if not is_batch:
                return
            continue

        matched_count = info["matched_count"]
        total_matched += matched_count

        if matched_count == 0:
            print("没有匹配到物品。可以尝试 --mode contains 关键字搜索。")
            continue

        print(
            f"匹配到 {matched_count} 个物品，"
            f"锌价比 >= {args.threshold:g} 的结果有 {len(results)} 个"
        )

        if not results:
            print("没有物品达到阈值。")
            continue

        print("高锌价比列表:")
        for i, row in enumerate(results, start=1):
            print(format_line(i, row))
            all_rows.append((query, row))

    if total_matched == 0:
        return

    if args.export_csv:
        try:
            export_rows = all_rows if is_batch else [r for _, r in all_rows]
            csv_path = write_csv(args.export_csv, export_rows)
            print(f"\nCSV 已导出: {csv_path}")
        except OSError as exc:
            print(f"\nCSV 导出失败: {exc}")


if __name__ == "__main__":
    main()
