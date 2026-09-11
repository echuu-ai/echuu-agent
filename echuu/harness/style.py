"""Candidate style warnings; semantic review may retain real corrections."""
import re

CONTRAST=re.compile(r'不是[^。！？\n]{0,45}?[，,！!。]\s*(?:而|其实)?是')

def contrast_patterns(lines):
    return [{'id':line['id'],'span':m.group()} for line in lines for m in CONTRAST.finditer(line['text'])]

def style_issues(lines):
    matches=contrast_patterns(lines)
    if len(matches)<3:
        return []
    ids=list(dict.fromkeys(m['id'] for m in matches[1:]))
    return [dict(id=i,decision='rewrite',code='style_repetition',rule_ids=['P_STYLE'],addition='none',origin='style_scan',
        reason=f'全篇检测到 {len(matches)} 处“不是…是…”结构。检查此处是否重复先否定再解释；真实纠正误会可保留，其余直接写动作、感受或原因，不改写相容经历。') for i in ids]
