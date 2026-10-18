# -*- coding: utf-8 -*-
"""Hugging Face Spaces 入口文件（Gradio 托管 + 原生工作台挂载）。

识别、组价全部在 CPU 上完成，不需要 GPU：Space 硬件请选 CPU，不要选 ZeroGPU。
"""
import os
import sys
import time

# 公开部署一律按生产环境运行：未登录请求不会落到默认租户，初始管理员密码不会有默认值
os.environ.setdefault("ENV", "production")

# 禁用 Gradio Node SSR，防止 Node.js 抢占 7860 端口
os.environ["GRADIO_SSR_MODE"] = "False"

# 将 backend 目录加入 Python 模块搜索路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, "backend"))

import gradio as gr
from fastapi.staticfiles import StaticFiles
from backend.app import app as fastapi_app, tenant_middleware
from db import init_db, db_recover_interrupted_jobs
from cad_bootstrap import bootstrap_cad_converters

# 0. 自动引导与探测 CAD 原生转换引擎 (LibreDWG & acadrust)
bootstrap_cad_converters()

# 1. 初始化数据库与恢复冷启动中断任务
init_db()
db_recover_interrupted_jobs()

# 2. 构建 Gradio 原生界面，作为 Hugging Face Spaces 标准入口（95vh 工业级宽屏嵌入）
custom_css = """
body, html { margin: 0; padding: 0; height: 100%; overflow: hidden; background-color: #0b1120; }
.gradio-container { max-width: 100% !important; padding: 0 !important; margin: 0 !important; }
footer { display: none !important; }
"""

with gr.Blocks(title="电柜智核 · 配电箱智能工作台 (CabinetCore AI)") as demo:
    gr.HTML("""
    <div style="width: 100%; height: 98vh; margin: 0; padding: 0; display: flex; flex-direction: column;">
        <iframe src="/workbench/index.html" style="width: 100%; height: 100%; border: none; flex-grow: 1;"></iframe>
    </div>
    """)

# 3. 在 demo 启动前挂载租户认证中间件
demo.app.middleware("http")(tenant_middleware)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    # 4. 启动 Gradio 原生服务
    server_app, local_url, _ = demo.launch(
        server_name="0.0.0.0",
        server_port=port,
        css=custom_css,
        prevent_thread_lock=True,
    )

    # 5. 在运行中的 server_app 上挂载原生前端静态资源到 /workbench
    server_app.mount(
        "/workbench",
        StaticFiles(directory=os.path.join(BASE_DIR, "frontend"), html=True),
        name="workbench",
    )

    # 6. 将 FastAPI 的所有电气核图、组价、BOM 导出与认证 API 路由同步到 server_app
    for route in fastapi_app.routes:
        if getattr(route, "name", "") != "frontend":
            server_app.routes.append(route)

    print(f"[Startup] CabinetCore AI workbench mounted on {local_url}workbench/index.html")

    # 7. 保持主进程持续运行
    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        demo.close()
