"""项目管理数据层 — JSON文件存储项目元数据"""
import json
import uuid
from datetime import datetime
from pathlib import Path

from 配置 import DATA_DIR

PROJECTS_FILE = DATA_DIR / "项目.json"
RULES_FILE = DATA_DIR / "rules.json"


def _read_json(path: Path, default=None):
    if default is None:
        default = []
    if not path.exists():
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return default


def _write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ══════════════════════════════════════
# 项目管理
# ══════════════════════════════════════

def list_projects():
    return _read_json(PROJECTS_FILE, [])


def create_project(data: dict):
    projects = _read_json(PROJECTS_FILE, [])
    project = {
        "id": uuid.uuid4().hex[:8],
        "name": data.get("name", "未命名项目"),
        "client": data.get("client", ""),
        "description": data.get("description", ""),
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "history": [],
    }
    projects.append(project)
    _write_json(PROJECTS_FILE, projects)
    return project


def get_project(project_id: str):
    projects = _read_json(PROJECTS_FILE, [])
    for p in projects:
        if p["id"] == project_id:
            return p
    return None


def delete_project(project_id: str):
    projects = _read_json(PROJECTS_FILE, [])
    projects = [p for p in projects if p["id"] != project_id]
    _write_json(PROJECTS_FILE, projects)
    return True


def add_run_to_project(project_id: str, run_data: dict):
    projects = _read_json(PROJECTS_FILE, [])
    for p in projects:
        if p["id"] == project_id:
            p["history"].append({
                "run_id": run_data.get("run_id", ""),
                "time": datetime.now().isoformat(),
                "item_count": run_data.get("item_count", 0),
                "input_summary": str(run_data.get("input", {}))[:200],
            })
            p["updated_at"] = datetime.now().isoformat()
            _write_json(PROJECTS_FILE, projects)
            return p
    return None


# ══════════════════════════════════════
# 规则配置
# ══════════════════════════════════════

DEFAULT_RULES = {
    "supply_defaults": {"钢材": "乙供", "混凝土": "乙供", "砂浆": "乙供", "防水": "乙供"},
    "custom_loss_rates": {},
    "brand_preferences": {},
    "procurement_groups": [
        "钢材类", "商混类", "模板类", "砌体类", "防水类",
        "保温类", "装饰类", "门窗类", "管材类", "阀门类",
        "电缆类", "辅材类", "涂料类", "石材类", "玻璃类"
    ],
}

def get_rules():
    return _read_json(RULES_FILE, DEFAULT_RULES)


def update_rules(data: dict):
    current = _read_json(RULES_FILE, DEFAULT_RULES)
    current.update(data)
    _write_json(RULES_FILE, current)
    return current


def reset_rules():
    _write_json(RULES_FILE, DEFAULT_RULES)
    return DEFAULT_RULES
