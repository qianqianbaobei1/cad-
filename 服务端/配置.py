"""全局配置 — 含 .env 加载"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
内部知识库_DIR = BASE_DIR / "标准知识库"
KB_DIR = 内部知识库_DIR / "源数据"
WEB_DIR = BASE_DIR / "前端"
DATA_DIR = BASE_DIR / "项目数据"

KB_01 = KB_DIR / "01_定额库"
KB_FJ = KB_DIR / "01_房屋建筑与装饰工程"
KB_AZ = KB_DIR / "02_通用安装工程"
KB_NORM = KB_DIR / "03_国家规范库"
KB_CAT = KB_DIR / "06_品类树"
KB_PROV = KB_DIR / "省份适配"
KB_PROV_DATA = KB_PROV / "CSV导出"

HOST = "127.0.0.1"
PORT = 8765

DATA_DIR.mkdir(parents=True, exist_ok=True)

# ══════════════════════════════════════
# 加载 .env 文件（零依赖版）
# ══════════════════════════════════════
def _load_dotenv(path: Path):
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip()
            if key and val:  # 有值就覆盖，解决首次加载空值后续不更新的问题
                os.environ[key] = val

_load_dotenv(BASE_DIR / ".env")
