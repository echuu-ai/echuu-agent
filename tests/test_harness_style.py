from echuu.harness.style import contrast_patterns,style_issues
from echuu.harness.repair import line_items
from echuu.harness.report import canon_explanation

def test_repeated_contrast_variants():
    lines=line_items('不是不好吃！是咖喱太香了。不是盒子小，是我装太满。\n哈，不是。是刚才没看清。\n不是因为被说中，是突然记起。')
    assert len(contrast_patterns(lines))==4
    assert {i['id'] for i in style_issues(lines)}=={'L001','L002','L003'}
    assert all(i['origin']=='style_scan' for i in style_issues(lines))

def test_single_real_correction_is_not_flagged():
    assert not style_issues(line_items('不是这个盒子，是旁边那个。'))
    assert not contrast_patterns(line_items('不是每个人都喜欢咖喱。今天是晴天。'))

def test_invalid_quote_explains_judge_failure_not_character_failure():
    note=canon_explanation({'canon_judge_error':'ValueError'},{'status':'invalid','reason':'invented canon quote'})
    assert '连续原文' in note and '不等于剧本' in note
    assert '未单独运行' in canon_explanation({'repair':{'status':'accepted'}},None)
