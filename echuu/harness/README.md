# Echuu Harness：内容生产、自动修复与实验

Harness 默认接入局部修复：生成（或载入旧稿）→ 分行双视角审查 → 争议复核 → 定点修改 → 重新检查整篇 → 通过后 TTS。可用 `--no-repair` 回到纯观测基线。另有完整内容生产入口 `harness_story_v1`，已接本地 Python live engine；远端平台部署不在本次改动内。

## 创作政策

`creative_policy.py` 是生成、评审、修复共用的规则：

- **允许**依据人物性格、职业、成长背景补充相容的动作、偏好和普通过去经历。输入未逐字列出不构成错误。例如雪兔以前把饭盒装得太满，与其食量相容，可以保留。
- **修复**已知人设冲突、秘密泄露、未来知识、事件因果错位、无关抽象升华、语义不通、错误的直播话语。例：给没有魔力的雪兔独立施法能力。
- 区分事实断言与假设、愿望、玩笑；假设拥有能力不自动等于声称真有能力。
- **复核**会重写原作重大关系、童年真相等尚未确定的内容。不能因为拿不准就删除所有经历。待复核行保持不动，其他无争议行仍可局部修复；整篇继续标记 `needs_review`。
- 新增经历在当前故事中可以用第一人称讲述，但来源记录为 `model_invented_not_verified_canon`、作用域 `run_only`。不自动写进官方事实或长期角色记忆。

`fixtures-creative.json` 是两张原作卡的新授权版本。旧 `fixtures-canon.json` 与旧 run 不覆盖。普通角色仍可用 `fixtures.json`；`topic` / `evidence` 可按角色自定义。提示中的创作授权与角色、当前场景共同生效。

## 运行

在 `echuu-agent` 根目录，用已配置 LLM/TTS 的环境：

```sh
.venv/bin/python -m echuu.harness --fixtures echuu/harness/fixtures-creative.json --output output/repair-new --jobs 2
.venv/bin/python -m echuu.harness --fixtures echuu/harness/fixtures-creative.json --replay-dir output/harness-canon-2026-09-06 --output output/repair-replay --jobs 2
.venv/bin/python -m echuu.harness --output output/observation-baseline --no-repair
.venv/bin/python -m echuu.harness --output output/repair-new --report-only
```

- 默认只运行 `legacy_v4`（2026-09-06 用户选择）。Harness 继续提供观测与自动修复；`refactor_no_dossier` / `refactor_full` 仅在显式传入 `--variants` 时运行。
- `--characters` 选择 fixture ID，`--seed` 设生成 seed，`--no-audio` 只跑文本。
- `--repair-rounds 0|1|2` 控制修改次数；0 仍做放行检查，`--no-repair` 才完全旁路。
- `--no-judge` 只关闭旧版建议性 Judge；开启自动修复时，新放行审查不能被这个选项跳过。
- `--no-references` 关闭原有 raw few-shot。这个兼容 CLI 只运行旧生成器；新 Pattern / Humor 实验见下文。
- `--replay-dir` 读取已有 `text.txt`，按角色+variant 精确匹配；同一组合多份来源会报歧义，避免暗中选优。重放不调用原生成器。
- 每次创建 UUID run，保留输入、draft、所有请求、拒绝版本及选中结果。未通过修复的 run 为 `needs_review`，不触发 TTS，CLI 返回非零；不会假装完成或以旧坏稿自动播放。

## 放行与修复边界

1. 两次不同提示的审查分别强调人物约束与事件/语言，均检查每行和整篇结局。当前使用同一个配置模型，不冒充独立模型共识。
2. Judge 只给稳定行 ID。程序从原稿取回文本，验证行覆盖、引用规则和 verdict 的一致性；不能用伪造引文得出结论。结构失效最多再请求一次，再失败则待复核。
3. 明确非直播场景的 `SC / 下播 / 弹幕` 、跨行至少 35 字且至少两处句末/分号标点的逐字重复与已有 prompt 泄漏检查，不能被 Judge 的全通过覆盖。其他语义错误仍依赖审查质量，无法保证消灭所有误放行。实测 v5 出现重复对话仍被放行，v6 已针对该漏检增加检查，历史记录保留。
4. 语义指控由第三次有针对性的复核确认；撤销把普通经历当抽象总结等误报，保留理由。确定性问题不可被复核撤销。复核结构失效重试一次，再失败则停止放行。Writer 只能返回被指出行的 patch；未标记行保持逐字不变。拒绝未知行、重复 patch、跨行替换、无效修改和删掉过多文本的“修复”。
5. 每个候选重新检查所有行和故事因果；出现新问题或问题没有减少时回退到前一候选。下一轮带上被拒绝候选及理由，避免重复提交。最多两轮，不循环重写。
6. 只有两个审查有效、语义指控经复核后均解决且确定性检查通过才放行。未观察到的完整原作知识，不因当前日常故事通过就被宣称全面覆盖。

## Inspector / 产物

打开输出目录 `index.html`，可看原稿、最终稿、修改决策、保留的相容扩写、每个候选与所有模型调用。HTML 转义模型内容；本地直接打开或 localhost 服务均可。

- `draft.txt`：修复前文本；`text.txt`：选中候选（是否可播放须看 run status）。
- `repair_candidate` / `repair_selection` / `repair_result`：修改、选择和回退链路。
- `compiled_show` 的 `lines` / `text` 始终对应选中稿；原引擎 Show 单独留在上游 draft，不把旧结构冒充修复后的 Show。
- `run.json`：管线、模型、seed、执行状态、修改轮次、有效审查数、扩写来源。
- `events.jsonl`、`artifacts/{sha256}.json`：追加事件与不可覆盖快照。
- 音频按最终文本逐行合成并合并 WAV，记录音色、型号、时轴、哈希。未做 ASR 或人类盲听。
- token usage 使用供应商值；价格未配置时成本为 null。Prompt 与角色资料会写入本地 trace 并送往配置的模型服务。

这个兼容 CLI 的历史测试只覆盖生成与合成。新的 live engine 入口记录实时事件并审核弹幕改写；长期记忆自动写入和平台分发不在本地内容生产链路中。种子不保证供应商字节级复现，原管线长短不一，也不能直接把短稿当作质量更好。

## 校验

```sh
.venv/bin/python -m pytest tests/test_harness_repair.py tests/test_harness.py tests/test_engine_dossier.py tests/test_engine_unit_render.py -q
.venv/bin/python -m echuu.harness.verify output/repair-replay --expected 4
```

覆盖相容经历保留、Judge 假通过被确定性检查拦截、行引用/覆盖失效、未标记文本不可改、回退与次数上限、未放行不调用 TTS。`verify` 分别报告文件完整性和内容放行数量；完整性通过不等于内容获准播放。


## 内容生产入口（harness_story_v1）

实现链路：输入及 Evidence → 3 个 Story Intent → 全候选评审 → 3 个四段 Outline → 全候选评审 → 按选中结构检索 Pattern → 逐段候选（默认 1，可设 3）→ 整篇结构门禁 → 双视角逐行审查、争议复核、最多 2 次局部修复 → 全文复查 → 可选 Humor Pass → 编译四段 Show → 运行前检查 → 现有实时引擎 / TTS。

- 完整性或清晰度失败回到另一个大纲，最多尝试 2 个计划；人物和语言问题先做局部修复。预算耗尽或仍待复核则拒绝播放，不以未经审核的 legacy 自动兜底。
- Humor 最多改一行，原稿与 3 个候选比较；A/B 调换顺序均胜出才进入编辑后审查，否则沿用原稿。候选保留 expectation / surprise / resolution / delivery。评审均为同一配置模型的不同调用，不能宣称独立模型共识。
- `reference_mode=none|raw|pattern`。Pattern 为 5 个**项目原创机制卡**，通过大纲输出的机制标签匹配，不是从公开语料训练得来。raw 仅接收调用方提供的 `raw_examples`，本轮没有导入公开动漫台词或声线数据。
- 400–700 字为对比目标，实际长度单独标记。长度偏差不通过删稿凑数，也不被隐藏；本轮结果须结合字数解释。
- `max_calls` 约束规划、writer、judge、repair 和 humor 的全部调用。无已配置单价时成本是 null，token 使用供应商真实值。
- `run_production(..., resume_from=...)` 将旧 run 的节点缓存导入**新目录**。先验证全部哈希、快照、父引用、fixture/flags/model；缓存命中仍重新执行 schema 验证。旧调用仍占预算。每次 run 不覆盖历史。
- 发现括号舞台说明时，由 Delivery Compiler 将必要动作转为可朗读口语，仅改命中行，再经双视角与全文审核；失败不交给 TTS。
- 编译保留稳定台词 ID、beat ID、声明的 evidence ID 和修复/幽默版本来源。声明的 evidence 引用仅验证存在，不冒充独立证明的语义溯源；普通新增经历只在本次故事有效。

运行完整五角色消融实验：

```sh
.venv/bin/python -m echuu.harness.experiment --output output/new-production-experiment --workers 3 --seed 42
.venv/bin/python -m echuu.harness.judge_comparison output/new-production-experiment --workers 4
.venv/bin/python -m echuu.harness.comparison output/new-production-experiment
.venv/bin/python -m echuu.harness.verify output/new-production-experiment
```

四组：受控长度的 legacy_v4（原生成器、不自动修复、不加载 raw 示例）、Harness 无参考、Harness + Pattern、在**同一通过稿件**上追加 Humor。输入、seed、模型一致。Humor 的源稿未通过时不生成该分支，缺失组明确记录；不能只把成功角色算入分母。一种子是探索实验，不提供统计显著性结论。

### 接入本地直播

默认仍为 legacy_v4。显式配置：

```python
engine.setup(
    name="林桥", persona="二十七岁修理铺女店主，观察细致，说话干脆。",
    background="社区义卖帮忙。", topic="讲完标签贴反的小事故。",
    story_pipeline="harness_story_v1", generation_seed=42,
    character_config={
        "harness_fixture": {"id": "lin_qiao", "evidence": [
            {"id": "e1", "claim": "免费试吃与五元一袋的标签贴反，发现后换回。"}
        ]},
        "harness_flags": {"reference_mode": "pattern", "humor": False},
        "harness_output": "output/harness-live",
    },
)
for event in engine.run(max_steps=100):
    # 由现有客户端播放事件中的 audio。
    ...
```

也可用 `ECHUU_STORY_PIPELINE=harness_story_v1` 选择本地默认入口。平台服务已有 engine.setup/run 调用可沿用；没有修改或部署远端 `echuu-ai/echuu`。

每次 `run` 新建 `runtime/<session>/`，记录生成稿哈希、稳定行 ID、事件、音频字节哈希和停止状态。事件内容不改变，关闭生成器也完成 manifest。弹幕改写先保存在候选中，全文复核通过才替换尚未播放的行；失败恢复原文。即时回应在合成前双视角审核，拒绝则不播放。互动生成与审核共享每 session 12 次调用预算；当前是同步检查，会增加互动延迟，应据真实延迟决定异步化，而非直接用于低延迟生产承诺。

`render_audio` 从已通过的 `compiled_content` 经真实 engine.run 和 TTS 生成 `listening.wav`、逐块时轴和 runtime trace。仅验证非空波形与格式，不冒充主观盲听或 ASR。

```sh
.venv/bin/python -m echuu.harness.render_audio output/new-production-experiment/<approved-run>
```

Inspector 的匿名 A/B 偏好保存到浏览器 localStorage，可导出 JSON；不会自动更新人物原作事实、自动训练或伪造用户评价。

### Shareable clip writer

`legacy_v4` 的原生引擎入口现在默认 `output_format=shareable_clip`：事件候选 → 单一事件选择 → 口头正文 → 精确引文审核 → 必要时修复并复审（3–5次模型调用；审核格式错误最多额外重试1次，总上限6次）。保留 `output_format=casual` 可显式运行旧短稿模式。上一轮超过45秒时，下一轮默认仅生成1个事件候选；保留90秒正文目标。

口癖设计存在 `echuu/generators/shareable_clip.py`，明确标记为中文改编，非原作固定台词。用户配置 `voice_design`/`speech_tics` 优先。动态生成角色习惯只作为设计，不作原作证据。1–2句 quotables 必须精确存在于最终正文；审核引用伪造、结论矛盾、修复后仍有问题均不返回可播放台词。

文稿字数只是时长估算。`python -m echuu.harness.clip_report OUTPUT_DIRECTORY` 使用已合成 WAV 帧数校验60–120秒，并将文稿通过、实际时长和分享偏好分开显示；不要把文本估算称为音频验收。实时引擎仍按既有流式TTS播放，未实现先完整合成再强制截断。

运行完整实测：`python -m echuu.harness.clip_experiment --output output/new-clip-run --model qwen3-max`（输出目录必须不存在）。运行相同协议的模型对照可设 `--model qwen-plus`。当前 Qwen 原生 clip 入口默认使用 qwen3-max，可通过 ECHUU_CLIP_MODEL 或 character_config.clip_model 覆盖；其他模型提供商保持原客户端。legacy_v4 管线名称不变，默认 clip 的模型变化单独记录。官方模型说明：https://www.alibabacloud.com/help/en/model-studio/model-qwen3-max 。
