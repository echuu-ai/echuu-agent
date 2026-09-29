"""
LLM 客户端封装（Claude）。
"""

from __future__ import annotations

import os
from typing import Optional


class LLMClient:
    """Claude LLM 客户端（仅真实模式）。"""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.model = model or os.getenv("DEFAULT_MODEL", "claude-3-haiku-20240307")
        self.client = None

        if self.api_key:
            try:
                import anthropic

                self.client = anthropic.Anthropic(api_key=self.api_key)
                print(f"LLM 已初始化: {self.model}")
            except ImportError:
                raise ImportError("anthropic 未安装，请先安装 anthropic")
        else:
            raise ValueError("未设置 ANTHROPIC_API_KEY，无法使用真实模式")

    def call(self, prompt: str, system: Optional[str] = None, max_tokens: int = 1000, response_schema=None) -> str:
        """调用 LLM。"""
        if self.client:
            try:
                kwargs = {
                    "model": self.model,
                    "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                }
                if self.model.startswith("claude-sonnet-5"):
                    kwargs["thinking"] = {"type": "disabled"}
                if response_schema is not None:
                    kwargs["output_config"] = {"format": {"type": "json_schema", "schema": response_schema}}
                if system:
                    kwargs["system"] = system
                response = self.client.messages.create(**kwargs)
                if getattr(response, "stop_reason", None) == "max_tokens":
                    raise RuntimeError("Claude output truncated at max_tokens; no partial script accepted")
                text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
                if not text.strip():
                    raise RuntimeError("Claude response contains no text blocks")
                return text
            except Exception as exc:
                raise RuntimeError(f"LLM 调用失败: {exc}") from exc
        raise RuntimeError("LLM 未初始化，无法调用")

    def call_structured(self, prompt, system=None, max_tokens=3000, response_schema=None):
        return self.call(prompt, system=system, max_tokens=max_tokens, response_schema=response_schema)
