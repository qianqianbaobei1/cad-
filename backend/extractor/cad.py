# -*- coding: utf-8 -*-
"""CAD (DWG / DXF) 原生解析与渲染转换流水线。

本模块实现：
1. DWG -> DXF / SVG 本地无头转码；
2. DXF -> 结构化矢量实体与文本提取 (TEXT / MTEXT / INSERT 属性)；
3. DXF / SVG -> 标准 PDF 高保真光栅化，无缝对接下游 VisionExtractor 及 Web 视口。
"""

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
from typing import Any

import ezdxf
from ezdxf.addons.drawing import Frontend, RenderContext, layout
from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration, HatchPolicy
from ezdxf.addons.drawing.pymupdf import PyMuPdfBackend
from ezdxf.addons.drawing.svg import SVGBackend
from ezdxf.math import BoundingBox2d
from ezdxf.path import Path
from ezdxf.fonts import ttfonts
import pymupdf

from .cad_ledger import entity_source, join_insert_path

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

from .config import pipeline as _pipeline_top

MAX_TEXT_ENTITIES_LIMIT = int(_pipeline_top()["cad"]["max_text_entities_limit"])


def is_cad_path(path: str) -> bool:
    """检查文件是否为 DWG 或 DXF 格式。"""
    ext = os.path.splitext(path)[1].lower()
    return ext in (".dwg", ".dxf")


def find_dwg2dxf_tool() -> str | None:
    """查找系统中可用的 dwg2dxf 工具路径。"""
    configured = os.environ.get("EXTRACTOR_DWG2DXF_BIN")
    base_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    candidates = [
        configured,
        "/tmp/libredwg/bin/dwg2dxf",
        "/tmp/bin/dwg2dxf",
        os.path.join(base_root, "bin", "dwg2dxf"),
        os.path.join(base_root, "backend", "bin", "dwg2dxf"),
    ] + list(DWG2DXF_SEARCH_PATHS)
    for p in candidates:
        if p and os.path.exists(p) and os.access(p, os.X_OK):
            return p
    return shutil.which("dwg2dxf")


def find_dwg2svg_tool() -> str | None:
    """查找系统中可用的 dwg2SVG 工具路径。"""
    configured = os.environ.get("EXTRACTOR_DWG2SVG_BIN")
    base_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    candidates = [
        configured,
        "/tmp/libredwg/bin/dwg2SVG",
        "/tmp/libredwg/bin/dwg2svg",
        "/tmp/bin/dwg2SVG",
        "/tmp/bin/dwg2svg",
        os.path.join(base_root, "bin", "dwg2SVG"),
        os.path.join(base_root, "bin", "dwg2svg"),
    ] + list(DWG2SVG_SEARCH_PATHS)
    for p in candidates:
        if p and os.path.exists(p) and os.access(p, os.X_OK):
            return p
    return shutil.which("dwg2SVG") or shutil.which("dwg2svg")


def sanitize_surrogates(text: str) -> str:
    """清理字符串中的非法 Unicode 代理对（surrogates），防止 UTF-8 编码与 JSON 序列化崩溃。"""
    if not text:
        return ""
    if not isinstance(text, str):
        text = str(text)
    return text.encode("utf-8", "ignore").decode("utf-8")


def decode_cad_controls(text: str) -> str:
    """把 CAD 控制码还原成可读字符。DXF 与 PDF 文字层共用。

    %%U 这类排版开关直接去掉，否则位号会带上脏前缀。%%133 这类数字码还原成上标。
    认不出的控制码原样保留。直径符号沿用配电箱口径，写成 Φ。
    """
    if not text or "%" not in text:
        return text
    escapes = {
        "P": "±",
        "D": "°",
        "C": "Φ",
        "A": "°",
        "S": "/",
        "N": " ",
    }
    toggles = set("UOLKX")
    numbers = {
        "130": "Φ",
        "131": "HRB335",
        "132": "²",
        "133": "³",
        "179": "³",
        "176": "°",
        "177": "±",
        "248": "Φ",
    }
    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        if text[index] == "%" and index + 1 < length and text[index + 1] == "%":
            rest = text[index + 2:]
            code3 = rest[:3]
            if len(code3) == 3 and code3.isdigit():
                mapped = numbers.get(code3)
                if mapped is None:
                    number = int(code3)
                    mapped = chr(number) if 32 <= number < 127 else ""
                out.append(mapped)
                index += 5
                continue
            letter = rest[:1]
            key = letter.upper()
            if key in toggles:
                out.append("")
            elif key in escapes:
                out.append(escapes[key])
            else:
                out.append(text[index:index + (3 if letter else 2)])
            index += 3 if letter else 2
        else:
            out.append(text[index])
            index += 1
    return "".join(out)


def clean_mtext(raw_text: str) -> str:
    """清理 AutoCAD MTEXT 常见的格式控制代码（如 \\P, \\A1;, \\fSimSun; 等）与非法控制字符。"""
    if not raw_text:
        return ""
    t = sanitize_surrogates(raw_text)
    # 先解 %% 控制码。ezdxf 会把 %%c 收成 Ø，配电箱口径要的是 Φ，所以不能等它先处理。
    t = decode_cad_controls(t)
    # 优先使用 ezdxf 经过实战检验的 fast_plain_mtext 剔除全部 MTEXT 格式控制序列
    try:
        from ezdxf.tools.text import fast_plain_mtext
        t = fast_plain_mtext(t)
    except Exception:
        t = t.replace(r"\P", "\n").replace(r"\p", "\n")
        t = re.sub(r"[{}]", "", t)
    # 解码 AutoCAD 的 \\U+xxxx Unicode 转义字符（如 \\U+201C -> “，\\U+201D -> ”）
    try:
        t = re.sub(r"\\+U\+([0-9A-Fa-f]{4})", lambda m: chr(int(m.group(1), 16)), t)
    except Exception:
        pass
    # 移除残留的控制码与未识别转义（如 \\ud83d, \\A1; 等）
    t = re.sub(r"\\+[A-Za-z0-9]+\;?", "", t)
    # 过滤无法在 XML / Excel / JSON 中安全显示的 ASCII 控制字符 (ASCII 0-31，保留 \t, \n, \r)
    t = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", t)
    # 合并多余空白
    t = re.sub(r"[ \t]+", " ", t).strip()
    return t


def _libredwg_to_dxf(dwg_path: str, dxf_path: str) -> bool:
    """调用 LibreDWG 的 dwg2dxf 将 DWG 转为 DXF。

    通过条件是退出码 0，并且产物通过 DXF 结构校验。转换写到临时文件，
    校验通过后才替换目标路径。失败时不改目标文件，也不到目录里挑另一张图顶替。
    警告、外参和代理对象记在转换记录里，由读取盘点决定是降级还是关键条件不足。
    """
    from .cad_ledger import (
        MIN_DWG_BYTES,
        classify_stderr,
        conversion_accepted,
        remember_conversion,
    )

    def _remember(record: dict[str, Any]) -> bool:
        remember_conversion(dwg_path, record)
        if dxf_path:
            remember_conversion(dxf_path, record)
        return bool(record["accepted"])

    try:
        source_size = os.path.getsize(dwg_path)
    except OSError:
        source_size = 0
    if source_size < MIN_DWG_BYTES:
        return _remember({
            "tool": "dwg2dxf",
            "exit_code": None,
            "output_nonempty": False,
            "accepted": False,
            "dxf_valid": False,
            "warning_lines": 0,
            "error_lines": 1,
            "stderr_excerpt": "",
            "log_missing": False,
            "note": f"输入只有 {source_size} 字节，明显不是有效 DWG，未调用转换器",
        })

    tool = find_dwg2dxf_tool()
    if not tool:
        raise RuntimeError("未检测到 dwg2dxf 转换工具，请确保已安装 libredwg")

    out_dir = os.path.dirname(os.path.abspath(dxf_path)) or "."
    os.makedirs(out_dir, exist_ok=True)
    staging = dxf_path + ".converting"
    if os.path.exists(staging):
        try:
            os.remove(staging)
        except OSError:
            pass

    cmd = [tool, "-y", "-o", staging, dwg_path]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace", check=False,
                         timeout=CONVERT_TIMEOUT_S)
    staging_exists = os.path.exists(staging)
    output_nonempty = staging_exists and os.path.getsize(staging) > 0
    from .cad_fallback import validate_document
    dxf_valid, dxf_why = validate_document(staging) if staging_exists else (False, "本图未产出 .dxf")
    accepted = conversion_accepted(res.returncode, output_nonempty) and dxf_valid
    if accepted:
        os.replace(staging, dxf_path)
    elif staging_exists:
        try:
            os.remove(staging)
        except OSError:
            pass
    warning_lines, error_lines, excerpt = classify_stderr(res.stderr or "")
    if dxf_valid:
        note = "退出码 0 且 DXF 结构校验通过。这不表示对象、依赖和单位已经读全"
    elif res.returncode != 0:
        note = "转换器未正常退出，目标文件保持原样，不用目录里其他 DXF 顶替"
    else:
        note = f"产物不是可用 DXF（{dxf_why}），已丢弃，不用目录里其他 DXF 顶替"
    return _remember({
        "tool": os.path.basename(tool),
        "exit_code": res.returncode,
        "output_nonempty": output_nonempty,
        "accepted": accepted,
        "dxf_valid": dxf_valid,
        "warning_lines": warning_lines,
        "error_lines": error_lines,
        "stderr_excerpt": excerpt,
        "log_missing": False,
        "note": note,
    })


def dwg_to_dxf(dwg_path: str, dxf_path: str) -> bool:
    """LibreDWG first, acadrust on failure; never union two converter documents."""
    from .cad_fallback import convert_acadrust
    from .cad_ledger import MIN_DWG_BYTES, conversion_for, remember_conversion
    os.makedirs(os.path.dirname(os.path.abspath(dxf_path)), exist_ok=True)
    if os.path.realpath(dwg_path) == os.path.realpath(dxf_path):
        raise ValueError("不允许覆盖原始 DWG")
    try:
        accepted = _libredwg_to_dxf(dwg_path, dxf_path)
        primary = dict(conversion_for(dwg_path) or {})
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        accepted = False
        primary = {"tool": "dwg2dxf", "accepted": False, "exit_code": None,
                   "error_lines": 1, "warning_lines": 0, "log_missing": False,
                   "note": f"LibreDWG 未完成：{type(exc).__name__}: {exc}"}
    primary["converter"] = "libredwg"
    attempts = [primary]
    chosen = primary
    if not accepted and os.path.isfile(dwg_path) and os.path.getsize(dwg_path) >= MIN_DWG_BYTES:
        secondary = convert_acadrust(dwg_path, dxf_path)
        attempts.append(secondary)
        if secondary["accepted"]:
            chosen = secondary
    record = {**chosen, "attempts": attempts,
              "fallback_used": chosen.get("converter") == "acadrust"}
    for path in (dwg_path, dxf_path):
        remember_conversion(path, record)
    # Provenance survives a backend restart alongside the source-hash cache stamp.
    if record.get("accepted") and os.path.isfile(dwg_path) and os.path.isfile(dxf_path):
        from .cad_ledger import sha256_file
        record["source_sha256"] = sha256_file(dwg_path)
        record["output_sha256"] = sha256_file(dxf_path)
        try:
            with open(dxf_path + ".conversion.json", "w", encoding="utf-8") as stream:
                json.dump(record, stream, ensure_ascii=False, indent=2)
        except OSError:
            pass
    return bool(record.get("accepted"))


def resolve_dwg_dxf(dwg_path: str) -> str:
    """返回这张 DWG 自己的 DXF 缓存。

    缓存名带文件大小，旁边的标记文件写源文件哈希。同目录里另一张图的 DXF，
    或只有文件名对得上的旧缓存，都不会被当成这张图。
    """
    from .cad_ledger import MIN_DWG_BYTES, is_valid_dxf, sha256_file

    if not os.path.isfile(dwg_path):
        raise RuntimeError(f"DWG 不存在: {dwg_path}")
    source_size = os.path.getsize(dwg_path)
    work_dir = os.path.dirname(os.path.abspath(dwg_path))
    base_name = os.path.splitext(os.path.basename(dwg_path))[0]
    cache_dir = os.path.join(work_dir, ".cad_cache")
    os.makedirs(cache_dir, exist_ok=True)
    cache_key = hashlib.md5(f"{base_name}_{source_size}".encode()).hexdigest()
    cached_dxf = os.path.join(cache_dir, f"{cache_key}.dxf")
    stamp_path = cached_dxf + ".src"
    if source_size < MIN_DWG_BYTES:
        dwg_to_dxf(dwg_path, cached_dxf)
        raise RuntimeError(
            f"{os.path.basename(dwg_path)} 只有 {source_size} 字节，明显不是有效 DWG，已直接判失败"
        )
    source_hash = sha256_file(dwg_path)
    valid, _why = is_valid_dxf(cached_dxf)
    stamped = ""
    if valid:
        try:
            with open(stamp_path, encoding="utf-8") as handle:
                stamped = handle.read().strip()
        except OSError:
            stamped = ""
    if valid and stamped == source_hash:
        from .cad_ledger import remember_conversion
        try:
            with open(cached_dxf + ".conversion.json", encoding="utf-8") as stream:
                record = json.load(stream)
            if (record.get("source_sha256") == source_hash
                    and record.get("output_sha256") == sha256_file(cached_dxf)):
                remember_conversion(dwg_path, record)
                remember_conversion(cached_dxf, record)
        except (OSError, ValueError, AttributeError):
            pass
        return cached_dxf

    for stale in (cached_dxf, stamp_path):
        if os.path.exists(stale):
            try:
                os.remove(stale)
            except OSError:
                pass
    if not dwg_to_dxf(dwg_path, cached_dxf):
        raise RuntimeError(
            f"无法把 {os.path.basename(dwg_path)} 转成可用 DXF。"
            "空壳、坏文件和目录里的其他图纸都不会拿来顶替。"
        )
    try:
        with open(stamp_path, "w", encoding="utf-8") as handle:
            handle.write(source_hash)
    except OSError:
        pass
    return cached_dxf


def dwg_to_svg_fallback(dwg_path: str, svg_path: str) -> bool:
    """使用 dwg2SVG 直接将 DWG 输出为 SVG（备用路径）。"""
    tool = find_dwg2svg_tool()
    if not tool:
        return False
    cmd = [tool, dwg_path]
    try:
        with open(svg_path, "wb") as f_out:
            res = subprocess.run(cmd, stdout=f_out, stderr=subprocess.PIPE, check=False, timeout=CONVERT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return False
    return res.returncode == 0 and os.path.exists(svg_path) and os.path.getsize(svg_path) > 0


def load_dxf_document(dxf_path: str):
    """安全读取 DXF 文件，在格式异常时自动尝试 recover 模式。"""
    try:
        doc = ezdxf.readfile(dxf_path)
        doc.extractor_recovered = False
        return doc
    except Exception:
        from ezdxf import recover
        doc, _ = recover.readfile(dxf_path)
        doc.extractor_recovered = True
        return doc


# ---------------------------------------------------------------------------
# 预览：sheets.py 按真实图框分幅，系统图 / 原理图每张整页渲染成一页，不做任何启发式裁剪；
# 箱柜与回路的位置由前端在整张图上画高亮框（assign_preview_bboxes 按图框坐标精确换算）。
# 几何判据与领域词典来自 config/pipeline.json 的 cad / takeoff 段与 config/domain.json。
# ---------------------------------------------------------------------------
from .config import domain as _domain, pipeline as _pipeline  # noqa: E402

_CAD = _pipeline()["cad"]
_DOM = _domain()["cad"]

ISO_LANDSCAPE_SIZES = tuple(tuple(pair) for pair in _CAD["iso_landscape_sizes"])
FALLBACK_TEXT_HEIGHT = float(_CAD["fallback_text_height"])
UNIT_CAPTION_DEFAULT_HEIGHT = float(_CAD["unit_caption_default_height"])
DWG2DXF_SEARCH_PATHS = tuple(_CAD["dwg2dxf_search_paths"])
DWG2SVG_SEARCH_PATHS = tuple(_CAD["dwg2svg_search_paths"])
CONVERT_TIMEOUT_S = int(_CAD["convert_timeout_s"])
PAGE_LONG_MM_MIN, PAGE_LONG_MM_MAX = float(_CAD["page_long_mm_min"]), float(_CAD["page_long_mm_max"])
GRID_CELL = float(_CAD["grid_cell"])
SYSTEM_SHEET_MIN_BREAKERS = int(_pipeline()["takeoff"]["sheets"].get("system_min_breakers", 3))


def _entity_bounds(entity: Any) -> tuple[float, float, float, float] | None:
    """按实体类型取真实二维包围盒；取不到返回 None。

    必须分类型取锚点：CIRCLE/ARC 用 center+radius，LWPOLYLINE/POLYLINE 用顶点，
    SOLID 用 vtx0..vtx3。旧实现统一取 dxf.insert 或 dxf.start，把没有该属性的图元
    整类丢掉。
    """
    t = entity.dxftype()
    d = entity.dxf
    try:
        if t == "LINE":
            pts = [d.start, d.end]
        elif t == "POINT":
            pts = [d.location]
        elif t in ("TEXT", "MTEXT", "ATTDEF", "ATTRIB", "ACAD_TABLE", "OLE2FRAME", "SHAPE"):
            pts = [d.insert]
        elif t in ("CIRCLE", "ARC"):
            c, r = d.center, float(d.radius)
            return c.x - r, c.y - r, c.x + r, c.y + r
        elif t == "ELLIPSE":
            c, mx = d.center, d.major_axis
            r = math.hypot(float(mx.x), float(mx.y))
            return c.x - r, c.y - r, c.x + r, c.y + r
        elif t == "LWPOLYLINE":
            pts = [(p[0], p[1]) for p in entity.get_points("xy")]
        elif t == "POLYLINE":
            pts = [(p[0], p[1]) for p in entity.points()]
        elif t in ("SOLID", "TRACE", "3DFACE"):
            pts = [getattr(d, k) for k in ("vtx0", "vtx1", "vtx2", "vtx3") if d.hasattr(k)]
        elif t == "SPLINE":
            pts = list(entity.control_points) or list(entity.fit_points)
        elif t == "DIMENSION":
            pts = [getattr(d, k) for k in ("defpoint", "defpoint2", "defpoint3", "text_midpoint")
                   if d.hasattr(k)]
        elif t == "LEADER":
            pts = list(entity.vertices)
        elif t == "HATCH":
            pts = [v for p in entity.paths for v in getattr(p, "vertices", [])]
        else:
            return None
    except Exception:
        return None
    if not pts:
        return None
    xs = [float(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _apply_matrix(m, box):
    corners = [(box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])]
    out = [m.transform((x, y, 0.0)) for x, y in corners]
    xs = [float(p[0]) for p in out]
    ys = [float(p[1]) for p in out]
    return min(xs), min(ys), max(xs), max(ys)


def _block_bounds(doc: Any, name: str, cache: dict, depth: int = 0):
    """图块定义的局部范围（按块名缓存）。

    自己逐图元算并递归限深，而不用 ``ezdxf.bbox.extents``：后者对 AutoCAD
    Electrical 那类深层嵌套块会反复整块递归，实测一张 39MB 图要 50 秒。
    """
    if name in cache:
        return cache[name]
    cache[name] = None  # 防循环引用
    box = None
    try:
        for e in doc.blocks[name]:
            b = None
            kind = e.dxftype()
            if kind == "INSERT" and depth < 4:
                sub = _block_bounds(doc, e.dxf.name, cache, depth + 1)
                if sub is not None:
                    try:
                        b = _apply_matrix(e.matrix44(), sub)
                    except Exception:
                        b = _entity_bounds(e)
                else:
                    b = _entity_bounds(e)
            else:
                b = _entity_bounds(e)
            if b is not None:
                box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]),
                                             max(box[2], b[2]), max(box[3], b[3]))
    except Exception:
        box = None
    cache[name] = box
    return box


def _insert_bounds(doc: Any, ins: Any, cache: dict) -> tuple[float, float, float, float] | None:
    """块参照的实际包围盒：块范围四角经块参照矩阵（缩放/旋转/镜像）变换。"""
    box = _block_bounds(doc, ins.dxf.name, cache)
    if box is None:
        return None
    try:
        return _apply_matrix(ins.matrix44(), box)
    except Exception:
        return None


class GeometryIndex:
    """模型空间实体的一次性包围盒索引与空间网格。

    后续所有“这块图里有哪些图元”的判断都走它，避免每个图框/单元块重新遍历一遍
    十几万实体，也避免把实体复制进新文档（复制会丢失图层颜色、线型，还会因为
    图元类型不受支持而静默失败）。
    """

    def __init__(self, doc: Any):
        self.doc = doc
        self.entities: list[Any] = []
        self.boxes: list[tuple[float, float, float, float]] = []
        self._grid: dict[tuple[int, int], list[int]] = {}
        self.block_cache: dict = {}          # 块定义局部范围（块名 -> bbox）
        self.block_text_cache: dict = {}     # 块定义内部文字（块名 -> [文字]）
        for e in doc.modelspace():
            b = _insert_bounds(doc, e, self.block_cache) if e.dxftype() == "INSERT" else _entity_bounds(e)
            if b is None:
                continue
            i = len(self.entities)
            self.entities.append(e)
            self.boxes.append(b)
            for gx in range(int(b[0] // GRID_CELL), int(b[2] // GRID_CELL) + 1):
                for gy in range(int(b[1] // GRID_CELL), int(b[3] // GRID_CELL) + 1):
                    self._grid.setdefault((gx, gy), []).append(i)

    def query(self, rect: tuple[float, float, float, float]) -> list[int]:
        """返回包围盒与 rect 相交的实体下标（包围盒相交即算，跨框的整条线不会被切掉归属）。"""
        x0, y0, x1, y1 = rect
        seen = set()
        out = []
        for gx in range(int(x0 // GRID_CELL), int(x1 // GRID_CELL) + 1):
            for gy in range(int(y0 // GRID_CELL), int(y1 // GRID_CELL) + 1):
                for i in self._grid.get((gx, gy), ()):
                    if i in seen:
                        continue
                    seen.add(i)
                    b = self.boxes[i]
                    if b[0] <= x1 and b[2] >= x0 and b[1] <= y1 and b[3] >= y0:
                        out.append(i)
        return out


def detect_drawing_frames(doc: Any, index: GeometryIndex) -> list[dict[str, Any]]:
    """图纸上的全部真实图幅（sheets.detect_sheets：图框块的幅面变体、闭合外框、图纸目录取图号图名）。
    识别不到图幅时返回空列表，由调用方整图渲染。"""
    from .native_takeoff import _Walker
    from .sheets import detect_sheets

    texts = _Walker(doc).layout_texts(doc.modelspace(), "模型空间")
    if not texts:
        return []
    return [{"bbox": s.bbox, "number": s.number, "name": s.name, "kind": s.kind, "source": s.source}
            for s in detect_sheets(doc, index, texts)]


def _guess_plot_scale(w: float, h: float) -> float:
    """由幅面尺寸推断出图比例（1:N 的 N）。

    只用于给渲染定物理页面尺寸。按 ISO A 系列横放匹配最接近的一种，再对
    N 做一次合理区间收口，保证下游按“长边约 2400px”光栅化时不会撞到 DPI 上下限。
    """
    long_u, short_u = max(w, h), max(min(w, h), 1e-6)
    best, best_err = None, None
    for iso_long, iso_short in ISO_LANDSCAPE_SIZES:
        s1, s2 = long_u / iso_long, short_u / iso_short
        mean = (s1 + s2) / 2.0
        err = abs(s1 - s2) / max(mean, 1e-9)
        if best is None or err < best_err:
            best, best_err = mean, err
    scale = max(float(best or FALLBACK_TEXT_HEIGHT), 1e-6)
    long_mm = long_u / scale
    if long_mm < PAGE_LONG_MM_MIN:
        scale = long_u / PAGE_LONG_MM_MIN
    elif long_mm > PAGE_LONG_MM_MAX:
        scale = long_u / PAGE_LONG_MM_MAX
    return scale


def _frame_texts(index: GeometryIndex, rect, cached: dict) -> list[dict[str, Any]]:
    """取落在矩形内的文字（含块参照内部文字与属性）。"""
    if rect in cached:
        return cached[rect]
    out: list[dict[str, Any]] = []
    for i in index.query(rect):
        e = index.entities[i]
        kind = e.dxftype()
        if kind in ("TEXT", "MTEXT"):
            raw = e.dxf.text if kind == "TEXT" else e.text
            txt = clean_mtext(raw)
            if txt:
                out.append({"text": txt, "x": float(e.dxf.insert.x), "y": float(e.dxf.insert.y),
                            "layer": str(e.dxf.layer), "height": _text_height(e, kind)})
        elif kind == "INSERT":
            for sub in _explode_texts(e):
                if rect[0] <= sub["x"] <= rect[2] and rect[1] <= sub["y"] <= rect[3]:
                    out.append(sub)
    cached[rect] = out
    return out


def _text_height(e: Any, kind: str) -> float:
    try:
        return float(e.dxf.height if kind == "TEXT" else e.dxf.char_height)
    except Exception:
        return 10.0


def _frame_texts(index: GeometryIndex, rect, cached: dict, block_text: bool = False) -> list[dict[str, Any]]:
    """取落在矩形内的文字。

    ``block_text=False`` 只读模型空间文字（图名、箱名都在模型空间，最便宜）；
    ``block_text=True`` 再把块参照内部的文字与属性折成绝对坐标一并取出，
    供原生文字层送给视觉模型参考。
    """
    key = (rect, block_text)
    if key in cached:
        return cached[key]
    texts: list[dict[str, Any]] = []
    inserts: list[Any] = []
    for i in index.query(rect):
        e = index.entities[i]
        kind = e.dxftype()
        if kind == "TEXT" or kind == "MTEXT":
            raw = e.dxf.text if kind == "TEXT" else e.text
            txt = clean_mtext(raw)
            if txt:
                texts.append({"text": txt, "x": float(e.dxf.insert.x), "y": float(e.dxf.insert.y),
                              "layer": str(e.dxf.layer), "height": _text_height(e, kind),
                              "source": entity_source(e, owner="modelspace", insert_path="")})
        elif kind == "INSERT":
            inserts.append(e)
    if block_text:
        for ins in inserts:
            for sub in _explode_texts(ins, index.block_text_cache):
                if rect[0] <= sub["x"] <= rect[2] and rect[1] <= sub["y"] <= rect[3]:
                    texts.append(sub)
    cached[key] = texts
    return texts


def _local_block_texts(doc: Any, name: str, cache: dict, depth: int = 0) -> list[dict[str, Any]]:
    """图块定义内部的文字，坐标已折到该块自身的局部坐标系（按块名缓存）。

    同一块名在模型空间可能被引用上千次，逐引用展开会重复几十万次；按块名缓存后
    每个块定义只展开一次。
    """
    if name in cache:
        return cache[name]
    cache[name] = []
    out: list[dict[str, Any]] = []
    try:
        for e in doc.blocks[name]:
            kind = e.dxftype()
            if kind in ("TEXT", "MTEXT", "ATTRIB"):
                raw = e.text if kind == "MTEXT" else getattr(e.dxf, "text", "")
                txt = clean_mtext(raw)
                if txt:
                    out.append({"text": txt, "x": float(e.dxf.insert.x), "y": float(e.dxf.insert.y),
                                "layer": str(getattr(e.dxf, "layer", "")), "height": _text_height(e, kind),
                                "source": entity_source(e, owner=name, insert_path="")})
            elif kind == "INSERT" and depth < 4:
                sub = _local_block_texts(doc, e.dxf.name, cache, depth + 1)
                if not sub:
                    continue
                try:
                    m = e.matrix44()
                except Exception:
                    continue
                nested = entity_source(e, owner=name, insert_path="")
                for s in sub:
                    p = m.transform((s["x"], s["y"], 0.0))
                    src = dict(s.get("source") or {})
                    src["insert_path"] = join_insert_path(
                        f"{e.dxf.name}@{nested['handle']}", str(src.get("insert_path") or ""))
                    src["owner"] = str(e.dxf.name)
                    out.append({**s, "x": float(p[0]), "y": float(p[1]), "source": src})
    except Exception:
        pass
    cache[name] = out
    return out


def _explode_texts(ins: Any, cache: dict) -> list[dict[str, Any]]:
    """取块参照内部的文字与属性文字，坐标折到模型空间绝对坐标。

    插入路径带上这次 INSERT 的 handle。块定义里的文字 handle 在每次插入时相同，
    不能单独拿来当器件身份。
    """
    out: list[dict[str, Any]] = []
    insert_identity = entity_source(ins, owner="modelspace", insert_path="")
    instance_path = f"{ins.dxf.name}@{insert_identity['handle']}"
    doc = getattr(ins, "doc", None)
    if doc is not None:
        local = _local_block_texts(doc, ins.dxf.name, cache)
        if local:
            try:
                m = ins.matrix44()
            except Exception:
                m = None
            for s in local:
                src = dict(s.get("source") or {})
                src["insert_path"] = join_insert_path(instance_path, str(src.get("insert_path") or ""))
                src["owner"] = str(ins.dxf.name)
                if m is not None:
                    p = m.transform((s["x"], s["y"], 0.0))
                    out.append({**s, "x": float(p[0]), "y": float(p[1]), "source": src})
                else:
                    out.append({**s, "source": src})
    for attrib in getattr(ins, "attribs", []):
        txt = clean_mtext(str(getattr(attrib.dxf, "text", "")))
        if txt:
            attrib_identity = entity_source(attrib, owner=str(ins.dxf.name), insert_path="")
            out.append({"text": txt, "x": float(attrib.dxf.insert.x), "y": float(attrib.dxf.insert.y),
                        "layer": str(getattr(attrib.dxf, "layer", "")),
                        "height": _text_height(attrib, "ATTRIB"),
                        "source": entity_source(
                            attrib,
                            owner=str(ins.dxf.name),
                            insert_path=join_insert_path(instance_path, attrib_identity["handle"]),
                        )})
    return out


def _render_block_pdf(doc: Any, index: GeometryIndex, block: dict[str, Any], ctx: RenderContext,
                      cfg: Configuration) -> bytes:
    """把一个单元块渲染成单页 PDF：只画该块相交的实体，靠 render_box 精确裁切。"""
    x0, y0, x1, y1 = block["rect"]
    w, h = x1 - x0, y1 - y0
    scale = block["scale"]
    page = layout.Page(w / scale, h / scale, layout.Units.mm)
    settings = layout.Settings(scale=1.0 / scale)
    entities = [index.entities[i] for i in index.query((x0, y0, x1, y1))]
    backend = PyMuPdfBackend()
    frontend = Frontend(ctx, backend, config=cfg)
    frontend.set_background("#ffffff")
    frontend.draw_entities(entities)
    return backend.get_pdf_bytes(page, settings=settings,
                                 render_box=BoundingBox2d([(x0, y0), (x1, y1)]))


def render_cad_unit_blocks(doc: Any, blocks: list[dict[str, Any]], out_pdf_path: str,
                           index: GeometryIndex) -> list[dict[str, Any]]:
    """逐块渲染并合成多页 PDF，同时按页导出原生 CAD 文字。"""
    ctx = RenderContext(doc)
    ctx.set_current_layout(doc.modelspace())
    cfg = Configuration(background_policy=BackgroundPolicy.WHITE, color_policy=ColorPolicy.COLOR,
                        hatch_policy=HatchPolicy.IGNORE)
    combined = pymupdf.open()
    texts: list[dict[str, Any]] = []
    text_cache: dict = {}
    try:
        for idx, block in enumerate(blocks, 1):
            try:
                page_bytes = _render_block_pdf(doc, index, block, ctx, cfg)
            except Exception as exc:  # noqa: BLE001 - 单块渲染失败不能拖垮整张图
                print(f"[CAD] 单元块渲染降级 ({block.get('label')}): {exc}")
                page_bytes = _fallback_page(block)
            with pymupdf.open(stream=page_bytes, filetype="pdf") as page_doc:
                combined.insert_pdf(page_doc)
            for t in _frame_texts(index, block["rect"], text_cache, block_text=True):
                texts.append({"type": "TEXT", "text": t["text"], "x": round(t["x"], 1),
                              "y": round(t["y"], 1), "page": idx, "sheet": block["label"],
                              "box_code": block.get("box_code") or "",
                              "layer": t["layer"],
                              "split_source": block.get("split_source") or "unit",
                              "split_reason": block.get("split_reason") or ""})
    finally:
        combined.save(out_pdf_path)
        combined.close()
    texts.sort(key=lambda item: (item["page"], -item["y"], item["x"]))
    return texts


def select_system_frames(frames: list[dict[str, Any]], index: GeometryIndex) -> list[dict[str, Any]]:
    """要出预览页的图幅：图名是系统图 / 原理图类；图名认不出时，图里有 3 条以上开关型号文字也算。
    设计说明、目录、材料表、平面图不出页（箱柜不在这些图上）。"""
    from .cad_extractor import _text_is_breaker

    out = []
    cache: dict = {}
    for f in frames:
        kind = f.get("kind") or ""
        if kind == "system":
            out.append(f)
        elif kind in ("", "other"):
            texts = _frame_texts(index, f["bbox"], cache)
            if sum(1 for t in texts if _text_is_breaker(t["text"])) >= SYSTEM_SHEET_MIN_BREAKERS:
                out.append(f)
    return out


def render_cad_sheets(doc: Any, frames: list[dict[str, Any]], out_pdf_path: str,
                      index: GeometryIndex) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """每张图幅整页渲染（矢量，按图框精确裁边），返回 (按页的原生文字, 每页图框坐标)。"""
    blocks = []
    meta = []
    for f in frames:
        x0, y0, x1, y1 = f["bbox"]
        label = " ".join(v for v in (f.get("number") or "", f.get("name") or "") if v) or "系统图"
        blocks.append({"rect": f["bbox"], "label": label, "scale": _guess_plot_scale(x1 - x0, y1 - y0),
                       "box_code": "", "split_source": "sheet", "split_reason": ""})
    texts = render_cad_unit_blocks(doc, blocks, out_pdf_path, index)
    for page, (f, blk) in enumerate(zip(frames, blocks), 1):
        meta.append({"page": page, "rect": [round(v, 3) for v in f["bbox"]],
                     "number": f.get("number") or "", "name": f.get("name") or "", "label": blk["label"]})
    return texts, meta


def _fallback_page(block: dict[str, Any]) -> bytes:
    """单块矢量渲染失败时的白底占位页，保证页序与块序一一对应。"""
    x0, y0, x1, y1 = block["rect"]
    scale = block["scale"]
    doc = pymupdf.open()
    page = doc.new_page(width=(x1 - x0) / scale / 25.4 * 72, height=(y1 - y0) / scale / 25.4 * 72)
    page.insert_text((36, 36), f"{block.get('label', '')} (矢量渲染降级)", fontsize=14)
    return doc.convert_to_pdf()


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
            height = float(getattr(entity.dxf, "height", UNIT_CAPTION_DEFAULT_HEIGHT))
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
                "source": entity_source(entity, owner="modelspace", insert_path=""),
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
            height = float(getattr(entity.dxf, "char_height", UNIT_CAPTION_DEFAULT_HEIGHT))
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
                "source": entity_source(entity, owner="modelspace", insert_path=""),
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
                    insert_identity = entity_source(entity, owner="modelspace", insert_path="")
                    attrib_identity = entity_source(attrib, owner=block_name, insert_path="")
                    extracted.append({
                        "type": "ATTRIB",
                        "block": block_name,
                        "tag": tag,
                        "text": val,
                        "x": round(float(attrib.dxf.insert.x), 2),
                        "y": round(float(attrib.dxf.insert.y), 2),
                        "height": round(float(getattr(attrib.dxf, "height", UNIT_CAPTION_DEFAULT_HEIGHT)), 2),
                        "layer": layer,
                        "source": entity_source(
                            attrib,
                            owner=block_name,
                            insert_path=join_insert_path(
                                f"{block_name}@{insert_identity['handle']}",
                                attrib_identity["handle"],
                            ),
                        ),
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
    """主入口：尝试将 DWG 或 DXF 转为 PDF，并提取可识别的原生文字记录。

    针对平铺多张图纸的 CAD 文件，尝试探测电气系统图图框并进行多页矢量切片；
    针对单张图纸 CAD，直接高质量渲染；
    具备持久化转换缓存，二次处理 0 耗时秒开。
    """
    ext = os.path.splitext(cad_path)[1].lower()
    work_dir = os.path.dirname(cad_path)
    base_name = os.path.splitext(os.path.basename(cad_path))[0]

    from .cad_ledger import build_read_report, is_valid_dxf, stamp_records, write_read_report

    dxf_to_clean = None
    converter = "native_dxf"
    dxf_path = cad_path
    split_summary: dict[str, Any] = {
        "mode": "unset",
        "unit_blocks": 0,
        "frame_fallbacks": 0,
        "total_blocks": 0,
        "missed_captions": [],
        "uncertainties": [],
    }

    def _stamp_split(texts: list[dict[str, Any]], source: str, reason: str = "") -> None:
        for rec in texts:
            rec.setdefault("split_source", source)
            if reason and not rec.get("split_reason"):
                rec["split_reason"] = reason

    def _deliver(texts: list[dict[str, Any]], doc: Any, used_converter: str) -> tuple[str, list[dict[str, Any]]]:
        stamp_records(texts, cad_path)
        report = build_read_report(
            cad_path,
            doc=doc,
            converter=used_converter,
            dxf_path=dxf_path if used_converter != "svg_fallback" else None,
            recovered=bool(getattr(doc, "extractor_recovered", False)) if doc is not None else False,
            split=split_summary,
        )
        write_read_report(out_pdf_path, report)
        return out_pdf_path, texts

    if ext == ".dwg":
        converter = "libredwg"
        dxf_success = False
        try:
            dxf_path = resolve_dwg_dxf(cad_path)
            from .cad_ledger import conversion_for
            converter = (conversion_for(cad_path) or {}).get("converter", "libredwg")
            dxf_success = True
        except Exception as e:
            print(f"dwg2dxf 失败: {e}")

        if not dxf_success or not is_valid_dxf(dxf_path)[0]:
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
                    return _deliver([], None, "svg_fallback")
            raise RuntimeError(f"无法将 DWG 文件 {os.path.basename(cad_path)} 转换为预览格式")
    elif ext == ".dxf":
        dxf_path = cad_path
        converter = "native_dxf"
    else:
        raise ValueError(f"不支持的 CAD 文件格式: {ext}")

    try:
        doc = load_dxf_document(dxf_path)

        # 1. 按真实图框分幅（sheets.py），系统图 / 原理图一张图幅渲染成一页，不做任何裁剪；
        #    箱柜、回路的位置在前端用高亮框标在整张图上（page_rects 记录每页对应的图框坐标）。
        try:
            index = GeometryIndex(doc)
            frames = detect_drawing_frames(doc, index)
            pages = select_system_frames(frames, index)
            if pages:
                extracted_texts, page_meta = render_cad_sheets(doc, pages, out_pdf_path, index)
                if os.path.exists(out_pdf_path) and os.path.getsize(out_pdf_path) > 0:
                    split_summary.update({
                        "mode": "sheets",
                        "sheets": len(page_meta),
                        "frames_detected": len(frames),
                        "pages": page_meta,
                        "uncertainties": [],
                    })
                    return _deliver(extracted_texts, doc, converter)
            split_summary.update({
                "mode": "no_system_sheet",
                "frames_detected": len(frames),
                "uncertainties": ["未识别出系统图图幅，已整图渲染"],
            })
        except Exception as exc:  # noqa: BLE001 - 分幅异常时整图渲染，不能让上传直接失败
            print(f"[CAD] 图幅识别失败，整图渲染: {exc}")
            split_summary.update({
                "mode": "sheets_failed",
                "uncertainties": [f"图幅识别异常，已整图渲染：{exc}"],
            })

        # 3. 若未探测出系统图分幅图框，检查是否属于不包含系统图的海量平面施工图
        msp = doc.modelspace()
        text_count = len(msp.query("TEXT")) + len(msp.query("MTEXT"))
        if text_count > MAX_TEXT_ENTITIES_LIMIT:
            raise ValueError(
                f"图纸包含海量文字实体 ({text_count} 条)，未检测到配电箱电气系统图（均为建筑施工平面图）。"
                f"请上传包含配电箱结线与回路的电气系统图 DWG 或 PDF 切图。"
            )

        # 3. 整图渲染：一页即整个模型空间
        extracted_texts = extract_cad_entities(dxf_path, doc=doc)
        ok = render_dxf_to_pdf(dxf_path, out_pdf_path, doc=doc)
        if not ok:
            raise RuntimeError("CAD 渲染为 PDF 失败")
        _stamp_split(extracted_texts, "page_fallback", "full_page_render")
        for rec in extracted_texts:
            rec.setdefault("page", 1)
        try:
            from ezdxf import bbox as _bbox
            ext = _bbox.extents(doc.modelspace(), fast=True)
            if ext.has_data:
                split_summary["pages"] = [{"page": 1, "rect": [ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y],
                                           "number": "", "name": "整图"}]
        except Exception:
            pass
        split_summary["mode"] = "page_fallback" if split_summary.get("mode") == "unset" else split_summary["mode"]
        return _deliver(extracted_texts, doc, converter)
    finally:
        if dxf_to_clean:
            try:
                os.remove(dxf_to_clean)
            except OSError:
                pass
