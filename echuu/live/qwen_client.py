"""
LLM 客户端封装（Qwen / DashScope OpenAI 兼容模式）。
"""

from __future__ import annotations

import os
from typing import Optional

# 华北2（北京）地域；新加坡地域为 dashscope-intl.aliyuncs.com
_CN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_INTL_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"


class QwenClient:
    """Qwen LLM 客户端（DashScope OpenAI 兼容模式）。"""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        self.model = model or os.getenv("QWEN_MODEL", "qwen-plus")
        self.seed: Optional[int] = None
        self.client = None

        if not self.api_key:
            raise ValueError("未设置 DASHSCOPE_API_KEY，无法使用 Qwen")

        region = os.getenv("QWEN_REGION", "cn").lower()
        base_url = _INTL_BASE_URL if region in ("sg", "intl", "international") else _CN_BASE_URL

        try:
            from openai import OpenAI

            self.client = OpenAI(api_key=self.api_key, base_url=base_url, timeout=25, max_retries=0)
            print(f"LLM 已初始化: {self.model} (DashScope)")
        except ImportError:
            raise ImportError("openai 未安装，请先安装 openai>=1.52.0")

    def call(self, prompt: str, system: Optional[str] = None, max_tokens: int = 1000, response_schema=None) -> str:
        """调用 LLM。"""
        if not self.client:
            raise RuntimeError("LLM 未初始化，无法调用")
        try:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            request = {
                "model": self.model,
                "messages": messages,
                "max_tokens": max_tokens,
            }
            if self.model.startswith('qwen3.8'):
                request['extra_body'] = {'enable_thinking': False}
            if response_schema is not None:
                request['response_format'] = {'type': 'json_object'}
            if self.seed is not None:
                request["seed"] = int(self.seed)
            response = self.client.chat.completions.create(
                **request,
            )
            if response.choices[0].finish_reason == 'length':
                raise RuntimeError('Qwen output truncated; no partial script accepted')
            return response.choices[0].message.content
        except Exception as exc:
            raise RuntimeError(f"LLM 调用失败: {exc}") from exc

    def call_with_search(self, prompt: str, system: Optional[str] = None, max_tokens: int = 800) -> str:
        """带联网搜索的调用（DashScope enable_search，qwen-plus 支持）。

        用于主题 grounding，保留服务端检索来源，避免只收到模型生成的无来源总结。
        """
        if not self.client:
            raise RuntimeError("LLM 未初始化，无法调用")
        try:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            # Native DashScope returns retrieval metadata; the OpenAI-compatible
            # endpoint returns prose only, so its generated URLs are not evidence.
            import httpx
            region = os.getenv("QWEN_REGION", "cn").lower()
            host = "dashscope-intl.aliyuncs.com" if region in ("sg", "intl", "international") else "dashscope.aliyuncs.com"
            response = httpx.post(
                f"https://{host}/api/v1/services/aigc/text-generation/generation",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "input": {"messages": messages}, "parameters": {
                    "max_tokens": max_tokens, "result_format": "message", "enable_search": True,
                    "search_options": {"forced_search": True, "enable_source": True, "enable_citation": True},
                }},
                timeout=15,
            )
            response.raise_for_status()
            output = response.json().get("output", {})
            sources = output.get("search_info", {}).get("search_results", [])
            sources = [s for s in sources if isinstance(s, dict) and str(s.get("url", "")).startswith(("https://", "http://"))]
            if not sources:
                return "未能核实：搜索服务未返回可追溯来源。不要将未检索到解释为事件未发生。"
            choice = output["choices"][0]
            if choice.get("finish_reason") == "length":
                raise RuntimeError("Search brief truncated")
            content = choice["message"]["content"]
            references = "\n".join(f"[{s.get('index', i)}] {s.get('title', '')} {s['url']}" for i, s in enumerate(sources, 1))
            return f"{content}\n\n【搜索服务返回的来源】\n{references}"

        except Exception as exc:
            raise RuntimeError(f"LLM 联网调用失败: {exc}") from exc

    def call_structured(self, prompt, system=None, max_tokens=3000, response_schema=None):
        return self.call(prompt, system=system, max_tokens=max_tokens, response_schema=response_schema)
