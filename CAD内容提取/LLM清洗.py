#!/usr/bin/env python3
"""
做法表 LLM 语义清洗 —— DeepSeek API（OpenAI 兼容格式）

用法：
    python3 llm_clean_table.py                    # 处理所有表
    python3 llm_clean_table.py --table 0          # 只处理 S-1#
    python3 llm_clean_table.py --dry-run          # 打印 prompt 不调用 API

环境变量：DEEPSEEK_API_KEY（或自动从 ../本地执行平台/.env 加载）
"""

import json, sys, os, argparse, re
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# ── 配置 ──────────────────────────────
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"  # 非推理模型，速度快；v4-pro 推理太慢
TABLE_JSON_DIR = "output/建筑"
OUTPUT_DIR = "output/建筑"

BUILDING_NAMES = {
    "0": "S-1#楼", "1": "S-2#楼", "2": "S-3#楼", "3": "S-4#楼",
}

BOQ_CONTEXT = """
宿州302项目 BOQ 中与本图纸相关的项：
- 第4项：公共区域地砖（600×600），工程量 4,430 m²（全部楼栋合计）
- 第5项：公共区域墙砖（600×600,600×300），工程量 6,300 m²
- 第1项：室内公共区域吊顶油漆，工程量 1,850 m²
- 第2项：室内公共区域墙面天花油漆，工程量 10,200 m²
"""

SYSTEM_PROMPT = """你是一位装修工程预算员。从 CAD 图纸提取的文本碎片中，整理出结构化的做法表。

## 核心规则
1. **拼接碎片**：CAD 把一句话切成多个 TEXT 片段，必须按语义拼接成完整描述
2. **严格过滤**：只提取地面（地X）和楼面（楼X）做法。内墙（内墙X）、屋面（屋面X）、顶棚（顶X）、外墙（外墙X）等属于其他章节的内容全部忽略！不要混入输出！
3. **构造层次从下到上**：序号1是最底层（素土夯实/结构板），序号递增到面层（地砖/预留装修面层）
4. **部位提取**：从"适用范围：XXX"或"XXX部位"等字段提取部位，必须是字符串
5. **燃烧性能等级**：出现"A级""B1级"标注到对应做法的"燃烧性能等级"字段
6. 不要编造任何不曾出现在文本碎片中的内容
7. 如果某些做法不完整，标注 "完整度": "部分" 或 "完整度": "完整"
8. **编号规范**：编号必须是"地面X"或"楼X"格式，从原文中提取。如原文"地1 地下地面1"→编号"地面1"；"楼1 地上楼面1"→编号"楼1"

## 输出格式（严格遵守！）
<json>
{
  "building": "Y-1#楼",
  "地面做法": [
    {
      "编号": "地面1",
      "部位": "商业",
      "构造层次": [
        {"序号": 1, "做法": "素土夯实", "完整度": "完整"},
        {"序号": 2, "做法": "100厚2:8灰土", "完整度": "完整"}
      ]
    }
  ],
  "楼面做法": [
    {
      "编号": "楼1",
      "部位": "商业",
      "构造层次": [
        {"序号": 1, "做法": "现浇钢筋混凝土楼面板", "完整度": "完整"}
      ]
    }
  ]
}
</json>

注意：部位必须是字符串如"商业"，不能是数组。构造层次中字段名必须是"做法"不能是"描述"。"""


def load_dotenv(dotenv_path: str = None) -> dict:
    """加载 .env 文件，返回键值对 dict"""
    if dotenv_path is None:
        # 自动查找：脚本目录 → 上级目录
        candidates = [
            Path(__file__).parent / ".env",
            Path(__file__).parent.parent / ".env",
            Path(__file__).parent.parent / "本地执行平台" / ".env",
        ]
        dotenv_path = next((p for p in candidates if p.exists()), None)

    env_vars = {}
    if dotenv_path and Path(dotenv_path).exists():
        with open(dotenv_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, val = line.partition("=")
                    env_vars[key.strip()] = val.strip().strip('"').strip("'")
    return env_vars


def get_api_key(args_key: str = None) -> str:
    """获取 API Key，优先级：命令行 > 环境变量 > .env 文件"""
    key = args_key or os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return key

    dotenv = load_dotenv()
    key = dotenv.get("DEEPSEEK_API_KEY")
    if key:
        return key

    return None


def load_table(table_path: str) -> dict:
    """加载做法表 JSON 文件，支持索引号（兼容旧用法）或完整路径"""
    if isinstance(table_path, int) or table_path.isdigit():
        path = Path(TABLE_JSON_DIR) / f"地面楼面做法_{table_path}.json"
    else:
        path = Path(table_path)
    if not path.exists():
        raise FileNotFoundError(f"找不到 {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def detect_format(table_data: dict) -> str:
    """检测表格格式：commercial 或 residential"""
    for section in table_data.values():
        if isinstance(section, dict) and section.get("format") == "residential":
            return "residential"
    return "commercial"


def build_user_prompt(building: str, table_data: dict) -> str:
    parts = [f"## 项目：宿州302 {building}\n"]
    parts.append(BOQ_CONTEXT)

    fmt = detect_format(table_data)
    if fmt == "residential":
        parts.append("\n注意：这是住宅楼格式，多个材料做法表（楼地面/内墙面/屋面/顶棚/外墙面）横向排列在同一高度。")
        parts.append("你的任务是只提取「楼地面」表的内容，忽略内墙X/屋面X/顶X/外墙X的所有行。")
        parts.append("识别方法：")
        parts.append("- 编号为\"地X\"或\"楼X\"的条目属于楼地面表")
        parts.append("- 编号为\"内墙X\"\"屋面X\"\"顶X\"\"外墙X\"的条目属于其他表，跳过")
        parts.append("- 适用范围（如\"住宅厅、房等居室\"\"卫生间\"\"电梯厅\"等）标注到对应做法的部位字段")
        parts.append("- 编号如\"地1 地下地面1\"→简化为\"地面1\"；\"楼1 地上楼面1\"→简化为\"楼1\"")

    for section_name, section in table_data.items():
        rows = section.get("rows", [])
        hx = section.get("header_x", 0)
        hy = section.get("header_y", 0)
        parts.append(f"\n### {section_name}（表头 x={hx:.0f} y={hy:.0f}）\n")
        for i, row in enumerate(rows):
            fragments = " │ ".join(row)
            parts.append(f"行{i}: {fragments}")

    parts.append("\n请输出 <json>...</json>")
    return "\n".join(parts)


def call_deepseek(prompt: str, api_key: str, max_tokens: int = 2048) -> dict:
    """OpenAI 兼容格式调用 DeepSeek API"""
    body = {
        "model": DEEPSEEK_MODEL,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    }

    req = Request(
        DEEPSEEK_API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    print("  调用 DeepSeek API ...")
    try:
        with urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode())
    except HTTPError as e:
        err = e.read().decode()
        raise RuntimeError(f"API 错误 {e.code}: {err[:500]}")
    except URLError as e:
        raise RuntimeError(f"网络错误: {e}")

    # 提取 content（OpenAI 格式）
    choices = result.get("choices", [])
    if not choices:
        raise RuntimeError(f"API 返回无 choices: {json.dumps(result, ensure_ascii=False)[:300]}")

    text = choices[0].get("message", {}).get("content", "")
    if not text:
        raise RuntimeError(f"无文本: {json.dumps(choices[0], ensure_ascii=False)[:300]}")

    # 解析 JSON
    if "<json>" in text and "</json>" in text:
        json_str = text.split("<json>")[1].split("</json>")[0]
    else:
        json_str = text

    # 清理可能的前后空白和 markdown 代码块
    json_str = re.sub(r"^```(?:json)?\s*", "", json_str.strip())
    json_str = re.sub(r"\s*```$", "", json_str)

    return json.loads(json_str)


def normalize_result(result):
    """归一化 LLM 输出到标准格式"""
    if isinstance(result, list):
        # 平铺数组 → 按类型分组的 dict
        normalized = {"地面做法": [], "楼面做法": []}
        for item in result:
            code = str(item.get("编号", ""))
            if code.startswith("地面"):
                normalized["地面做法"].append(_normalize_item(item))
            elif code.startswith("楼"):
                normalized["楼面做法"].append(_normalize_item(item))
        return normalized

    if isinstance(result, dict):
        # 如果顶层有 "做法表" key，取其值
        if "做法表" in result and isinstance(result["做法表"], list):
            normalized = {"地面做法": [], "楼面做法": []}
            for item in result["做法表"]:
                item_type = str(item.get("类型", "")).lower()
                code = str(item.get("编号", ""))
                if "地面" in item_type or code.startswith("地面"):
                    normalized["地面做法"].append(_normalize_item(item))
                elif "楼面" in item_type or code.startswith("楼"):
                    normalized["楼面做法"].append(_normalize_item(item))
            return normalized

        # 如果已经是 {地面做法: [...], 楼面做法: [...]} 格式
        if "地面做法" in result or "楼面做法" in result:
            normalized = {}
            for key in ["地面做法", "楼面做法"]:
                items = result.get(key, [])
                if isinstance(items, list):
                    normalized[key] = [_normalize_item(item) for item in items]
            return normalized

    return {"地面做法": [], "楼面做法": []}


def _normalize_item(item: dict) -> dict:
    """归一化单个做法条目"""
    out = {
        "编号": item.get("编号", ""),
        "部位": _normalize_location(item.get("部位", "")),
    }

    layers = item.get("构造层次", [])
    if isinstance(layers, list):
        out["构造层次"] = []
        for layer in layers:
            if isinstance(layer, str):
                # 字符串格式的构造层次
                out["构造层次"].append({"做法": layer})
            elif isinstance(layer, dict):
                norm_layer = {
                    "序号": layer.get("序号"),
                    "做法": layer.get("做法") or layer.get("描述", ""),
                }
                if layer.get("完整度"):
                    norm_layer["完整度"] = layer["完整度"]
                if layer.get("燃烧性能等级"):
                    norm_layer["燃烧性能等级"] = layer["燃烧性能等级"]
                if layer.get("厚度"):
                    norm_layer["厚度"] = layer["厚度"]
                out["构造层次"].append(norm_layer)

    if item.get("完整度"):
        out["完整度"] = item["完整度"]
    if item.get("燃烧性能等级"):
        out["燃烧性能等级"] = item["燃烧性能等级"]

    return out


def _normalize_location(loc) -> str:
    """确保部位是字符串"""
    if isinstance(loc, list):
        return "、".join(str(x) for x in loc if x)
    return str(loc) if loc else ""


def print_result(result):
    """兼容 dict 和 list 两种输出格式"""
    if isinstance(result, list):
        # 平铺格式：按编号前缀分组显示
        building = "?"
        groups = {}
        for item in result:
            code = item.get("编号", "?")
            prefix = "地面" if code.startswith("地面") else "楼面" if code.startswith("楼") else "其他"
            groups.setdefault(prefix, []).append(item)
        for section_key in ["地面", "楼面", "其他"]:
            items = groups.get(section_key, [])
            if not items:
                continue
            print(f"\n  [{building}] {section_key}做法:")
            for item in items:
                code = item.get("编号", "?")
                loc = item.get("部位", "?")
                name = item.get("名称", "")
                print(f"    {code} [{name}] / {loc}")
                for layer in item.get("构造层次", []):
                    seq = layer.get("序号", "?")
                    desc = layer.get("做法", "")[:120]
                    materials = ", ".join(layer.get("材料", []))
                    integrity = layer.get("完整度", "")
                    extra = f" [{integrity}]" if integrity else ""
                    print(f"      {seq}. {desc}{extra}")
    else:
        building = result.get("building", "?")
        for section_key in ["地面做法", "楼面做法"]:
            items = result.get(section_key, [])
            if not items:
                continue
            print(f"\n  [{building}] {section_key}:")
            for item in items:
                code = item.get("编号", "?")
                loc = item.get("部位", "?")
                print(f"    {code} / {loc}")
                for layer in item.get("构造层次", []):
                    seq = layer.get("序号", "?")
                    desc = layer.get("做法", "")[:120]
                    materials = ", ".join(layer.get("材料", []))
                    print(f"      {seq}. {desc}  [{materials}]")


def find_table_files() -> list:
    """自动发现所有 地面楼面做法_*.json 文件"""
    import glob
    pattern = str(Path(TABLE_JSON_DIR) / "地面楼面做法_*.json")
    files = sorted(glob.glob(pattern))
    # 也扫描 做法表索引 来获取 building 信息
    index_files = sorted(glob.glob(str(Path(TABLE_JSON_DIR) / "*_做法表索引.json")))
    return files, index_files


def guess_building_name(file_path: str, index_files: list) -> str:
    """从文件名或索引文件推断楼栋名称"""
    stem = Path(file_path).stem  # e.g. "地面楼面做法_0"
    # 尝试从索引文件中找到对应的 building
    for idx_f in index_files:
        with open(idx_f, encoding="utf-8") as f:
            idx_data = json.load(f)
        for key, sec in idx_data.get("sections", {}).items():
            if sec.get("file") == file_path or Path(sec.get("file", "")).name == Path(file_path).name:
                return idx_data.get("building", stem)
    # 回退：从文件名提取
    parts = stem.replace("地面楼面做法_", "")
    return BUILDING_NAMES.get(parts, f"表{parts}")


def main():
    parser = argparse.ArgumentParser(description="LLM 清洗做法表（DeepSeek）")
    parser.add_argument("--table", type=str, default=None, help="指定表索引号 或 JSON 文件路径")
    parser.add_argument("--file", type=str, default=None, help="直接指定 地面楼面做法_X.json 路径")
    parser.add_argument("--building", type=str, default=None, help="楼栋名称")
    parser.add_argument("--dry-run", action="store_true", help="只打印 prompt 不调用 API")
    parser.add_argument("--api-key", default=None, help="DeepSeek API Key")
    args = parser.parse_args()

    api_key = get_api_key(args.api_key)
    if not api_key and not args.dry_run:
        print("请设置 DEEPSEEK_API_KEY 环境变量，或传 --api-key，或在上级目录放置 .env 文件")
        sys.exit(1)

    # 确定要处理的文件列表
    table_files, index_files = find_table_files()

    if args.file:
        targets = [args.file]
    elif args.table:
        # 支持索引号或直接路径
        targets = [args.table]
    elif table_files:
        targets = table_files
    else:
        # 回退：兼容旧的 S-1~S-4 (0-3)
        targets = list(range(4))

    for target in targets:
        try:
            table_data = load_table(target)
        except FileNotFoundError:
            print(f"跳过（文件不存在）: {target}")
            continue
        if not table_data:
            continue

        building = args.building or guess_building_name(
            str(target) if isinstance(target, str) else f"地面楼面做法_{target}.json",
            index_files
        )
        prompt = build_user_prompt(building, table_data)

        if args.dry_run:
            print(f"\n{'='*60}")
            print(f"{building} - Prompt ({len(prompt)} 字符)")
            print(f"{'='*60}")
            print(prompt[:4000])
            if len(prompt) > 4000:
                print(f"\n... 共 {len(prompt)} 字符")
            continue

        print(f"\n{'='*60}")
        print(f"{building}")

        try:
            raw_result = call_deepseek(prompt, api_key, max_tokens=4096)
            result = normalize_result(raw_result)
            result["building"] = building

            # 输出文件名
            if isinstance(target, str):
                stem = Path(target).stem.replace("地面楼面做法_", "")
            else:
                stem = str(target)
            out_path = Path(OUTPUT_DIR) / f"做法表_结构化_{stem}.json"
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(f"  已保存: {out_path}")
            print_result(result)
        except Exception as e:
            print(f"  失败: {e}")


if __name__ == "__main__":
    main()
