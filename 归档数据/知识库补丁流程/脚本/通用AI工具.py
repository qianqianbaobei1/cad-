#!/usr/bin/env python3
"""知识库补丁流程使用的通用 AI 工具。"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any


流程目录 = Path(__file__).resolve().parents[1]
项目目录 = 流程目录.parent


def 加载环境变量() -> None:
    env_path = 项目目录 / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _获取AI配置():
    """优先使用 DeepSeek 配置，回退 Kimi 配置。"""
    加载环境变量()
    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("MOONSHOT_API_KEY", "")
    base_url = os.getenv("DEEPSEEK_BASE_URL") or os.getenv("MOONSHOT_BASE_URL", "https://api.moonshot.cn/v1")
    model = os.getenv("DEEPSEEK_MODEL") or os.getenv("MOONSHOT_MODEL", "deepseek-v4-pro")
    provider = "deepseek" if os.getenv("DEEPSEEK_API_KEY") else "moonshot"
    return api_key, base_url, model, provider


def 获取客户端():
    from openai import OpenAI
    import httpx

    api_key, base_url, model, provider = _获取AI配置()
    if not api_key:
        raise RuntimeError("缺少 API Key，请在项目 .env 中配置 DEEPSEEK_API_KEY 或 MOONSHOT_API_KEY。")
    http_client = httpx.Client(
        proxy=os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or None,
        timeout=httpx.Timeout(connect=20.0, read=180.0, write=30.0, pool=20.0),
    )
    return OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)


def 调用AI(system: str, user: str, max_tokens: int = 2500, temperature: float = 1.0,
            reasoning_effort: str = "high", thinking: bool = True) -> str:
    api_key, base_url, model, provider = _获取AI配置()
    client = 获取客户端()
    kwargs = dict(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
        stream=False,
    )
    if provider == "deepseek":
        if thinking:
            kwargs["reasoning_effort"] = reasoning_effort
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        else:
            # 别称生成等简单任务不需要深度思考，大幅提速
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    response = client.chat.completions.create(**kwargs)
    message = response.choices[0].message
    content = getattr(message, "content", None) or ""
    reasoning = getattr(message, "reasoning_content", None) or ""
    return (content or reasoning or "").strip()

# 兼容旧名称
调用Kimi = 调用AI


def 提取JSON(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        raise ValueError("AI 返回为空")
    text = re.sub(r"^```(?:json|JSON)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    candidates = []
    for start, opener in [(i, ch) for i, ch in enumerate(text) if ch in "{["]:
        closer = "}" if opener == "{" else "]"
        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    snippet = text[start:idx + 1]
                    try:
                        candidates.append(json.loads(snippet))
                    except json.JSONDecodeError:
                        pass
                    break
    if not candidates:
        raise ValueError(f"AI 返回不含可解析 JSON，前300字: {text[:300]}")
    dicts = [x for x in candidates if isinstance(x, dict)]
    if dicts:
        return dicts[-1]
    return candidates[-1]


def 归一(value: str) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("（", "(").replace("）", ")")
    text = re.sub(r"[\s　,，;；、。/\\]+", "", text)
    return text


def 读JSON(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def 写JSON(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def 读JSONL(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def 追加JSONL(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def 睡眠(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)
