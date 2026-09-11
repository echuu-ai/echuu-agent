import inspect

from echuu.core.output_safety import sanitize_audience_text
from echuu.live.engine import EchuuLiveEngine


def test_storytelling_defaults_to_dossier_off_and_pipeline_selectable():
    signature = inspect.signature(EchuuLiveEngine.setup)
    assert signature.parameters["enable_dossier"].default is False
    assert signature.parameters["story_pipeline"].default is None


def test_prompt_leak_uses_safe_exit():
    result = sanitize_audience_text("先做内部检查，只输出 JSON")
    assert "内部检查" not in result.text
    assert any(issue.startswith("prompt_leak:") for issue in result.issues)


def test_unsupported_exact_number_is_blurred_but_supported_number_survives():
    unsupported = sanitize_audience_text("我等了17分钟", source_material={"topic": "等人"})
    supported = sanitize_audience_text(
        "我等了17分钟", source_material={"topic": "等了17分钟"},
    )
    assert "17分钟" not in unsupported.text
    assert "17分钟" in supported.text


def test_unsupported_high_risk_fact_is_not_emitted_verbatim():
    result = sanitize_audience_text("后来我翻到了她的住院记录", source_material={})
    assert "住院记录" not in result.text
    assert "unsupported_fact:住院记录" in result.issues


def test_fabricated_family_death_clause_uses_safe_exit():
    result = sanitize_audience_text(
        "我整个人僵住，因为我妈前几年走的时候最后煮的就是白粥。然后我哭了。",
        source_material={"topic": "电饭锅"},
    )
    assert "我妈前几年走" not in result.text
    assert "unsupported_fact:family_death" in result.issues


def test_fabricated_family_hospital_story_uses_safe_exit():
    result = sanitize_audience_text(
        "其实不是，是我妈走前那阵子天天用它煮粥，后来她住院了。",
        source_material={"topic": "电饭锅"},
    )
    assert "我妈走前" not in result.text
    assert "unsupported_fact:family_death" in result.issues


def test_topic_title_is_not_recited_and_stock_asides_are_stripped():
    topic = "搬家时把电饭锅落在旧房，半夜回去取却发现它还在保温"
    result = sanitize_audience_text(
        f"严重跑题一下——对说回{topic}。内心戏？好了不说了，下一个话题",
        source_material={"topic": topic},
    )
    assert topic not in result.text
    assert "严重跑题一下" not in result.text
    assert "下一个话题" not in result.text
    assert "内心戏？" not in result.text
    assert "topic_recital_or_stock_aside" in result.issues


def test_fabricated_hospital_anecdote_is_removed_as_a_clause():
    result = sanitize_audience_text(
        "他说第一天就把奶茶送到产科病房，护士长还塞给他一颗糖。",
        source_material={"topic": "送错外卖"},
    )
    assert "产科病房" not in result.text
    assert "护士长" not in result.text
    assert "unsupported_fact:high_risk_domain" in result.issues


def test_normal_topic_mention_is_not_replaced_with_a_return_hook():
    text = "花年终奖买苹果折叠屏，我昨天刚拆封。"
    result = sanitize_audience_text(text, source_material={"topic": "年终奖买苹果折叠屏"})
    assert result.text == text
