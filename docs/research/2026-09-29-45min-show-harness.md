# 从 1-2 分钟杂谈到 45 分钟直播：echuu-agent 的 harness 调研与方案（2026-09-29）

轨道：research（本地 echuu-agent 预研）。本文是调研与思考，不含代码改动；承接
`2026-09-12-harness-and-vtuber-roadmap.md` 第四梯队（#11 流水线化、#12 心跳编导、#13 跨片段账本）。

## 一、先说结论

1. **没有现成的 "AI VTuber performance harness" 可以直接拿来用。** 市面上的开源项目分两类：
   语音 agent 框架（LiveKit Agents / Pipecat）解决传输、打断、轮次，不管"节目好不好看"；
   AI VTuber 项目（Open-LLM-VTuber / AIRI / Ikaros AI-Vtuber）是"聊天机器人 + 皮套"，
   靠观众喂话，没有叙事结构。echuu 的强项（故事内核、结构破坏、gag 账本、steering）
   正好是它们没有的，反过来它们的强项（平台弹幕接入、ASR/TTS 插件化）对 45 分钟目标帮助不大。
2. **所有真正跑过长时间的系统都不是"一条长稿"，而是"节目单 + 编导循环 + 片段生产器"。**
   Neuro-sama（2-3 小时，多环节轮换）、Nothing Forever（24/7，director 模块管理场景切换与缓冲）、
   DLIOS（抖音 36 小时压测，四段式 prompt 调度 + 弹幕快速反应引擎）、Fable Showrunner
   （14 场 22 分钟一集，synopsis → 场景 → 台词的 prompt chain）。
3. **echuu 现有 3-4 分钟片段生成器不需要重做，它就是 45 分钟节目里的"一个 block"。**
   缺的是上面一层：节目单（rundown）、按库存触发的编导循环、贯穿全场的账本、
   多样性约束、以及一个几乎不会失败的兜底环节。
4. **对 45 分钟质量没信心的真正来源不是单句质量，而是四件事**：重复感（同一套故事形状用 12 次）、
   没有全场弧线、跨片段记忆断裂、冷场/等待。这四件都在 harness 层，不在模型层。

## 二、现状诊断（基于本地代码）

- `engine.create_performance()`：一次性生成 2-4 个 unit 的完整 Show，全稿过 gate 后才开播。
  `run(max_steps=12)` 按行播放，每行结束处理一条弹幕，`topic_evolution` / `story_steerer`
  改未播行。这是"编剧写完 → 演员照稿演 → 观众推一下改后面几句"的范式。
- 直接把 `max_units` 拉到 30 的问题：
  - 长稿一致性随长度下降（Dramatron 的核心发现：必须靠中间层 beats 才能保持长程连贯）；
  - 整稿 gate + repair 的调用次数和等待时间线性增长，开播前等待不可接受；
  - `StructureBreaker` / `TriggerBank` / `StoryNucleus` 每段独立随机，跨 12 段没有去重，
    观众会明显听到"又是'诶我说到哪了'"；
  - `GagLedger`、`memory.promises` 是单场片段生命周期，`_load_prev_memory_summary` 只跨场取
    3 条 story_points，没有场内跨 block 的账本；
  - 没有"当前没弹幕、稿子生成又失败"时的兜底节目。
- 已有但没接入主链路的资产（09-12 文档已列）：`harness/repair.py` 双裁判、`harness/runtime.py`
  的 artifact store 与 steering 审查、`modes/reaction.py`、`modes/singing.py`、`modes/rundown.py`
  （开场/收尾段）。这些正好是节目单里的其他 block 类型。

## 三、外部案例给我们的具体启发

| 案例 | 可借鉴点 | 对应 echuu 落点 |
|---|---|---|
| Neuro-sama | 9 小时改 2-3 小时因为"会无聊"；靠环节轮换（杂谈/游戏/看视频/唱歌/联动）维持；输出保持简单、typed、易校验，执行下沉到下游系统（neuro-sdk 的 context / actions / forced actions） | 45 分钟必须是多环节；performer cue 保持结构化 |
| Nothing Forever | 直播不能挑片，流水线必须稳定且"失败也要有趣"；director 管镜头/场景时长与缓冲；一次模型降级导致过滤失效被封 14 天 | 安全过滤必须独立于模型（`output_safety` 已是这个方向）；缓冲 ≥1 个 block |
| DLIOS（arXiv 2603.03060） | 四段式 prompt 调度（opening / empathy / narrative / closing）；弹幕分两路：关键词路由到静态紧急话术，其余走 LLM；36h 零弹幕重叠零死锁，LLM→语音 P95 ≤ 2.1s | 礼物/问好走快速固定话术（<0.5s），复杂互动走现有 steering |
| Showrunner / SHOW-1 | simulation 数据作"创作燃料"；synopsis → 场景标题 → 台词的分层链；程序化注入 dramatic operators（反转、伏笔、cliffhanger）；14 场组成一集 | 节目单层 = synopsis；block brief = 场景标题；DrOps = 现有 nucleus/rupture 的全场级排布 |
| Dramatron | logline → 人物/beats → 台词三层，中间层让整体情节能塞进上下文 | block brief 只带"已发生摘要 + 本 block beats"，不带全文 |
| OpenMic（arXiv 2601.08288） | ComedyDirector 先出结构（hook / 2-3 bits / callback / tag），JokeWriter 被要求保留长程 callback，critics 标记漏掉的 callback | `GagLedger` 升级为全场级 callback 依赖图，收尾与后段生成强制回收 |
| 现场喜剧 LLM 综述（arXiv 2501.08474） | 观众宁要及时的普通回应，不要延迟的高质量回应；叙事脚手架比生成单句更有效 | 互动路径先保时延；质量投入放在预生成的 block 上 |
| Generative Agents | 记忆流 + 反思 + 情境检索 | 已在 09-12 #14，建议在编导循环跑通后再做 |

## 四、建议架构：Showrunner Loop

```
ShowPlan (rundown, 45min)            ← 开播前一次生成，可被 director 改
   │
Director heartbeat (按库存触发)     ← 观察: 时间余量 / 缓冲块数 / 弹幕密度 / 情绪曲线 / 未回收梗
   │   决策: 下一个 block 类型 + brief；库存 < 1 时立即生产；生产失败 → 兜底块
   ▼
Block Producer                       ← 复用 create_performance(brief)；reaction / singing / rundown /
   │                                    chat-only 也是 producer；block 级 gate + 时间预算
   ▼
Performer runtime (现有 run 循环)     ← 逐行播放；快速反应通道；steering / topic_evolution
   │
Show Ledger                          ← 每 block 播完写摘要、梗、承诺、观众名、已用的故事形状
```

### 1. ShowPlan / rundown
- 45 分钟 ≈ 10-12 个 block：opening(1-2 min) → 故事 block ×4-5（每个 3-4 min）→ 互动/读弹幕 block ×2-3
  （2-3 min）→ 一个换模式 block（reaction 或 singing，5-8 min）→ closing。
- 计划里带一条"全场主题线"（today's through-line）和情绪曲线（起-稳-高-缓-收），
  由 director 决定每个故事 block 的 nucleus / trigger / rupture 类型，**全场不重复**。
- 每个 block 有 brief：主题、beats（2-3 条）、必须回收的梗、禁止重复的形状、
  "刚进来的观众"一句重新定向（观众是分段进来的，局部连贯比全场弧线更重要）。

### 2. Director heartbeat（09-12 #12）
- 触发条件是"缓冲区不足"而不是时钟：当前 block 剩余 < 1 分钟且下一 block 未就绪 → 立刻生产。
- 决策输入全部是可观测状态：剩余时间、弹幕速率、最近 3 个 block 的类型、未回收梗数、
  观众提出未回答的问题、上一 block 的 gate 状态（`completed` / `completed_degraded`）。
- 输出 typed JSON（block_type, brief），不输出台词；执行交给 producer（Vedal 的"输出简单、执行下沉"）。
- 兜底块：纯回应弹幕 + 读账本里"聊过什么/答应过什么"的 chat-only block，不依赖新故事生成，
  没弹幕时用账本和 persona 自言自语。它保证任何 block 生产失败时直播不停。

### 3. Block Producer
- 故事 block = 现有 `create_performance`，输入增加 brief 与 ledger 摘要；`max_units` 保持 3-4。
- gate 改为 block 级，加时间预算：下一 block 必须在当前 block 结束前就绪；
  预算耗尽按 09-13 决策播出最接近达标的版本并标 `completed_degraded`。
- 多样性注册表：记录每个 block 用过的 nucleus / trigger / rupture / ending 类型和开场句式，
  采样时排除最近 2-3 个 block 的选择；`StructureBreaker` 的"诶我说到哪了"类插入设全场频次预算。

### 4. Performer runtime
- 增加 DLIOS 式快速反应通道：礼物、问好、关注类弹幕直接走人设化预生成的固定话术池
  （开播时按 persona 生成 20-30 条，播时不调模型），< 0.5 s；实质性弹幕才走 steering。
- 每行 TTS 预取下一行，block 之间零等待；保留 `time_to_first_audio` 指标。

### 5. Show Ledger（09-12 #13）
- 全场生命周期：block 摘要（2-3 句）、已用故事形状、梗与回收状态、承诺、被点名的观众、
  话题历史（`topic_history` 已有）。
- 供 director 与 producer 读；场末落盘替代现在只存 3 条 story_points 的 memory.json。

## 五、怎么验证"45 分钟质量"

- 离线 dry run：用 `danmaku_sim` 喂 45 分钟模拟弹幕（含空档期），headless 跑完整 Showrunner loop，
  产出时间线与音频。指标：
  - 冷场：任意两段音频间隔 > 4 s 的次数；
  - 及时性：弹幕到播出的延迟 P95；首音频时间；
  - 重复：跨 block 的开场句式 / 结尾类型 / 故事形状复用率，n-gram 重复率；
  - 回收：梗与承诺的回收率；
  - 降级：`completed_degraded` 与兜底块占比。
- 人工听感用"窗口法"：随机抽 3 个 5 分钟窗口听，而不是听全场。观众就是这样进来的。
- 用现有 `model_comparison.py` 先回答 09-12 #5：便宜模型在 block 级是否够用，
  director 用强模型、block 用中档模型是否可行（成本决定能不能天天播 45 分钟）。

## 六、优先级与不做的事

建议顺序（每步都能独立验收）：
1. Show Ledger + ShowPlan 数据结构 + director 循环骨架，producer 直接复用现有生成器，跑通 45 分钟 dry run。
2. 多样性注册表 + 快速反应通道 + chat-only 兜底块。
3. block 级 gate 与时间预算；把 `harness/repair.py` 双裁判接到 block 级（不再是整稿）。
4. reaction / singing 作为 block 类型接入节目单。
5. 之后再考虑记忆流范式（#14）与微调数据（#15）。

不建议：
- 把 `max_units` 直接拉长生成一条 45 分钟稿；
- 整体迁移到 Open-LLM-VTuber / AIRI（它们解决的是另一个问题；可参考其平台弹幕接入代码）；
- 现在就微调（先用 #5 的对比实验确认瓶颈在流程还是模型）。

关于 LiveKit：release 轨道已经用 LiveKit 传输，LiveKit Agents 可以作为未来托管 runtime
的容器（轮次、打断、TTS 流），但它不提供编导层；本文的 Showrunner loop 应作为独立 Python
模块，既能在本地 REST/WS 跑，也能被 LiveKit Agent 包一层。

## 参考

- Neuro-sama 条目与 Vedal 设计取向：https://en.wikipedia.org/wiki/Neuro-sama ；
  https://medium.com/@sid2001.blr/what-neuro-samas-do-i-exist-moment-tells-us-about-2026-ai-engineering-9477d61d3a7f
- Nothing Forever 复盘：https://minimaxir.com/2024/08/ai-seinfeld/
- DLIOS（抖音直播 AI 主播 36h 压测）：https://arxiv.org/abs/2603.03060
- Showrunner Agents / SHOW-1：https://fablestudio.github.io/showrunner-agents/
- Dramatron：https://arxiv.org/abs/2209.14958
- OpenMic 多 agent 单口喜剧：https://arxiv.org/abs/2601.08288
- 现场喜剧 LLM 系统综述：https://arxiv.org/abs/2501.08474
- Open-LLM-VTuber：https://github.com/Open-LLM-VTuber ；Project AIRI：https://github.com/moeru-ai/airi ；
  Ikaros AI-Vtuber：https://github.com/Ikaros-521/AI-Vtuber
- 语音 agent 框架对比：https://soniox.com/wiki/voice-agent-frameworks
