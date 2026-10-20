# Python SDK 交付说明

## 2026-10-18 自适应字数补写与可见失败

完整改写连续得到短稿时，改为补写一个最短段落，保留其余段落和逐字引用；
补写后仍不足则按剩余缺口继续，沿用总调用最多 6 次的上限。
所有结果继续经过原字数、5–10 段、编号、引用及表达约束校验，未达标仍拒绝。
`ClipValidationError.details` 向宿主提供字数范围、实际字数和问题代码，供页面展示安全失败原因。
不将正文原文或供应商响应直接传到宿主页面。

新增定向补写、保留原稿、260 字仍拒绝并继续修复、最终不足仍失败的回归。
专项 18 passed，源码全量 412 passed；真实同一联调频道设定连续三次通过：307、342、292 字，
分别调用 4、5、2 次。初版定向补写曾仍得到 260 字，已保留失败结果并在自适应补写后复测。
这些样本不保证所有模型输出都成功，失败仍必须明确显示并允许受控重试。
制品需要下游真实语音/礼物/回执验收后才推广；没有推送或部署。

## 2026-10-18 正文草稿修复候选

本地 `codex/sdk-clip-repair` 修复预生成遇到段落数/JSON 格式错误直接退出、
以及一次字数修复仍不合规的问题。格式和内容问题均进入最多两次的完整草稿修复，
修复提示包含实际字数、剩余字数和原有预算；最终仍执行原校验。
5..10 段、连续 ID、非空文本、字数上下限、真实引用和既有表达限制没有放宽。
未启用 `ECHUU_FAST_CLIP`，也未将不合格正文填充为成功结果。

新增 6 个回归用例，修复前 5 failed / 1 passed，修复后相关测试 15 passed。
现有拒绝不合格草稿的测试补足第二次模拟修复响应，仍断言拒绝及有限调用次数。
补齐临时 Web 测试依赖后，源码全量 409 passed（6 个已有弃用警告）。
初次全量收集缺少 fastapi/boto3，补齐后发现还缺少 sqlalchemy；
随后使用 Web requirements 的临时环境重跑通过，未修改依赖配置。
候选制品须通过下游真实模型、语音和回执验证后再推广；没有推送、发布或部署。

本分支 `codex/sdk-baseline` 以 dev `72f50d81ff22693d5389b6b2705b58e71657fcc6` 为基础，提供可安装配置、LiveSession、礼物契约、播放确认、归档与构建工具。目标合入 dev；目前是测试候选实现，非正式稳定发行。

下游当前使用源码 `2c6f5df384a966c896dde6e72d3ded8a1566f1ff` 构建的 `0.2.0.dev2+g2c6f5df384a9`。本分支之后的文档提交不改变该 wheel。下游 [集成交付指南](https://github.com/echuu-ai/echuu/blob/codex/sdk-gift-integration/docs/echuu-sdk-handoff.md) 是产品测试入口；其中 manifest 固定源码与 wheel 哈希。

## 干净安装验证

前提：Git、uv 和依赖下载网络；以下为 POSIX shell。完整克隆以保留构建所需历史与标签。构建拒绝 dirty checkout 和非空输出目录，故验证环境与产物放在仓库外。

```bash
git clone --branch codex/sdk-baseline https://github.com/echuu-ai/echuu-agent.git
cd echuu-agent
git rev-parse HEAD
SDK_CHECK_DIR=$(mktemp -d)
uv run --no-project scripts/build_sdk.py --out-dir "$SDK_CHECK_DIR/dist"
for SDK_PYTHON in 3.10 3.11; do
  uv venv --python "$SDK_PYTHON" "$SDK_CHECK_DIR/py$SDK_PYTHON"
  uv pip install --python "$SDK_CHECK_DIR/py$SDK_PYTHON/bin/python" "$SDK_CHECK_DIR"/dist/*.whl pytest pytest-asyncio pydub
  uv run --no-project --python "$SDK_CHECK_DIR/py$SDK_PYTHON/bin/python" scripts/verify_sdk_baseline.py --output "$SDK_CHECK_DIR/report$SDK_PYTHON"
done
```

每条命令必须退出 0；安装门禁在 checkout 外运行，禁止模型网络调用，并写出命令、日志和报告。保留输出目录供 PR 附件上传，不把本机临时路径当长期证据。新 HEAD 的 Git 派生版本可能高于下游固定版本，这是预期行为；不会自动修改下游依赖。要复验下游当前制品，可从下游 vendor 取得 wheel 并替换安装命令中的 wheel 路径。

## 验收与发布

API、配置、宿主生命周期和 GiftBatchInput 见 [使用说明](SDK_USAGE.md)。原 Web 应用需 Python 3.11；它不包含在 Python wheel 中。库本身支持 Python 3.10 起。

10 月 6 日功能提交曾通过源码 403 项、wheel 双版本各 107 项测试；这不代表新 checkout 的检查已经执行。仓库历史全量 lint 尚有债务；真实模型/TTS、双会话音色、目标部署环境仍需单列验收。Web 需确认正常结束/取消时四类归档与记忆确认状态，产品验收步骤见下游指南。

提交到 dev 的 PR 触发 `.github/workflows/sdk-build.yml`，检查构建和双 Python 安装报告；单独推送 codex 分支不触发该工作流。通过后仍须维护者配置 `sdk-release` 审核环境，并手动 dispatch `publish=true` 才发布 Release。未执行的 Actions、发布或灰度不能标记为成功。独立 LICENSE 文件仍需维护者确认。

下游通过已发布 manifest/源码 SHA/wheel 哈希提出升级 PR；不会直接跟随移动的 dev，也不会自动合并或部署。上游不需要引入下游数据库或 LiveKit 依赖。

## 2026-10-09 交付复测

重新建立 Python 3.10.21 / 3.11.16 环境，安装下游固定的 dev2 wheel，再分别运行 `scripts/verify_sdk_baseline.py --output <仓库外新目录>`：probe 与 regression 均退出 0，各 **107 passed, 1 warning**。警告为 DashScope Assistants API 弃用提示。未重跑全量源码/真实模型测试；这些结果不冒充远端 Actions 结果。
