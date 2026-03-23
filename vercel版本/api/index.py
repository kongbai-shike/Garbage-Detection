import tempfile
from pathlib import Path
import sys
import os

from flask import Flask, jsonify, request, send_from_directory

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from analyzer import find_high_ratio_items  # noqa: E402
from item_catalog import load_catalog, refresh_catalog, resolve_catalog_item  # noqa: E402
from ocr_pipeline import find_junk_from_image  # noqa: E402
from query_input import parse_queries_text  # noqa: E402
from wfm_client import WFMClient  # noqa: E402

app = Flask(__name__, static_folder=str(ROOT_DIR / "public"), static_url_path="")
client = WFMClient()


def _to_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _to_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _normalize_queries(raw_query, raw_queries):
    merged = []
    if isinstance(raw_query, str) and raw_query.strip():
        merged.extend(parse_queries_text(raw_query))

    if isinstance(raw_queries, list):
        for part in raw_queries:
            if isinstance(part, str):
                merged.extend(parse_queries_text(part))

    seen = set()
    unique = []
    for q in merged:
        key = q.lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(q)
    return unique


@app.get("/")
def index_page():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/health")
def health():
    catalog_items, catalog_updated_at = load_catalog()
    return jsonify(
        {
            "ok": True,
            "catalog_size": len(catalog_items),
            "catalog_updated_at": catalog_updated_at,
        }
    )


@app.post("/api/search")
def search():
    payload = request.get_json(silent=True) or {}
    queries = _normalize_queries(payload.get("query", ""), payload.get("queries", []))
    if not queries:
        return jsonify({"ok": False, "error": "query is required"}), 400

    mode = str(payload.get("mode", "contains")).strip().lower()
    if mode not in {"exact", "contains"}:
        mode = "contains"

    threshold = _to_float(payload.get("threshold", 10.0), 10.0)
    top = max(1, _to_int(payload.get("top", 20), 20))
    sample_size = max(1, _to_int(payload.get("sample_size", 5), 5))
    debug = bool(payload.get("debug", False))

    items = []
    stats = []
    catalog_items, _ = load_catalog()

    for query in queries:
        resolved_query = query
        resolved_mode = mode

        matched = resolve_catalog_item(query, catalog_items)
        if matched is not None:
            resolved_query = matched.url_name
            resolved_mode = "exact"

        rows, info = find_high_ratio_items(
            query=resolved_query,
            mode=resolved_mode,
            threshold=threshold,
            top=top,
            sample_size=sample_size,
            debug=debug,
            client=client,
        )

        stats.append(
            {
                "query": query,
                "resolved_query": resolved_query,
                "mode": resolved_mode,
                "matched_count": info.get("matched_count", 0),
                "high_ratio_count": len(rows),
            }
        )

        for row in rows:
            items.append(
                {
                    "source_query": query,
                    "item_name": row.item_name,
                    "url_name": row.url_name,
                    "ducats": row.ducats,
                    "average_price": row.average_price,
                    "ratio": row.ratio,
                }
            )

    items.sort(key=lambda x: x["ratio"], reverse=True)

    return jsonify(
        {
            "ok": True,
            "count": len(items),
            "items": items,
            "stats": stats,
            "params": {
                "mode": mode,
                "threshold": threshold,
                "top": top,
                "sample_size": sample_size,
            },
        }
    )


@app.post("/api/ocr")
def ocr_search():
    if "image" not in request.files:
        return jsonify({"ok": False, "error": "image file is required"}), 400

    image = request.files["image"]
    if image.filename is None or image.filename.strip() == "":
        return jsonify({"ok": False, "error": "invalid image file"}), 400

    threshold = _to_float(request.form.get("threshold", 10.0), 10.0)
    top = max(1, _to_int(request.form.get("top", 20), 20))
    sample_size = max(1, _to_int(request.form.get("sample_size", 5), 5))
    ocr_engine = str(request.form.get("ocr_engine", "auto")).strip().lower()
    if ocr_engine not in {"auto", "rapid", "paddle"}:
        ocr_engine = "auto"

    suffix = Path(image.filename).suffix or ".png"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp:
        temp_path = Path(temp.name)
        image.save(temp)

    try:
        rows, info = find_junk_from_image(
            image_path=str(temp_path),
            threshold=threshold,
            top=top,
            sample_size=sample_size,
            ocr_engine=ocr_engine,
            debug=False,
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        temp_path.unlink(missing_ok=True)

    results = [
        {
            "recognized_name": row.recognized_name,
            "matched_name": row.matched_name,
            "url_name": row.url_name,
            "count": row.count,
            "ducats": row.ducats,
            "average_price": row.average_price,
            "ratio": row.ratio,
        }
        for row in rows
    ]

    return jsonify({"ok": True, "count": len(results), "items": results, "info": info})


@app.post("/api/catalog/refresh")
def catalog_refresh():
    token = os.getenv("CATALOG_REFRESH_TOKEN", "").strip()
    if not token:
        return jsonify({"ok": False, "error": "refresh disabled"}), 403
    if request.headers.get("x-refresh-token", "").strip() != token:
        return jsonify({"ok": False, "error": "invalid token"}), 403

    try:
        count, path = refresh_catalog()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500

    return jsonify({"ok": True, "count": count, "path": str(path)})


@app.get("/<path:path>")
def static_proxy(path):
    public_dir = ROOT_DIR / "public"
    target = public_dir / path
    if target.exists() and target.is_file():
        return send_from_directory(app.static_folder, path)
    return send_from_directory(app.static_folder, "index.html")

