"""Compile inline performance directions into speakable text, without deleting actions."""
import re
from .nodes import require,exact_ids,nonempty
from .models import GateBlocked
from .repair import run_repair

DIRECTION=re.compile(r'[（(][^（）()\n]+[）)]')

def prepare_delivery(nodes,fixture,text,lines,parent):
    targets=[line for line in lines if DIRECTION.search(line['text'])]
    if not targets:return text,lines,parent
    ids=[l['id'] for l in targets]
    def validate(d):
        exact_ids(d.get('patches'),ids)
        for p in d['patches']:
            require(nonempty(p.get('text')) and '\n' not in p['text'] and not DIRECTION.search(p['text']),'delivery must be directly speakable')
    d,aid=nodes.call('delivery_compile','''这些行混入括号舞台说明，不能直接交给TTS。仅重写targets中的行，保留动作、人物选择和因果信息，把必要动作转成自然的第一人称口语叙述；不要机械删除动作，不新增事实。不写任何括号舞台说明。返回 {patches:[{id,text:整行台词}]}。未指定的行禁止修改。''',{'fixture':fixture,'lines':lines,'targets':ids},[parent],validate,max_tokens=2400)
    patches={p['id']:p['text'] for p in d['patches']};result=[dict(l,text=patches.get(l['id'],l['text'])) for l in lines]
    proposed='\n'.join(l['text'] for l in result)
    pid=nodes.store.add('delivery_candidate',{'text':proposed,'lines':result,'modified_ids':ids},parents=[parent,aid])
    _,_,review,rid=run_repair(proposed,fixture,nodes.llm,nodes.store,pid,0)
    from .pipeline import assess_story
    ok,_,gid=assess_story(nodes,fixture,proposed,pid)
    if review['status']!='accepted' or not ok:
        nodes.store.add('delivery_rejected',{'reason':'spoken rewrite failed semantic recheck'},parents=[pid,rid,gid],status='rejected')
        raise GateBlocked('delivery compilation needs review')
    cid=nodes.store.add('delivery_approved',{'text':proposed,'lines':result},parents=[pid,rid,gid])
    return proposed,result,cid
