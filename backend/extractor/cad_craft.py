# -*- coding: utf-8 -*-
"""CADCraft CLI 适配封装：提供原生物件信息查询、空间切图渲染、文字实体句柄检索。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any


def find_cadcraft_tool() -> str | None:
    """寻找编译好的 cadcraft-cli 二进制可执行文件。"""
    configured = os.environ.get("EXTRACTOR_CADCRAFT_BIN")
    bundled = (
        Path(__file__).resolve().parents[2]
        / "integrations/cadcraft/source/target/release/cadcraft-cli"
    )
    for candidate in (configured, str(bundled), shutil.which("cadcraft-cli")):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def cadcraft_info(dxf_path: str) -> dict[str, Any] | None:
    """调用 cadcraft-cli info 获取图纸图层、实体总数、空间边界、视口等元数据。"""
    tool = find_cadcraft_tool()
    if not tool or not os.path.isfile(dxf_path):
        return None
    try:
        proc = subprocess.run(
            [tool, "info", dxf_path],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return json.loads(proc.stdout)
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as exc:
        print(f"[cadcraft] info failed: {exc}")
    return None


def cadcraft_find(dxf_path: str, keyword: str) -> dict[str, Any] | None:
    """调用 cadcraft-cli run --cmd 'find ...' 搜索特定关键字匹配的实体对象及 handle 句柄。"""
    tool = find_cadcraft_tool()
    if not tool or not os.path.isfile(dxf_path) or not keyword:
        return None
    cmd_arg = json.dumps({"find": keyword}, ensure_ascii=False)
    try:
        proc = subprocess.run(
            [tool, "run", dxf_path, "--cmd", f"find {cmd_arg}"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return json.loads(proc.stdout)
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as exc:
        print(f"[cadcraft] find failed: {exc}")
    return None


def cadcraft_render_slice(
    dxf_path: str,
    out_png: str,
    window: tuple[float, float, float, float],
) -> bool:
    """调用 cadcraft-cli convert --window x0,y0,x1,y1 截取任意矢量坐标子区域并渲染为超清 PNG。"""
    tool = find_cadcraft_tool()
    if not tool or not os.path.isfile(dxf_path):
        return False
    x0, y0, x1, y1 = window
    if x0 >= x1 or y0 >= y1:
        return False
    os.makedirs(os.path.dirname(os.path.abspath(out_png)), exist_ok=True)
    window_str = f"{x0},{y0},{x1},{y1}"
    try:
        proc = subprocess.run(
            [tool, "convert", dxf_path, out_png, "--window", window_str],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60,
            check=False,
        )
        return (
            proc.returncode == 0
            and os.path.isfile(out_png)
            and os.path.getsize(out_png) > 0
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        print(f"[cadcraft] render_slice failed: {exc}")
        return False


def cadcraft_render_full(dxf_path: str, out_png: str) -> bool:
    """调用 cadcraft-cli convert 将全图导出为 PNG。"""
    tool = find_cadcraft_tool()
    if not tool or not os.path.isfile(dxf_path):
        return False
    os.makedirs(os.path.dirname(os.path.abspath(out_png)), exist_ok=True)
    try:
        proc = subprocess.run(
            [tool, "convert", dxf_path, out_png],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60,
            check=False,
        )
        return (
            proc.returncode == 0
            and os.path.isfile(out_png)
            and os.path.getsize(out_png) > 0
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        print(f"[cadcraft] render_full failed: {exc}")
        return False


def cadcraft_inspect(dxf_path: str, handle: str) -> dict[str, Any] | None:
    """查询指定图元详细坐标、图层与属性（优先 CADCraft CLI 高速透视，自动兜底 ezdxf 全图元解析）。"""
    if not os.path.isfile(dxf_path) or not handle:
        return None
    import re
    tool = find_cadcraft_tool()
    parsed: dict[str, Any] = {
        "handle": handle,
        "raw": "",
        "type": None,
        "layer": None,
        "coords": None,
        "text": None,
        "height": None,
    }

    if tool:
        cmd_arg = json.dumps({"handles": [handle]}, ensure_ascii=False)
        try:
            proc = subprocess.run(
                [tool, "run", dxf_path, "--cmd", f"list {cmd_arg}"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=30,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                raw_json = json.loads(proc.stdout)
                msg = raw_json.get("message") or ""
                parsed["raw"] = msg

                # 匹配实体类型与图层
                m_type = re.search(r"^\s*([A-Z_]+)\s+Layer:\s*\"([^\"]+)\"", msg, re.M)
                if m_type:
                    parsed["type"] = m_type.group(1).strip()
                    parsed["layer"] = m_type.group(2).strip()

                # 匹配点坐标：start point, center point, from point, at point
                m_pt = re.search(r"(?:start|center|from|at)\s+point,\s*X=([-\d\.]+)\s*Y=([-\d\.]+)", msg, re.IGNORECASE)
                if m_pt:
                    parsed["coords"] = [float(m_pt.group(1)), float(m_pt.group(2))]

                # 匹配普通文本与字高
                m_txt = re.search(r"text\s+(.+)$", msg, re.M)
                if m_txt:
                    parsed["text"] = m_txt.group(1).strip()
                m_h = re.search(r"height\s+([-\d\.]+)", msg)
                if m_h:
                    parsed["height"] = float(m_h.group(1))

                # 匹配嵌在 message 中的 JSON 结构（MTEXT, INSERT, TABLE, DIMENSION 等）
                m_json = re.search(r"(\{.*\})", msg, re.S)
                if m_json:
                    try:
                        ins_data = json.loads(m_json.group(1))
                        if "type" in ins_data and not parsed["type"]:
                            parsed["type"] = str(ins_data["type"]).upper()
                        if "insert" in ins_data and isinstance(ins_data["insert"], dict):
                            parsed["coords"] = [float(ins_data["insert"].get("x", 0)), float(ins_data["insert"].get("y", 0))]
                        elif "def" in ins_data and isinstance(ins_data["def"], dict):
                            parsed["coords"] = [float(ins_data["def"].get("x", 0)), float(ins_data["def"].get("y", 0))]
                        elif "textMid" in ins_data and isinstance(ins_data["textMid"], dict):
                            parsed["coords"] = [float(ins_data["textMid"].get("x", 0)), float(ins_data["textMid"].get("y", 0))]

                        if "contents" in ins_data and not parsed["text"]:
                            parsed["text"] = str(ins_data["contents"])
                        if "height" in ins_data and parsed["height"] is None:
                            parsed["height"] = float(ins_data["height"])
                        if "block" in ins_data:
                            parsed["block"] = ins_data["block"]
                        if "attribs" in ins_data:
                            parsed["attribs"] = ins_data["attribs"]
                    except Exception:
                        pass
        except Exception as exc:
            print(f"[cadcraft] inspect CLI warning: {exc}")

    # 实体未能完全解析坐标或类型时，使用 ezdxf 作为无缝统一兜底
    if not parsed["type"] or not parsed["coords"]:
        try:
            import ezdxf
            from .cad import clean_mtext
            try:
                doc = ezdxf.readfile(dxf_path)
            except Exception:
                doc = ezdxf.readfile(dxf_path, encoding="gbk")
            e = doc.entitydb.get(handle)
            if e:
                parsed["type"] = parsed["type"] or e.dxftype()
                parsed["layer"] = parsed["layer"] or getattr(e.dxf, "layer", "0")
                if not parsed["coords"]:
                    pos = getattr(e.dxf, "insert", None) or getattr(e.dxf, "center", None) or getattr(e.dxf, "start", None)
                    if pos:
                        parsed["coords"] = [round(pos.x, 1), round(pos.y, 1)]
                    elif e.dxftype() == "LWPOLYLINE":
                        pts = e.get_points("xy")
                        if pts:
                            parsed["coords"] = [round(pts[0][0], 1), round(pts[0][1], 1)]
                if not parsed["text"]:
                    raw_t = getattr(e.dxf, "text", "")
                    if raw_t:
                        parsed["text"] = clean_mtext(raw_t)
                if parsed["height"] is None:
                    parsed["height"] = float(getattr(e.dxf, "height", 200))
        except Exception as exc:
            print(f"[cadcraft] inspect fallback warning: {exc}")

    return parsed if (parsed["type"] or parsed["coords"]) else None


def cadcraft_get_vectors(dxf_path: str, max_entities: int = 25000) -> dict[str, Any]:
    """生成或读取极简结构化矢量几何流，用于前端纯矢量 Canvas 模式及图层实时控制。"""
    cache_path = f"{dxf_path}.vectors.json"
    if os.path.isfile(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
                sample_texts = [t[2] for t in cached.get("texts", []) if isinstance(t, list) and len(t) > 2]
                # 若旧缓存存在大面积 ?? 替代符或 surrogate 损坏特征，则自动失效并重新生成
                has_corrupted = any("??" in s or "\ufffd" in s for s in sample_texts[:50])
                if not has_corrupted or not sample_texts:
                    return cached
        except Exception:
            pass

    import ezdxf
    from .cad import clean_mtext
    try:
        # ezdxf.readfile 默认会读取 DXF 头的 $DWGCODEPAGE (如 ANSI_936 -> GBK)，切勿硬编码强制 utf-8
        doc = ezdxf.readfile(dxf_path)
    except Exception:
        try:
            doc = ezdxf.readfile(dxf_path, encoding="gbk")
        except Exception:
            try:
                doc = ezdxf.readfile(dxf_path, encoding="gb18030")
            except Exception:
                doc = ezdxf.readfile(dxf_path, encoding="utf-8")
    msp = doc.modelspace()

    def clean_str(s: Any) -> str:
        if not isinstance(s, str):
            s = str(s or "")
        # 如果包含 Python surrogateescape (0xdc80..0xdcff)，尝试按 GBK / GB18030 逆向挽救
        if any("\udc80" <= ch <= "\udcff" for ch in s):
            try:
                raw_bytes = s.encode("utf-8", "surrogateescape")
                for enc in ("gb18030", "gbk", "cp936", "utf-8"):
                    try:
                        return raw_bytes.decode(enc)
                    except UnicodeDecodeError:
                        continue
            except Exception:
                pass
        return s.encode("utf-8", "replace").decode("utf-8")

    layers_data = []
    layer_map = {}
    for i, l in enumerate(doc.layers):
        name = clean_str(l.dxf.name)
        color = getattr(l.dxf, "color", 7)
        layer_map[l.dxf.name] = i
        layer_map[name] = i
        layers_data.append({"name": name, "color": color, "visible": True})

    lines = []
    texts = []
    polylines = []
    circles = []

    count = 0
    for e in msp:
        if count >= max_entities:
            break
        dxftype = e.dxftype()
        layer_idx = layer_map.get(e.dxf.layer, 0)
        h = str(e.dxf.handle)
        if dxftype == "LINE":
            lines.append([
                round(e.dxf.start.x, 1),
                round(e.dxf.start.y, 1),
                round(e.dxf.end.x, 1),
                round(e.dxf.end.y, 1),
                layer_idx,
                h
            ])
            count += 1
        elif dxftype in ("TEXT", "MTEXT"):
            raw_t = getattr(e.dxf, "text", "")
            pos = getattr(e.dxf, "insert", None)
            height = getattr(e.dxf, "height", 200)
            if pos and raw_t:
                cleaned_text = clean_str(clean_mtext(raw_t))[:80]
                if cleaned_text.strip():
                    texts.append([
                        round(pos.x, 1),
                        round(pos.y, 1),
                        cleaned_text,
                        round(height, 1),
                        layer_idx,
                        h
                    ])
                    count += 1
        elif dxftype == "LWPOLYLINE":
            pts = [[round(p[0], 1), round(p[1], 1)] for p in e.get_points("xy")]
            if 2 <= len(pts) <= 60:
                polylines.append([pts, layer_idx, h])
                count += 1
        elif dxftype == "CIRCLE":
            center = getattr(e.dxf, "center", None)
            radius = getattr(e.dxf, "radius", 0)
            if center and radius > 0:
                circles.append([
                    round(center.x, 1),
                    round(center.y, 1),
                    round(radius, 1),
                    layer_idx,
                    h
                ])
                count += 1
        elif dxftype == "INSERT":
            # 块引用：提取内部属性文字（ATTRIB），常用于开关、箱号、配电箱图例标注
            for att in getattr(e, "attribs", []):
                att_t = getattr(att.dxf, "text", "")
                att_pos = getattr(att.dxf, "insert", None) or getattr(e.dxf, "insert", None)
                att_h = getattr(att.dxf, "height", 200)
                if att_pos and att_t:
                    cleaned_att = clean_str(clean_mtext(att_t))[:80]
                    if cleaned_att.strip():
                        texts.append([
                            round(att_pos.x, 1),
                            round(att_pos.y, 1),
                            cleaned_att,
                            round(att_h, 1),
                            layer_idx,
                            str(att.dxf.handle)
                        ])
                        count += 1

    all_x = [l[0] for l in lines] + [t[0] for t in texts] + [c[0] for c in circles] + [0]
    all_y = [l[1] for l in lines] + [t[1] for t in texts] + [c[1] for c in circles] + [0]
    min_x = min(all_x)
    min_y = min(all_y)
    max_x = max(all_x + [l[2] for l in lines])
    max_y = max(all_y + [l[3] for l in lines])

    data = {
        "extents": [[min_x, min_y], [max_x, max_y]],
        "layers": layers_data,
        "lines": lines,
        "texts": texts,
        "polylines": polylines,
        "circles": circles,
        "total": count
    }
    tmp_path = f"{cache_path}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp_path, cache_path)
    except Exception as exc:
        print("[cadcraft] vectors cache save error:", exc)
        if os.path.isfile(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
    return data

