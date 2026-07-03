# glm52_thinking_compare.py
# pip install requests

import json
import os
import sys
import time
from pathlib import Path

import requests


# =========================
# 1. 基础配置
# =========================

# 推荐通过环境变量设置，不要把 Key 写死在代码里：
# PowerShell:
#   $env:GLM_API_KEY = "你的 API Key"
#
# CMD:
#   set GLM_API_KEY=你的 API Key
#
# Linux / macOS:
#   export GLM_API_KEY="你的 API Key"

API_KEY = "4a70edb8950a41b18ee122437213e87d.fu1I2p3yz8UEzmLj"

# 三种常见 endpoint，按你的账号类型选择其一：
# 智谱开放平台：
API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

# Z.AI 通用 API：
# API_URL = "https://api.z.ai/api/paas/v4/chat/completions"

# GLM Coding Plan 专用 Coding API：
# API_URL = "https://api.z.ai/api/coding/paas/v4/chat/completions"


MODEL = "glm-5.2"

# 换成你想测试的实际问题即可。
# 建议用有一定推理步骤的问题，否则 reasoning_content 可能较短。
PROMPT = """
某商品先涨价 20%，再打八折，最后在折后价格基础上再降价 10%。
请计算最终价格相对于原价的变化百分比，并说明完整计算过程。
"""

# 输出目录：会保存完整原始 JSON 和请求体（不含 API Key）
OUTPUT_DIR = Path("glm52_compare_output")

# 为了尽量降低随机采样带来的差异，设为 False。
# 注意：不同调用之间仍可能存在少量非确定性。
DO_SAMPLE = False
MAX_TOKENS = 4096


# =========================
# 2. 工具函数
# =========================

def pretty_json_or_raw(text: str) -> str:
    """尽可能格式化 JSON；若响应不是 JSON，则原样返回。"""
    try:
        data = json.loads(text)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        return text


def save_text(filename: str, content: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / filename
    path.write_text(content, encoding="utf-8")
    return path


def call_glm(thinking_type: str) -> dict:
    """
    发起一次非流式调用。
    thinking_type: "enabled" 或 "disabled"
    """
    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": PROMPT.strip(),
            }
        ],
        "stream": False,
        "thinking": {
            "type": thinking_type,
        },
        "max_tokens": MAX_TOKENS,
        "do_sample": DO_SAMPLE,
    }

    # reasoning_effort 仅在开启思维链时设置。
    if thinking_type == "enabled":
        payload["reasoning_effort"] = "max"

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    # 保存请求内容，但不会保存 API Key。
    request_file = save_text(
        f"request_thinking_{thinking_type}.json",
        json.dumps(payload, ensure_ascii=False, indent=2),
    )

    print("\n" + "=" * 100)
    print(f"开始调用：thinking = {thinking_type}")
    print("=" * 100)
    print("\n请求 JSON：")
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    start = time.perf_counter()

    try:
        response = requests.post(
            API_URL,
            headers=headers,
            json=payload,
            timeout=(15, 600),  # 连接超时 15 秒，读取超时 10 分钟
        )
    except requests.RequestException as e:
        print(f"\n请求失败：{type(e).__name__}: {e}")
        return {
            "thinking_type": thinking_type,
            "ok": False,
            "error": str(e),
            "request_file": str(request_file),
        }

    elapsed = time.perf_counter() - start
    raw_body = response.text
    formatted_body = pretty_json_or_raw(raw_body)

    raw_file = save_text(
        f"response_thinking_{thinking_type}_raw.txt",
        raw_body,
    )
    pretty_file = save_text(
        f"response_thinking_{thinking_type}.json",
        formatted_body,
    )

    print(f"\nHTTP 状态码：{response.status_code}")
    print(f"耗时：{elapsed:.2f}s")

    print("\n响应 Headers：")
    print(json.dumps(dict(response.headers), ensure_ascii=False, indent=2))

    print("\n完整原始响应 JSON：")
    print(formatted_body)

    result = {
        "thinking_type": thinking_type,
        "ok": response.ok,
        "status_code": response.status_code,
        "elapsed_seconds": round(elapsed, 2),
        "response_headers": dict(response.headers),
        "raw_body": raw_body,
        "request_file": str(request_file),
        "raw_response_file": str(raw_file),
        "pretty_response_file": str(pretty_file),
    }

    # 额外提取常用字段，便于快速观察，但不影响完整 JSON 输出。
    try:
        data = response.json()
        message = data["choices"][0]["message"]

        result["content"] = message.get("content")
        result["reasoning_content"] = message.get("reasoning_content")
        result["usage"] = data.get("usage")

        print("\n" + "-" * 100)
        print("快速提取字段")
        print("-" * 100)
        print("content：")
        print(message.get("content"))

        print("\nreasoning_content：")
        print(message.get("reasoning_content"))

        print("\nusage：")
        print(json.dumps(data.get("usage"), ensure_ascii=False, indent=2))

    except (ValueError, KeyError, IndexError, TypeError) as e:
        print(f"\n无法按标准结构提取 choices[0].message：{e}")

    return result


# =========================
# 3. 主流程
# =========================

def main():
    if not API_KEY:
        print("错误：未检测到 GLM_API_KEY 环境变量。")
        print('PowerShell 示例：$env:GLM_API_KEY = "你的 API Key"')
        sys.exit(1)

    print(f"API URL: {API_URL}")
    print(f"Model: {MODEL}")
    print(f"输出目录: {OUTPUT_DIR.resolve()}")

    enabled_result = call_glm("enabled")
    disabled_result = call_glm("disabled")

    print("\n" + "#" * 100)
    print("对比摘要")
    print("#" * 100)

    for result in [enabled_result, disabled_result]:
        print(f"\nthinking = {result['thinking_type']}")
        print(f"成功：{result['ok']}")
        print(f"状态码：{result.get('status_code')}")
        print(f"耗时：{result.get('elapsed_seconds')}s")
        print(f"完整 JSON 文件：{result.get('pretty_response_file')}")

        reasoning = result.get("reasoning_content")
        if reasoning is None:
            print("reasoning_content：未返回 / 为 null")
        else:
            print(f"reasoning_content 字符数：{len(reasoning)}")

    print("\n建议重点比较两个文件：")
    print("  1. response_thinking_enabled.json")
    print("  2. response_thinking_disabled.json")


if __name__ == "__main__":
    main()