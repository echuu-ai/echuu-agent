"""开场/收尾 rundown 段：结构、自然感机制挂钩、TTS 降级。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from echuu.core.structure_breaker import StructureBreaker
from echuu.modes.rundown import produce_opening_events, produce_closing_events


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.prompts = []

    def call(self, prompt, max_tokens=0, **kwargs):
        self.prompts.append(prompt)
        return self.payload


class FakeTTS:
    def __init__(self):
        self.instructions = []

    def set_instruction(self, instruction):
        self.instructions.append(instruction)

    def synthesize(self, text):
        return None  # 无 TTS 环境：音频降级为 None


class FakeEngine:
    def __init__(self, payload):
        self.llm = FakeLLM(payload)
        self.tts = FakeTTS()
        self.structure_breaker = StructureBreaker()


def test_opening_shape_and_stage():
    engine = FakeEngine('[{"text":"诶，来了来了","emotion":"joy"},'
                        '{"text":"今天咱一起看个视频","emotion":"excited"},'
                        '{"text":"那就开始吧","emotion":"joy"}]')
    events = produce_opening_events(engine, "小回", "毒舌主播", "整活视频", "reaction")
    assert 2 <= len(events) <= 3
    for ev in events:
        assert ev["type"] == "step"
        assert ev["stage"] == "opening"
        assert ev["event"] == "opening"
        assert ev["mode"] == "reaction"
        assert ev["speech"]
        assert ev["cls"] == "NATURAL_CASUAL"
        assert ev["motion"] == {"state": "greet"}
        assert "timeline_t" not in ev
    # 开场 prompt 必须带模式意图（reaction=一起看视频）
    assert "看" in engine.llm.prompts[0]


def test_closing_goes_through_structure_breaker():
    engine = FakeEngine('[{"text":"今天这视频比我想的离谱","emotion":"joy"},'
                        '{"text":"所以说这次经历真的教会了我很多人生道理","emotion":"relaxed"}]')
    events = produce_closing_events(engine, "小回", "毒舌主播", "整活视频", "reaction")
    assert 1 <= len(events) <= 2
    last = events[-1]
    assert last["stage"] == "closing"
    assert last["motion"] == {"state": "farewell"}
    # break_structure 会处理升华结尾（删除或改写为非闭合），不应原样保留说教句
    assert last["speech"] != "所以说这次经历真的教会了我很多人生道理"


def test_opening_invokes_thread_loss_mechanism():
    """自然感硬约束回归锁：opening 必须实际调用 insert_thread_loss。"""
    engine = FakeEngine('[{"text":"诶，来了","emotion":"joy"},{"text":"今天聊点事","emotion":"joy"}]')
    calls = []
    original = engine.structure_breaker.insert_thread_loss

    def spy(lines, probability=0.2):
        calls.append((len(lines), probability))
        return original(lines, probability)

    engine.structure_breaker.insert_thread_loss = spy
    produce_opening_events(engine, "小回", "毒舌主播", "话题", "storytelling")
    assert calls, "opening 没有经过 insert_thread_loss"
    assert calls[0][0] == 2


def test_tts_instruction_uses_natural_casual():
    engine = FakeEngine('[{"text":"哈喽哈喽","emotion":"joy"}]')
    produce_opening_events(engine, "小回", "毒舌主播", "话题", "storytelling")
    assert engine.tts.instructions  # set_instruction 被调用


def test_opening_receives_current_user_background():
    engine = FakeEngine('[{"text":"新手机刚拆箱。","emotion":"joy"}]')
    background = '今年已经拿年终奖买了苹果折叠屏。'
    produce_opening_events(engine, '角色', '数码爱好者', '年终奖买苹果折叠屏', 'storytelling', background=background)
    assert background in engine.llm.prompts[0]
    assert '优先保留其中已发生的事实' in engine.llm.prompts[0]


def test_closing_receives_current_background_and_spoken_interactions():
    engine = FakeEngine('[{"text":"膜贴好了，下次见。","emotion":"joy"}]')
    produce_closing_events(engine, '角色', '人设', '购机', 'storytelling',
                           background='已买折叠屏', spoken_lines=['观众送信提醒贴膜，我已经贴好了。'])
    assert '已买折叠屏' in engine.llm.prompts[0]
    assert '观众送信提醒贴膜，我已经贴好了。' in engine.llm.prompts[0]
