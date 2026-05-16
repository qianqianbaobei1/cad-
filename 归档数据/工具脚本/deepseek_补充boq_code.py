#!/usr/bin/env python3
"""用 DeepSeek 为 Q1 中 boq_code 为空的定额条目推断最匹配的 BOQ 清单编码。

流程：
1. 读取 Q1 中 boq_code 为空的条目（含 project_name/chapter/spec/materials）
2. 按章节分组，每批 15-25 条
3. 为每批构造 prompt（包含对应 Q0 附录的编码列表）
4. 调用 DeepSeek API 推理
5. 解析 JSON 响应，验证 boq_code 合法性
6. 更新 CSV + JSON 索引
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
源数据 = ROOT / "标准知识库" / "源数据" / "01_定额库"
索引目录 = ROOT / "项目数据" / "本地知识库包" / "索引"

Q1_CSV = 源数据 / "Q1_定额索引.csv"
Q2_CSV = 源数据 / "Q2_定额材料消耗.csv"
Q0_安装 = 源数据 / "Q0_清单项目编码_安装工程.csv"
Q0_房建 = 源数据 / "Q0_清单项目编码_房建工程.csv"
Q1_JSON = 索引目录 / "q1_按ID.json"

BATCH_SIZE = 20
MAX_TOKENS = 16000
TEMPERATURE = 0.2


# ── Chapter → Q0 Appendix mapping ──
# Q1 chapter_name → (Q0文件名, Q0附录名称)
CHAPTER_TO_Q0 = {
    "建筑智能化工程": ("安装", "建筑智能化工程"),
    "自动化控制仪表安装工程": ("安装", "自动化控制仪表安装工程"),
    "通风空调工程": ("安装", "通风空调工程"),
    "热力设备安装工程": ("安装", "热力设备安装工程"),
    "机械设备安装工程": ("安装", "机械设备安装工程"),
    "刷油、防腐蚀、绝热工程": ("安装", "刷油、防腐蚀、绝热工程"),
    "电气设备安装工程": ("安装", "电气设备安装工程"),
    "给排水、采暖、燃气安装工程": ("安装", "给排水、采暖、燃气工程"),
    "消防工程": ("安装", "消防工程"),
    "工业管道工程": ("安装", "工业管道工程"),
    "静置设备与工艺金属结构制作安装工程": ("安装", "静置设备与工艺金属结构制作安装工程"),
    "通信设备及线路工程": ("安装", "通信设备及线路工程"),
    "阴极保护工程": ("安装", "刷油、防腐蚀、绝热工程"),
    "措施项目(超高增加)": ("安装", "措 施 项 目"),
    "其他机械安装及设备灌浆": ("安装", "机械设备安装工程"),
    # 房建
    "土石方工程": ("房建", "土石方工程"),
    "地基处理与边坡支护工程": ("房建", "地基处理与边坡支护工程"),
    "地基处理和基坑支护工程": ("房建", "地基处理与边坡支护工程"),
    "保温隔热防腐工程": ("房建", "保温、隔热、防腐工程"),
    "混凝土及钢筋混凝土工程": ("房建", "混凝土及钢筋混凝土工程"),
    "金属结构工程": ("房建", "金属结构工程"),
    "木结构工程": ("房建", "木结构工程"),
    "砌筑工程": ("房建", "砌 筑 工 程"),
    "楼地面装饰工程": ("房建", "楼地面装饰工程"),
    "墙、柱面装饰与隔断、幕墙工程": ("房建", "墙、柱面装饰与隔断、幕墙工程"),
    "天棚工程": ("房建", "天 棚 工 程"),
    "门窗工程": ("房建", "门 窗 工 程"),
    "油漆、涂料、裱糊工程": ("房建", "油漆、涂料、裱糊工程"),
    "其他装饰工程": ("房建", "其他装饰工程"),
    "屋面及防水工程": ("房建", "屋面及防水工程"),
    "桩基工程": ("房建", "桩 基 工 程"),
    "其他及附属工程": ("安装", "其他及附属工程"),
}


def load_q0_reference() -> dict[str, dict[str, list[str]]]:
    """加载 Q0 编码参考：{appendix: {section: [code name]}}"""
    ref = defaultdict(lambda: defaultdict(list))
    for q0_path, _label in [(Q0_安装, "安装"), (Q0_房建, "房建")]:
        with open(q0_path, "r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                code = row.get("项目编码", "").strip()
                name = row.get("项目名称", "").strip()
                appendix = row.get("附录名称", "").strip()
                section = row.get("分部名称", "").strip()
                if code and len(code) == 9 and appendix:
                    ref[appendix][section].append(f"{code} {name}")
    return ref


def load_empty_entries() -> list[dict]:
    """加载 Q1 中 boq_code 为空的条目，附带完整上下文。"""
    # 先加载 Q2 material_name_raw
    empty_ids = set()
    with open(Q1_CSV, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if not row.get("boq_code", "").strip():
                empty_ids.add(row["quota_id"].strip())

    q2_mats = defaultdict(list)
    with open(Q2_CSV, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            qid = row.get("quota_id", "").strip()
            if qid in empty_ids:
                mn = row.get("material_name_raw", "").strip()
                if mn:
                    q2_mats[qid].append(mn)

    entries = []
    with open(Q1_CSV, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if not row.get("boq_code", "").strip():
                qid = row["quota_id"].strip()
                entries.append({
                    "quota_id": qid,
                    "province": row.get("province", ""),
                    "chapter_name": (row.get("chapter_name", "")
                                     or row.get("章名称", "")
                                     or row.get("chapter", "")).strip(),
                    "section_name": (row.get("section_name", "")
                                     or row.get("节名称", "")).strip(),
                    "project_name": (row.get("project_name", "")
                                     or row.get("项目名称", "")).strip(),
                    "project_spec": (row.get("project_spec", "")
                                     or row.get("项目规格", "")).strip(),
                    "unit": (row.get("unit", "")
                             or row.get("定额单位", "")).strip(),
                    "work_content": (row.get("work_content", "")
                                     or row.get("工作内容", "")).strip(),
                    "materials": q2_mats.get(qid, [])[:15],
                })
    return entries


def format_q0_for_chapter(appendix: str, q0_ref: dict, max_items: int = 200) -> str:
    """为给定附录格式化 Q0 编码参考文本。"""
    if appendix not in q0_ref:
        # 尝试模糊匹配
        for key in q0_ref:
            if _similar(key, appendix) > 0.6:
                appendix = key
                break
        else:
            # 返回相关章节的编码
            lines = []
            for app, sections in q0_ref.items():
                if _similar(app, appendix) > 0.4:
                    lines.append(f"## {app}")
                    for sec, items in sections.items():
                        lines.append(f"  ### {sec}")
                        for item in items[:15]:
                            lines.append(f"    {item}")
            return "\n".join(lines[:200])

    lines = [f"## {appendix}"]
    total = 0
    for section, items in sorted(q0_ref[appendix].items()):
        if total >= max_items:
            break
        lines.append(f"  ### {section}")
        for item in items:
            if total >= max_items:
                break
            lines.append(f"    {item}")
            total += 1
    return "\n".join(lines)


def _similar(a: str, b: str) -> float:
    """简单的字符级重叠相似度。"""
    a_set = set(a.replace(" ", ""))
    b_set = set(b.replace(" ", ""))
    if not a_set or not b_set:
        return 0
    return len(a_set & b_set) / len(a_set | b_set)


def _clean_chapter_name(raw: str) -> str:
    """清理乱码章节名：去掉引号、'相应'、'子目'、'。' 等碎片。"""
    s = raw.replace("”", "").replace("“", "").replace("。", "").replace("，", "")
    s = re.sub(r"的相应.*$", "", s)
    s = re.sub(r"相应.*$", "", s)
    s = re.sub(r"按.*$", "", s)
    s = re.sub(r"至第.*$", "", s)
    s = s.strip()
    return s if len(s) >= 4 else raw


def resolve_q0_appendix(entry: dict, q0_ref: dict) -> str:
    """为条目找到最匹配的 Q0 附录名。返回 None 表示需用全量。"""
    chapter = entry["chapter_name"]
    province = entry.get("province", "")

    # 1) 精确映射
    if chapter in CHAPTER_TO_Q0:
        _, appendix = CHAPTER_TO_Q0[chapter]
        if appendix in q0_ref:
            return appendix

    # 2) 清理后重新匹配
    cleaned = _clean_chapter_name(chapter)
    if cleaned != chapter and cleaned in CHAPTER_TO_Q0:
        _, appendix = CHAPTER_TO_Q0[cleaned]
        if appendix in q0_ref:
            return appendix

    # 3) 清理后模糊匹配 CHAPTER_TO_Q0 keys
    for mapped_ch in CHAPTER_TO_Q0:
        if _similar(cleaned, mapped_ch) > 0.5:
            _, appendix = CHAPTER_TO_Q0[mapped_ch]
            if appendix in q0_ref:
                return appendix

    # 4) 清理后模糊匹配 Q0 appendix names
    best_score = 0
    best_appendix = None
    for app in q0_ref:
        score = _similar(cleaned, app)
        if score > best_score:
            best_score = score
            best_appendix = app
    if best_score > 0.4:
        return best_appendix

    # 5) 从 quota_id 推断类型放缩
    # Q-HN-AZ-... = 安装, Q-HN-FJ-... = 房建
    qid = entry.get("quota_id", "")
    if "-AZ-" in qid:
        # 返回安装类中最相关的
        for app in q0_ref:
            if _similar(cleaned, app) > 0.3:
                return app
        return None
    elif "-FJ-" in qid:
        for app in q0_ref:
            if _similar(cleaned, app) > 0.3:
                return app
        return None

    return None


def _infer_type_from_batch(batch: list[dict]) -> str:
    """推断批次类型：'安装' 或 '房建' 或 '混合'。"""
    az = sum(1 for e in batch if "-AZ-" in e.get("quota_id", ""))
    fj = sum(1 for e in batch if "-FJ-" in e.get("quota_id", ""))
    if az > fj * 2:
        return "安装"
    elif fj > az * 2:
        return "房建"
    # 从项目名称推断
    building_keywords = [
        "土方", "砖", "砌", "混凝土", "钢筋", "模板", "脚手架", "涂料", "油漆",
        "保温", "防水", "屋面", "地面", "墙面", "天棚", "门窗", "栏杆", "围栏",
        "铁艺", "桩", "地基", "装修", "装饰", "幕墙", "隔断", "吊顶", "抹灰",
        "木材", "木结构", "钢结构", "钢构件", "螺栓", "防腐", "隔热",
    ]
    install_keywords = [
        "管道", "阀门", "风机", "水泵", "空调", "制冷", "锅炉", "电气", "电缆",
        "配电", "变压器", "开关", "插座", "灯具", "消防", "报警", "监控",
        "自动化", "仪表", "通信", "网络", "综合布线", "电梯", "起重机",
        "压缩机", "泵", "风机", "冷却塔", "换热", "通风",
    ]
    build_score = sum(1 for e in batch for kw in building_keywords
                      if kw in e.get("project_name", ""))
    inst_score = sum(1 for e in batch for kw in install_keywords
                     if kw in e.get("project_name", ""))
    if build_score > inst_score * 2:
        return "房建"
    elif inst_score > build_score * 2:
        return "安装"
    return "混合"


def build_messages(batch: list[dict], q0_ref: dict) -> list[dict]:
    """为一组条目构建 API messages。"""
    # 找出这批条目的主要 Q0 附录
    batch_type = _infer_type_from_batch(batch)
    appendixes = set()
    no_match_count = 0
    for e in batch:
        app = resolve_q0_appendix(e, q0_ref)
        if app:
            appendixes.add(app)
        else:
            no_match_count += 1

    # 构建 Q0 参考
    q0_text_parts = []
    for app in sorted(appendixes):
        q0_text_parts.append(format_q0_for_chapter(app, q0_ref))

    # 如果很多条目没匹配到，根据类型补全参考
    if no_match_count > 0:
        if batch_type == "安装":
            extra_apps = [a for a in q0_ref if a not in appendixes]
        elif batch_type == "房建":
            extra_apps = [a for a in q0_ref if a not in appendixes]
        else:
            extra_apps = []
        # 只补充最相关的（匹配度>0.2）
        for app in extra_apps:
            for e in batch:
                if _similar(_clean_chapter_name(e["chapter_name"]), app) > 0.25:
                    q0_text_parts.append(format_q0_for_chapter(app, q0_ref, max_items=80))
                    break

    # 跨类型补充：当批次显然是房建类但 Q0 参考只有安装类时（或反之），
    # 额外加入对方的主要附录
    existing_apps = set()
    for part in q0_text_parts:
        m = re.search(r"^## (.+)$", part, re.MULTILINE)
        if m:
            existing_apps.add(m.group(1))

    if batch_type == "房建":
        cross_apps = [
            "金属结构工程", "土石方工程", "砌 筑 工 程",
            "混凝土及钢筋混凝土工程", "保温、隔热、防腐工程",
            "屋面及防水工程", "楼地面装饰工程", "门 窗 工 程",
            "油漆、涂料、裱糊工程", "其他装饰工程",
            "墙、柱面装饰与隔断、幕墙工程", "桩 基 工 程",
        ]
        for app in cross_apps:
            if app in q0_ref and app not in existing_apps:
                q0_text_parts.append(format_q0_for_chapter(app, q0_ref, max_items=60))
                existing_apps.add(app)
                if len(q0_text_parts) >= 8:
                    break
    elif batch_type == "安装":
        cross_apps = [
            "机械设备安装工程", "热力设备安装工程", "电气设备安装工程",
            "通风空调工程", "工业管道工程", "消防工程",
            "给排水、采暖、燃气工程", "建筑智能化工程",
            "自动化控制仪表安装工程", "刷油、防腐蚀、绝热工程",
            "通信设备及线路工程", "静置设备与工艺金属结构制作安装工程",
        ]
        for app in cross_apps:
            if app in q0_ref and app not in existing_apps:
                q0_text_parts.append(format_q0_for_chapter(app, q0_ref, max_items=60))
                existing_apps.add(app)
                if len(q0_text_parts) >= 8:
                    break

    if q0_text_parts:
        q0_reference = "\n\n".join(q0_text_parts)
    else:
        # 最后兜底：返回对应类型的主要附录
        q0_reference = "请根据条目名称从GB 50500清单编码规范中选择最合适的9位编码。\n"
        type_filter = batch_type if batch_type != "混合" else None
        for app, sections in q0_ref.items():
            if type_filter == "安装" and app in {
                "土石方工程", "地基处理与边坡支护工程", "桩 基 工 程",
                "混凝土及钢筋混凝土工程", "砌 筑 工 程", "金属结构工程",
                "保温、隔热、防腐工程", "墙、柱面装饰与隔断、幕墙工程",
                "楼地面装饰工程", "门 窗 工 程", "屋面及防水工程",
            }:
                continue
            if type_filter == "房建" and app in {
                "机械设备安装工程", "热力设备安装工程", "静置设备与工艺金属结构制作安装工程",
                "电气设备安装工程", "建筑智能化工程", "自动化控制仪表安装工程",
                "通风空调工程", "工业管道工程", "消防工程",
                "给排水、采暖、燃气工程", "通信设备及线路工程",
                "刷油、防腐蚀、绝热工程", "措 施 项 目", "其他及附属工程",
            }:
                continue
            q0_text_parts.append(format_q0_for_chapter(app, q0_ref, max_items=60))
            if len(q0_text_parts) >= 4:
                break
        q0_reference = "\n\n".join(q0_text_parts)

    # 构建条目列表
    entries_text_parts = []
    for i, e in enumerate(batch):
        parts = [
            f"条目{i+1}:",
            f"  ID: {e['quota_id']}",
            f"  章节: {e['chapter_name']} / {e['section_name']}",
            f"  项目名称: {e['project_name']}",
        ]
        if e["project_spec"]:
            parts.append(f"  项目规格: {e['project_spec'][:200]}")
        if e["unit"]:
            parts.append(f"  计量单位: {e['unit']}")
        if e["work_content"]:
            parts.append(f"  工作内容: {e['work_content'][:200]}")
        if e["materials"]:
            parts.append(f"  主要材料: {', '.join(e['materials'][:8])}")
        entries_text_parts.append("\n".join(parts))

    entries_text = "\n\n".join(entries_text_parts)

    system_prompt = (
        "你是一个建筑工程造价专家，精通中国《建设工程工程量清单计价规范》(GB 50500)。"
        "你的任务是根据定额条目的项目名称、规格、工作内容、章节、材料等信息，"
        "从Q0清单项目编码库中选择最匹配的9位清单编码。"
        "请仔细对比条目描述与Q0项目名称的语义，选择最准确的编码。"
        "只返回JSON数组，不要有任何其他文字说明。"
    )

    user_prompt = f"""## Q0清单项目编码参考

{q0_reference}

## 待匹配定额条目

{entries_text}

## 要求
对每条定额条目选择最匹配的1个Q0清单项目编码。
返回格式（严格JSON数组，无其他内容）：
[
  {{"id": "条目ID", "boq_code": "9位编码", "confidence": 0.95, "reason": "匹配理由(20字内)"}},
  ...
]

注意事项：
1. boq_code 必须是上述Q0参考中存在的9位数字编码
2. 优先匹配项目名称语义最接近的Q0项目
3. 如果条目是安装辅助工序（如验收、调试、冲洗等），请匹配到其所属设备的编码
4. confidence 0.9+为高度确定，0.7-0.9为较有把握，0.5-0.7为推断，<0.5为猜测
5. reason 用中文简述匹配依据（如"名称匹配"、"功能对应"、"材料指向"等）
"""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def call_deepseek(messages: list[dict], model: str, temperature: float,
                  max_tokens: int, timeout: float) -> dict:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY 环境变量")
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    try:
        from openai import OpenAI
        import httpx
    except ImportError as exc:
        raise RuntimeError("缺少 openai/httpx 包") from exc

    http_client = httpx.Client(proxy=None, timeout=timeout)
    client = OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=False,
    )
    content = response.choices[0].message.content or ""
    parsed = None
    parse_error = ""
    try:
        parsed = extract_json(content)
    except Exception as exc:
        parse_error = str(exc)
    return {
        "content": content,
        "json": parsed,
        "parse_error": parse_error,
        "finish_reason": response.choices[0].finish_reason,
        "usage": {
            "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
            "completion_tokens": response.usage.completion_tokens if response.usage else 0,
            "total_tokens": response.usage.total_tokens if response.usage else 0,
        },
    }


def extract_json(text: str) -> Any:
    """从文本中提取 JSON。"""
    text = text.strip()
    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 提取 ```json ... ``` 块
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if m:
        return json.loads(m.group(1).strip())
    # 尝试找最外层的 [ ... ] 或 { ... }
    for start_char, end_char in [("[", "]"), ("{", "}")]:
        start = text.find(start_char)
        end = text.rfind(end_char)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end+1])
            except json.JSONDecodeError:
                pass
    raise ValueError(f"无法解析 JSON: {text[:200]}...")


def validate_result(result: dict, q0_all_codes: set[str]) -> tuple[bool, str]:
    """验证单条 AI 结果。"""
    bc = result.get("boq_code", "").strip()
    if not bc:
        return False, "boq_code 为空"
    if not re.match(r"^\d{9}$", bc):
        return False, f"boq_code 格式错误: {bc}"
    if bc not in q0_all_codes:
        return False, f"boq_code {bc} 不在 Q0 编码库中"
    conf = result.get("confidence", 0)
    if isinstance(conf, str):
        try:
            conf = float(conf)
        except ValueError:
            conf = 0.5
    if conf < 0.5:
        return False, f"confidence {conf} 过低"
    return True, ""


def save_results(results: dict[str, dict], stats: dict):
    """将结果写入 CSV 和 JSON 索引。"""
    # 更新 CSV
    fieldnames = None
    rows = []
    with open(Q1_CSV, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            qid = row["quota_id"].strip()
            if qid in results:
                r = results[qid]
                row["boq_code"] = r.get("boq_code", "")
                row["boq_match_type"] = "ai_filled"
                row["boq_match_confidence"] = str(r.get("confidence", ""))
                row["boq_match_reason"] = r.get("reason", "")
                row["boq_risk_tags"] = json.dumps(["ai_filled"], ensure_ascii=False)
                row["boq_match_method"] = "ai_inference"
            rows.append(row)

    if fieldnames:
        with open(Q1_CSV, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    # 更新 JSON 索引
    if Q1_JSON.exists():
        with open(Q1_JSON, "r") as f:
            index = json.load(f)
        for qid, r in results.items():
            if qid in index:
                index[qid]["boq_code"] = r.get("boq_code", "")
                index[qid]["boq_match_type"] = "ai_filled"
                index[qid]["boq_match_confidence"] = r.get("confidence", "")
                index[qid]["boq_match_reason"] = r.get("reason", "")
                index[qid]["boq_risk_tags"] = json.dumps(["ai_filled"], ensure_ascii=False)
                index[qid]["boq_match_method"] = "ai_inference"
        with open(Q1_JSON, "w") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)

    print(f"  已保存: {len(results)} 条结果到 CSV + JSON")


def main():
    parser = argparse.ArgumentParser(description="DeepSeek 补充 Q1 boq_code")
    parser.add_argument("--model", default=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"))
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--max-tokens", type=int, default=MAX_TOKENS)
    parser.add_argument("--temperature", type=float, default=TEMPERATURE)
    parser.add_argument("--request-timeout", type=float, default=120.0)
    parser.add_argument("--batch-limit", type=int, default=0,
                        help="最多处理几批 (0=全部)")
    parser.add_argument("--resume", type=str, default="",
                        help="断点续传进度文件路径")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印首批 prompt，不调用 API")
    args = parser.parse_args()

    print("=" * 60)
    print("DeepSeek 补充 Q1 boq_code")
    print(f"model={args.model}  batch_size={args.batch_size}  max_tokens={args.max_tokens}")
    print("=" * 60)

    # 加载数据
    print("\n[1/5] 加载 Q0 编码参考...")
    q0_ref = load_q0_reference()
    q0_all_codes = set()
    for app_sections in q0_ref.values():
        for items in app_sections.values():
            for item in items:
                code = item.split()[0] if item else ""
                if len(code) == 9:
                    q0_all_codes.add(code)
    print(f"  Q0: {len(q0_ref)} 个附录, {len(q0_all_codes)} 个唯一编码")

    print("[2/5] 加载空 boq_code 条目...")
    entries = load_empty_entries()
    print(f"  空条目: {len(entries)}")

    if not entries:
        print("没有需要补充的条目，退出。")
        return

    # 按章节分组
    print("[3/5] 按章节分组并构建批处理...")
    by_chapter_appendix = defaultdict(list)
    for e in entries:
        key = (e["chapter_name"], e["province"])
        by_chapter_appendix[key].append(e)

    # 构建批次（尽量同章节同省放一起）
    batches = []
    for entries_group in by_chapter_appendix.values():
        for i in range(0, len(entries_group), args.batch_size):
            batch = entries_group[i:i+args.batch_size]
            batches.append(batch)

    print(f"  共 {len(batches)} 个批次")

    # 断点续传：加载已完成的结果，过滤掉已填充的条目
    all_results: dict[str, dict] = {}
    if args.resume and os.path.exists(args.resume):
        with open(args.resume, "r") as f:
            saved = json.load(f)
            all_results = saved.get("results", {})
            print(f"  断点续传: 已加载 {len(all_results)} 条先前结果")

    # 过滤掉已有结果的条目（避免重复填充）
    original_count = len(entries)
    entries = [e for e in entries if e["quota_id"] not in all_results]
    if len(entries) < original_count:
        print(f"  过滤已填充条目: {original_count} → {len(entries)}")

    if not entries:
        # 所有条目都有结果了，直接保存
        print("  所有条目已填充，直接保存...")
        save_results(all_results, {})
        print(f"\n  完成: {len(all_results)} 条")
        return

    # 重新构建批次
    by_chapter_appendix = defaultdict(list)
    for e in entries:
        key = (e["chapter_name"], e["province"])
        by_chapter_appendix[key].append(e)

    batches = []
    for entries_group in by_chapter_appendix.values():
        for i in range(0, len(entries_group), args.batch_size):
            batch = entries_group[i:i+args.batch_size]
            batches.append(batch)

    print(f"  共 {len(batches)} 个批次 (已有 {len(all_results)} 条结果)")

    if args.dry_run:
        print("\n[DRY RUN] 首批 prompt 预览:")
        batch = batches[0]
        msgs = build_messages(batch, q0_ref)
        print(f"  条目数: {len(batch)}")
        print(f"  System prompt: {len(msgs[0]['content'])} chars")
        print(f"  User prompt: {len(msgs[1]['content'])} chars")
        print(f"\n{'='*40} USER PROMPT START {'='*40}")
        print(msgs[1]['content'][:3000])
        print(f"\n{'='*40} USER PROMPT END {'='*41}")
        return

    # 处理批次（总是从 0 开始，因为已过滤了已完成的条目）
    max_batches = min(len(batches), args.batch_limit) if args.batch_limit else len(batches)
    print(f"\n[4/5] 开始调用 DeepSeek API (处理 {max_batches} 批)...")
    stats = {
        "total": 0,
        "valid": 0,
        "invalid": 0,
        "parse_errors": 0,
        "api_errors": 0,
    }

    for batch_idx in range(max_batches):

        batch = batches[batch_idx]
        print(f"\n--- 批次 {batch_idx+1}/{len(batches)} ({len(batch)} 条) ---")
        messages = build_messages(batch, q0_ref)
        try:
            api_result = call_deepseek(
                messages, args.model, args.temperature,
                args.max_tokens, args.request_timeout,
            )
        except Exception as exc:
            print(f"  API 错误: {exc}")
            stats["api_errors"] += len(batch)
            # 保存进度
            _save_progress(all_results, batch_idx, args.resume or "boq_progress.json")
            time.sleep(3)
            continue

        if api_result["parse_error"]:
            print(f"  解析错误: {api_result['parse_error']}")
            print(f"  原始输出前 300 字符: {api_result['content'][:300]}")
            stats["parse_errors"] += len(batch)
            _save_progress(all_results, batch_idx + 1, args.resume or "boq_progress.json")
            continue

        results_list = api_result["json"]
        if isinstance(results_list, dict):
            results_list = [results_list]
        if not isinstance(results_list, list):
            print(f"  返回格式异常: {type(results_list)}")
            stats["parse_errors"] += len(batch)
            continue

        batch_valid = 0
        for r in results_list:
            qid = r.get("id", "")
            ok, err = validate_result(r, q0_all_codes)
            stats["total"] += 1
            if ok:
                all_results[qid] = {
                    "boq_code": r["boq_code"].strip(),
                    "confidence": r.get("confidence", ""),
                    "reason": r.get("reason", ""),
                }
                stats["valid"] += 1
                batch_valid += 1
            else:
                stats["invalid"] += 1
                if batch_valid < 5:
                    print(f"  无效: {qid} → {r.get('boq_code','')} ({err})")

        usage = api_result.get("usage", {})
        print(f"  本批有效: {batch_valid}/{len(batch)} | "
              f"累计: {stats['valid']}有效/{stats['invalid']}无效 "
              f"| tokens: {usage.get('total_tokens', '?')}")

        # 每 5 批保存一次
        if (batch_idx + 1) % 5 == 0 or (batch_idx + 1) == len(batches):
            save_results(all_results, stats)
            _save_progress(all_results, batch_idx + 1, args.resume or "boq_progress.json")
            print(f"  ✓ 已保存进度")

        time.sleep(1)

    # 最终保存
    print(f"\n[5/5] 最终保存...")
    save_results(all_results, stats)

    # 打印统计
    remaining = len(entries) - stats["valid"]
    print(f"\n{'='*60}")
    print(f"完成统计:")
    print(f"  总处理: {stats['total']}")
    print(f"  有效填充: {stats['valid']}")
    print(f"  无效/拒绝: {stats['invalid']}")
    print(f"  解析错误: {stats['parse_errors']}")
    print(f"  API 错误: {stats['api_errors']}")
    print(f"  剩余空值: {remaining}")
    print(f"{'='*60}")


def _save_progress(results: dict, batch_idx: int, path: str):
    with open(path, "w") as f:
        json.dump({"results": results, "batch_idx": batch_idx}, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
