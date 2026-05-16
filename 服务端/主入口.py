#!/usr/bin/env python3
"""
材料清单助手 · 本地执行平台
Flask 后端主入口 — 启动后访问 http://127.0.0.1:8765
"""
import sys
import time
from pathlib import Path

# 确保 服务端 目录在 sys.path 中
SERVER_DIR = Path(__file__).resolve().parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from flask import Flask, send_from_directory
from 路由 import api
from 配置 import WEB_DIR, HOST, PORT

app = Flask(__name__, static_folder=str(WEB_DIR), static_url_path="")
app.register_blueprint(api, url_prefix="/api")

@app.route("/")
def index():
    return send_from_directory(str(WEB_DIR), "index.html")

@app.route("/<path:path>")
def static_files(path):
    return send_from_directory(str(WEB_DIR), path)

# ── 启动时预热索引 ──
def _预热索引():
    t0 = time.time()
    from 知识库读取 import _读索引
    索引列表 = ["q1_按ID", "q2_按定额ID", "t3_fj_按物料ID", "t3_az_按物料ID", "品类_按ID"]
    for name in 索引列表:
        idx = _读索引(name)
        print(f"  [预热] {name}: {len(idx):,} 条 ({time.time()-t0:.1f}s)")
    from 知识库读取 import _加载分类库
    kb = _加载分类库()
    print(f"  [预热] 三级分类材料库: {kb['summary']['category_count']} 分类 ({time.time()-t0:.1f}s)")

if __name__ == "__main__":
    print(f"\n{'='*60}")
    print(f"  材料清单助手 · 本地执行平台")
    print(f"  访问地址: http://{HOST}:{PORT}")
    print(f"  按 Ctrl+C 停止服务")
    print(f"{'='*60}")
    print(f" 预热索引中...")
    _预热索引()
    print(f"{'='*60}\n")
    app.run(host=HOST, port=PORT, debug=True)
