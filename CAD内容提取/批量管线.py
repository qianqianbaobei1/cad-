#!/usr/bin/env python3
"""
批量处理全部建筑 DWG → 五类做法表提取 → LLM 语义清洗 → BOQ 匹配汇总

覆盖：地面做法、楼面做法、内墙做法、外墙做法、顶棚做法
匹配 BOQ 全部 6 项工程量清单

前提：brew install libredwg + DEEPSEEK_API_KEY

用法：
    python3 批量管线.py                               # 自动检测项目，全部楼栋
    python3 批量管线.py --project 宿州302              # 指定项目
    python3 批量管线.py --building Y-1#                # 只处理指定楼栋
    python3 批量管线.py --skip-llm                     # 只提取表格
    python3 批量管线.py --dry-run                      # 只列出文件
"""

import json, sys, os, subprocess, re, argparse, time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from collections import defaultdict

# ══════════════════════════════════════════════════════════════
# 项目检测
# ══════════════════════════════════════════════════════════════

SCRIPT_DIR = Path(__file__).parent
PROJECTS_DIR = SCRIPT_DIR / "projects"

def detect_project(project_name: str = None) -> Path:
    """检测或选择项目目录"""
    if project_name:
        p = PROJECTS_DIR / project_name
        if p.exists(): return p
        raise FileNotFoundError(f"项目不存在: {p}")
    # 自动选第一个
    projects = sorted(d for d in PROJECTS_DIR.iterdir() if d.is_dir())
    if not projects:
        raise FileNotFoundError(f"未找到项目，请在 {PROJECTS_DIR} 下创建项目目录")
    return projects[0]

# 运行时解析（main 里重新赋值）
PROJECT = None
CAD_DIR = None
OUTPUT_CACHE = None       # 解析缓存/dump/
OUTPUT_EXTRACT = None     # 提取结果/做法表/
OUTPUT_FINAL = None       # 输出产物/

def setup_paths(project: Path):
    global PROJECT, CAD_DIR, OUTPUT_CACHE, OUTPUT_EXTRACT, OUTPUT_FINAL
    PROJECT = project
    CAD_DIR = SCRIPT_DIR / "宿州302项目cad/1.建筑"
    OUTPUT_CACHE = project / "解析缓存/dump"
    OUTPUT_EXTRACT = project / "提取结果/做法表"
    OUTPUT_FINAL = project / "输出产物"
    for d in [OUTPUT_CACHE, OUTPUT_EXTRACT, OUTPUT_FINAL]:
        d.mkdir(parents=True, exist_ok=True)

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
TIMEOUT_DWGREAD = 300
ROW_Y_TOLERANCE = 600
CELL_X_TOLERANCE = 300

BUILDING_MAP = {
    "S-1#": "S-1号楼", "S-2#": "S-2号楼", "S-3#": "S-3号楼",
    "S-4#": "S-4号楼", "S-5#": "S-5号楼",
    "Y-1#": "Y-1号楼", "Y-2#": "Y-2号楼", "Y-3#": "Y-3号楼",
    "Y-4#": "Y-4号楼", "Y-5#": "Y-5号楼", "Y-6#": "Y-6号楼",
    "Y-7#": "Y-7号楼", "Y-8#": "Y-8号楼", "Y-9#": "Y-9号楼",
    "Y-10#": "Y-10号楼", "Y-11#": "Y-11号楼",
}

BOQ_ITEMS = [
    {"序号": 1, "名称": "室内公共区域吊顶油漆", "规格": "腻子两遍打磨平整涂刷乳胶漆", "量": 1850, "单位": "m²", "匹配类别": "顶棚做法"},
    {"序号": 2, "名称": "室内公共区域墙面天花油漆", "规格": "腻子3遍+石膏找平+乳胶漆3遍", "量": 10200, "单位": "m²", "匹配类别": "内墙做法/顶棚做法"},
    {"序号": 3, "名称": "室内户内天花油漆", "规格": "2遍腻子", "量": 36450, "单位": "m²", "匹配类别": "顶棚做法"},
    {"序号": 4, "名称": "公共区域地砖", "规格": "600×600", "量": 4430, "单位": "m²", "匹配类别": "地面做法/楼面做法"},
    {"序号": 5, "名称": "公共区域墙砖", "规格": "600×600, 600×300", "量": 6300, "单位": "m²", "匹配类别": "内墙做法/外墙做法"},
    {"序号": 6, "名称": "公共区域吊顶", "规格": "公共区域吊顶", "量": 2100, "单位": "m²", "匹配类别": "顶棚做法"},
]

# 五类做法表
ALL_CATEGORIES = ["地面做法", "楼面做法", "内墙做法", "外墙做法", "顶棚做法"]

# System Prompt 模板（运行时填充目标类别）
SYSTEM_PROMPT_GROUND = """你是装修工程预算员。从CAD图纸文本碎片整理结构化的地面和楼面做法表。

核心规则：
1. 拼接碎片：CAD把一句话切成多个TEXT片段，必须按语义拼接成完整描述
2. 严格过滤：只提取地面（地X/地面X）和楼面（楼X）做法。内墙X、屋面X、顶X、外墙X等属于其他章节，全部忽略！
3. 构造层次从下到上：序号1是最底层（素土夯实/结构板），序号递增到面层（地砖/面层）
4. 部位提取：从"适用范围：XXX"等字段提取部位，必须是字符串
5. 燃烧性能等级：A级/B1级标注到对应做法
6. 不编造内容；不完整标注"完整度":"部分"或"完整度":"完整"
7. 编号如"地1 地下地面1"→"地面1"；"楼1 地上楼面1"→"楼1"

输出格式（严格遵守）：
<json>
{"地面做法": [{"编号":"地面1","部位":"商业","构造层次":[{"序号":1,"做法":"素土夯实","完整度":"完整"}]}],
 "楼面做法": [{"编号":"楼1","部位":"商业","构造层次":[{"序号":1,"做法":"现浇钢筋混凝土楼面板","完整度":"完整"}]}]}
</json>
部位必须是字符串不能是数组。构造层次用"做法"不用"描述"。
"""

SYSTEM_PROMPT_WALL = """你是装修工程预算员。从CAD图纸文本碎片整理结构化的内墙和外墙做法表。

核心规则：
1. 拼接碎片：CAD把一句话切成多个TEXT片段，必须按语义拼接成完整描述
2. 严格过滤：只提取内墙（内墙X）和外墙（外墙X）做法。地面X、楼面X、屋面X、顶X等全部忽略！
3. 构造层次从底到面：序号1=基层墙体，递增到面层（涂料/面砖）
4. 部位提取：从"适用范围：XXX"提取部位，必须字符串
5. 燃烧性能等级：A级/B1级标注到对应做法
6. 不编造内容；不完整标注"完整度":"部分"或"完整度":"完整"
7. "(有保温)""(无保温)""(分户墙)""(非分户墙)"等标注保留在做法描述中

输出格式：
<json>
{"内墙做法": [{"编号":"内墙1","部位":"住宅厅、房等居室","构造层次":[{"序号":1,"做法":"基层墙体清扫干净","完整度":"完整"}]}],
 "外墙做法": [{"编号":"外墙1","部位":"仿石涂料外墙","构造层次":[{"序号":1,"做法":"基层墙体","完整度":"完整"}]}]}
</json>
"""

SYSTEM_PROMPT_CEILING = """你是装修工程预算员。从CAD图纸文本碎片整理结构化的顶棚做法表。

核心规则：
1. 拼接碎片：CAD把一句话切成多个TEXT片段，按语义拼接
2. 严格过滤：只提取顶棚（顶X）做法。地面X、楼面X、内墙X、外墙X、屋面X全部忽略！
3. 构造层次：序号1=结构板底，递增到面层（涂料/吊顶）
4. 部位提取：从"适用范围：XXX"提取部位，必须字符串
5. 燃烧性能等级：A级/B1级标注
6. 不编造内容；标注完整度
7. 编号如"顶1 地下顶棚1"→"顶棚1"

输出格式：
<json>
{"顶棚做法": [{"编号":"顶棚1","部位":"地下电梯厅","构造层次":[{"序号":1,"做法":"现浇钢筋混凝土楼面板","完整度":"完整"}]}]}
</json>
"""

# 住宅楼五个材料做法表 → 对应的 LLM prompt 类型
RESIDENTIAL_TABLES = {
    "材料做法表（楼地面）": "ground",
    "材料做法表（内墙面）": "wall",
    "材料做法表（外墙面）": "wall",
    "材料做法表（顶棚）": "ceiling",
}

# 商业楼章节名 → LLM prompt 类型
COMMERCIAL_SECTION_MAP = {
    "地面做法": "ground", "楼面做法": "ground",
    "内墙做法": "wall", "外墙做法": "wall",
    "顶棚做法": "ceiling",
}


# ══════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════

def get_api_key():
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key: return key
    for p in [Path(__file__).parent.parent / ".env",
              Path(__file__).parent.parent / "本地执行平台" / ".env"]:
        if p.exists():
            for line in open(p, encoding="utf-8"):
                if line.strip().startswith("DEEPSEEK_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def extract_building_id(filename: str) -> str:
    for key, val in BUILDING_MAP.items():
        if key in filename: return val
    return Path(filename).stem[:20]


def cluster_rows(texts, tolerance=ROW_Y_TOLERANCE):
    if not texts: return []
    sorted_t = sorted(texts, key=lambda t: -t["y"])
    rows, cur, cur_y = [], [sorted_t[0]], sorted_t[0]["y"]
    for t in sorted_t[1:]:
        if abs(t["y"] - cur_y) <= tolerance: cur.append(t)
        else:
            rows.append(sorted(cur, key=lambda x: x["x"]))
            cur, cur_y = [t], t["y"]
    rows.append(sorted(cur, key=lambda x: x["x"]))
    return rows


def merge_cells(row, tolerance=CELL_X_TOLERANCE):
    if not row: return []
    cells, cur = [], [row[0]]
    for t in row[1:]:
        if abs(t["x"] - cur[-1]["x"]) <= tolerance: cur.append(t)
        else:
            cells.append("".join(c["text"] for c in cur))
            cur = [t]
    cells.append("".join(c["text"] for c in cur))
    return cells


# ══════════════════════════════════════════════════════════════
# Step 1: DWG → JSON
# ══════════════════════════════════════════════════════════════

def dwg_to_json(dwg_path: Path) -> Path:
    json_path = OUTPUT_CACHE / f"{dwg_path.stem}_dump.json"
    if json_path.exists() and dwg_path.exists():
        if json_path.stat().st_mtime > dwg_path.stat().st_mtime:
            print(f"  JSON缓存有效，跳过")
            return json_path
    size_mb = dwg_path.stat().st_size / (1024**2)
    print(f"  dwgread ({size_mb:.1f}MB)...", end=" ", flush=True)
    t0 = time.time()
    subprocess.run(["dwgread", "-O", "JSON", str(dwg_path), "-o", str(json_path)],
                   capture_output=True, timeout=TIMEOUT_DWGREAD, check=True)
    print(f"{time.time()-t0:.0f}s → {json_path.stat().st_size/(1024**2):.0f}MB")
    return json_path


# ══════════════════════════════════════════════════════════════
# Step 2: 提取 TEXT 实体
# ══════════════════════════════════════════════════════════════

def load_text_entities(json_path: Path) -> list:
    with open(json_path, encoding="utf-8", errors="replace") as f:
        data = json.load(f)
    layer_map = {}
    for obj in data.get("OBJECTS", []):
        if isinstance(obj, dict) and "name" in obj and "handle" in obj:
            h = tuple(obj.get("handle", []))
            if h: layer_map[h] = str(obj["name"])
    texts = []
    for obj in data.get("OBJECTS", []):
        if not isinstance(obj, dict): continue
        if obj.get("entity") != "TEXT" or "text_value" not in obj: continue
        ins = obj.get("ins_pt")
        if not ins or len(ins) < 2: continue
        texts.append({"text": obj["text_value"], "x": ins[0], "y": ins[1]})
    texts.sort(key=lambda t: (-t["y"], t["x"]))
    return texts


# ══════════════════════════════════════════════════════════════
# Step 3: 坐标表格重建（全覆盖：地面+楼面+内墙+外墙+顶棚）
# ══════════════════════════════════════════════════════════════

def extract_all_tables(texts, building_id: str) -> dict:
    """提取所有五类做法表。返回按 LLM prompt 类型分组的 dict。"""
    results = {"ground": {}, "wall": {}, "ceiling": {}}

    # ── 住宅楼格式：五个「材料做法表」横向排列 ──
    residential_anchors = []
    for t in texts:
        for table_name, prompt_type in RESIDENTIAL_TABLES.items():
            # 兼容有无空格：「材料做法表（楼地面）」和「材料做法表 （楼地面）」
            txt_clean = t["text"].replace(" ", "")
            tbl_clean = table_name.replace(" ", "")
            if tbl_clean in txt_clean:
                residential_anchors.append((prompt_type, table_name, t))
                break

    for prompt_type, table_name, anchor in residential_anchors:
        ax, ay = anchor["x"], anchor["y"]
        region = [t for t in texts
                  if ay - 50000 <= t["y"] <= ay + 5000
                  and ax - 18000 <= t["x"] <= ax + 18000]
        rows = cluster_rows(region)
        data_rows = []
        for row in rows:
            cells = merge_cells(row)
            combined = "".join(cells).strip()
            skip_titles = ["材料做法表", "《室内外装修做法表》", "室内外装修做法表"]
            if any(combined == s or combined.replace(" ", "") == s.replace(" ", "")
                   for s in skip_titles):
                continue
            if len(combined) < 3: continue
            data_rows.append(cells)

        if data_rows:
            key = table_name.replace(" ", "")
            results[prompt_type][key] = {
                "header_x": ax, "header_y": ay, "rows": data_rows, "format": "residential",
            }
            print(f"    → {table_name}: {len(data_rows)}行 (x={ax:.0f})")

    # ── 商业楼格式：单张室内外装修做法表，内含各章节 ──
    for t in texts:
        if "室内外装修做法表" not in t["text"]: continue
        ax, ay = t["x"], t["y"]
        region = [r for r in texts
                  if ay - 50000 <= r["y"] <= ay + 10000
                  and abs(r["x"] - ax) < 150000]

        # 找各章节标题
        sections = {}
        for kw in ["地面做法", "楼面做法", "内墙做法", "外墙做法", "顶棚做法"]:
            candidates = [r for r in region if kw in r["text"]]
            if candidates:
                sections[kw] = (sum(r["x"] for r in candidates) / len(candidates),
                               max(r["y"] for r in candidates))

        for sec_name, (sx, sy) in sections.items():
            prompt_type = COMMERCIAL_SECTION_MAP.get(sec_name, "ground")
            col_texts = [r for r in region
                         if sy - 65000 <= r["y"] <= sy + 2000
                         and sx - 40000 <= r["x"] <= sx + 100000]
            rows = cluster_rows(col_texts)
            merged = [merge_cells(r) for r in rows]

            data_rows = []
            found_header = False
            for row in merged:
                rt = "".join(row)
                if "编号" in rt and ("分层做法" in rt or "做法" in rt):
                    found_header = True; continue
                if found_header and not any("编号" in c for c in row):
                    if any(f"{c}、" in rt for c in "一二三四五六七八九十"): break
                    if any(w in rt for w in ["1:100", "建筑设计总说明"]): continue
                    data_rows.append(row)

            if data_rows:
                results[prompt_type][sec_name] = {
                    "header_x": sx, "header_y": sy, "rows": data_rows, "format": "commercial",
                }
                print(f"    → {sec_name}: {len(data_rows)}行")

    return results


# ══════════════════════════════════════════════════════════════
# Step 4: LLM 语义清洗（分三类调用：地面+楼面 / 内墙+外墙 / 顶棚）
# ══════════════════════════════════════════════════════════════

def build_prompt(building: str, sections: dict, prompt_type: str) -> str:
    lines = [f"## 项目：宿州302 {building}\n"]
    type_name = {"ground": "地面/楼面", "wall": "内墙/外墙", "ceiling": "顶棚"}[prompt_type]
    lines.append(f"你要提取的是：{type_name}做法。请忽略不属于{type_name}的内容。")
    lines.append("\nBOQ关联:")
    for item in BOQ_ITEMS:
        if prompt_type in item.get("匹配类别", ""):
            lines.append(f"  - 第{item['序号']}项：{item['名称']} {item['规格']} {item['量']}{item['单位']}")
    lines.append("")

    has_residential = False
    for sec_name, sec_data in sections.items():
        rows = sec_data.get("rows", [])
        if not rows: continue
        if sec_data.get("format") == "residential": has_residential = True
        lines.append(f"\n### {sec_name} ({len(rows)}行)")
        for i, row in enumerate(rows):
            lines.append(f"行{i}: {' │ '.join(row)}")

    if has_residential:
        lines.insert(3, "注意：这是住宅楼格式，多个材料做法表横向排列在同一高度。你只提取当前类别的做法，严格忽略其他类别。")

    lines.append("\n请输出 <json>...</json>")
    return "\n".join(lines)


def call_llm(prompt: str, api_key: str, system_prompt: str, max_retries: int = 2) -> dict:
    body = {
        "model": DEEPSEEK_MODEL, "max_tokens": 4096,
        "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}],
    }
    for attempt in range(max_retries + 1):
        if attempt > 0:
            print(f"(重试{attempt})...", end=" ", flush=True)
            body["messages"].append({
                "role": "user",
                "content": "上次JSON格式有误，请严格按<json>...</json>重新输出，确保JSON完整闭合。"
            })
        req = Request(DEEPSEEK_URL, data=json.dumps(body).encode(),
                      headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
        with urlopen(req, timeout=180) as resp:
            result = json.loads(resp.read().decode())
        text = result["choices"][0]["message"]["content"]
        if "<json>" in text: text = text.split("<json>")[1].split("</json>")[0]
        text = re.sub(r"^```(?:json)?\s*", "", text.strip())
        text = re.sub(r"\s*```$", "", text)
        text = re.sub(r',\s*}', '}', text)
        text = re.sub(r',\s*]', ']', text)
        try: return json.loads(text)
        except json.JSONDecodeError:
            if attempt < max_retries: continue
    return {}


def normalize(raw: dict, building: str) -> dict:
    """归一化 LLM 输出"""
    norm = {"building": building}
    for cat in ALL_CATEGORIES: norm[cat] = []
    if isinstance(raw, list):
        for item in raw:
            code = str(item.get("编号", ""))
            for cat in ALL_CATEGORIES:
                if cat.replace("做法", "") in code or code.startswith(cat[:2]):
                    norm[cat].append(_norm_item(item)); break
            else: norm["内墙做法"].append(_norm_item(item))  # fallback
    elif isinstance(raw, dict):
        for cat in ALL_CATEGORIES:
            if cat in raw and isinstance(raw[cat], list):
                norm[cat] = [_norm_item(i) for i in raw[cat]]
    return norm


def _norm_item(item):
    out = {"编号": item.get("编号", ""), "部位": _norm_loc(item.get("部位", ""))}
    for l in (item.get("构造层次", []) or []):
        if "构造层次" not in out: out["构造层次"] = []
        if isinstance(l, str): out["构造层次"].append({"做法": l})
        elif isinstance(l, dict):
            nl = {"序号": l.get("序号"), "做法": l.get("做法") or l.get("描述", "")}
            for k in ["完整度", "燃烧性能等级", "厚度"]:
                if l.get(k): nl[k] = l[k]
            out["构造层次"].append(nl)
    if item.get("完整度"): out["完整度"] = item["完整度"]
    return out


def _norm_loc(loc):
    if isinstance(loc, list): return "、".join(str(x) for x in loc if x)
    return str(loc) if loc else ""


# ══════════════════════════════════════════════════════════════
# Step 5: 汇总
# ══════════════════════════════════════════════════════════════

def generate_summary(all_results: dict):
    print("\n" + "=" * 70)
    print("BOQ 全部六项匹配汇总")
    print("=" * 70)

    counts = {cat: sum(len(v.get(cat, [])) for v in all_results.values()) for cat in ALL_CATEGORIES}
    print(f"\n{len(all_results)}栋楼 | " + " | ".join(f"{k}:{v}" for k, v in counts.items() if v > 0))

    boq_match = {
        1: {"categories": ["顶棚做法"], "kw": ["涂料", "乳胶漆", "油漆", "腻子", "无机涂料"]},
        2: {"categories": ["内墙做法", "顶棚做法"], "kw": ["涂料", "乳胶漆", "油漆", "腻子", "石膏"]},
        3: {"categories": ["顶棚做法"], "kw": ["腻子", "涂料", "油漆"]},
        4: {"categories": ["地面做法", "楼面做法"], "kw": ["地砖", "面砖"]},
        5: {"categories": ["内墙做法", "外墙做法"], "kw": ["墙砖", "面砖", "瓷砖"]},
        6: {"categories": ["顶棚做法"], "kw": ["吊顶", "龙骨", "石膏板"]},
    }

    for item in BOQ_ITEMS:
        cfg = boq_match[item["序号"]]
        matched = []
        for bld, data in sorted(all_results.items()):
            for cat in cfg["categories"]:
                for it in data.get(cat, []):
                    for l in reversed(it.get("构造层次", []) or []):
                        desc = l.get("做法", "")
                        if any(kw in desc for kw in cfg["kw"]):
                            matched.append((bld, cat, it["编号"], it["部位"], desc[:80]))
                            break

        status = "✅" if matched else "⚠"
        print(f"\n{status} BOQ第{item['序号']}项「{item['名称']}」{item['量']}{item['单位']}")
        print(f"   匹配: {len(matched)} 条 → 覆盖 {len(set(m[0] for m in matched))} 栋楼")
        for bld, cat, code, loc, desc in matched[:10]:
            print(f"     {bld} {code} / {loc} → {desc}")
        if len(matched) > 10:
            print(f"     ... 共 {len(matched)} 条")

    # 保存
    summary = {"项目": "宿州302", "生成时间": time.strftime("%Y-%m-%d %H:%M:%S"),
               "覆盖楼栋": len(all_results),
               "各类别数量": counts,
               "各楼栋数据": all_results}
    sp = OUTPUT_FINAL / "全部汇总.json"
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n汇总: {sp}")


# ══════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=None, help="项目名称（默认自动检测）")
    parser.add_argument("--building", default=None)
    parser.add_argument("--start", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-llm", action="store_true")
    parser.add_argument("--api-key", default=None)
    args = parser.parse_args()

    # 项目路径设置
    setup_paths(detect_project(args.project))
    print(f"项目: {PROJECT.name}  |  图纸: {CAD_DIR}  |  输出: {OUTPUT_FINAL}")

    # 文件收集（自然排序）
    import re as _re
    def _natural_key(path):
        m = _re.search(r'[SY]-\d+#?', path.name)
        if m: return (m.group()[0], int(_re.search(r'\d+', m.group()).group()))
        return (path.name, 0)
    dwg_files = sorted(CAD_DIR.glob("*.dwg"), key=_natural_key)
    dwg_files = [f for f in dwg_files if "总平面" not in f.name]

    if args.building:
        dwg_files = [f for f in dwg_files if args.building in f.name]
    elif args.start:
        found = False
        filtered = []
        for f in dwg_files:
            if args.start in f.name: found = True
            if found: filtered.append(f)
        dwg_files = filtered

    if args.dry_run:
        print(f"将处理 {len(dwg_files)} 个文件:")
        for f in dwg_files:
            print(f"  {extract_building_id(f.name):12s} ← {f.name[:70]}")
        return

    api_key = args.api_key or get_api_key()
    if not api_key and not args.skip_llm:
        print("⚠ 未找到 DEEPSEEK_API_KEY，将跳过LLM清洗")

    all_results = {}
    success, skip = 0, 0

    for i, dwg_path in enumerate(dwg_files):
        building_id = extract_building_id(dwg_path.name)
        print(f"\n{'─'*50}")
        print(f"[{i+1}/{len(dwg_files)}] {building_id}")

        try:
            json_path = dwg_to_json(dwg_path)
            texts = load_text_entities(json_path)

            # 检查是否有做法表
            if not any("室内外装修做法表" in t["text"] or "材料做法表" in t["text"] for t in texts):
                print(f"  无做法表，跳过")
                skip += 1; continue

            # 提取所有五类表格
            sections_by_type = extract_all_tables(texts, building_id)
            total_rows = sum(len(s.get("rows", [])) for sections in sections_by_type.values() for s in sections.values())
            if total_rows == 0:
                print(f"  未提取到任何章节")
                skip += 1; continue

            # 保存中间结果
            mid_path = OUTPUT_CACHE.parent / f"全做法表_{building_id}.json"
            # 转换 sections_by_type 为可序列化格式
            serializable = {k: {sk: {"header_x": sv.get("header_x",0), "header_y": sv.get("header_y",0),
                                    "rows": sv.get("rows",[]), "format": sv.get("format","")}
                               for sk, sv in v.items()} for k, v in sections_by_type.items()}
            with open(mid_path, "w", encoding="utf-8") as f:
                json.dump(serializable, f, ensure_ascii=False, indent=2)

            # LLM清洗：分三类调用
            if api_key and not args.skip_llm:
                llm_configs = [
                    ("ground", SYSTEM_PROMPT_GROUND, ["地面做法", "楼面做法"]),
                    ("wall", SYSTEM_PROMPT_WALL, ["内墙做法", "外墙做法"]),
                    ("ceiling", SYSTEM_PROMPT_CEILING, ["顶棚做法"]),
                ]
                combined_result = {"building": building_id}
                for cat in ALL_CATEGORIES: combined_result[cat] = []

                for prompt_type, sys_prompt, target_cats in llm_configs:
                    sections = sections_by_type.get(prompt_type, {})
                    if not sections:
                        print(f"  {prompt_type}: 无数据，跳过")
                        continue
                    if not any(s.get("rows") for s in sections.values()):
                        print(f"  {prompt_type}: 空表格，跳过")
                        continue

                    prompt = build_prompt(building_id, sections, prompt_type)
                    print(f"  LLM {prompt_type} (prompt {len(prompt)}字)...", end=" ", flush=True)
                    try:
                        raw = call_llm(prompt, api_key, sys_prompt)
                        norm = normalize(raw, building_id)
                        for cat in target_cats:
                            if cat in norm and norm[cat]:
                                combined_result[cat].extend(norm[cat])
                        counts_str = ", ".join(f"{cat}:{len(combined_result.get(cat,[]))}" for cat in target_cats)
                        print(f"→ {counts_str}")
                    except Exception as e:
                        print(f"失败: {e}")

                # 保存
                out_path = OUTPUT_FINAL / f"做法表_{building_id}.json"
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(combined_result, f, ensure_ascii=False, indent=2)
                all_results[building_id] = combined_result
                total = sum(len(combined_result.get(c, [])) for c in ALL_CATEGORIES)
                print(f"  保存: {out_path.name} ({total}条)")

            success += 1
        except subprocess.TimeoutExpired:
            print(f"  ✗ dwgread 超时")
        except Exception as e:
            print(f"  ✗ 失败: {e}")

    print(f"\n{'='*70}")
    print(f"完成: {success} 成功, {skip} 跳过")
    if all_results:
        generate_summary(all_results)
    print(f"\n输出: {OUTPUT_FINAL}")


if __name__ == "__main__":
    main()
