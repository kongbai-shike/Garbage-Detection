import csv
import os
import threading
import tempfile
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from analyzer import find_high_ratio_items
from item_catalog import refresh_catalog, start_catalog_auto_refresh
from ocr_pipeline import JunkResult, find_junk_from_image
from query_input import parse_queries_text, read_queries_from_file


def write_csv(output_path: str, rows) -> Path:
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.writer(fp)
        is_batch = bool(rows) and isinstance(rows[0], tuple)
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


class RatioFinderGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Warframe 遗物锌价比查询")
        self.root.geometry("1050x720")

        self.results = []
        self.result_kind = "search"
        self.searching = False

        self.query_var = tk.StringVar()
        self.mode_var = tk.StringVar(value="exact")
        self.threshold_var = tk.StringVar(value="10")
        self.top_var = tk.StringVar(value="20")
        self.sample_size_var = tk.StringVar(value="5")
        self.debug_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="就绪")
        self.batch_queries = []

        self._build_layout()
        self.root.bind_all("<Control-v>", self.on_shortcut_paste_image)

    def _build_layout(self) -> None:
        container = ttk.Frame(self.root, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        controls = ttk.LabelFrame(container, text="查询参数", padding=10)
        controls.pack(fill=tk.X)

        ttk.Label(controls, text="物品名 / 关键词:").grid(row=0, column=0, sticky=tk.W, padx=(0, 8), pady=4)
        query_entry = ttk.Entry(controls, textvariable=self.query_var, width=42)
        query_entry.grid(row=0, column=1, sticky=tk.W, pady=4)
        query_entry.bind("<Return>", lambda _: self.on_search())

        ttk.Label(controls, text="模式:").grid(row=0, column=2, sticky=tk.W, padx=(18, 8), pady=4)
        mode_box = ttk.Combobox(
            controls,
            textvariable=self.mode_var,
            values=["exact", "contains"],
            state="readonly",
            width=10,
        )
        mode_box.grid(row=0, column=3, sticky=tk.W, pady=4)

        ttk.Checkbutton(controls, text="显示调试日志", variable=self.debug_var).grid(
            row=0, column=4, sticky=tk.W, padx=(18, 8), pady=4
        )

        self.search_btn = ttk.Button(controls, text="开始查询", command=self.on_search)
        self.search_btn.grid(row=0, column=5, sticky=tk.E, padx=(18, 0), pady=4)

        self.ocr_file_btn = ttk.Button(controls, text="选择图片 OCR", command=self.on_pick_image_ocr)
        self.ocr_file_btn.grid(row=0, column=6, sticky=tk.W, padx=(8, 0), pady=4)

        self.ocr_clipboard_btn = ttk.Button(controls, text="粘贴板图片 OCR", command=self.on_clipboard_image_ocr)
        self.ocr_clipboard_btn.grid(row=0, column=7, sticky=tk.W, padx=(8, 0), pady=4)

        ttk.Label(controls, text="阈值(锌价比):").grid(row=1, column=0, sticky=tk.W, padx=(0, 8), pady=4)
        ttk.Entry(controls, textvariable=self.threshold_var, width=12).grid(row=1, column=1, sticky=tk.W, pady=4)

        ttk.Label(controls, text="Top N:").grid(row=1, column=2, sticky=tk.W, padx=(18, 8), pady=4)
        ttk.Entry(controls, textvariable=self.top_var, width=12).grid(row=1, column=3, sticky=tk.W, pady=4)

        ttk.Label(controls, text="取样卖单数:").grid(row=1, column=4, sticky=tk.W, padx=(18, 8), pady=4)
        ttk.Entry(controls, textvariable=self.sample_size_var, width=12).grid(row=1, column=5, sticky=tk.W, pady=4)

        self.export_btn = ttk.Button(controls, text="导出 CSV", command=self.on_export, state=tk.DISABLED)
        self.export_btn.grid(row=1, column=6, sticky=tk.E, padx=(18, 0), pady=4)

        self.refresh_catalog_btn = ttk.Button(controls, text="更新物品库", command=self.on_refresh_catalog)
        self.refresh_catalog_btn.grid(row=1, column=7, sticky=tk.W, padx=(8, 0), pady=4)

        batch_wrap = ttk.LabelFrame(container, text="批量查询（每行一个，可粘贴）")
        batch_wrap.pack(fill=tk.X, pady=(10, 0))

        batch_bar = ttk.Frame(batch_wrap)
        batch_bar.pack(fill=tk.X, padx=8, pady=(6, 4))
        ttk.Button(batch_bar, text="导入 txt/csv", command=self.on_import_batch).pack(side=tk.LEFT)
        ttk.Button(batch_bar, text="清空批量输入", command=self.clear_batch_input).pack(side=tk.LEFT, padx=(8, 0))

        self.batch_text = tk.Text(batch_wrap, height=4, wrap=tk.WORD)
        self.batch_text.pack(fill=tk.X, padx=8, pady=(0, 8))

        table_wrap = ttk.LabelFrame(container, text="结果列表")
        table_wrap.pack(fill=tk.BOTH, expand=True, pady=(12, 8))

        table_frame = ttk.Frame(table_wrap)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        columns = ("source_query", "item_name", "count", "url_name", "ducats", "avg_plat", "ratio")
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings")
        self.table.heading("source_query", text="查询词")
        self.table.heading("item_name", text="物品")
        self.table.heading("count", text="数量")
        self.table.heading("url_name", text="Slug")
        self.table.heading("ducats", text="金币")
        self.table.heading("avg_plat", text="均价(白金)")
        self.table.heading("ratio", text="锌价比")

        self.table.column("source_query", width=180, anchor=tk.W)
        self.table.column("item_name", width=260, anchor=tk.W)
        self.table.column("count", width=80, anchor=tk.E)
        self.table.column("url_name", width=220, anchor=tk.W)
        self.table.column("ducats", width=90, anchor=tk.E)
        self.table.column("avg_plat", width=120, anchor=tk.E)
        self.table.column("ratio", width=110, anchor=tk.E)

        y_scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.table.yview)
        x_scroll = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.table.xview)
        self.table.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        self.table.grid(row=0, column=0, sticky=tk.NSEW)
        y_scroll.grid(row=0, column=1, sticky=tk.NS)
        x_scroll.grid(row=1, column=0, sticky=tk.EW)

        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        debug_wrap = ttk.LabelFrame(container, text="调试日志")
        debug_wrap.pack(fill=tk.BOTH, expand=False, pady=(0, 8))

        debug_bar = ttk.Frame(debug_wrap)
        debug_bar.pack(fill=tk.X, padx=8, pady=(6, 4))
        ttk.Button(debug_bar, text="清空日志", command=self.clear_debug).pack(side=tk.RIGHT)

        self.debug_text = tk.Text(debug_wrap, height=10, wrap=tk.WORD)
        self.debug_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        status = ttk.Label(container, textvariable=self.status_var, anchor=tk.W)
        status.pack(fill=tk.X)

    def _append_debug(self, message: str) -> None:
        def _write() -> None:
            ts = datetime.now().strftime("%H:%M:%S")
            self.debug_text.insert(tk.END, f"[{ts}] {message}\n")
            self.debug_text.see(tk.END)

        self.root.after(0, _write)

    def clear_debug(self) -> None:
        self.debug_text.delete("1.0", tk.END)

    def clear_batch_input(self) -> None:
        self.batch_text.delete("1.0", tk.END)
        self.batch_queries = []

    def on_import_batch(self) -> None:
        file_path = filedialog.askopenfilename(
            title="导入批量查询文件",
            filetypes=[("文本或 CSV", "*.txt;*.csv"), ("所有文件", "*.*")],
        )
        if not file_path:
            return

        try:
            queries = read_queries_from_file(file_path)
        except OSError as exc:
            messagebox.showerror("导入失败", str(exc))
            return

        if not queries:
            messagebox.showwarning("导入结果", "文件里没有有效查询词。")
            return

        self.batch_queries = queries
        self.batch_text.delete("1.0", tk.END)
        self.batch_text.insert(tk.END, "\n".join(queries))
        self.status_var.set(f"已导入批量查询词 {len(queries)} 条")

    def _collect_queries(self):
        merged = []

        single = self.query_var.get().strip()
        if single:
            merged.extend(parse_queries_text(single))

        batch_text = self.batch_text.get("1.0", tk.END).strip()
        if batch_text:
            merged.extend(parse_queries_text(batch_text))

        seen = set()
        uniq = []
        for query in merged:
            key = query.lower()
            if key in seen:
                continue
            seen.add(key)
            uniq.append(query)
        return uniq

    def on_search(self) -> None:
        if self.searching:
            return

        queries = self._collect_queries()
        if not queries:
            messagebox.showwarning("缺少查询词", "请在单条输入或批量输入中填写查询词。")
            return

        try:
            threshold, top, sample_size = self._parse_search_params()
        except ValueError as exc:
            messagebox.showerror("输入错误", str(exc))
            return

        self.searching = True
        self._set_busy(True)
        self.export_btn.configure(state=tk.DISABLED)
        self.status_var.set(f"正在查询 warframe.market... 共 {len(queries)} 条")

        if self.debug_var.get():
            self._append_debug("开始新查询")

        worker = threading.Thread(
            target=self._search_worker,
            args=(queries, self.mode_var.get(), threshold, top, sample_size, self.debug_var.get()),
            daemon=True,
        )
        worker.start()

    def _search_worker(
        self,
        queries,
        mode: str,
        threshold: float,
        top: int,
        sample_size: int,
        debug: bool,
    ) -> None:
        all_rows = []
        query_stats = []

        for index, query in enumerate(queries, start=1):
            if debug:
                self._append_debug(f"--- 批量进度 {index}/{len(queries)}：{query} ---")
            try:
                results, info = find_high_ratio_items(
                    query=query,
                    mode=mode,
                    threshold=threshold,
                    top=top,
                    sample_size=sample_size,
                    debug=debug,
                    debug_log=self._append_debug,
                )
            except RuntimeError as exc:
                if debug:
                    self._append_debug(f"查询失败（已跳过）: {exc}")
                query_stats.append((query, 0, 0))
                continue

            query_stats.append((query, info.get("matched_count", 0), len(results)))
            for row in results:
                all_rows.append((query, row))

        self.root.after(0, lambda: self._finish_with_results(all_rows, query_stats, threshold))

    def _finish_with_error(self, error_text: str) -> None:
        self.searching = False
        self._set_busy(False)
        self.status_var.set("查询失败")
        if self.debug_var.get():
            self._append_debug(f"请求失败: {error_text}")
        messagebox.showerror("请求失败", error_text)

    def _finish_with_results(self, results, query_stats, threshold: float) -> None:
        self.searching = False
        self._set_busy(False)
        self.result_kind = "search"
        self.results = results

        for row_id in self.table.get_children():
            self.table.delete(row_id)

        for source_query, row in results:
            self.table.insert(
                "",
                tk.END,
                values=(
                    source_query,
                    row.item_name,
                    "-",
                    row.url_name,
                    row.ducats,
                    f"{row.average_price:.2f}",
                    f"{row.ratio:.2f}",
                ),
            )

        total_queries = len(query_stats)
        matched_queries = sum(1 for _, matched, _ in query_stats if matched > 0)
        high_count = len(results)
        self.status_var.set(f"批量查询完成：{total_queries} 条，命中 {matched_queries} 条，高锌价比结果 {high_count} 条")

        if self.debug_var.get():
            self._append_debug(self.status_var.get())
            for query, matched, high in query_stats:
                self._append_debug(f"查询词[{query}] -> 候选 {matched}，高锌价比 {high}（阈值>={threshold:g}）")

        if results:
            self.export_btn.configure(state=tk.NORMAL)
        else:
            self.export_btn.configure(state=tk.DISABLED)

    def _set_busy(self, busy: bool) -> None:
        state = tk.DISABLED if busy else tk.NORMAL
        self.search_btn.configure(state=state)
        self.ocr_file_btn.configure(state=state)
        self.ocr_clipboard_btn.configure(state=state)
        self.refresh_catalog_btn.configure(state=state)

    def on_refresh_catalog(self) -> None:
        if self.searching:
            return

        self.searching = True
        self._set_busy(True)
        self.status_var.set("正在更新物品库...")

        worker = threading.Thread(target=self._refresh_catalog_worker, daemon=True)
        worker.start()

    def _refresh_catalog_worker(self) -> None:
        try:
            count, path = refresh_catalog(debug_log=self._append_debug if self.debug_var.get() else None)
        except RuntimeError as exc:
            self.root.after(0, lambda: self._finish_with_error(str(exc)))
            return
        except OSError as exc:
            self.root.after(0, lambda: self._finish_with_error(str(exc)))
            return

        self.root.after(0, lambda: self._finish_catalog_refresh(count, path))

    def _finish_catalog_refresh(self, count: int, path: Path) -> None:
        self.searching = False
        self._set_busy(False)
        self.status_var.set(f"物品库已更新：{count} 项")
        if self.debug_var.get():
            self._append_debug(f"物品库已更新：{count} 项 -> {path}")

    def _parse_search_params(self):
        try:
            threshold = float(self.threshold_var.get().strip())
            top = int(self.top_var.get().strip())
            sample_size = int(self.sample_size_var.get().strip())
        except ValueError:
            raise ValueError("阈值、Top N、取样卖单数必须是数字。")

        if top < 0 or sample_size <= 0:
            raise ValueError("Top N 必须 >= 0，取样卖单数必须 > 0。")
        return threshold, top, sample_size

    def on_pick_image_ocr(self) -> None:
        image_path = filedialog.askopenfilename(
            title="选择要识别的图片",
            filetypes=[("图片文件", "*.png;*.jpg;*.jpeg;*.bmp;*.webp"), ("所有文件", "*.*")],
        )
        if not image_path:
            return
        self._start_ocr_search(image_path, "文件")

    def on_shortcut_paste_image(self, _event=None):
        focus = self.root.focus_get()
        if focus is not None and str(focus.winfo_class()) in {"Entry", "Text", "TEntry"}:
            return None

        clipboard_data = self._resolve_clipboard_image(show_warning=False)
        if clipboard_data is None:
            return None

        image_path, source_name, temp_file = clipboard_data
        self._start_ocr_search(image_path, source_name, temp_file)
        return "break"

    def _resolve_clipboard_image(self, show_warning: bool):
        def warn(text: str) -> None:
            if show_warning:
                messagebox.showwarning("未检测到图片", text)

        try:
            from PIL import ImageGrab
        except ImportError:
            if show_warning:
                messagebox.showerror("缺少依赖", "未安装 Pillow，请先执行: pip install pillow")
            return None

        clip = ImageGrab.grabclipboard()
        if clip is None:
            warn("剪贴板中没有图片。")
            return None

        if isinstance(clip, list):
            for item in clip:
                try:
                    path = Path(item)
                except TypeError:
                    continue
                if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}:
                    return str(path), "剪贴板文件", ""

            warn("剪贴板中没有可用图片。")
            return None

        if not hasattr(clip, "save"):
            warn("剪贴板内容不是图片。")
            return None

        temp_path = ""
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                temp_path = tmp.name
            clip.save(temp_path)
        except OSError as exc:
            if show_warning:
                messagebox.showerror("图片处理失败", str(exc))
            return None

        return temp_path, "剪贴板", temp_path

    def on_clipboard_image_ocr(self) -> None:
        clipboard_data = self._resolve_clipboard_image(show_warning=True)
        if clipboard_data is None:
            return

        image_path, source_name, temp_file = clipboard_data
        self._start_ocr_search(image_path, source_name, temp_file)

    def _start_ocr_search(self, image_path: str, source_name: str, temp_file_to_remove: str = "") -> None:
        if self.searching:
            return

        try:
            threshold, top, sample_size = self._parse_search_params()
        except ValueError as exc:
            messagebox.showerror("输入错误", str(exc))
            if temp_file_to_remove:
                try:
                    os.remove(temp_file_to_remove)
                except OSError:
                    pass
            return

        self.searching = True
        self._set_busy(True)
        self.export_btn.configure(state=tk.DISABLED)
        self.status_var.set(f"正在执行 OCR（{source_name}）...")

        if self.debug_var.get():
            self._append_debug(f"开始 OCR 查询: {image_path}")

        worker = threading.Thread(
            target=self._ocr_worker,
            args=(image_path, threshold, top, sample_size, self.debug_var.get(), temp_file_to_remove),
            daemon=True,
        )
        worker.start()

    def _ocr_worker(
        self,
        image_path: str,
        threshold: float,
        top: int,
        sample_size: int,
        debug: bool,
        temp_file_to_remove: str,
    ) -> None:
        try:
            results, info = find_junk_from_image(
                image_path=image_path,
                threshold=threshold,
                top=top,
                sample_size=sample_size,
                ocr_engine="auto",
                debug=debug,
                debug_log=self._append_debug,
            )
        except (RuntimeError, OSError) as exc:
            self.root.after(0, lambda: self._finish_with_error(str(exc)))
            return
        finally:
            if temp_file_to_remove:
                try:
                    os.remove(temp_file_to_remove)
                except OSError:
                    pass

        self.root.after(0, lambda: self._finish_with_ocr_results(results, info))

    def _finish_with_ocr_results(self, results: list[JunkResult], info: dict) -> None:
        self.searching = False
        self._set_busy(False)
        self.result_kind = "ocr"
        self.results = results

        for row_id in self.table.get_children():
            self.table.delete(row_id)

        for row in results:
            self.table.insert(
                "",
                tk.END,
                values=(
                    row.recognized_name,
                    row.matched_name,
                    row.count,
                    row.url_name,
                    row.ducats,
                    f"{row.average_price:.2f}",
                    f"{row.ratio:.2f}",
                ),
            )

        self.status_var.set(
            "OCR 完成："
            f"引擎 {info.get('ocr_engine', 'unknown')}，"
            f"OCR 行数 {info.get('ocr_line_count', 0)}，"
            f"识别 {info.get('recognized_count', 0)}，"
            f"有匹配 {info.get('matched_query_count', 0)}，"
            f"跳过 {info.get('skipped_unknown_count', 0)}，"
            f"结果 {len(results)}"
        )
        if self.debug_var.get():
            if info.get("catalog_size", 0):
                self._append_debug(
                    f"物品库: {info.get('catalog_size', 0)} 项，更新时间 {info.get('catalog_updated_at', 'unknown')}"
                )
            self._append_debug(self.status_var.get())

        self.export_btn.configure(state=tk.NORMAL if results else tk.DISABLED)

    def _write_ocr_csv(self, output_path: str, rows: list[JunkResult]) -> Path:
        path = Path(output_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)

        with path.open("w", encoding="utf-8-sig", newline="") as fp:
            writer = csv.writer(fp)
            writer.writerow(["recognized_name", "matched_name", "count", "ducats", "avg_plat", "ratio", "url_name"])
            for row in rows:
                writer.writerow(
                    [
                        row.recognized_name,
                        row.matched_name,
                        row.count,
                        row.ducats,
                        f"{row.average_price:.2f}",
                        f"{row.ratio:.2f}",
                        row.url_name,
                    ]
                )

        return path

    def on_export(self) -> None:
        if not self.results:
            messagebox.showinfo("无可导出数据", "当前没有可导出的结果。")
            return

        output_path = filedialog.asksaveasfilename(
            title="导出高锌价比列表",
            defaultextension=".csv",
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
        )
        if not output_path:
            return

        try:
            if self.result_kind == "ocr":
                saved_path = self._write_ocr_csv(output_path, self.results)
            else:
                saved_path = write_csv(output_path, self.results)
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc))
            return

        self.status_var.set(f"CSV 已导出: {saved_path}")
        if self.debug_var.get():
            self._append_debug(f"CSV 已导出: {saved_path}")
        messagebox.showinfo("导出成功", f"已保存到:\n{saved_path}")


def run_gui() -> None:
    start_catalog_auto_refresh()
    root = tk.Tk()
    RatioFinderGUI(root)
    root.mainloop()


if __name__ == "__main__":
    run_gui()

