from types import SimpleNamespace

import pytest

from echuu.config import EngineConfig
from echuu.content_language import has_expected_speech_language, prepared_body_limits
from echuu.live.danmaku_interleave import DanmakuInterleaver
from echuu.live.engine import EchuuLiveEngine
from echuu.live.language import Language
from echuu.prepared_story import PreparedStory, generate_story, load_story
from echuu.session_types import SessionInput, SessionOptions
from echuu.storytelling import StorytellingDriver
from tests.test_clip_repair import draft, plan
from tests.test_japanese_prepared_story import ScriptModel

SCRIPT_TEXT = {
    "en": (
        "I opened the cupboard and saw that my cat's toy box had somehow moved. I wondered if one of us had left the door open.",
        "My friend said she had already checked the box. I asked her to try shaking it, and something small rattled right away.",
        "That sound belonged to a tiny bell, but I couldn't see its toy anywhere. The cat looked up and immediately turned away.",
        "I moved the box closer to the window and found an empty wrapper beside it. Now I knew that I hadn't imagined the bell.",
        "When I walked across the room, my cat stretched out one paw and pulled the blanket closer. I waited to see what she did.",
        "A minute later I heard the other bell under that same blanket. The cat had tucked the whole toy under her head like a pillow.",
        "I was about to take it back when she rang the bell with her nose. She'd figured out a much easier way to get my attention.",
    ),
    "ko": (
        "찬장 문을 열어 보니 고양이 장난감이 들어 있던 상자가 어제보다 가벼웠어요. 처음에는 제가 어딘가에 옮겨 놓은 줄 알았어요.",
        "친구가 상자를 흔들어 보자고 해서 바닥에 조심히 내려놓았어요. 그때 상자 안에서 작은 방울 소리가 딱 한 번 들렸어요.",
        "방울 소리는 분명 장난감에서 나던 소리였는데 아무리 찾아도 고양이가 좋아하던 장난감은 보이지 않았어요. 참 이상했어요.",
        "창가로 가서 상자를 자세히 살펴봤어요. 빈 종이 봉투 옆에 단추만 하나 떨어져 있고 나머지는 그대로 남아 있었어요.",
        "그사이에 고양이가 자기 이불을 앞발로 슬쩍 끌어당기는 게 보였어요. 모르는 척하고 한 발 뒤로 물러서서 기다렸어요.",
        "잠시 뒤에 이불 아래에서 다시 방울 소리가 났어요. 고양이는 장난감을 베개처럼 머리 밑에 넣고 태연하게 자고 있었어요.",
        "장난감을 꺼내려다가 고양이가 코로 방울을 한 번 더 울리는 걸 봤어요. 저를 부르는 자기만의 방법을 찾은 모양이에요.",
    ),
}

SECTIONS = {
    "en": ("Hi everyone, come listen to what happened for a minute.",
           "I heard a noise by the cupboard that I couldn't quite place.",
           "That's all from me tonight. I'll see you again soon!"),
    "ko": ("안녕하세요, 오늘 있었던 일 하나 들려드릴게요.",
           "찬장 앞에서 이상한 소리를 하나 들었거든요.",
           "오늘 얘기는 여기까지 할게요, 다음에 또 만나요."),
}


def localized_draft(language):
    texts = SCRIPT_TEXT[language]
    return {
        "lines": [{"id": f"L{index:03}", "text": text} for index, text in enumerate(texts, 1)],
        "quotables": [{"line_id": "L001", "quote": texts[0][:40], "why_shareable": "first clue"}],
    }


def localized_sections(language):
    a,b,c=SECTIONS[language]
    return {
        "opening": [
            {"id": "O001", "text": a, "emotion": "joy"},
            {"id": "O002", "text": b, "emotion": "excited"},
        ],
        "closing": [{"id": "C001", "text": c, "emotion": "relaxed"}],
    }


@pytest.mark.parametrize(("language", "primary", "instruction"), [
    ("en", Language.EN, "natural spoken English"),
    ("ko", Language.KO, "한국어 구어체"),
])
def test_generation_rereads_locale_length_and_preserves_saved_lines(language, primary, instruction):
    final_draft = localized_draft(language)
    final_sections = localized_sections(language)
    bad_sections = {
        **final_sections, "closing": [{
            "id": "C001", "text": "今天的直播到此就暂时结束，我们下次还有更多事情可以一起讨论。",
            "emotion": "relaxed",
        }],
    }
    llm = ScriptModel(plan(), draft(), final_draft, bad_sections, final_sections)
    config = EngineConfig()
    engine = EchuuLiveEngine(config=config, llm=llm, tts=SimpleNamespace(enabled=False))
    source = SessionInput(session_id=language, name="Gumi", persona="猫のおもちゃが好き", topic="猫玩具", language=language)
    artifact = generate_story(source, config, SessionOptions(grounding=False), engine=engine)
    assert artifact.inputs.topic == "猫玩具"
    assert artifact.schema_version == 2
    assert artifact.draft == {**final_draft, **final_sections}
    assert engine._legacy_writer_trace["draft_attempts"][0]["issues"][0]["code"] == "wrong_language"
    assert engine._prepared_rundown_trace[0]["issues"][0]["code"] == "wrong_language"
    assert all(instruction in f"{prompt}\n{system}" for prompt, system in llm.requests)
    min_chars, max_chars = prepared_body_limits(language)
    assert min_chars <= sum(len(line["text"]) for line in final_draft["lines"]) <= max_chars
    restored = SimpleNamespace()
    load_story(restored, PreparedStory.model_validate_json(artifact.model_dump_json()))
    assert restored.stream_lang_context.primary_language is primary
    assert [line.text for line in restored.state.script_lines] == list(SCRIPT_TEXT[language])


@pytest.mark.parametrize(("language", "response"), [
    ("en", "Thanks for asking, I will tell you what happened."),
    ("ko", "댓글 고마워요, 아까 들었던 이야기를 더 해 볼게요."),
])
def test_interaction_language_is_explicit_for_foreign_chat(language, response):
    items = iter(("謝謝你的留言。", response))
    agent = DanmakuInterleaver(SimpleNamespace(generate=lambda _: next(items)))
    fields = dict(identity="Gumi", verbal_tics=[], topic="猫玩具", last_line="猫が見ていた。",
                  danmaku_text="そのあとどうなったの？",user="Guest",language=language)
    assert agent.respond(**fields) is None
    assert agent.respond(**fields) == response
    assert has_expected_speech_language(response, language)


@pytest.mark.parametrize("language", ["en", "ko"])
def test_unbound_rooms_prepare_all_sections_in_configured_language(language):
    config = EngineConfig()
    llm = ScriptModel(plan(), localized_draft(language), localized_sections(language))
    engine = EchuuLiveEngine(config=config, llm=llm, tts=SimpleNamespace(enabled=False))
    inputs = SessionInput(session_id="unbound", name="Gumi", persona="", topic="猫玩具", language=language)
    driver = StorytellingDriver(inputs, config, SessionOptions(grounding=False), engine=engine)
    try:
        preview = driver.prepare()
        assert preview[:2] == list(SECTIONS[language][:2])
        assert preview[-1] == SECTIONS[language][-1]
        assert engine.stream_lang_context.explicit_language == language
    finally:
        driver.close()
