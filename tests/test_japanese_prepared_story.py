import json
from types import SimpleNamespace

import pytest

from echuu.config import EngineConfig
from echuu.live.danmaku_interleave import DanmakuInterleaver
from echuu.live.engine import EchuuLiveEngine
from echuu.live.language import Language
from echuu.prepared_story import PreparedStory, generate_story, load_story
from echuu.session_types import SessionInput, SessionOptions
from tests.test_clip_repair import draft, plan


def japanese_draft():
    texts = [
        "棚を開けたら、昨日買ったおやつの箱が妙に軽いんです。おかしいなと思って振ってみたら、中から小さな音が一度だけして、すぐ止まりました。",
        "箱をそっと置いたら、また音がしたんですよ。まさかと思って顔を近づけたら、部屋の隅で寝ていた猫が急にこっちを見て、目をそらしました。",
        "箱の横には透明な袋が落ちてました。口は開いたままで、おやつはまだ三つも残ってる。じゃあ軽くなったのは何だろうって考えたんです。",
        "よく見たら、一緒に入れておいた鈴付きの小さなおもちゃがないんです。猫の寝床をのぞこうとしたら、前足でそっと布をかぶせていました。",
        "それで少し離れて待っていたら、布の下から小さな鈴の音がしました。どうやら箱に残っていたのは、おもちゃから取れたもう一つの鈴でした。",
        "ちなみにおやつは三つとも無事でした。猫の方は何も知らない顔で毛づくろいを始めて、音が鳴るたびに耳だけこっちを向けていました。",
    ]
    return {
        "lines": [{"id": f"L{index:03}", "text": text} for index, text in enumerate(texts, 1)],
        "quotables": [{
            "line_id": "L001", "quote": "おやつの箱が妙に軽いんです",
            "why_shareable": "小さな疑問が気になるから",
        }],
    }


def japanese_sections():
    return {
        "opening": [
            {"id": "O001", "text": "こんばんは、ちょっと聞いてください。", "emotion": "joy"},
            {"id": "O002", "text": "さっき棚の前で変な音がしたんです。", "emotion": "excited"},
        ],
        "closing": [
            {"id": "C001", "text": "じゃあ今日はここまで、またね。", "emotion": "relaxed"},
        ],
    }


class ScriptModel:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.requests = []

    def call(self, prompt, *, system=None, **kwargs):
        self.requests.append((prompt, system))
        return json.dumps(next(self.responses), ensure_ascii=False)


def test_japanese_saved_script_repairs_wrong_language_and_uses_original_text(monkeypatch):
    monkeypatch.setenv("ECHUU_FAST_CLIP", "1")
    good_sections = japanese_sections()
    bad_sections = {**good_sections, "closing": [
        {"id": "C001", "text": "好了那么今天我们就讲到这里吧。", "emotion": "relaxed"},
    ]}
    llm = ScriptModel(plan(), draft(), japanese_draft(), bad_sections, good_sections)
    config = EngineConfig()
    engine = EchuuLiveEngine(config=config, llm=llm, tts=SimpleNamespace(enabled=False))
    inputs = SessionInput(
        session_id="jp-room", name="ナナ", persona="猫の話をするのが好き",
        topic="猫玩具", language="ja",
    )
    story = generate_story(inputs, config, SessionOptions(grounding=False), engine=engine)
    assert story.inputs.topic == "猫玩具"
    assert story.inputs.language == "ja"
    assert story.schema_version == 2
    assert story.draft["lines"] == japanese_draft()["lines"]
    assert story.draft["opening"] == good_sections["opening"]
    assert story.draft["closing"] == good_sections["closing"]
    assert engine._legacy_writer_trace["draft_attempts"][0]["issues"][0]["code"] == "wrong_language"
    assert engine._prepared_rundown_trace[0]["issues"][0]["code"] == "wrong_language"
    assert all("日本語" in f"{prompt}\n{system}" for prompt, system in llm.requests)
    restored_engine = SimpleNamespace()
    parsed = PreparedStory.model_validate_json(story.model_dump_json())
    load_story(restored_engine, parsed)
    assert restored_engine.stream_lang_context.primary_language is Language.JA
    assert [line.text for line in restored_engine.state.script_lines] == [
        line["text"] for line in story.draft["lines"]
    ]

    corrupt = parsed.model_dump(mode="json")
    corrupt["inputs"]["language"] = "en"
    corrupt["sha256"] = PreparedStory.digest(corrupt)
    with pytest.raises(ValueError, match="Unsupported prepared story language"):
        PreparedStory.model_validate(corrupt)


def test_japanese_reply_uses_selected_language_for_non_japanese_chat():
    prompts = []
    responses = iter(("谢谢你，我会继续说。", "コメントを読んだよ、ありがとう！"))

    def generate(prompt):
        prompts.append(prompt)
        return next(responses)

    interleaver = DanmakuInterleaver(SimpleNamespace(generate=generate))
    params = dict(
        identity="猫が好き", verbal_tics=[], topic="猫玩具", last_line="猫が見ていた。",
        danmaku_text="Can you tell us more?", user="guest", language="ja",
    )
    assert interleaver.respond(**params) is None
    assert interleaver.respond(**params) == "コメントを読んだよ、ありがとう！"
    assert all("自然な日本語" in prompt and "Can you tell us more?" in prompt for prompt in prompts)
