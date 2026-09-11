# Echuu 算法预研交接

协作分支：`codex/research-agent`。本分支从 2026-09-11 的 agent `dev` 建立，用于算法实验、生成质量评估和本地联调，不代表上线版本已验收。

## 行为目标

输入：本次人设、背景、主题，以及真实收到的弹幕或礼物。
输出：主播台词、声音与动作提示。steer 应将弹幕内容或礼物语义接入还没播的故事，保留本次设定；收到礼物不能只道谢，接入后也不能替换成示例故事。

示例只指导表达节奏，不提供人物经历或事实。当前采样器仅传表达统计；当前用户设定、历史记忆和联网参考区分来源，开场/收尾共享本次背景，收尾另收到实际已播台词。

## 获取配套代码

两个仓库使用同名分支：

```bash
git clone --branch codex/research-agent https://github.com/echuu-ai/nextjs-vtuber-mocap.git
cd nextjs-vtuber-mocap
git clone --branch codex/research-agent https://github.com/CoryLee1/echuu-agent.git echuu-agent
```

需要两个私有仓库的读取权限，分支链接本身不会授予访问权。

启动步骤见主仓库 [.agents/skills/echuu-startup-alignment/SKILL.md](https://github.com/echuu-ai/nextjs-vtuber-mocap/blob/codex/research-agent/.agents/skills/echuu-startup-alignment/SKILL.md)。Python 3.10+，先安装根 requirements.txt 和 echuu-web/backend/requirements.txt；凭据自行放本地 `.env`，不提交。

- agent：在 `echuu-web/backend` 用本机虚拟环境执行 `python -m uvicorn main:app --host 127.0.0.1 --port 8000`。
- 主前端：`echuu-ux-r3f-vite`，5174；agent REST/WS 指向本地 8000，`VITE_ECHUU_USE_LIVEKIT=0`。完整环境覆盖命令见启动 skill，平台账号请求仍可能使用云端。
- 源码改动后重启不带 reload 的 Python 服务；核对 PID、启动时间，避免复用旧代码。

## 代码入口与验证

- 剧情生成：`echuu/generators/legacy_v4.py`、`shareable_clip.py`。
- few-shot 隔离：`echuu/generators/example_sampler.py`。
- 弹幕/礼物 steer：`echuu/live/story_steerer.py`，`engine.py` 的 `_steer_remaining_show`。
- 预研评测：`echuu/harness/README.md`；实验输出保留在本机。
- 回归：`python -m pytest tests -q`，2026-09-11 本机 301 passed。pytest 提示现有 asyncio_mode 配置未识别。

## 当前未通过项

few-shot 隔离定向测试通过，但不能据此宣称完整产品已修好。最近最终代码两次整场生成分别被 `clip constraints unresolved` / `semantic repair unresolved` 拦截，不能放松检查冒充成功。细节见 [复测记录](2026-09-11-fewshot-leakage-retest.md)。报告引用的 output 目录为 Cory 本机证据，不随分支分发。

建议下一步先复现同一主题“年终奖买苹果折叠屏”，核对主题前提、红包弹幕、信封礼物改写和收尾一致性；失败输出应落盘、分析拒绝原因，质量问题与 few-shot 串入分开记录。

分享内容含所依赖的本地生成器与 harness 源码、测试和文档；不含数据库、密钥、录屏、音频或实验输出。本分支不自动合并到平台后端或前端发布分支。
