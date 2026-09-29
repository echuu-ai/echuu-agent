# Echuu Agent：在哪里、怎么跑、弹幕和礼物怎么影响内容

给扣扣的交接说明 · 2026-09-30

## 1. Agent 在哪里，怎么启动？

团队仓库：[echuu-ai/echuu-agent](https://github.com/echuu-ai/echuu-agent)，本次交付使用 **dev 分支**。这是生成台词、语音并处理观众互动的 Python 服务。前端是另一个仓库 `echuu-ai/nextjs-vtuber-mocap`。

Cory 已验证的本地源码在 `/Users/cory/.codex/echuu-runtime/topic-evolution-20260929`，服务端口8002。不要启动旧的 `/Users/cory/Desktop/nextjs-vtuber-mocap/echuu-agent`，那里仍有其他工作的合并冲突。交接后的启动方式以团队仓库dev为准。

新电脑先安装 Python 3.10+，然后：

```bash
git clone --branch dev https://github.com/echuu-ai/echuu-agent.git
cd echuu-agent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r echuu-web/backend/requirements.txt
```

Windows 激活环境用 `.venv\Scripts\activate`。在仓库根目录新建自己的 `.env`，按使用的模型配置凭据。例如Qwen：

```dotenv
ECHUU_LLM_PROVIDER=qwen
DASHSCOPE_API_KEY=填写你自己的密钥
```

模型、音色等可选项以仓库 `.env.example` 和 `echuu/live/llm_factory.py` 为准；不要把密钥提交到Git。启动：

```bash
python scripts/start_research.py --env-file .env --port 8000
```

接口说明页是 `http://127.0.0.1:8000/docs`；REST前缀 `/api/v1`，WebSocket为 `/ws?room_id=房间ID`。端口也可以用8002，但前端REST、WS和音频地址必须指向同一个Agent。

前端在另一个仓库的 `echuu-ux-r3f-vite` 中运行：

```bash
npm install
VITE_ECHUU_API_BASE_URL=http://127.0.0.1:8000/api/v1 VITE_ECHUU_AGENT_URL=http://127.0.0.1:8000 VITE_ECHUU_USE_LIVEKIT=0 npm run dev
```

上面是本地Agent接入方式，命令里的环境变量写法用于macOS/Linux。云端平台和LiveKit是另一条接入路线；本次推送不等于平台已部署。

## 2. 输入和输出分别是什么？

**输入：让谁，以什么人设，聊什么；直播中再告诉它谁发了什么弹幕、送了什么礼物。**

- 开播输入：角色名字、人设、背景、话题、音色和语言。
- 弹幕输入：观众昵称和文字。
- 礼物输入：观众昵称、礼物ID、文字及amount字段。Agent会把旧资源ID转换成真实名称；`white-pebble`在这里表示**饭团**，不是石头。

**输出：角色接下来实际要说的台词、对应语音，以及这次互动有没有改变后续话题。**

接入顺序：创建房间 `POST /api/v1/room` → 连接WS → `POST /api/v1/start-inline`生成本场 → 收到`ready`后`POST /api/v1/begin`开始 → 直播中`POST /api/v1/danmaku`发互动。开播、开始和停止需要创建房间时返回的`owner_token`。

饭团请求示例：

```json
{
  "room_id": "房间ID",
  "client_id": "本次事件唯一ID",
  "user": "小明",
  "kind": "gift",
  "gift_id": "white-pebble",
  "text": "送了一个饭团",
  "amount": 8
}
```

弹幕改成`kind: "chat"`，填写昵称和文字即可。

WS中，`step`给实际台词和`audio_url`；`steering`给互动处理进度。判断是否改了后续，看`story_changed`，新方向看`current_topic`。初始`ready`里的预览不会包含之后才发生的互动，以实际`step`为准。

## 3. 四种礼物，哪个更贵？

**现在还没有确定的商品价格或礼物等级规则。** 当前前端带的演示排序值是：

| 礼物 | ID | amount |
|---|---|---:|
| 信封 | sealed-envelope | 40 |
| 牛角包 | golden-croissant | 20 |
| 包子 | baozi | 12 |
| 饭团 | white-pebble | 8 |

数值写在前端 `src/App.tsx` 的 `GIFT_STEER_VALUE`。后端排互动队列时会用到amount，但**这个数字不传给话题决策模型，也不决定剧情改变的强度**。不能把这张表当成已确认的充值/售卖价格。

## 4. 什么礼物会有什么反馈？是写死的吗？

固定的是“这个ID代表什么”：饭团、包子、牛角包是食物，信封是信件。这张表在Agent的 `echuu/live/gifts.py::GIFT_CATALOG`。

**台词和剧情分支没有按礼物种类写死。** AI结合角色、人设、正在聊的事和这条互动，决定：

- **简单回应**：比如感谢饭团，随后继续当前内容。
- **补充当前话题**：比如正在聊加班吃饭，就顺着饭团聊晚饭。
- **转入相关话题**：比如正在聊换手机的预算，观众提议“省下来给妈妈买蛋糕”，可以顺着聊长大后如何关心父母。采用后，后续台词会沿新方向继续，仍保留前面说过的内容。

这些是说明用途的例子，不是预设台词。不是每次互动都换话题，同一礼物在不同语境下可以有不同结果。前端的礼物动画/表情映射是单独的显示规则，不等于内容分支。

## 5. 用户能调吗？在哪一端调？

用户可以通过角色人设、背景、开播话题和直播互动影响内容。**目前没有“饭团必须聊A、牛角包必须聊B”的用户配置面板。**

要修改通用的回应方式或转话题规则，开发者改 **Agent服务端的提示词**；要改礼物名称，改服务端 `gifts.py`，并同步前端展示名称；要改演示amount，改前端 `GIFT_STEER_VALUE`。真正的商品价格和支付规则应由平台另行定义。

## 6. 到底是不是插入prompt？谁拼的，谁发的，内容在哪？

**是。前端发送结构化的互动事件；Agent收到后，把固定提示词加上本场上下文和这条互动，发给模型。前端不需要为每种礼物写一段剧情prompt。**

固定提示词原文已经单独放在 [INTERACTION_PROMPTS.md](https://github.com/echuu-ai/echuu-agent/blob/dev/docs/INTERACTION_PROMPTS.md)，内容从当前代码提取，不是另写一份示意文案。

| 做什么 | prompt内容在哪里 | 在哪段代码拼好并发送给模型 |
|---|---|---|
| 生成一句短回应 | `echuu/live/danmaku_interleave.py` 的 `_PROMPT` | `DanmakuInterleaver.respond()`：`_PROMPT.format(...)`填入人设、当前话题、刚说的话和互动，再调用`self.llm.generate(prompt)` |
| 决定是否改变后续，并写新稿 | `echuu/live/topic_evolution.py` 的 `PROMPT` | `TopicEvolution._propose_once()`：`PROMPT + json.dumps(data, ...)`，然后调用`call_structured(...)`；不支持时退回`call/generate` |
| 检查新稿或短回应有没有编造、矛盾 | 同文件的 `REVIEW_PROMPT` | `TopicEvolution._review()`拼入上下文和待检查内容，再调用模型；`approve_reply()`复用这个检查来检查短回应 |

动态填进去的内容包括：角色与人设、背景、当前话题、之前说过的话、已处理的互动、这次谁说了什么/送了什么。改稿另外收到待改的句子ID，以及准备期间还会播出的原稿`committed_bridge`。这些由Agent从当前直播状态读取，不需要前端每发一条弹幕都重传。

收到事件后的实际调用路线是：

```text
前端 useEchuuLive.enqueueSteer → EchuuClient.sendDanmaku
    → POST /api/v1/danmaku
    → LiveService.inject_danmaku：转换礼物名称、入队
    → live_service.py 播放期间调用 BackgroundInteractions.tick()
        ├─ _reply → engine._maybe_interleave_danmaku
        │           → DanmakuInterleaver.respond → 发短回应prompt
        └─ _rewrite → engine._steer_remaining_show → _evolve_topic
                    → TopicEvolution.propose → 发改稿prompt及检查prompt
```

`_evolve_topic()`负责组装话题和记忆。`BackgroundInteractions`负责并行准备和采用时机。最底层通过 `engine.py::_LLMGenerateAdapter` 转给配置的模型客户端；Qwen最终由 `echuu/live/qwen_client.py::QwenClient.call()` 中的 `self.client.chat.completions.create(...)` 发出模型请求。

如果前端走LiveKit，`src/App.tsx::steerWithGift()`会通过`room.localParticipant.sendText()`发送礼物文字，并附一句“请结合人设与当前话题自然回应；合适时补充当前话题或转入相关新话题，不必每次转向”。这是另一条消息入口；云端收到这句话不代表已经接上本地Agent的改稿和记忆逻辑。本次验证和上面的调用路线是REST/WS直连Agent。

通用规则用人话说就是：保持角色人设；结合本条互动判断要不要展开；不强行换话题；一旦换了就继续这个方向；不推翻前面已经说过的话。**未来打算可以提出，没来源的过去经历不能当事实编出来。** 例如“周末带块蛋糕回去看看她好了”允许；“上次给妈妈买了蛋糕，她一直最爱草莓味”没有依据时不允许。

## 7. 为什么现在不会等改完稿才回应？

短回应和后续改稿在后台分别准备，当前语音继续播放。短回应先准备好就从句子边界插入；新稿和语音准备好后，在预留原稿播完的接入位置一起采用。

准备最多25秒，没赶上或结果过期就继续原稿，不让角色一直静音。因为两条工作分开，`steering.replied`可能早于`applied`；前端不能再把它们当固定先后顺序。`replied`里`rewrite_pending: true`表示短回应已入播放队列，后续决定尚未采用。

已完成97项回归。两场真实浏览器整场分别播完18、23段音频，最长句间空档约1.2秒，覆盖成功转向和超时继续原稿。短回应约9–13秒入播放队列，期间正文持续播放；不是零延迟，也不是每次改稿都一定成功。初始生成、结束归档、平台上线不包含在这个延迟结论里。
