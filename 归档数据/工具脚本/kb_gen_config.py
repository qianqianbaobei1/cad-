#!/usr/bin/env python3
"""安装工程知识库生成 — 配置与断点系统。"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "过程数据" / "AI候选_安装"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
CHECKPOINT_PATH = OUTPUT_DIR / "checkpoint.json"

Q0_INSTALL_CSV = ROOT / "标准知识库" / "源数据" / "01_定额库" / "Q0_清单项目编码_安装工程.csv"
T3_INSTALL_CSV = ROOT / "标准知识库" / "T3_安装_标准物料库.csv"
CLASSIFICATION_KB = ROOT / "标准知识库" / "三级分类材料库.json"
EXISTING_DEFAULTS = ROOT / "项目数据" / "项目默认值库.json"
Q1_INDEX_JSON = ROOT / "项目数据" / "本地知识库包" / "索引" / "q1_按ID.json"
Q2_INDEX_JSON = ROOT / "项目数据" / "本地知识库包" / "索引" / "q2_按定额ID.json"

# 14 个安装附录，按优先级排序。P0 优先跑，P3 最后跑。
APPENDIX_PRIORITY: dict[str, dict] = {
    "通风空调工程":           {"priority": 0, "phase": "暖通安装工程"},
    "给排水、采暖、燃气工程":   {"priority": 0, "phase": "给排水安装工程"},
    "消防工程":               {"priority": 1, "phase": "消防安装工程"},
    "建筑智能化工程":          {"priority": 1, "phase": "智能化安装工程"},
    "机械设备安装工程":        {"priority": 1, "phase": "机械设备安装工程"},
    "工业管道工程":           {"priority": 2, "phase": "工业管道安装工程"},
    "刷油、防腐蚀、绝热工程":   {"priority": 2, "phase": "绝热防腐工程"},
    "自动化控制仪表安装工程":   {"priority": 2, "phase": "仪表安装工程"},
    "电气设备安装工程":        {"priority": 2, "phase": "电气安装工程",
                               "note": "电气16个分部已由原脚本生成，仅需迁移"},
    "热力设备安装工程":        {"priority": 3, "phase": "热力设备安装工程"},
    "静置设备与工艺金属结构制作安装工程": {"priority": 3, "phase": "静置设备安装工程"},
    "通信设备及线路工程":      {"priority": 3, "phase": "通信安装工程"},
    "其他及附属工程":          {"priority": 3, "phase": "附属工程"},
    "措 施 项 目":            {"priority": 3, "phase": "措施项目"},
}

P0_APPENDIXES = sorted(
    [k for k, v in APPENDIX_PRIORITY.items() if v["priority"] == 0],
    key=lambda k: APPENDIX_PRIORITY[k]["priority"],
)

# 每批 API 参数
BATCH_DEFAULTS = {
    "temperature": 0.2,
    "max_tokens": 16000,
    "retries": 3,
    "retry_sleep": 10.0,
    "request_timeout": 300.0,
}

# checkpoint 状态机
STATUS_PENDING = "pending"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


def load_env() -> dict[str, str]:
    """读取 .env 返回 API 配置字典。"""
    env_path = ROOT / ".env"
    result: dict[str, str] = {}
    if not env_path.exists():
        return result
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            result[key] = value
    return result


def get_api_config() -> dict:
    """获取 API 配置，优先 DeepSeek，其次 Moonshot/Kimi。"""
    env = load_env()
    api_key = env.get("DEEPSEEK_API_KEY") or env.get("MOONSHOT_API_KEY", "")
    base_url = env.get("DEEPSEEK_BASE_URL") or env.get("MOONSHOT_BASE_URL", "https://api.deepseek.com/v1")
    model = env.get("DEEPSEEK_MODEL") or env.get("MOONSHOT_MODEL", "deepseek-v4-pro")
    return {"api_key": api_key, "base_url": base_url, "model": model}


def load_checkpoint() -> dict:
    """读取断点文件，不存在则返回空结构。"""
    if not CHECKPOINT_PATH.exists():
        return {
            "pipeline": "安装工程知识库生成",
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "updated_at": "",
            "batches": {},
        }
    with open(CHECKPOINT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_checkpoint(checkpoint: dict) -> None:
    """写入断点文件。先写临时文件再 rename，防止写入中断损坏数据。"""
    checkpoint["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    tmp = CHECKPOINT_PATH.with_suffix(".tmp.json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(checkpoint, f, ensure_ascii=False, indent=2)
    tmp.replace(CHECKPOINT_PATH)


def batch_key(appendix_name: str, section_code: str) -> str:
    """生成批次的唯一标识键。"""
    safe = appendix_name.replace(" ", "").replace("、", "")
    return f"{safe}_{section_code}"


def is_batch_completed(checkpoint: dict, appendix: str, section_code: str) -> bool:
    """检查某批次是否已成功完成。"""
    key = batch_key(appendix, section_code)
    batch_info = checkpoint.get("batches", {}).get(key, {})
    return batch_info.get("status") == STATUS_COMPLETED


def mark_batch(checkpoint: dict, appendix: str, section_code: str,
               status: str, output_file: str = "", **extra) -> dict:
    """更新 checkpoint 中某批次的状态。"""
    key = batch_key(appendix, section_code)
    entry = {
        "appendix": appendix,
        "section_code": section_code,
        "status": status,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    if output_file:
        entry["output_file"] = output_file
    entry.update(extra)
    checkpoint.setdefault("batches", {})[key] = entry
    return checkpoint


def batch_output_path(appendix: str, section_code: str) -> Path:
    """给定附录和分部编码，返回独立的输出文件路径。"""
    safe_appendix = appendix.replace(" ", "_").replace("、", "").replace("/", "_")
    safe_section = section_code.replace(" ", "_").replace("/", "_")
    filename = f"{safe_appendix}_{safe_section}.json"
    return OUTPUT_DIR / filename


def load_t3_install_context(appendix_filter: str = "") -> list[dict]:
    """读取 T3 安装物料库，可选过滤附录，作为 prompt 上下文注入。"""
    import csv
    rows = []
    if not T3_INSTALL_CSV.exists():
        return rows
    with open(T3_INSTALL_CSV, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            appendix_field = row.get("适用范围(附录)", "")
            if appendix_filter and appendix_filter not in str(appendix_field):
                continue
            rows.append({
                "material_name": row.get("标准名称", ""),
                "category": f"{row.get('分类(一级)','')}/{row.get('分类(二级)','')}",
                "unit": row.get("采购单位", ""),
                "standard_code": row.get("标准代号", ""),
                "loss_rate": row.get("品类标准损耗率", ""),
            })
    return rows


def load_q0_install_rows() -> list[dict]:
    """从 Q0 CSV 读取安装工程清单行。"""
    import csv
    rows = []
    with open(Q0_INSTALL_CSV, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rows.append({
                "trade": row.get("专业工程名称", ""),
                "appendix_name": row.get("附录名称", ""),
                "section_code": row.get("分部编码", ""),
                "section_name": row.get("分部名称", ""),
                "item_code": row.get("项目编码", ""),
                "item_name": row.get("项目名称", ""),
                "features": row.get("项目特征", ""),
                "unit": row.get("计量单位", ""),
                "work_content": row.get("工作内容", ""),
            })
    return [r for r in rows if r["trade"] == "通用安装工程"]


def load_q2_context_for_appendix(appendix_name: str, top_n: int = 80) -> dict:
    """加载 Q1→Q2 定额材料数据，为 AI 提供「该附录真实使用的材料」约束。

    链路：appendix_name → Q0 编码 → Q1 quota_id → Q2 材料名
    返回 {'materials': [...], 'sample_quota_ids': [...], 'total_quotas': N}
    """
    import csv
    from collections import Counter

    # Step 1: 找到该附录下所有 Q0 编码
    q0_codes: set[str] = set()
    if Q0_INSTALL_CSV.exists():
        with open(Q0_INSTALL_CSV, "r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if row.get("附录名称", "").strip() == appendix_name:
                    code = row.get("项目编码", "").strip()
                    if code:
                        q0_codes.add(code)

    if not q0_codes:
        return {"materials": [], "sample_quota_ids": [], "total_quotas": 0, "note": "no Q0 codes found"}

    # Step 2: 从 Q1 索引找该附录下的 quota_id
    appendix_quota_ids: list[str] = []
    if Q1_INDEX_JSON.exists():
        with open(Q1_INDEX_JSON, "r", encoding="utf-8") as f:
            q1_index = json.load(f)
        for qid, entry in q1_index.items():
            if entry.get("boq_code", "") in q0_codes:
                appendix_quota_ids.append(qid)

    if not appendix_quota_ids:
        return {"materials": [], "sample_quota_ids": [], "total_quotas": 0, "note": "no Q1 quotas found"}

    # Step 3: 从 Q2 索引加载这些定额的材料名
    material_counter: Counter = Counter()
    sample_ids: list[str] = []
    if Q2_INDEX_JSON.exists():
        with open(Q2_INDEX_JSON, "r", encoding="utf-8") as f:
            q2_index = json.load(f)
        # q2_index is {quota_id: [material_rows]}
        count = 0
        for qid in appendix_quota_ids:
            q2_rows = q2_index.get(qid, [])
            if q2_rows:
                sample_ids.append(qid)
                for mr in q2_rows:
                    name = (mr.get("material_name_raw") or "").strip()
                    if name and len(name) >= 2:
                        material_counter[name] += 1
                count += 1
                if len(sample_ids) >= 20:
                    break

    # Step 4: 返回去重排序后的材料列表（按频率）
    top_materials = [name for name, _ in material_counter.most_common(top_n)]
    return {
        "materials": top_materials,
        "sample_quota_ids": sample_ids[:10],
        "total_quotas": len(appendix_quota_ids),
        "total_unique_materials": len(material_counter),
    }


def format_q2_prompt_block(q2_context: dict) -> str:
    """将 Q2 材料上下文格式化为 prompt 约束块。"""
    materials = q2_context.get("materials", [])
    if not materials:
        return ""

    sample = materials[:60]
    lines = [
        "\n## 该分部在真实定额库(Q2)中的实际材料消耗记录（AI 生成材料时必须优先使用这些真实存在的材料名）",
        f"该附录下共有 {q2_context.get('total_quotas', 0)} 条定额，" f"{q2_context.get('total_unique_materials', 0)} 种不同材料。",
        "以下为高频出现的材料名（按出现次数降序）：",
    ]
    lines.append("、".join(sample))
    lines.append("\n约束规则：")
    lines.append("1. 推荐材料名称应尽量与上述 Q2 真实材料名一致或相近（允许统一命名规范后的变体）")
    lines.append("2. 如果 Q2 中已有该材料，优先使用 Q2 中的标准写法")
    lines.append("3. 不得编造 Q2 中完全不存在且明显不属于该分部的材料")
    lines.append("4. 如果确实需要新增材料（Q2 未覆盖），在 review_notes 中注明\"Q2无此材料，需人工确认\"")
    return "\n".join(lines)
