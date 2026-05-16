"""AI API 客户端 — 兼容 OpenAI SDK，优先 DeepSeek，回退 Kimi/Moonshot"""
import json
import os
import re
from openai import OpenAI

_client = None


def _获取AI配置():
    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("MOONSHOT_API_KEY", "")
    base_url = os.getenv("DEEPSEEK_BASE_URL") or os.getenv("MOONSHOT_BASE_URL", "https://api.moonshot.cn/v1")
    model = os.getenv("DEEPSEEK_MODEL") or os.getenv("MOONSHOT_MODEL", "deepseek-v4-pro")
    provider = "deepseek" if os.getenv("DEEPSEEK_API_KEY") else "moonshot"
    return api_key, base_url, model, provider


def get_client():
    global _client
    if _client is None:
        api_key, base_url, model, provider = _获取AI配置()
        if not api_key:
            raise RuntimeError("未配置 API Key，请在 .env 中设置 DEEPSEEK_API_KEY 或 MOONSHOT_API_KEY")
        import httpx
        http_client = httpx.Client(proxy=None, timeout=180.0)
        _client = OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)
    return _client


def _extract_json_from_text(text: str) -> dict | None:
    """从文本中提取JSON对象。依次尝试：直接解析 → 正则提取第一个{...}块 → 从markdown代码块提取。"""
    if not text:
        return None
    text = text.strip()
    # 去掉markdown代码块包裹
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()
    # 直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 正则提取第一个完整JSON对象
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None


def chat(messages: list[dict], model: str = None, temperature: float = 1.0,
         max_tokens: int = 4096, thinking: bool = True) -> dict:
    client = get_client()
    api_key, base_url, default_model, provider = _获取AI配置()
    model = model or default_model

    kwargs = dict(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=False,
    )
    if provider == "deepseek":
        if thinking:
            kwargs["reasoning_effort"] = "high"
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        else:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

    response = client.chat.completions.create(**kwargs)

    choice = response.choices[0]
    content = choice.message.content or ""
    reasoning = ""
    if hasattr(choice.message, "reasoning_content"):
        reasoning = choice.message.reasoning_content or ""

    # 当content为空时，尝试从reasoning_content中提取有意义的内容
    if not content and reasoning:
        # DeepSeek thinking模式有时把最终答案放在reasoning末尾
        # 优先从reasoning中提取JSON（可能是最终答案），回退到最后一段非空文本
        json_candidate = _extract_json_from_text(reasoning)
        if json_candidate:
            content = json.dumps(json_candidate, ensure_ascii=False)
        else:
            # 取reasoning后半部分（最后2000字符）中第一个实质性段落
            tail = reasoning.strip()[-2000:]
            paragraphs = [p.strip() for p in tail.split("\n\n") if p.strip()]
            if paragraphs:
                content = paragraphs[-1]

    return {
        "content": content,
        "finish_reason": choice.finish_reason,
        "thinking": reasoning,
        "usage": {
            "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
            "completion_tokens": response.usage.completion_tokens if response.usage else 0,
            "total_tokens": response.usage.total_tokens if response.usage else 0,
        },
    }


def chat_json(messages: list[dict], model: str = None, temperature: float = 1.0,
              max_tokens: int = 4096, thinking: bool = True) -> dict:
    json_instruction = "\n请严格返回 JSON 格式，不要输出任何 JSON 之外的内容。"
    msgs = [dict(m) for m in messages]
    has_system = False
    for m in msgs:
        if m["role"] == "system":
            m["content"] += json_instruction
            has_system = True
            break
    if not has_system:
        msgs.insert(0, {"role": "system", "content": "请严格返回 JSON 格式。" + json_instruction})

    result = chat(msgs, model=model, temperature=temperature,
                  max_tokens=max_tokens, thinking=thinking)

    # 尝试从content中解析JSON
    parsed = _extract_json_from_text(result["content"])
    if parsed is not None:
        result["json"] = parsed
        return result

    # content解析失败 → 从reasoning_content中搜索JSON
    reasoning = result.get("thinking", "")
    if reasoning:
        parsed = _extract_json_from_text(reasoning)
        if parsed is not None:
            result["json"] = parsed
            return result

    # 都失败了
    result["json"] = None
    result["parse_error"] = result["content"][:500] if result["content"] else "(content为空, reasoning前500字: " + reasoning[:500] + ")"
    return result
