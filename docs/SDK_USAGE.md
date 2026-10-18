# 作为 Python 包使用

本次改动基于 dev 的 `72f50d81ff22693d5389b6b2705b58e71657fcc6`。
现已提供 U1 配置和 U2/U3 的 LiveSession 门面，公开契约见下文。
下游应依赖经过验证的 wheel 或完整提交，不直接跟随移动的 dev 分支。
下游当前验收制品为 `0.2.0.dev2+g2c6f5df384a9`，源码提交 `2c6f5df384a966c896dde6e72d3ded8a1566f1ff`。
尚未作为正式 Release 发布；版本由干净 Git 提交派生，不覆盖同版本制品。
交付入口与安装验证见 [SDK_HANDOFF.md](SDK_HANDOFF.md)。

## 构建与安装

```bash
uv run --no-project scripts/build_sdk.py --out-dir /tmp/echuu-sdk-new-build
# 将实际产物路径传给宿主 uv；不要从未构建源码安装 0+unbuilt
uv add /absolute/path/to/echuu-<构建版本>-py3-none-any.whl
```

Python 包支持 Python >=3.10。源码 Web 预研应用使用 `asyncio.timeout_at`，
要求 Python >=3.11；`scripts/start_research.py` 在读取环境文件前检查版本。
Web 应用仍在源码目录中，不随 wheel 发布。

基础 wheel 包含 Qwen TTS 实现。MP3 转换需要 `audio` extra 中的 pydub，
以及宿主安装的 ffmpeg；基础 WAV 输出不需要它们。
安装时可使用 `uv add '/absolute/path/to/echuu-<构建版本>-py3-none-any.whl[audio]'`。
`all` 包含 audio/web/notebook/dev。Pydantic 2 现在是直接声明的依赖。
其余模型依赖暂时保留：根包仍公开导出各 provider，完整的延迟导入拆分需要后续改造。

## 显式配置

```python
import os
from echuu import EchuuLiveEngine, EngineConfig, TTSConfig

engine = EchuuLiveEngine(config=EngineConfig(
    provider="qwen",
    model="qwen-plus",
    api_key=os.environ["DASHSCOPE_API_KEY"],
    tts=TTSConfig(
        api_key=os.environ["DASHSCOPE_API_KEY"],
        voice="Cherry",
        model="qwen3-tts-flash-realtime",
    ),
))
try:
    # setup 开始调用模型；run 是同步生成器。
    engine.setup(name="六螺", persona="爱吐槽的女主播", topic="下班路上的小事")
    for step in engine.run(max_steps=3, save_audio=False):
        print(step["speech"])
finally:
    engine.tts.close_preparation()
```

构造函数不加载 `.env`、不调用生成 API、不创建输出目录。
`EngineConfig()` 和 `TTSConfig()` 本身不读取环境变量。
显式配置缺少模型 provider/key 时会报错，除非注入 `llm`。
省略 TTS key 表示禁用语音，不会偷偷使用进程中的 key。
两个引擎可以分别设置 Cherry、Serena，无需修改 `os.environ`。

`annotated_data_path`、`examples_path` 接受文件路径；不指定则不加载参考语料。
指定的文件不存在时立即抛出 `FileNotFoundError`。原始数据不随 wheel 发布。
默认不自动保存生成的剧本；传入 `output_dir=Path(...)` 才启用导出。
`run(save_audio=True)` 和历史 `run_streaming()` 仍是显式保存音频的入口。
需要 harness 语料库等外部资源的功能仍需源码资源，尚未承诺完整离线可用。

也可注入 `EchuuLiveEngine(config=EngineConfig(), llm=my_llm, tts=my_tts)`。
实例会直接使用提供的对象；宿主负责其生命周期。
模型对象沿用现有 `.call(prompt, system=..., max_tokens=...)` 契约，语音对象需实现
所使用引擎路径要求的合成/准备方法。稳定的 Protocol 与统一 close/cancel 留在后续阶段。

## 旧入口与迁移

- `EchuuLiveEngine()` 继续从现有环境变量构建兼容配置；源码 checkout 可复用现有 data/output 位置。
- 如需 `.env`，由应用入口显式调用 `load_dotenv`；库不再自动向上查找和读取它。
- 可用 `EngineConfig.from_env()` 显式捕获环境；提供 `project_root=...` 才选择源码默认数据与导出位置。
- `workflow.backend.tts_client` 在源码仓库保留兼容导入；wheel 中的正式路径是 `echuu.live.qwen_tts`。
- 显式 TTS 配置初始化失败会抛出异常；无参数直接构造 `TTSClient()` 保留旧的禁用并报告错误行为。
- 公开示例使用 `run()`，旧文档中的 `run_sync()` 实际不存在。

## 当前隔离边界

实时 TTS 在连接前绑定实例密钥，不写入 DashScope 的进程默认 key。
旧离线 SpeechSynthesizer 只能在构造时读取全局 key；兼容适配器加锁、临时设置、立即恢复。
这个锁仅协调本包内部的构造，不能保护宿主直接并发创建的其他 DashScope 离线对象。
需要严格隔离时使用实时路径或注入宿主自己的 TTS 实现。
Qwen LLM 的地域配置仍由旧适配器读取 `QWEN_REGION`；需要完全独立的 provider 配置可注入客户端。
U1 不代表全部缓存、线程、取消和播放反馈已经隔离。

回归使用确定性替身并禁用网络；不等于真实模型、音频听感或 LiveKit 已验收。
发布前仍需维护者确认版本和许可证：现有元数据标 MIT，但仓库缺少独立 LICENSE 文件，
本次不替维护者补写授权声明。

## LiveSession（0.2.0a4 开始）

```python
from echuu import LiveSession, SessionInput, EngineConfig, TTSConfig

# config 的密钥由宿主提供，沿用上文的显式配置。
session = LiveSession(
    SessionInput(session_id="stream-id", name="六螺", persona="爱吐槽", topic="下班路上"),
    config,
)
try:
    await session.prepare()  # 同步引擎在专用线程运行；这里不阻塞事件循环
    await session.start()
    async for event in session.events():
        if event.type == "step":
            step = event.step
            await session.report_playback(step.step_id, "COMMITTED")
            await host_play_audio(step.audio_data, step.audio)
            await session.report_playback(step.step_id, "PLAYED")
        elif event.type == "closed":
            if event.payload["reason"] != "completed":
                raise RuntimeError(event.payload["reason"])
finally:
    await session.aclose()
```

`host_play_audio` 由宿主实现，返回表示输出端已经播放完毕；收到字节或入队不等于完成。
中断用 CANCELLED；只有 COMMITTED 可转为 PLAYED。重复回执幂等，乱序或未知步骤报错。
单会话只有一个事件消费者，一次仅暴露一个待播步骤；准备阶段预取一段正文音频，
开场可选，超过等待期限时跳过，不丢弃已经生成的正文。
`SessionOptions` 配置准备/操作/播放期限、事件容量和互动容量。
全量原稿预生成改为有界预取，后续段在上一段完成后推进；改稿仍遵循原有快照、音频就绪及原稿校验。

使用 `submit_interaction(Interaction(...))` 非阻塞提交。去重键为 source/user_id/event_id，
作用域为当前会话；容量耗尽抛出 CapacityExceeded，停止/收尾后抛出 InvalidTransition。
结构化 chat/gift/collect/tangent 继续使用原有审核和后台改稿逻辑。
`played_steps` 是确认播出的完整片段；显式 estimated=True 的浏览器估算结果另放 `estimated_steps`。
生成的剧情、人物扩写和未采用改稿不能直接并入用户的长期事实记忆。

stop/aclose 幂等；停止会封闭输出并取消未完成播放，不能强制终止同步 provider 调用。
清理操作在专用线程中排在运行中的调用之后执行，后台模型和语音仍依赖自身网络超时退出。
注入 driver 的资源所有权由其 close 契约确定；直接注入旧 engine 的 StorytellingDriver 不关闭调用者的 TTS。

`echuu.session_types` 发布 SpeechProvider、SessionDriver、SessionRecorder 协议及错误分类。
可在 `StorytellingDriver(..., engine=EchuuLiveEngine(..., llm=..., tts=...))` 注入现有客户端；
LiveSession 接受 driver 和异步 recorder。记录器故障显式终止会话；终止阶段故障仅记录错误类型，不能阻止关闭。
音频元数据和 bytes 分开：JSON 序列化不会包含 audio_data，首版会话音频契约为 PCM16 WAV。

runtime 的打印改为 `echuu.diagnostics` DEBUG 日志，默认静默；eval/CLI 的结果打印保留。
ready 事件携带 SDK 版本、provider/model/pipeline 和准备指标，不包含密钥。
这些元数据帮助定位版本，不能保证模型逐字复现。

公开类型门禁：

```bash
uv run --with mypy mypy --config-file mypy-public.ini echuu/session_types.py echuu/session.py echuu/config.py
```

## 兼容与发布策略

此前 0.2.0a4 是临时验收版本；现在版本由 Git 自动生成，未上传公开索引。保留 EchuuLiveEngine.setup/run 与旧 TTS 导入路径。
新增门面采用 schema_version=1；破坏字段或状态语义时提高 schema 主版本并提供迁移说明。
正式发布前不把 alpha 接口当作永久承诺；发行变更须通过仓库外安装门禁和下游播放/数据库验收。
Web storytelling 的两种入口共用 LiveSession；reaction/singing 保持原入口。
旧 Web 播放协议没有真实回执，适配器明确标注 estimated，不能宣传为确认已播。


## 从 Git 构建可追溯版本

```bash
uv run --no-project scripts/build_sdk.py --out-dir /tmp/echuu-sdk-new
```

构建要求完整 Git 历史及干净工作区，拒绝未跟踪文件、未提交改动、浅克隆及非空输出目录。
从 HEAD 的 git archive 构建，不复制工作目录中的缓存或秘密文件。
`echuu/_build.json` 保存完整 source_commit、base_commit、base_tag、提交距离与包版本。
`echuu/_version.py` 由构建生成，运行时版本与包元数据一致；未构建源码显示 `0+unbuilt`。
sdist 包含生成后的版本和来源，离开 Git 仓库重建 wheel 仍保留同一版本。

没有正式标签时，以 `72f50d81ff22693d5389b6b2705b58e71657fcc6` 为计数起点：
`0.2.0.dev<N>+g<12位源码SHA>`。这个基线是开发约定，不伪造上游发布标签。
有可达的 `vX.Y.Z` 或 `X.Y.Z` 标签后，选择提交距离最近的标签；同距离取最高版本。
HEAD 就是标签时输出 `X.Y.Z`；后续提交默认递增 patch，输出 `X.Y.(Z+1).dev<N>+g<SHA>`。
N 是 `git rev-list --count <base>..HEAD`，不手动维护；相同源码和标签集合得到相同版本。
标签改变会影响计算，正式标签应不可变；合并/rebase 后按最终提交重新构建。
完整 SHA 用于追溯，短 SHA 用于展示；哈希保证制品内容，版本不保证二进制逐字节可复现。

携带 `+g...` 的开发包用于文件/GitHub Release/私有索引，不能上传公共 PyPI。
正式发布使用维护者确认的版本标签。自动触发构建需要后续 CI 配置，本脚本不监听远端。


## 可靠礼物与播放门禁

`GiftBatchInput` 携带可信宿主提供的事件、用户、礼物价值/效果/句数快照及 `start_within` 剩余时限。
`submit_gift_batch()` 返回本批感谢实际播完的 Future[bool]；只允许一个在途批次，同 ID 同内容复用结果，内容冲突拒绝。
`start(autoplay=False)` 只消费礼物，正文不推进；宿主确认业务 LIVE 后 `set_autoplay(True)`。
礼物步骤携带 purpose=gift_ack、batch_id、event_ids。宿主必须先向自己的交付系统确认 start，核对期限，再 COMMITTED/播放/PLAYED。
感谢失败/过期回报 CANCELLED，效果不生效；只有感谢 PLAYED 后才提交仍匹配原稿和 revision 的候选效果。
生成/改写默认预算 2 秒；宿主可通过 `EngineConfig.gift_model_timeout_seconds` 设置最多 8 秒，实际预算还受本批剩余时间一半限制，为语音预留时间；不会延长批次启动期限。异常保持原稿和固定感谢；候选音频准备失败同样保持原稿。效果最多一种且最多三行，不跨会话继承。
`StorytellingDriver.gift_diagnostics` 提供最近 20 批的脱敏效果证据，区分 prepared/applied/played 与失败原因；仅真实 PLAYED 回调覆盖一行全部音频分段后计为已播，感谢成功不等于效果成功。证据含原稿/改写哈希、计数、耗时与字段校验代码，不含原文或供应商响应。
礼物感谢与已验证的改写音频在独立的两线程语音池中提前排队，全部准备后仍按原顺序播放；未变化的句子不重新合成。
`audio_seconds` 是整个语音阶段墙钟耗时，`ack_audio_seconds` 是感谢音频就绪耗时，`audio_chunks` 是任务数量。
`audio_tasks` 仅包含感谢/改写位置、排队秒数、生成秒数、尝试次数和状态；并发任务耗时不能相加，生成耗时包含重试。
TTS 等待 `session.updated` 后才发送文本，提前断连或错误不接受部分音频；准备缓存也不接受缺失呼吸分组的音频。
所有任务共享原批次启动期限，失败后取消尚未开始的任务；感谢实际播完前不采用候选改写，也不提前确认实际播放成功。
已在运行的同步模型调用无法强杀，超时后的结果不得发布；新礼物不排队堆积同一模型工作线程。
`finish()` 拒绝新输入并有界等待当前 COMMITTED 步骤；`stop()` 强制封闭并取消未完成步骤。
宿主没有可靠回执时必须 estimated=True，不能将估算当作可靠礼物成功。

## 公开归档

`session.export_artifacts()` 返回缓存的 `SessionArtifacts`，包含原始剧本（含旧 metadata）、可空人物小传及终态记忆。
宿主保存文件/S3；SDK 不隐式落盘。确认和估算历史分别输出，story_points 只来自确认播出，承诺只保留内容实际出现在确认文本中的未履行项。
Web 补回 full_script.json/dossier.json，并写 artifacts.json 标明缺失资料。旧记忆缺少来源标志时不再自动作为确认经历注入。

## 自动化

`.github/workflows/sdk-build.yml` 在 dev/PR/tag 上构建和验证双 Python 安装；发布须手动 workflow_dispatch publish=true，并经过 sdk-release 环境。
维护者应为该环境配置审核人。发布 GitHub Release 的版本及源码 manifest，拒绝覆盖已有 tag；本地验证不能证明远端工作流已经执行。
