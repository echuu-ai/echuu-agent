"""主题语义 grounding：联网检索主题真实含义，注入 background。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from echuu.modes.topic_brief import produce_topic_brief, merge_brief_into_background


class SearchLLM:
    def __init__(self, answer="云养猫指自己不养猫，通过刷猫视频、追猫博主获得养猫体验。"):
        self.answer = answer
        self.prompts = []

    def call_with_search(self, prompt, system=None, max_tokens=0):
        self.prompts.append(prompt)
        return self.answer


class PlainLLM:
    """没有 call_with_search 的 provider（Claude/Gemini）。"""

    def call(self, prompt, max_tokens=0):
        return "should not be used"


def test_brief_uses_search_call():
    llm = SearchLLM()
    brief = produce_topic_brief(llm, "云养猫")
    assert "刷猫视频" in brief
    assert "云养猫" in llm.prompts[0]


def test_brief_skips_provider_without_search():
    assert produce_topic_brief(PlainLLM(), "云养猫") == ""


def test_brief_empty_topic():
    assert produce_topic_brief(SearchLLM(), "") == ""


def test_brief_search_failure_degrades_to_empty():
    class Broken:
        def call_with_search(self, prompt, system=None, max_tokens=0):
            raise RuntimeError("network down")

    assert produce_topic_brief(Broken(), "云养猫") == ""


def test_merge_brief_into_background():
    merged = merge_brief_into_background("原有背景", "云养猫", "真实含义……")
    assert "原有背景" in merged
    assert "真实含义" in merged
    # 空 brief 不改动 background
    assert merge_brief_into_background("原有背景", "云养猫", "") == "原有背景"
    # 空 background 也能独立成段
    assert "真实含义" in merge_brief_into_background("", "云养猫", "真实含义……")


def test_merge_brief_forbids_using_search_results_to_reverse_the_premise():
    """docs/research/2026-09-11-fewshot-leakage-retest.md issue #2: 即使联网检索
    返回"这个产品现实中不存在"，也不能被用来推翻用户本次给定的故事前提。"""
    merged = merge_brief_into_background(
        "本次用户设定：已经用年终奖买了最新的苹果折叠屏",
        "年终奖买了最新的苹果折叠屏",
        "苹果目前并未发售折叠屏手机，该说法在现实中查无此产品。",
    )
    assert "不得引用检索结果反过来证明本次前提是假的" in merged
    assert "优先级低于本次用户设定" in merged


def test_brief_prompt_instructs_against_fact_checking_the_users_own_premise():
    """搜索 prompt 本身不能邀请模型去核实用户已给定的经历/物品是否真实存在。"""
    from echuu.modes.topic_brief import _BRIEF_PROMPT
    assert "不是事实核查请求" in _BRIEF_PROMPT
    assert "官网查无此产品" in _BRIEF_PROMPT
