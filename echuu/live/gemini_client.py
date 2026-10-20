"""
LLM 客户端封装（Google Gemini 3）。
Supports Gemini 3 Pro, Flash, and Image generation models.
"""

from __future__ import annotations

import os
from typing import Literal, Optional

from echuu.diagnostics import diagnostic

# Valid thinking levels for Gemini 3
ThinkingLevel = Literal["low", "medium", "high", "minimal"]

# Valid media resolution levels
MediaResolution = Literal[
    "media_resolution_low",
    "media_resolution_medium",
    "media_resolution_high",
    "media_resolution_ultra_high",
]


class GeminiClient:
    """Google Gemini LLM 客户端（支持 Gemini 3）。"""

    # Gemini 3 model constants
    MODEL_GEMINI_3_PRO = "gemini-3-pro-preview"
    MODEL_GEMINI_3_FLASH = "gemini-3-flash-preview"
    MODEL_GEMINI_3_IMAGE = "gemini-3-pro-image-preview"

    # Legacy models
    MODEL_GEMINI_2_FLASH = "gemini-2.0-flash"
    MODEL_GEMINI_2_PRO = "gemini-2.5-pro"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        thinking_level: Optional[ThinkingLevel] = None,
    ):
        """
        Initialize Gemini client.

        Args:
            api_key: Gemini API key (defaults to GEMINI_API_KEY env var).
            model: Model name (defaults to GEMINI_MODEL env var or gemini-3-flash-preview).
            thinking_level: Default thinking level for Gemini 3 models.
                - "low": Minimizes latency, simple tasks
                - "high": Maximum reasoning depth (default for Gemini 3 Pro/Flash)
                - "medium": Balanced (Gemini 3 Flash only)
                - "minimal": Fastest, minimal thinking (Gemini 3 Flash only)
        """
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
        self.thinking_level = thinking_level
        self.client = None

        if self.api_key:
            try:
                from google import genai

                self.client = genai.Client(api_key=self.api_key)
                diagnostic(f"Gemini LLM 已初始化: {self.model}")
                if self.thinking_level:
                    diagnostic(f"  Thinking Level: {self.thinking_level}")
            except ImportError:
                raise ImportError("google-genai 未安装，请先运行: pip install google-genai")
        else:
            raise ValueError("未设置 GEMINI_API_KEY，无法使用 Gemini")

        # Determine if this is a Gemini 3 model
        self._is_gemini3 = self.model.startswith("gemini-3-")

    def call(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 1000,
        thinking_level: Optional[ThinkingLevel] = None,
        temperature: Optional[float] = None,
        media_resolution: Optional[MediaResolution] = None,
    ) -> str:
        """
        调用 Gemini LLM。

        Args:
            prompt: User prompt text.
            system: System instruction (optional).
            max_tokens: Maximum output tokens (default: 1000).
            thinking_level: Override thinking level for this request.
            temperature: Temperature (0.0-2.0). For Gemini 3, recommended to keep default 1.0.
            media_resolution: Media resolution for vision tasks (Gemini 3 only).

        Returns:
            Generated text response.
        """
        if not self.client:
            raise RuntimeError("Gemini LLM 未初始化，无法调用")

        try:
            from google.genai import types

            # Build generation config
            config_kwargs = {}

            if max_tokens:
                config_kwargs["max_output_tokens"] = max_tokens

            # Set thinking level for Gemini 3 models
            effective_thinking = thinking_level or self.thinking_level

            # For Gemini 3, if no thinking level is set, default to high
            if self._is_gemini3 and not effective_thinking:
                effective_thinking = "high"

            if self._is_gemini3 and effective_thinking:
                config_kwargs["thinking_config"] = types.ThinkingConfig(
                    thinking_level=effective_thinking
                )

            # Set temperature if specified (use with caution on Gemini 3)
            if temperature is not None:
                config_kwargs["temperature"] = temperature

            config = types.GenerateContentConfig(**config_kwargs)

            # Add system instruction if provided
            if system:
                config.system_instruction = system

            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=config,
            )

            # Handle empty responses (can happen with minimal thinking)
            result_text = response.text
            if not result_text or result_text.strip() == "":
                diagnostic(f"[Gemini] 收到空响应，thinking_level={effective_thinking}，重试中...")
                # Retry with higher thinking level
                if self._is_gemini3 and effective_thinking in ["low", "minimal"]:
                    config_kwargs["thinking_config"] = types.ThinkingConfig(
                        thinking_level="high"
                    )
                    config = types.GenerateContentConfig(**config_kwargs)
                    if system:
                        config.system_instruction = system
                    response = self.client.models.generate_content(
                        model=self.model,
                        contents=prompt,
                        config=config,
                    )
                    result_text = response.text

            if not result_text:
                raise RuntimeError("Gemini 返回空响应")

            return result_text
        except Exception as exc:
            raise RuntimeError(f"Gemini LLM 调用失败: {exc}") from exc

    def call_with_image(
        self,
        prompt: str,
        image_data: bytes,
        mime_type: str = "image/jpeg",
        system: Optional[str] = None,
        max_tokens: int = 1000,
        media_resolution: Optional[MediaResolution] = None,
    ) -> str:
        """
        Call Gemini with image input.

        Args:
            prompt: Text prompt.
            image_data: Raw image bytes.
            mime_type: Image MIME type (default: image/jpeg).
            system: System instruction (optional).
            max_tokens: Maximum output tokens.
            media_resolution: Media resolution level (Gemini 3 only).

        Returns:
            Generated text response.
        """
        if not self.client:
            raise RuntimeError("Gemini LLM 未初始化，无法调用")

        try:
            import base64

            from google.genai import types

            # For media_resolution, we need v1alpha API
            http_options = {}
            if media_resolution and self._is_gemini3:
                http_options["api_version"] = "v1alpha"

            # Create client with specific API version if needed
            if http_options:
                from google import genai
                client = genai.Client(api_key=self.api_key, http_options=http_options)
            else:
                client = self.client

            # Build generation config
            config_kwargs = {}
            if max_tokens:
                config_kwargs["max_output_tokens"] = max_tokens

            config = types.GenerateContentConfig(**config_kwargs)

            if system:
                config.system_instruction = system

            # Build content with image
            image_part = types.Part(
                inline_data=types.Blob(
                    mime_type=mime_type,
                    data=image_data,
                )
            )

            # Add media resolution if specified
            if media_resolution:
                image_part.media_resolution = {"level": media_resolution}

            content = types.Content(
                parts=[
                    types.Part(text=prompt),
                    image_part,
                ]
            )

            response = client.models.generate_content(
                model=self.model,
                contents=content,
                config=config,
            )
            return response.text
        except Exception as exc:
            raise RuntimeError(f"Gemini LLM 调用失败 (图像): {exc}") from exc

    def generate_image(
        self,
        prompt: str,
        aspect_ratio: str = "16:9",
        image_size: str = "4K",
        use_search: bool = False,
    ) -> bytes:
        """
        Generate images using Gemini 3 Pro Image model.

        Args:
            prompt: Text description of the image to generate.
            aspect_ratio: Aspect ratio (e.g., "16:9", "4:3", "1:1").
            image_size: Image size ("2K", "4K").
            use_search: Whether to use Google Search for grounded generation.

        Returns:
            Generated image bytes.
        """
        if not self.client:
            raise RuntimeError("Gemini LLM 未初始化，无法调用")

        if not self._is_gemini3 or "image" not in self.model:
            raise RuntimeError("Image generation requires gemini-3-pro-image-preview model")

        try:
            from google.genai import types

            # Build config
            config_kwargs = {}

            # Add search tool if requested
            tools = []
            if use_search:
                tools.append({"google_search": {}})

            if tools:
                config_kwargs["tools"] = tools

            config_kwargs["image_config"] = types.ImageConfig(
                aspect_ratio=aspect_ratio,
                image_size=image_size,
            )

            config = types.GenerateContentConfig(**config_kwargs)

            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=config,
            )

            # Extract image data
            for part in response.candidates[0].content.parts:
                if part.inline_data:
                    return part.inline_data.data

            raise RuntimeError("No image data in response")
        except Exception as exc:
            raise RuntimeError(f"Gemini 图像生成失败: {exc}") from exc
