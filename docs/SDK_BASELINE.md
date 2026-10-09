# SDK 安装与行为基线（P0）

> 历史基线与修复过程，不是当前状态或安装指引。当前交付见 [SDK_HANDOFF.md](SDK_HANDOFF.md)。

2026-10-05；源基线 `dev@72f50d81ff22693d5389b6b2705b58e71657fcc6`。
本批仅新增验证工具与报告，不修改生成、提示词、TTS、依赖声明或既有测试。

## 结论

源码环境的定向回归通过，正式安装仍不能作为自包含 SDK 使用。

| 检查 | 实测结果 |
|---|---|
| wheel 与 sdist 构建 | 成功，版本 0.1.0 |
| 仓库外导入 | 成功，确认导入路径位于虚拟环境 site-packages |
| wheel 运行文件 | 110 个；包含 105 个 Python 文件 |
| TTS 实现 | wheel/sdist 均不包含动态加载所需的 workflow/backend/tts_client.py |
| 示例数据 | wheel 不包含 data/ 或 harness JSON 资源；各资源是否必需需在 U1 明确 |
| 已安装包定向回归 | 72 passed，1 failed |
| 源码定向回归 + REST/WS | 75 passed，7 warnings |
| 包首页示例 | 调用 run_sync()，类中没有该方法 |
| Python 3.10 检查 | 3.10.21 无 asyncio.timeout_at；Web storytelling 使用了该接口 |

安装环境 Python 3.11.16；pytest 使用确定性模型/音频替身，禁用网络连接。
本次 wheel SHA-256：`b291df8d9607569a7cdeddf22414bedba9bdf2eee282454898756405acbf1dbb`。
本次 sdist SHA-256：`b8e65b7b162d94095833d9b07318e43eaf864e8e4131dc76b74dc15c54e7dcd0`。
子进程不继承模型凭据或 PYTHONPATH，并设置 PYTHON_DOTENV_DISABLED=1。
源码 API 测试使用自身临时 SQLite 配置，没有初始化或迁移实际数据库。
这不证明真实模型、音频听感、LiveKit、生产数据库或长时间并发已经通过。

## 安装失败原文

```text
FAILED tests/test_topic_evolution.py::test_project_root_is_independent_of_checkout_name_and_working_directory
RuntimeError: Cannot locate echuu project root (pyproject.toml + workflow/backend)
1 failed, 72 passed in 0.86s
```

该测试在源码中通过，在已安装包中失败；保留原断言，未跳过或标记 xfail。
TTS 加载探针返回 None；因此构建成功和 import 成功不能等同 SDK 可运行。
源码测试结果：`75 passed, 7 warnings in 6.01s`；警告涉及 Pydantic Config、SQLAlchemy declarative_base、crypt 弃用。

## 复现命令

先选择一个新的仓库外目录，以下路径是本次实际使用的目录。

```bash
uv build --out-dir /tmp/echuu-sdk-p0.Awd4pT/dist
uv venv --python 3.11 /tmp/echuu-sdk-p0.Awd4pT/venv
uv pip install --python /tmp/echuu-sdk-p0.Awd4pT/venv/bin/python /tmp/echuu-sdk-p0.Awd4pT/dist/echuu-0.1.0-py3-none-any.whl pytest pytest-asyncio
uv run --no-project --python /tmp/echuu-sdk-p0.Awd4pT/venv/bin/python scripts/verify_sdk_baseline.py --output /tmp/echuu-sdk-p0.Awd4pT/checks
uv pip install --python /tmp/echuu-sdk-p0.Awd4pT/venv/bin/python -r echuu-web/backend/requirements.txt
uv pip install --python /tmp/echuu-sdk-p0.Awd4pT/venv/bin/python boto3 ruff
uv run --no-project --python /tmp/echuu-sdk-p0.Awd4pT/venv/bin/python scripts/verify_sdk_baseline.py --source-regression --output /tmp/echuu-sdk-p0.Awd4pT/source-checks
```

`boto3` 来自根 requirements.txt，是 Web 归档模块的导入依赖。
两个验证命令均返回 1：第一个包含安装回归失败；第二个源码回归通过，但安装探针仍发现阻塞。
工具不会把已知缺陷隐藏为成功，也不会自动重跑失败用例。输出目录必须不存在。

每次输出包括 report.json（执行命令与返回码）、probe.json（解释器/依赖版本/导入路径）、完整日志和 event-samples.json。
事件样例覆盖正常步骤、即时回应、改稿超时与停止；基于现有测试替身，不代表真实 LLM 全流程。
临时目录是本次证据，长期复现应重新运行脚本并保留产物；当前依赖解析结果不作为正式发布锁文件。

## 验证工具质量

初次 lint：`I001 [*] Import block is un-sorted or un-formatted`、`PLW1510 subprocess.run without explicit check argument`。
导入调整后仍有 I001 空行格式问题，使用 ruff 的导入修复解决；最终检查通过：

```bash
uv run --no-project --python /tmp/echuu-sdk-p0.Awd4pT/venv/bin/python ruff check scripts/verify_sdk_baseline.py
git diff --check
```

## U1 的具体范围

迁移 641 行 workflow/backend/tts_client.py 到发行包，旧路径兼容转发；改为正常导入。
解除引擎初始化对 checkout 根目录的要求；显式配置模型、音色、数据及输出路径。
补充正常构造和 wheel 安装回归，不依靠 __new__ 绕过构造函数。
修复 run_sync 文档并决定 Python 3.10 支持边界；不顺便更改内容算法。
这批超过 300 行，用户已批准 U1 范围；实现及离线安装回归已完成，尚未提交或发布。

## U1 验证（2026-10-05）

使用方式与兼容限制见 [SDK_USAGE.md](SDK_USAGE.md)。
新增配置测试先复现 12 项失败（`/tmp/echuu-sdk-p0.Awd4pT/u1-red.log`），
实现后扩充为 16 项配置/旧路径回归，全部通过。
原根目录测试改为构造任意名称 checkout，保持原断言语义；另加真实构造测试验证安装包，
没有用跳过或弱化断言掩盖原安装故障。

执行命令（上游仓库目录）：

```bash
uv build --out-dir /tmp/echuu-sdk-p0.Awd4pT/u1-final-dist
uv run --no-project --python /tmp/echuu-sdk-p0.Awd4pT/u1-venv/bin/python scripts/verify_sdk_baseline.py --output /tmp/echuu-sdk-p0.Awd4pT/u1-final-installed
uv run --no-project --python /tmp/echuu-sdk-p0.Awd4pT/u1-py310/bin/python scripts/verify_sdk_baseline.py --output /tmp/echuu-sdk-p0.Awd4pT/u1-final-installed-py310
uv run --no-project --python /tmp/echuu-sdk-p0.Awd4pT/venv/bin/python scripts/verify_sdk_baseline.py --output /tmp/echuu-sdk-p0.Awd4pT/u1-source --source-regression
uv run --no-project --python /tmp/echuu-sdk-p0.Awd4pT/venv/bin/python ruff check echuu/config.py tests/test_sdk_configuration.py tests/test_sdk_legacy_import.py scripts/verify_sdk_baseline.py workflow/backend/tts_client.py
git diff --check
```

- 两个干净环境只安装基础 wheel、pytest、pytest-asyncio；最终 wheel 通过 `uv pip install --python <环境>/bin/python --reinstall --no-deps <wheel>` 更新。
- Python 3.11.16：88 passed，1 条 DashScope 弃用警告。
- Python 3.10.21：88 passed，1 条 DashScope 弃用警告。
- 源码及 REST/WS：91 passed，8 warnings（源码行为验证完成后只补充发布元数据、文档与 Web 版本检查）。
- Web 3.10 入口预期退出码 2，原文：`Research backend requires Python >=3.11 (asyncio.timeout_at); the SDK supports >=3.10`。
- 新增文件 lint 与 diff 空白检查通过。
- 不读取环境文件、不连接 provider；事件样本与依赖版本保存在各报告目录中。

最终构建 SHA-256：

| 产物 | SHA-256 |
|---|---|
| wheel | `9fbdf390f214944c600e87f458b1deac1f6e6e69d6ebe7f114ff7cd633c7c8b9` |
| sdist | `45c0430ec9e044d109afd6ee8d21cb97496a65ef0cab35f43aa4a3c05f261f69` |

### 未通过的检查及边界

新增测试/兼容入口最初 lint 原文：`I001 [*] Import block is un-sorted or un-formatted`、
`PLC0414 Import alias does not rename original package`，现已修复。
扩大范围检查最初输出 `Found 108 errors.`；修正导入及新增 return 诊断后，以下命令仍失败：

```bash
uv run --no-project --python /tmp/echuu-sdk-p0.Awd4pT/venv/bin/python ruff check echuu/config.py echuu/live/qwen_tts.py echuu/live/tts_client.py echuu/live/engine.py echuu/__init__.py --output-format concise
```

最终原文：`Found 101 errors.`，完整输出在 `/tmp/echuu-sdk-p0.Awd4pT/u1-final-full-lint.log`。
对同样四个历史文件（新 qwen_tts 对应旧 workflow TTS）以相同配置比较：
dev 基线 110 项，本次 101 项，按规则与诊断文本计数没有新增项，见 `u1-lint-delta.json`。
未禁用 lint 规则或批量整理无关代码，因此不能声称整个上游 lint 已通过。

未运行真实 LLM/TTS、听感和 LiveKit 测试；未发布包、PR 或修改下游依赖。
尚需后续拆分 provider 延迟导入、完善统一资源清理，并由维护者确认许可证与正式版本。

## 2026-10-06：U2–U4 与下游接入验收补充

以上 U1 记录保留历史语境。当前开发制品为 `0.2.0a4`，已经增加 LiveSession、StorytellingDriver、公开事件/扩展协议、Web 宿主迁移和下游 LiveKit 接入。
最终 wheel 在 Python 3.10.21 和 3.11.16 仓库外安装回归均为 **102 passed**；公开配置/事件/会话严格类型检查通过。
下游 Agent **48 passed**，真实 PostgreSQL 相关测试 **10 passed**，本地 LiveKit 1.13.7 真实发布/订阅与播放完成验证通过。

最终 wheel SHA-256：`cae70ca8c077649c2cb8aab47eeb87cc975903a09e693a7bf46c9fa91f13615e`。
最终 sdist SHA-256：`1c7341c866e5e0f980ef8ac9c7454a1c0004deb0bec270ed0261289411b7a78d`。
安装回归报告在 `/tmp/echuu-sdk-final-py310` 和 `/tmp/echuu-sdk-final-py311`。

扩大源码检查为 **396 passed, 1 failed**，失败 `tests/test_prompt_example_literals.py::test_prompts_carry_no_quotable_examples`：
`AssertionError: prompt 模板里仍有可复述例句: {'danmaku_interleave': ['先回原来的故事']}`。
基线包含同一提示词，本次未弱化断言或更改提示词。历史实现 lint 仍为 `Found 101 errors.`。
真实 provider、音色听感、线上灰度、正式发布与许可证确认尚未完成；当前制品不代表维护者已发布或认可。
