# -*- coding: utf-8 -*-
"""Hugging Face Spaces 入口文件 (Gradio + FastAPI 混合托管)."""
import os
import sys

# 关键：禁用 Gradio Node SSR，防止 Node.js 抢占 7860 端口
os.environ["GRADIO_SSR_MODE"] = "False"

# 将 backend 目录加入 Python 模块搜索路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, "backend"))

# 兼容 Hugging Face ZeroGPU (zero-a10g) 运行环境启动静态扫描
try:
    import spaces

    @spaces.GPU
    def zero_gpu_pipeline(x: str = "") -> str:
        """ZeroGPU 挂载点，满足 Hugging Face ZeroGPU 启动检测。"""
        return x
except Exception:
    def zero_gpu_pipeline(x: str = "") -> str:
        return x

import gradio as gr
from backend.app import app as fastapi_app
from db import init_db, db_recover_interrupted_jobs

# 1. 初始化数据库与恢复冷启动中断任务
init_db()
db_recover_interrupted_jobs()

# 2. 构建 Gradio 界面并挂载到 FastAPI 应用
with gr.Blocks(title="电柜智核 · 配电箱智能工作台 (CabinetCore AI)") as demo:
    gr.HTML("""
    <div style="width: 100%; height: 95vh; margin: 0; padding: 0;">
        <iframe src="/index.html" style="width: 100%; height: 100%; border: none; border-radius: 8px;"></iframe>
    </div>
    """)

app = gr.mount_gradio_app(fastapi_app, demo, path="/gradio", ssr_mode=False)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 7860))
    uvicorn.run(app, host="0.0.0.0", port=port)
