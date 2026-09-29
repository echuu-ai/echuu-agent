[启动、输入输出与给扣扣的互动说明](docs/KOKO_HANDOFF.md) · [服务端提示词原文](docs/INTERACTION_PROMPTS.md)

> 2026-09-29 话题演变预研：饭团语义、持续转话题与启动方式见 [交接说明](docs/topic-evolution.md)。

# echuu-agent

AI VTuber 自动直播系统 - 从真实主播切片中学习表演模式，生成自然的多语言直播内容。

## 特性

- 🎭 **学习真实主播模式** - 从30+真实切片中提取表演规律
- 🧠 **Gemini 3 支持** - 最新 AI 模型，支持可调节思考深度
- 🌍 **多语言支持** - 中文、英文、日文自动检测与生成
- 💬 **智能弹幕互动** - 基于用户记忆和关系深度的自然回复
- 🎙️ **TTS 语音合成** - Qwen3 Realtime API，支持多语言音色
- 📦 **SDK 发布** - 独立的 `echuu-sdk-release` 目录可单独使用

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境（复制 .env.example 为 .env）
cp .env.example .env
# 编辑 .env，填入 GEMINI_API_KEY 和 DASHSCOPE_API_KEY

# 3. 运行演示
python demo.py --streaming   # 流式播放（带音频和自然停顿）
python demo.py --mp3         # 仅生成 MP3
```

## 环境配置

在 `.env` 文件中设置：

```bash
# Gemini 3（推荐）
GEMINI_API_KEY=your-key-here
GEMINI_MODEL=gemini-3-flash-preview
GEMINI_THINKING_LEVEL=high

# Qwen TTS
DASHSCOPE_API_KEY=your-key-here
TTS_MODEL=qwen3-tts-flash-realtime
TTS_VOICE=Cherry
```

## 核心功能

### 1. 自动剧本生成

```python
from echuu.live.engine import EchuuLiveEngine

engine = EchuuLiveEngine()
engine.setup(
    name="小梅",
    persona="活泼可爱的VTuber",
    topic="食堂打饭遇到的趣事",
    language="zh"
)

# 生成并执行
for result in engine.run(max_steps=10):
    print(result["speech"])  # 剧本文本
    print(result["cue"])     # VRM 控制指令
```

### 2. 实时流式播放

```python
# 串行播放音频，段落间有 1-5 秒自然停顿
engine.run_streaming(
    max_steps=10,
    save_audio=True,
    convert_to_mp3=True  # 自动转换为 MP3
)
```

### 3. 多语言支持

自动检测输入语言并生成对应内容：

| 语言 | Topic 示例 | 输出 |
|------|-----------|------|
| 中文 | 食堂打饭遇到的趣事 | 中文剧本 + 中文TTS |
| 英文 | Complaining about algorithm | 英文剧本 + 英文TTS |
| 日文 | 秋葉原でグッズ購入 | 日文剧本 + 日文TTS |

### 4. 用户记忆系统

- 自动记录观众互动次数
- 根据亲密度调整回复风格（陌生人→眼熟→老观众→核心粉丝）
- 新观众自动发送欢迎消息

## 项目结构

```
echuu-agent/
├── echuu-sdk-release/     # SDK 发布目录（可独立使用）
│   └── echuu/
│       ├── core/          # 核心模块
│       ├── generators/    # 剧本生成
│       ├── live/          # 直播引擎
│       └── vrm/           # VRM 控制
├── workflow/              # 工作流程脚本
├── data/                  # 数据文件
├── output/                # 输出文件
│   ├── scripts/           # 生成的剧本
│   └── archive/           # 归档文件
└── demo.py                # 演示脚本
```

## 输出示例

### 剧本输出（JSON）

```json
{
  "metadata": {
    "timestamp": "20260207_201136",
    "name": "小梅",
    "topic": "食堂打饭遇到的有趣故事"
  },
  "script": [
    {
      "id": "line_0",
      "text": "不知道为什么我突然想起以前的一个事...",
      "stage": "Hook",
      "cost": 0.2,
      "cue": {
        "emotion": {"key": "neutral", "intensity": 0.7},
        "gesture": {"clip": "react_think", "duration": 2.0},
        "look": {"target": "camera", "strength": 0.8}
      }
    }
  ]
}
```

### VRM 控制指令

```json
{
  "type": "expression",
  "blendShape": "happy",
  "weight": 0.85,
  "fadeIn": 0.15,
  "fadeOut": 0.25
}
```

## 文档

- [FEATURES.md](FEATURES.md) - 新功能说明（MP3 转换、流式播放）
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - 架构设计
- [docs/MULTILINGUAL_SUPPORT_PLAN.md](docs/MULTILINGUAL_SUPPORT_PLAN.md) - 多语言支持
- [docs/USER_MEMORY_SYSTEM.md](docs/USER_MEMORY_SYSTEM.md) - 用户记忆系统

## 依赖

- Python 3.10+
- Gemini 3 API（或 Anthropic/OpenAI）
- DashScope API（Qwen TTS）
- ffmpeg（音频处理）

## 许可证

Apache-2.0