# -*- coding: utf-8 -*-
"""项目、导出历史与运行设置的落盘。文件都放在 backend/data 下，纯 JSON，无数据库。

设置项可以覆盖 .env 里的同名配置：进程读取时先看设置文件，再回退到环境变量。
API Key 只在服务端流转，出参一律脱敏。
"""
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")

PROJECTS_FILE = os.path.join(DATA_DIR, "projects.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")

DEFAULT_SETTINGS = {
    "vision_model": "",
    "vision_base_url": "",
    "vision_api_key": "",       # 空表示沿用 .env
    "assistant_model": "",
    "temperature": 0,
    "seed": None,
    "excel_template": "",       # 自定义模板路径，空表示用内置版式
    "include_changes": True,
    "tile_large_pages": True,   # 大图（长边 >500mm）切块识别，小图不受影响
}


def _load(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _save(path: str, payload) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------- 设置 ----------

def settings() -> dict:
    return {**DEFAULT_SETTINGS, **_load(SETTINGS_FILE, {})}


def save_settings(patch: dict) -> dict:
    merged = {**settings(), **{k: v for k, v in patch.items() if k in DEFAULT_SETTINGS}}
    _save(SETTINGS_FILE, merged)
    return merged


def public_settings() -> dict:
    """出参：Key 只回传“是否已配置”，不泄露内容。"""
    current = settings()
    key = current.get("vision_api_key") or os.environ.get("VISION_API_KEY", "")
    return {
        **{k: v for k, v in current.items() if k != "vision_api_key"},
        "vision_api_key_set": bool(key),
        "vision_api_key_hint": f"***{key[-4:]}" if len(key) > 8 else ("***" if key else ""),
    }


def apply_settings_to_env() -> None:
    """设置优先于 .env；只覆盖非空值，避免把 .env 里的 Key 抹掉。"""
    current = settings()
    for key, env_name in (("vision_model", "VISION_MODEL"),
                          ("vision_base_url", "VISION_BASE_URL"),
                          ("vision_api_key", "VISION_API_KEY"),
                          ("assistant_model", "ASSISTANT_MODEL")):
        value = current.get(key)
        if value:
            os.environ[env_name] = str(value)
    if current.get("temperature") is not None:
        os.environ["VISION_TEMPERATURE"] = str(current["temperature"])
    if current.get("seed") not in (None, ""):
        os.environ["VISION_SEED"] = str(current["seed"])
    else:
        os.environ.pop("VISION_SEED", None)


# ---------- 项目 ----------

def projects() -> list[dict]:
    return _load(PROJECTS_FILE, [])


def ensure_project(name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("项目名称不能为空")
    items = projects()
    for item in items:
        if item.get("name") == name:
            return item
    item = {"name": name, "created_at": _now()}
    items.append(item)
    _save(PROJECTS_FILE, items)
    return item


def project_names() -> list[str]:
    return [item["name"] for item in projects() if item.get("name")]


def record_project_ai_usage(project_name: str, usage_summary: dict, job_id: str = "", filename: str = "") -> dict:
    """持久化记录项目的 AI 调用 token、费用汇总与详细日志。"""
    pname = (project_name or "未分组").strip() or "未分组"
    items = projects()
    matched = None
    for p in items:
        if p.get("name") == pname:
            matched = p
            break
    if not isinstance(usage_summary, dict):
        usage_summary = {}
    if not matched:
        matched = {"name": pname, "created_at": _now()}
        items.append(matched)

    p_tokens = int(usage_summary.get("prompt_tokens", 0) or 0)
    c_tokens = int(usage_summary.get("completion_tokens", 0) or 0)
    t_tokens = int(usage_summary.get("total_tokens", p_tokens + c_tokens) or 0)
    cost_in = float(usage_summary.get("cost_in", 0.0) or 0.0)
    cost_out = float(usage_summary.get("cost_out", 0.0) or 0.0)
    total_cost = float(usage_summary.get("total_cost", cost_in + cost_out) or 0.0)

    matched["ai_prompt_tokens"] = int(matched.get("ai_prompt_tokens", 0)) + p_tokens
    matched["ai_completion_tokens"] = int(matched.get("ai_completion_tokens", 0)) + c_tokens
    matched["ai_tokens_total"] = int(matched.get("ai_tokens_total", 0)) + t_tokens
    matched["ai_cost_in"] = round(float(matched.get("ai_cost_in", 0.0)) + cost_in, 5)
    matched["ai_cost_out"] = round(float(matched.get("ai_cost_out", 0.0)) + cost_out, 5)
    matched["ai_cost_total"] = round(float(matched.get("ai_cost_total", 0.0)) + total_cost, 5)

    log_entry = {
        "job_id": job_id,
        "filename": filename,
        "timestamp": _now(),
        "model": usage_summary.get("model", ""),
        "calls_count": usage_summary.get("calls_count", 1),
        "prompt_tokens": p_tokens,
        "completion_tokens": c_tokens,
        "total_tokens": t_tokens,
        "cost_in": cost_in,
        "cost_out": cost_out,
        "total_cost": total_cost,
        "currency": usage_summary.get("currency", "￥"),
        "details": usage_summary.get("logs", []),
    }
    if "ai_logs" not in matched:
        matched["ai_logs"] = []
    matched["ai_logs"].insert(0, log_entry)
    matched["ai_logs"] = matched["ai_logs"][:500]

    _save(PROJECTS_FILE, items)
    return matched


def get_project_ai_logs(project_name: str) -> dict:
    """获取项目的 AI 调用费用汇总与完整流水日志。"""
    pname = (project_name or "未分组").strip() or "未分组"
    items = projects()
    for p in items:
        if p.get("name") == pname:
            return {
                "project_name": pname,
                "ai_cost_total": round(float(p.get("ai_cost_total", 0.0)), 5),
                "ai_tokens_total": int(p.get("ai_tokens_total", 0)),
                "ai_prompt_tokens": int(p.get("ai_prompt_tokens", 0)),
                "ai_completion_tokens": int(p.get("ai_completion_tokens", 0)),
                "ai_cost_in": round(float(p.get("ai_cost_in", 0.0)), 5),
                "ai_cost_out": round(float(p.get("ai_cost_out", 0.0)), 5),
                "currency": "￥",
                "ai_logs": p.get("ai_logs", []),
            }
    return {
        "project_name": pname,
        "ai_cost_total": 0.0,
        "ai_tokens_total": 0,
        "ai_prompt_tokens": 0,
        "ai_completion_tokens": 0,
        "ai_cost_in": 0.0,
        "ai_cost_out": 0.0,
        "currency": "￥",
        "ai_logs": [],
    }


# ---------- 导出历史 ----------

def history(limit: int = 100) -> list[dict]:
    return _load(HISTORY_FILE, [])[:limit]


def add_history(entry: dict) -> dict:
    entry = {**entry, "exported_at": _now()}
    items = _load(HISTORY_FILE, [])
    items.insert(0, entry)
    _save(HISTORY_FILE, items[:500])
    return entry


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")
