# -*- coding: utf-8 -*-
"""PDF 框选区域提表（T9）。

输入一页 PDF 上的一块矩形区域，返回按行列组织的单元格文字，并标注每格的
来源：PDF_TABLE（PDF 矢量线框表格）/ PDF_TEXT（PDF 文字层）/ OCR（tesseract 兜底）。
不写入任务数据，纯只读预览；前端复制或下载 CSV 后再由用户决定是否采用。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from statistics import median
from typing import Any, Iterable, Sequence

_MEANINGFUL = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]")
_OCR_MIN_CONF = 55.0


class RegionError(ValueError):
    """区域参数或 PDF 内容不合法。"""


def _normalize_rect(rect: Any) -> tuple[float, float, float, float]:
    if isinstance(rect, dict):
        values = [rect.get(k) for k in ("x0", "y0", "x1", "y1")]
    elif isinstance(rect, (list, tuple)) and len(rect) == 4:
        values = list(rect)
    else:
        raise RegionError("rect 必须是 {x0,y0,x1,y1} 或四项数组")
    try:
        x0, y0, x1, y1 = (float(v) for v in values)
    except (TypeError, ValueError):
        raise RegionError("rect 四个值必须是 0~1 的数字")
    if not all(0.0 <= v <= 1.0 for v in (x0, y0, x1, y1)):
        raise RegionError("rect 坐标必须归一化到 0~1")
    if x1 - x0 < 0.002 or y1 - y0 < 0.002:
        raise RegionError("框选区域太小，请重新框选")
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def _cell(text: str, source: str) -> dict[str, str]:
    return {"text": (text or "").strip(), "source": source}


def _table_rows(page: Any, clip: Any) -> list[list[dict[str, str]]]:
    """优先用 PDF 线框表格；没有线框时返回空，交给文字聚类。"""
    if not hasattr(page, "find_tables"):
        return []
    try:
        finder = page.find_tables(clip=clip)
    except Exception:
        return []
    best: list[list[dict[str, str]]] = []
    for table in getattr(finder, "tables", []) or []:
        try:
            matrix = table.extract()
        except Exception:
            continue
        rows = [[_cell(str(c or ""), "PDF_TABLE") for c in row] for row in matrix]
        filled = sum(1 for row in rows for c in row if c["text"])
        if filled >= 4 and filled > sum(1 for row in best for c in row if c["text"]):
            best = rows
    return best


def _cluster_words(words: Sequence[Sequence[Any]], source: str) -> list[list[dict[str, str]]]:
    """把 PDF 文字词按行聚类，再按横向间距切成单元格（无表格线时的兜底）。"""
    items: list[dict[str, Any]] = []
    for w in words:
        try:
            x0, y0, x1, y1 = (float(w[0]), float(w[1]), float(w[2]), float(w[3]))
            text = str(w[4]).strip()
        except (IndexError, TypeError, ValueError):
            continue
        if not text or not _MEANINGFUL.search(text):
            continue
        items.append({"x0": x0, "x1": x1, "cy": (y0 + y1) / 2, "h": max(y1 - y0, 1.0), "text": text})
    if not items:
        return []
    tolerance = max(2.0, median(i["h"] for i in items) * 0.65)
    items.sort(key=lambda i: (i["cy"], i["x0"]))

    grouped: list[list[dict[str, Any]]] = []
    for item in items:
        if grouped and abs(item["cy"] - grouped[-1][-1]["cy"]) <= tolerance:
            grouped[-1].append(item)
        else:
            grouped.append([item])

    rows: list[list[dict[str, str]]] = []
    for line in grouped:
        line.sort(key=lambda i: i["x0"])
        gap_limit = max(6.0, median(i["h"] for i in line) * 1.8)
        cells: list[str] = []
        current = line[0]["text"]
        prev_x1 = line[0]["x1"]
        for item in line[1:]:
            if item["x0"] - prev_x1 > gap_limit:
                cells.append(current)
                current = item["text"]
            else:
                current += (" " if current and not current.endswith((" ", "　")) else "") + item["text"]
            prev_x1 = max(prev_x1, item["x1"])
        cells.append(current)
        if any(c.strip() for c in cells):
            rows.append([_cell(c, source) for c in cells])
    return rows


def _ocr_words(page: Any, clip: Any) -> list[list[Any]]:
    """框选区渲染后走 tesseract；没有 tesseract 或识别失败返回空。"""
    exe = shutil.which("tesseract")
    if not exe:
        return []
    dpi = 300
    scale = 72.0 / dpi
    try:
        pix = page.get_pixmap(dpi=dpi, clip=clip)
    except Exception:
        return []
    with tempfile.TemporaryDirectory() as tmp:
        img = os.path.join(tmp, "region.png")
        try:
            pix.save(img)
        except Exception:
            return []
        try:
            tsv = subprocess.run(
                [exe, img, "-", "-l", "chi_sim+eng", "--psm", "6", "tsv"],
                capture_output=True, text=True, timeout=180,
            ).stdout
        except (subprocess.TimeoutExpired, OSError):
            return []

    words: list[list[Any]] = []
    for line in tsv.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 12 or not parts[11].strip():
            continue
        try:
            conf = float(parts[10])
            left, top, width, height = (int(v) for v in parts[6:10])
        except ValueError:
            continue
        if conf < _OCR_MIN_CONF or not _MEANINGFUL.search(parts[11]):
            continue
        x0 = clip.x0 + left * scale
        y0 = clip.y0 + top * scale
        x1 = clip.x0 + (left + width) * scale
        y1 = clip.y0 + (top + height) * scale
        words.append([x0, y0, x1, y1, parts[11]])
    return words


def extract_region(pdf_path: str, page_num: int = 1,
                   rect: Any = (0.0, 0.0, 1.0, 1.0), use_ocr: bool = True) -> dict[str, Any]:
    """提取 PDF 指定页、指定归一化矩形内的表格文字。"""
    if not os.path.isfile(pdf_path):
        raise RegionError("图纸文件不存在")
    try:
        import pymupdf
    except ImportError:
        raise RegionError("服务器未安装 PyMuPDF，无法提取 PDF 区域")

    x0, y0, x1, y1 = _normalize_rect(rect)
    with pymupdf.open(pdf_path) as doc:
        page_total = len(doc)
        if not 1 <= int(page_num) <= page_total:
            raise RegionError(f"页码超出范围：1~{page_total}")
        page = doc[int(page_num) - 1]
        clip = pymupdf.Rect(
            x0 * page.rect.width, y0 * page.rect.height,
            x1 * page.rect.width, y1 * page.rect.height,
        )
        warnings: list[str] = []
        rows = _table_rows(page, clip)
        source = "PDF_TABLE" if rows else ""
        if not rows:
            words = page.get_text("words", clip=clip)
            if words:
                rows = _cluster_words(words, "PDF_TEXT")
                source = "PDF_TEXT"
            elif use_ocr:
                ocr_words = _ocr_words(page, clip)
                rows = _cluster_words(ocr_words, "OCR")
                source = "OCR" if rows else ""
            if not rows:
                warnings.append("该区域未识别到文字；若为扫描件，请确认本机已安装 tesseract 中文语言包。")

    return {
        "ok": True,
        "page": int(page_num),
        "page_count": page_total,
        "rect": {"x0": x0, "y0": y0, "x1": x1, "y1": y1},
        "source": source,
        "rows": rows,
        "cell_count": sum(1 for row in rows for c in row if c.get("text")),
        "warnings": warnings,
    }
