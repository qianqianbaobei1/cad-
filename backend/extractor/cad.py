# -*- coding: utf-8 -*-
"""CAD (DWG / DXF) 原生解析与渲染转换流水线。

本模块实现：
1. DWG -> DXF / SVG 本地无头转码；
2. DXF -> 结构化矢量实体与文本提取 (TEXT / MTEXT / INSERT 属性)；
3. DXF / SVG -> 标准 PDF 高保真光栅化，无缝对接下游 VisionExtractor 及 Web 视口。
"""

import concurrent.futures
import os
import re
import shutil
import subprocess
from typing import Any

import ezdxf
from ezdxf.addons.drawing import Frontend, RenderContext, layout
from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration, HatchPolicy
from ezdxf.addons.drawing.svg import SVGBackend
from ezdxf.path import Path
from ezdxf.fonts import ttfonts
import pymupdf

# 防护补丁：针对 CAD 图纸中引用的生僻或特殊损坏字形，防止 fontTools 抛出 'Glyph' object has no attribute 'flags' 导致渲染中断
_orig_get_glyph_path = getattr(ttfonts.TTFontRenderer, "get_glyph_path", None)
if _orig_get_glyph_path:
    def _safe_get_glyph_path(self, char: str):
        try:
            return _orig_get_glyph_path(self, char)
        except Exception:
            fallback_path = ttfonts.GlyphPath(Path())
            self._glyph_path_cache[char] = fallback_path
            return fallback_path.clone()

    ttfonts.TTFontRenderer.get_glyph_path = _safe_get_glyph_path

MAX_TEXT_ENTITIES_LIMIT = 25000

SYSTEM_KEYWORDS_INCLUDE = [
    "系统图", "接线图", "原理图", "干线图", "拓扑图", "一次图", "二次图", "结线图"
]
SYSTEM_KEYWORDS_EXCLUDE = [
    "平面图", "布置图", "接地平面", "防雷平面", "电缆敷设", "管线综合", "抗震说明", "设计说明", "图纸目录", "防爆区域划分"
]


def is_cad_path(path: str) -> bool:
    """检查文件是否为 DWG 或 DXF 格式。"""
    ext = os.path.splitext(path)[1].lower()
    return ext in (".dwg", ".dxf")


def find_dwg2dxf_tool() -> str | None:
    """查找系统中可用的 dwg2dxf 工具路径。"""
    common_paths = [
        "/opt/homebrew/bin/dwg2dxf",
        "/usr/local/bin/dwg2dxf",
        "/usr/bin/dwg2dxf",
    ]
    for p in common_paths:
        if os.path.exists(p) and os.access(p, os.X_OK):
            return p
    return shutil.which("dwg2dxf")


def find_dwg2svg_tool() -> str | None:
    """查找系统中可用的 dwg2SVG 工具路径。"""
    common_paths = [
        "/opt/homebrew/bin/dwg2SVG",
        "/usr/local/bin/dwg2SVG",
        "/usr/bin/dwg2SVG",
    ]
    for p in common_paths:
        if os.path.exists(p) and os.access(p, os.X_OK):
            return p
    return shutil.which("dwg2SVG")


def sanitize_surrogates(text: str) -> str:
    """清理字符串中的非法 Unicode 代理对（surrogates），防止 UTF-8 编码与 JSON 序列化崩溃。"""
    if not text:
        return ""
    if not isinstance(text, str):
        text = str(text)
    return text.encode("utf-8", "ignore").decode("utf-8")


def clean_mtext(raw_text: str) -> str:
    """清理 AutoCAD MTEXT 常见的格式控制代码（如 \\P, \\A1;, \\fSimSun; 等）。"""
    if not raw_text:
        return ""
    t = sanitize_surrogates(raw_text)
    # 替换特殊电气工程字符
    t = t.replace("%%c", "Φ").replace("%%C", "Φ").replace("%%d", "°").replace("%%p", "±")
    # 替换换行控制
    t = t.replace(r"\P", "\n").replace(r"\p", "\n")
    # 移除字体、堆叠、颜色等控制码 \F...; \C...; \H...; \W...; \A...;
    t = re.sub(r"\\[A-Za-z0-9]+\;?", "", t)
    # 移除花括号堆叠分组 {}
    t = re.sub(r"[{}]", "", t)
    # 合并多余空白
    t = re.sub(r"[ \t]+", " ", t).strip()
    return t


def dwg_to_dxf(dwg_path: str, dxf_path: str) -> bool:
    """调用 LibreDWG 的 dwg2dxf 工具将 DWG 转换为 DXF。"""
    tool = find_dwg2dxf_tool()
    if not tool:
        raise RuntimeError("未检测到 dwg2dxf 转换工具，请确保已安装 libredwg")
    cmd = [tool, "-y", "-o", dxf_path, dwg_path]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if res.returncode == 0 and os.path.exists(dxf_path) and os.path.getsize(dxf_path) > 0:
        return True
    return False


def dwg_to_svg_fallback(dwg_path: str, svg_path: str) -> bool:
    """使用 dwg2SVG 直接将 DWG 输出为 SVG（备用路径）。"""
    tool = find_dwg2svg_tool()
    if not tool:
        return False
    cmd = [tool, dwg_path]
    with open(svg_path, "wb") as f_out:
        res = subprocess.run(cmd, stdout=f_out, stderr=subprocess.PIPE, check=False)
    return res.returncode == 0 and os.path.exists(svg_path) and os.path.getsize(svg_path) > 0


def load_dxf_document(dxf_path: str):
    """安全读取 DXF 文件，在格式异常时自动尝试 recover 模式。"""
    try:
        return ezdxf.readfile(dxf_path)
    except Exception:
        from ezdxf import recover
        doc, _ = recover.readfile(dxf_path)
        return doc


def is_system_title(text: str) -> bool:
    """判断文字是否属于电气系统图标题。"""
    t = text.replace(" ", "").replace("\n", "").strip()
    if not t or len(t) > 35:
        return False
    # 排除说明句式标点
    if any(p in t for p in ["。", "；", "，", ";", ","]):
        return False
    # 排除序号开头的施工/设计附注（如 "1.", "2）", "7.6", "(3)"）
    if re.match(r"^(\d+[\.\、\)\）]|\d+\.\d+|\(\d+\)|（\d+）)", t):
        return False
    # 排除包含说明句式词汇
    if any(k in t for k in ["详见", "详弱电", "本子项", "本配电", "安装在", "取自", "规范", "设计说明", "技术要求"]):
        return False
    has_inc = any(k in t for k in SYSTEM_KEYWORDS_INCLUDE)
    if not has_inc:
        return False
    has_exc = any(k in t for k in SYSTEM_KEYWORDS_EXCLUDE)
    if has_exc and "系统图" not in t:
        return False
    return True


def detect_system_sheets(doc: Any) -> list[dict[str, Any]]:
    """扫描 CAD 模型空间中的图框标题栏，自动识别所有电气系统图并定位其坐标与包围盒。"""
    msp = doc.modelspace()
    candidates = []

    # 1. 扫描单行文字 TEXT 与多行文字 MTEXT
    for e in list(msp.query("TEXT")) + list(msp.query("MTEXT")):
        try:
            t = getattr(e.dxf, "text", "") if e.dxftype() == "TEXT" else getattr(e, "text", "")
            cleaned = clean_mtext(t)
            if is_system_title(cleaned):
                if any(cleaned.endswith(sfx) or cleaned.startswith(sfx) for sfx in ["系统图", "接线图", "原理图", "干线图", "拓扑图"]) or (
                    "系统图" in cleaned and ("一" in cleaned or "二" in cleaned or "三" in cleaned or "四" in cleaned or "五" in cleaned or "六" in cleaned or "七" in cleaned or "八" in cleaned or "九" in cleaned or "十" in cleaned or bool(re.search(r"\d", cleaned)))
                ):
                    candidates.append({
                        "title": cleaned,
                        "x": float(e.dxf.insert.x),
                        "y": float(e.dxf.insert.y),
                        "layer": str(getattr(e.dxf, "layer", "")),
                        "height": float(getattr(e.dxf, "height", 10.0) if e.dxftype() == "TEXT" else getattr(e.dxf, "char_height", 10.0)),
                    })
        except Exception:
            pass

    # 2. 扫描块参照 INSERT (支持正规设计院带 ATTRIB 属性的图框块)
    for ins in msp.query("INSERT"):
        try:
            ins_x, ins_y = float(ins.dxf.insert.x), float(ins.dxf.insert.y)
            ins_layer = str(getattr(ins.dxf, "layer", ""))
            for attrib in getattr(ins, "attribs", []):
                val = clean_mtext(getattr(attrib.dxf, "text", ""))
                tag = str(getattr(attrib.dxf, "tag", "")).upper()
                if is_system_title(val):
                    candidates.append({
                        "title": val,
                        "x": float(attrib.dxf.insert.x) if hasattr(attrib.dxf, "insert") else ins_x,
                        "y": float(attrib.dxf.insert.y) if hasattr(attrib.dxf, "insert") else ins_y,
                        "layer": ins_layer,
                        "height": float(getattr(attrib.dxf, "height", 20.0)),
                    })
        except Exception:
            pass

    if not candidates:
        return []

    # 3. 过滤图纸目录表（同一 X 轴紧密垂直堆叠，avg dy < 4000）
    by_x: dict[int, list[dict[str, Any]]] = {}
    for c in candidates:
        bucket = round(c["x"] / 2000) * 2000
        by_x.setdefault(bucket, []).append(c)

    filtered = []
    for bucket, group in by_x.items():
        if len(group) >= 3:
            ys = sorted([g["y"] for g in group])
            diffs = [ys[i + 1] - ys[i] for i in range(len(ys) - 1)]
            avg_diff = sum(diffs) / len(diffs)
            if avg_diff < 4000:
                continue
        filtered.extend(group)

    if not filtered:
        return []

    # 4. 去重并优先选取图签栏或大字高标题
    sheet_map: dict[str, dict[str, Any]] = {}
    for c in filtered:
        name = c["title"]
        score = 0
        if any(k in c["layer"] for k in ["图签", "图框", "TITLE", "PUB_TITLE", "BORDER"]):
            score += 20
        elif any(k in c["layer"] for k in ["PUB_TEXT", "01"]):
            score += 10
        if c["height"] >= 300:
            score += 5
        if name not in sheet_map or score > sheet_map[name]["score"]:
            c["score"] = score
            sheet_map[name] = c

    # 5. 按 Y 降序、X 升序排列
    detected = sorted(sheet_map.values(), key=lambda s: (-round(s["y"], -4), s["x"]))

    # 6. 扫描图框物理闭合多段线 (LWPOLYLINE / POLYLINE) 以获得高精度真实边界
    border_boxes: list[tuple[float, float, float, float]] = []
    for pl in list(msp.query("LWPOLYLINE")) + list(msp.query("POLYLINE")):
        try:
            is_closed = getattr(pl, "is_closed", False) or getattr(pl.dxf, "flags", 0) & 1
            if not is_closed:
                continue
            pts = [p[:2] for p in (list(pl.vertices()) if hasattr(pl, "vertices") else list(pl.get_points("xy")))]
            if len(pts) >= 4:
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                min_px, max_px = min(xs), max(xs)
                min_py, max_py = min(ys), max(ys)
                pw, ph = max_px - min_px, max_py - min_py
                if pw > 1000 and ph > 600:
                    aspect = max(pw, ph) / min(pw, ph)
                    if 1.2 <= aspect <= 1.8:
                        border_boxes.append((min_px, min_py, max_px, max_py))
        except Exception:
            pass

    # 7. 计算每张图框的实际包围盒 (Bounding Box)
    sheet_w = 118900.0
    if len(detected) >= 2:
        xs = sorted(s["x"] for s in detected)
        diffs = [xs[i + 1] - xs[i] for i in range(len(xs) - 1) if xs[i + 1] - xs[i] > 40000]
        if diffs:
            sheet_w = min(diffs)
    elif len(detected) == 1:
        # 单图自适应比例尺推导 (根据大字高推算 scale)
        th = detected[0].get("height", 10.0)
        scale = max(1.0, th / 3.5) if th > 15 else 1.0
        sheet_w = 1189.0 * scale

    sheet_h = sheet_w / 1.414  # ISO 216 标准宽高比 1.414

    for s in detected:
        tx, ty = s["x"], s["y"]
        # 优先使用包围该图签锚点的真实闭合多段线
        matched_box = None
        for bx0, by0, bx1, by1 in border_boxes:
            if bx0 <= tx <= bx1 and by0 <= ty <= by1:
                matched_box = (round(bx0, 1), round(by0, 1), round(bx1, 1), round(by1, 1))
                break
        if matched_box:
            s["bbox"] = matched_box
        else:
            s["bbox"] = (
                round(tx - sheet_w * 0.88, 1),
                round(ty - sheet_h * 0.05, 1),
                round(tx + sheet_w * 0.12, 1),
                round(ty + sheet_h * 0.95, 1),
            )

    return detected


def slice_and_render_cad_sheets(doc: Any, sheets: list[dict[str, Any]], out_pdf_path: str) -> list[dict[str, Any]]:
    """针对检测出的多个电气系统图进行单图框矢量切片与拼接，输出高清多页 PDF 与对应文字元数据。

    采用空间网格索引 (Spatial Grid Indexing) 与电气实体预剪枝，消除无用图元遍历，
    并发多线程极速渲染，保障工业级超大 DWG 秒级稳定切片。
    """
    msp = doc.modelspace()
    combined_pdf = pymupdf.open()
    total_texts: list[dict[str, Any]] = []

    if not sheets:
        return []

    # 1. 电气核心实体类型白名单（过滤 SPLINE、HATCH、DIMENSION 等耗时上万条的无用非电气装饰图元）
    ALLOWED_TYPES = {
        "LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "TEXT", "MTEXT", "INSERT", "SOLID"
    }

    # 2. 空间网格索引加速 (Cell Size 35000)
    CELL_SIZE = 35000.0
    grid: dict[tuple[int, int], list[tuple[Any, float, float]]] = {}

    for e in msp:
        try:
            if e.dxftype() not in ALLOWED_TYPES:
                continue
            pos = getattr(e.dxf, "insert", None) or getattr(e.dxf, "start", None)
            if pos is None:
                continue
            px, py = float(pos.x), float(pos.y)
            gx = int(px // CELL_SIZE)
            gy = int(py // CELL_SIZE)
            grid.setdefault((gx, gy), []).append((e, px, py))
        except Exception:
            pass

    def _render_sheet_worker(idx: int, sheet: dict[str, Any]) -> tuple[int, list[dict[str, Any]], bytes]:
        sheet_name = sheet["title"]
        bx0, by0, bx1, by1 = sheet["bbox"]

        # 从空间网格中仅提取与当前图框相交单元格的实体（亚毫秒级检索）
        min_gx, max_gx = int(bx0 // CELL_SIZE), int(bx1 // CELL_SIZE)
        min_gy, max_gy = int(by0 // CELL_SIZE), int(by1 // CELL_SIZE)

        candidates = []
        for gx in range(min_gx, max_gx + 1):
            for gy in range(min_gy, max_gy + 1):
                candidates.extend(grid.get((gx, gy), []))

        sheet_doc = ezdxf.new(doc.dxfversion)
        sheet_msp = sheet_doc.modelspace()

        sheet_texts = []
        for e, px, py in candidates:
            if bx0 <= px <= bx1 and by0 <= py <= by1:
                try:
                    sheet_msp.add_foreign_entity(e, copy=True)
                    if e.dxftype() in ("TEXT", "MTEXT"):
                        raw_text = getattr(e.dxf, "text", "") if e.dxftype() == "TEXT" else getattr(e, "text", "")
                        cleaned = clean_mtext(raw_text)
                        if cleaned:
                            sheet_texts.append({
                                "type": e.dxftype(),
                                "text": cleaned,
                                "x": round(px, 1),
                                "y": round(py, 1),
                                "page": idx,
                                "sheet": sheet_name,
                            })
                except Exception:
                    pass

        try:
            # 单页渲染为高清晰度白色背景 PDF
            ctx = RenderContext(sheet_doc)
            cfg = Configuration(
                background_policy=BackgroundPolicy.WHITE,
                color_policy=ColorPolicy.COLOR,
                hatch_policy=HatchPolicy.IGNORE,
            )
            backend = SVGBackend()
            page = layout.Page.from_dxf_layout(sheet_msp)
            frontend = Frontend(ctx, backend, config=cfg)
            frontend.draw_layout(sheet_msp, finalize=True)
            svg_content = backend.get_string(page)

            clean_svg = sanitize_surrogates(svg_content).encode("utf-8", "ignore")
            page_pdf_doc = pymupdf.open(stream=clean_svg, filetype="svg")
            page_bytes = page_pdf_doc.convert_to_pdf()
        except Exception as render_err:
            print(f"[CAD] 单页切片渲染降级 (sheet={sheet_name}): {render_err}")
            # 容错降级：生成标准 A3 白色图纸占位页，保证包含提取的图框名称
            fallback_doc = pymupdf.open()
            fb_page = fallback_doc.new_page(width=1190, height=842)
            fb_page.insert_text((50, 50), f"系统图: {sheet_name} (矢量渲染降级)", fontsize=18)
            page_bytes = fallback_doc.convert_to_pdf()

        return idx, sheet_texts, page_bytes

    if len(sheets) == 1:
        idx, sheet_texts, page_bytes = _render_sheet_worker(1, sheets[0])
        total_texts.extend(sheet_texts)
        with pymupdf.open(stream=page_bytes, filetype="pdf") as single_doc:
            combined_pdf.insert_pdf(single_doc)
    else:
        max_workers = min(len(sheets), max(1, os.cpu_count() or 4))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(_render_sheet_worker, idx, sheet)
                for idx, sheet in enumerate(sheets, 1)
            ]
            rendered_items = [f.result() for f in futures]

        rendered_items.sort(key=lambda x: x[0])
        for idx, sheet_texts, page_bytes in rendered_items:
            total_texts.extend(sheet_texts)
            with pymupdf.open(stream=page_bytes, filetype="pdf") as single_doc:
                combined_pdf.insert_pdf(single_doc)

    combined_pdf.save(out_pdf_path)
    return total_texts


def extract_cad_entities(dxf_path: str, doc: Any = None) -> list[dict[str, Any]]:
    """从 DXF 文件中高精度提取所有带坐标的文字及块属性实体。"""
    extracted: list[dict[str, Any]] = []
    if doc is None:
        try:
            doc = load_dxf_document(dxf_path)
        except Exception as exc:
            print(f"读取 DXF 失败: {exc}")
            return []

    msp = doc.modelspace()

    # 提取单行文字 TEXT
    for entity in msp.query("TEXT"):
        try:
            raw_text = getattr(entity.dxf, "text", "") or ""
            text = clean_mtext(raw_text)
            if not text:
                continue
            insert = entity.dxf.insert
            height = float(getattr(entity.dxf, "height", 10.0))
            rotation = float(getattr(entity.dxf, "rotation", 0.0))
            layer = sanitize_surrogates(str(getattr(entity.dxf, "layer", "0")))
            extracted.append({
                "type": "TEXT",
                "text": text,
                "x": round(float(insert.x), 2),
                "y": round(float(insert.y), 2),
                "height": round(height, 2),
                "rotation": round(rotation, 1),
                "layer": layer,
            })
        except Exception:
            continue

    # 提取多行文字 MTEXT
    for entity in msp.query("MTEXT"):
        try:
            raw_text = getattr(entity, "text", "") or ""
            text = clean_mtext(raw_text)
            if not text:
                continue
            insert = entity.dxf.insert
            height = float(getattr(entity.dxf, "char_height", 10.0))
            rotation = float(getattr(entity.dxf, "rotation", 0.0))
            layer = sanitize_surrogates(str(getattr(entity.dxf, "layer", "0")))
            extracted.append({
                "type": "MTEXT",
                "text": text,
                "x": round(float(insert.x), 2),
                "y": round(float(insert.y), 2),
                "height": round(height, 2),
                "rotation": round(rotation, 1),
                "layer": layer,
            })
        except Exception:
            continue

    # 提取图块引用 INSERT 中的属性文字
    for entity in msp.query("INSERT"):
        try:
            block_name = sanitize_surrogates(str(getattr(entity.dxf, "name", "")))
            layer = sanitize_surrogates(str(getattr(entity.dxf, "layer", "0")))
            for attrib in getattr(entity, "attribs", []):
                val = clean_mtext(str(getattr(attrib.dxf, "text", "")))
                tag = sanitize_surrogates(str(getattr(attrib.dxf, "tag", "")))
                if val:
                    extracted.append({
                        "type": "ATTRIB",
                        "block": block_name,
                        "tag": tag,
                        "text": val,
                        "x": round(float(attrib.dxf.insert.x), 2),
                        "y": round(float(attrib.dxf.insert.y), 2),
                        "height": round(float(getattr(attrib.dxf, "height", 10.0)), 2),
                        "layer": layer,
                    })
        except Exception:
            continue

    extracted.sort(key=lambda item: (-item["y"], item["x"]))
    return extracted


def render_dxf_to_pdf(dxf_path: str, out_pdf_path: str, doc: Any = None) -> bool:
    """使用 ezdxf 矢量渲染引擎将 DXF 转为高清白色背景 PDF。"""
    try:
        if doc is None:
            doc = load_dxf_document(dxf_path)
        msp = doc.modelspace()
        ctx = RenderContext(doc)
        cfg = Configuration(
            background_policy=BackgroundPolicy.WHITE,
            color_policy=ColorPolicy.COLOR,
            hatch_policy=HatchPolicy.IGNORE,
        )
        backend = SVGBackend()
        page = layout.Page.from_dxf_layout(msp)
        frontend = Frontend(ctx, backend, config=cfg)
        frontend.draw_layout(msp, finalize=True)
        svg_content = backend.get_string(page)

        clean_svg_bytes = sanitize_surrogates(svg_content).encode("utf-8", "ignore")
        pdf_doc = pymupdf.open(stream=clean_svg_bytes, filetype="svg")
        pdf_bytes = pdf_doc.convert_to_pdf()
        with open(out_pdf_path, "wb") as f:
            f.write(pdf_bytes)
        return True
    except Exception as exc:
        print(f"render_dxf_to_pdf 失败: {exc}")
        return False


def render_svg_to_pdf(svg_path: str, out_pdf_path: str) -> bool:
    """将 SVG 文件直接渲染为 PDF。"""
    try:
        with open(svg_path, "rb") as f:
            svg_bytes = f.read()
        try:
            clean_svg = svg_bytes.decode("utf-8", "ignore").encode("utf-8")
        except Exception:
            clean_svg = svg_bytes
        pdf_doc = pymupdf.open(stream=clean_svg, filetype="svg")
        pdf_bytes = pdf_doc.convert_to_pdf()
        with open(out_pdf_path, "wb") as f:
            f.write(pdf_bytes)
        return True
    except Exception as exc:
        print(f"render_svg_to_pdf 失败: {exc}")
        return False


def process_cad_file(cad_path: str, out_pdf_path: str) -> tuple[str, list[dict[str, Any]]]:
    """主入口：将 DWG 或 DXF 转为标准 PDF 并提取全部原生文字流。

    针对平铺多张图纸的 CAD 文件，自动探测所有电气系统图图框并进行多页矢量切片；
    针对单张图纸 CAD，直接高质量渲染；
    具备持久化转换缓存，二次处理 0 耗时秒开。
    """
    import hashlib

    ext = os.path.splitext(cad_path)[1].lower()
    work_dir = os.path.dirname(cad_path)
    base_name = os.path.splitext(os.path.basename(cad_path))[0]

    dxf_to_clean = None
    if ext == ".dwg":
        cache_dir = os.path.join(work_dir, ".cad_cache")
        os.makedirs(cache_dir, exist_ok=True)
        file_size = os.path.getsize(cad_path) if os.path.exists(cad_path) else 0
        cache_key = hashlib.md5(f"{base_name}_{file_size}".encode()).hexdigest()
        cached_dxf = os.path.join(cache_dir, f"{cache_key}.dxf")

        dxf_success = False
        if os.path.exists(cached_dxf) and os.path.getsize(cached_dxf) > 0:
            dxf_path = cached_dxf
            dxf_success = True
        else:
            try:
                dxf_success = dwg_to_dxf(cad_path, cached_dxf)
                if dxf_success:
                    dxf_path = cached_dxf
            except Exception as e:
                print(f"dwg2dxf 失败: {e}")

        if not dxf_success or not os.path.exists(dxf_path) or os.path.getsize(dxf_path) == 0:
            temp_svg = os.path.join(work_dir, f"{base_name}_temp.svg")
            if dwg_to_svg_fallback(cad_path, temp_svg):
                try:
                    ok = render_svg_to_pdf(temp_svg, out_pdf_path)
                finally:
                    try:
                        os.remove(temp_svg)
                    except OSError:
                        pass
                if ok and os.path.exists(out_pdf_path) and os.path.getsize(out_pdf_path) > 0:
                    return out_pdf_path, []
            raise RuntimeError(f"无法将 DWG 文件 {os.path.basename(cad_path)} 转换为预览格式")
    elif ext == ".dxf":
        dxf_path = cad_path
    else:
        raise ValueError(f"不支持的 CAD 文件格式: {ext}")

    try:
        doc = load_dxf_document(dxf_path)

        # 1. 自动探测多图框电气系统图并切片
        system_sheets = detect_system_sheets(doc)
        if len(system_sheets) >= 1:
            extracted_texts = slice_and_render_cad_sheets(doc, system_sheets, out_pdf_path)
            if os.path.exists(out_pdf_path) and os.path.getsize(out_pdf_path) > 0:
                return out_pdf_path, extracted_texts

        # 2. 若未探测出系统图分幅图框，检查是否属于不包含系统图的海量平面施工图
        msp = doc.modelspace()
        text_count = len(msp.query("TEXT")) + len(msp.query("MTEXT"))
        if text_count > MAX_TEXT_ENTITIES_LIMIT:
            raise ValueError(
                f"图纸包含海量文字实体 ({text_count} 条)，未检测到配电箱电气系统图（均为建筑施工平面图）。"
                f"请上传包含配电箱结线与回路的电气系统图 DWG 或 PDF 切图。"
            )

        # 3. 常规单图渲染
        extracted_texts = extract_cad_entities(dxf_path, doc=doc)
        ok = render_dxf_to_pdf(dxf_path, out_pdf_path, doc=doc)
        if not ok:
            raise RuntimeError("CAD 渲染为 PDF 失败")
        return out_pdf_path, extracted_texts
    finally:
        if dxf_to_clean:
            try:
                os.remove(dxf_to_clean)
            except OSError:
                pass
