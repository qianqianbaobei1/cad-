# -*- coding: utf-8 -*-
"""Hugging Face Spaces 入口文件 (Gradio + FastAPI 混合托管)."""
import os
import sys
import subprocess

print("=== HF SPACES DIAGNOSTICS START ===", flush=True)
try:
    print("--- PS AUX ---", flush=True)
    print(subprocess.check_output(["ps", "aux"]).decode(errors="ignore"), flush=True)
except Exception as e:
    print(f"ps error: {e}", flush=True)

try:
    print("--- NETSTAT / SS / LSOF ---", flush=True)
    out = subprocess.check_output("netstat -tuln 2>/dev/null || ss -tuln 2>/dev/null || lsof -i :7860 2>/dev/null", shell=True).decode(errors="ignore")
    print(out, flush=True)
except Exception as e:
    print(f"netstat error: {e}", flush=True)

print("--- ENV ---", flush=True)
for k, v in os.environ.items():
    if any(x in k.lower() for x in ["port", "host", "gradio", "space", "app"]):
        print(f"{k}={v}", flush=True)
print("=== HF SPACES DIAGNOSTICS END ===", flush=True)

# 将 backend 目录加入 Python 模块搜索路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, "backend"))

import gradio as gr
from backend.app import app as fastapi_app

# Gradio 界面：全屏直接嵌入配电箱智能扒图与成套组价原生工作台
with gr.Blocks(title="电柜智核 · 配电箱智能工作台 (CabinetCore AI)") as demo:
    gr.HTML("""
    <div style="width: 100%; height: 92vh; margin: 0; padding: 0;">
        <iframe src="/index.html" style="width: 100%; height: 100%; border: none; border-radius: 8px;"></iframe>
    </div>
    """)

# 将 Gradio 挂载到 FastAPI 应用上，兼容 Hugging Face 的 Gradio 启动器与原生 FastAPI 路由
app = gr.mount_gradio_app(fastapi_app, demo, path="/gradio")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)
