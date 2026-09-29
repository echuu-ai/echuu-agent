# 话题演变预研 · 2026-09-29

新增：start-inline故事模式支持边播边准备，短回应与改稿独立进行；原稿衔接后采用已准备的新稿及语音，准备超过25秒继续原稿。replied可早于applied；已准备完等待接入不算超时。未来打算允许，无来源过去事实仍拒绝。详见交付包BACKGROUND_PLAYBACK.md。

输入角色、人设、背景、话题、音色与观众互动，输出台词、语音、当前话题与处理状态。

- `reply` 只回应；`expand` 继续当前话题并展开观众的新角度；`transition` 沿相关新话题继续。
- 补充／转向会重写全部剩余未播稿，更新当前话题，保留已播内容。后续互动与收尾沿用新话题。
- `white-pebble` 是饭团旧ID，按饭团／食物理解；旧客户端误写的Pebble也会纠正。
- 结构校验、连续性检查、最多一次修正；失败保留原稿。没有按礼物种类写死话题分支。
- 默认最多更新80条未播台词；临近结束只回应。本场剩余句数不增长。

## 启动

Python 3.10+。在本仓库根目录创建虚拟环境，安装 `requirements.txt` 和 `echuu-web/backend/requirements.txt`。
在根目录自己的 `.env` 配置 `ECHUU_LLM_PROVIDER`、对应模型与凭据；TTS需 `DASHSCOPE_API_KEY`。
然后执行 `python scripts/start_research.py --port 8000`。可用 `--env-file /local/config.env`；密钥不要提交Git。
REST为 `/api/v1`，WS为 `/ws?room_id=...`。创建room后start-inline，等ready，再begin；互动走POST /danmaku。

## 返回

steering.item新增 action、story_changed、current_topic、previous_topic、reason、outcome、gift_name、gift_category、decision_trace。
queued不是改稿完成；applied也可能只是完成仅回应决策。看story_changed判断是否改稿。
step.show和memory事件也附带当前话题／变化记录；实际播出以step为准，不以初始ready预览为准。

## 验证

`python -m pytest -q tests/test_topic_evolution.py tests/test_topic_evolution_api.py tests/test_danmaku_interleave.py`

可选真实模型与TTS：`python scripts/verify_topic_evolution_live.py --env-file .env`，会调用付费服务，不上传归档。
本次完整定向回归86通过；真实Qwen/TTS验证固定起始稿后的互动，不代表从空白开播到平台发布的全流程验收。

## 代码入口与边界

实际提示词在 `echuu/live/topic_evolution.py` 和 `danmaku_interleave.py`，采用与记忆在 `engine.py`；礼物真实名称在 `gifts.py`。
主前端用LiveKit=0连接本地agent。平台多人房间、鉴权、支付和去重仍由平台集成承接，此后端目前是全局单场预研。
本实现基于org提交6bd4025加9月13日录屏修复，不包含8f18247与旧冲突目录的所有未整合修改。

## 触发与记忆修正

纯问好／继续听的完整短句由代码直接选择reply（保守匹配）；带具体问题或提议的消息仍由AI判断。其他表达由提示词与独立检查约束：必须由当前互动的实质内容支持改稿，历史不是待办。
已处理互动保存id、outcome、story_changed、disposition（adopted/not_adopted/responded）、pending=false和reason；不会自动延期执行未采用的旧提议。当前明确追问旧提议可重新评估。
decision_trace记录每次尝试的code/reason，结构或连续性失败最多修正一次。短回应检查与续写检查分开保存，不覆盖决策证据。
