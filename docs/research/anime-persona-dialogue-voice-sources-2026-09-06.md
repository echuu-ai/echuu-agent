# Echuu 人设、台词与声线数据源调研

核查日期：2026-09-06。基于项目/作者官网、仓库和 dataset card。此次核查页面、字段和公开条款；未批量下载语料，未实际训练，也未验证每条标注质量。下述“建议”是针对 Echuu 的工程判断。

## 目前算升级到 Harness 了吗

已经完成可执行的 Harness 实验层：统一 run、不可变 artifact、模型调用记录、旧稿重放、角色卡、局部修复、争议复核、重新评审、通过后 TTS 与 HTML Inspector。

尚未完成方案中的完整内容生产系统：Story Intent/Outline 多候选、逐 Unit 生成、Pattern Library 检索、完整语义 lineage、实时弹幕/播放 trace、人工反馈闭环。线上平台是否部署更不能从本地实验推出。

本地 `echuu/live/engine.py:506` 默认仍为 `legacy_v4`，接受 legacy/refactor；`echuu/harness/runner.py` 在引擎外围执行实验与修复。`echuu-web/backend/services/live_service.py` 仍直接调用 engine.setup。当前属于“观测层 + 部分质量修复层”，并非平台直播已整体切换。

## 人物设定与语言资料

| 来源 | 已核实内容 | 对 Echuu 的价值 | 使用边界 |
|---|---|---|---|
| [Bangumi Archive](https://github.com/bangumi/Archive) | 官方离线导出；角色 name/infobox/summary、作品、章节、角色—作品、声优—角色等关系；releases 下载 | 优先作为中文人设索引与角色卡原料 | [版权页](https://bangumi.tv/about/copyright)声明条目和角色信息按 CC BY-SA；原创日志/评论不在同一授权范围。保留出处，已有作品内容另有权利，不把整站视为统一可自由训练的素材 |
| [ChatHaruhi](https://github.com/LC1332/Chat-Haruhi-Suzumiya) | 人设提示、角色故事/对话片段、检索与数据构建方法；论文的 32 角色/54K 是模拟对话集，仓库后续扩充角色 | 最值得借鉴的角色 RAG 与台词库组织方式 | [角色库](https://huggingface.co/datasets/silk-road/ChatHaruhi-RolePlaying)标注 Apache-2.0；区分代码、原作摘录和合成对话，合成对话不可当原作金标准；上游文本权利不能只凭仓库标签推出 |
| [AniList API](https://docs.anilist.co/guide/introduction) | 动画/漫画数据 API | 少量按需查询补充索引 | [条款](https://docs.anilist.co/guide/terms-of-use)禁止囤积/批量收集数据；超过其免费商业额度需商业许可。因此不推荐拿它做全库镜像 |
| [OPUS OpenSubtitles](https://opus.nlpl.eu/datasets/OpenSubtitles) | 多语字幕与句子对齐 | 中日表达对照、字幕候选片段 | 非角色标注库，也非专门动漫库；需要额外匹配作品、集数和说话人，不能凭一句字幕确定角色。OPUS 明确不拥有源文本版权 |
| [AnimeSpeech](https://github.com/deeplearningcafe/animespeechdataset) | 输入视频和字幕，人工标注角色、声纹预测、修正，再导出对话与音频数据 | 当目标角色没有现成 dataset 时，借鉴制作流程 | 这是数据制作工具，不是现成的全动漫角色库；需要自行提供素材和人工校对，工具许可不等于视频许可 |

欧蒂娜已有 [Bangumi 人物条目](https://bangumi.tv/character/509)，但同页同时出现 TV 经历、后期真相与剧场版描述，不能直接当某一时刻的角色知识。此次未确认雪兔/欧蒂娜存在可直接复用且授权清楚的“角色标注台词 + 原声”完整包；这不是断言它们不存在。

## 声音与表演资料

| 来源 | 已核实内容 | 对 Echuu 的价值 | 使用边界 |
|---|---|---|---|
| [つくよみちゃん / Tsukuyomi-chan Corpus](https://tyc.rei-yumesaki.net/material/corpus/) | 明确以动漫角色风格录制；Vol.1 为 100 句，官网另链同声优扩展素材 | 原创动漫声线原型的优先候选 | 官方允许商业用途及音声合成软件/API发布，但有署名、输出用途和再分发条件；不是无限制公有领域，也不是雪兔/欧蒂娜原声 |
| [JVNV](https://sites.google.com/site/shinnosuketakamichi/research-topics/jvnv_corpus) | 日语，4 位说话人，6 类情绪，1,615 条/3.94 小时，含笑、哭等非语言表达；CC BY-SA 4.0 | 情绪、笑声、停顿等表演校准 | 不是原作角色库；台本由 ChatGPT 生成，不能作为角色事实或人类台词风格金标准 |
| [JVS](https://sites.google.com/site/shinnosuketakamichi/research-topics/jvs_corpus) | 100 位专业说话人，约30小时，普通/耳语/假声，含 F0 范围等标签 | 男女性音色跨度、发声方式和声学指标基准 | 音频允许指定研究/个人用途，商业使用需联系授权；标签 CC BY-SA 不等于音频同许可 |
| [Japanese Anime Speech v1](https://huggingface.co/datasets/joujiboi/japanese-anime-speech) / [v2](https://huggingface.co/datasets/joujiboi/japanese-anime-speech-v2) | 均主要来自视觉小说；v1 73,004 条/110小时；v2 292,637 条，且声明不是简单覆盖 v1；主要字段 audio/transcription | 动漫风格日语 ASR、读音覆盖研究 | 不具备开箱即用的完整角色标签。v1 标 CC0，v2 标 GPL，不能混写许可；作者声明不替代上游素材授权。存在领域/性别偏差，v2 的 SFW 划分也不是完全可靠 |
| [Anim400K](https://github.com/DavidMChan/Anim400K) | 超42.5万英日对齐音视频片段，763小时，超过190个作品；含作品/章节级信息及 ASR | 日英配音、音画对齐和表达节奏研究 | 需申请访问并遵守数据条款；仓库 MIT 不能当作片段的统一许可；也不能把作品级人物资料当逐句角色标注 |
| [Genshin Voice](https://huggingface.co/datasets/simon3000/genshin-voice/blob/main/README.md) | 中英日韩，audio/transcription/language/speaker/type 等字段，可按角色下载；卡片标明部分转写和角色信息缺失 | 中文二次元角色台词与声音的结构最贴近需求，可借鉴 schema 与筛选方式 | 数据由游戏解包取得，许可段明确写 COGNOSPHERE 保留所有权利。不能把“HF 上能下载”说成开放商用训练授权 |

声音要分开评：声线/音色、情绪/韵律、语言习惯。日语数据能提供声学与表演参考，但不能直接证明中文表达像这个人物；原声优还可能饰演多个角色，speaker_actor_id 与 character_id 不能混用。

## 与现有方案的衔接

方案已有 Chumor、MUCH、UR-FUNNY、MUStARD 等幽默/讽刺/多模态研究线索。它们与人物库互补，不替代人物关系、知情边界和称呼习惯。本轮没有重新核验方案中的每一项幽默论文与数字。

方案中“没有 Evidence 就不能编造经历”等早期约束，与用户后续明确要求不同。实施应遵循用户最新要求：允许依据性格和成长背景扩写普通经历，不得与已知核心事实冲突；新增故事不自动成为原作事实或长期记忆。

建议分四种资料存储：

1. 原作事实：身份、关系、能力；每条有作品版本、时间线、出处和核查状态。
2. 角色语言：说话人、听者、场景、称呼、自称、句尾、直接/委婉程度、情绪；原文/翻译分开。
3. 表演参考：台词对应音频、角色、声优、语言、情绪、语速、停顿与可用权限。
4. 允许扩写区域：普通偏好、动作、小经历；明确是创作推导，不冒充原作记载。

每条建议字段：source_url、source_version、license、content_origin（canon_extract / fan_summary / synthetic / user_authored）、work_id、continuity、episode、character_id、speaker_actor_id、listener、text_original、text_zh、emotion、audio_ref、review_status。字段缺失保持缺失，不让模型补成事实。

## 优先实施顺序（建议，未执行导入）

先做 Bangumi 小规模角色卡导入 + 借鉴 ChatHaruhi 的角色检索结构；为雪兔、欧蒂娜各人工核对一组带场景/听者的台词。三位原创角色用用户设定与批准的示范台词作标准，借鉴表达机制，不移植原作人物经历。

声音先用授权条件明确的动漫风格录音与 JVNV 做小样本实验。Genshin/影视解包数据在授权未明确前保持“候选来源”，不进入生产训练集。

在 Harness 中比较：无参考 / 人设事实 / 人设事实+语气特征 / 人设事实+短场景参考。长度、话题与 TTS 声线固定；同声线比较文案，不同声线比较表演，避免变量混杂。

独立留出未用于提示、检索或训练的集数/场景，人工判断人设与语气，再校准 Judge。负例包含换错称呼、未来知识、过度温柔化、抽象总结；正例专门保留“资料没写但相容的普通经历”，避免把 Judge 越训越保守。
