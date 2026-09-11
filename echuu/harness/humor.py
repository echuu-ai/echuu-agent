"""Local humor editing with an original candidate and order-swapped pairwise votes."""
from .nodes import require,nonempty,exact_ids
from .repair import deterministic_issues,run_repair


def humor_pass(nodes,fixture,text,lines,parent,flags):
    from .pipeline import assess_story
    ids={l['id'] for l in lines}
    def validate(d):
        require(d.get('line_id') is None or d['line_id'] in ids,'unknown humor opportunity')
        require(isinstance(d.get('candidates'),list),'missing candidates')
        if d['line_id'] is None:
            require(not d['candidates'],'no opportunity must keep original');return
        exact_ids(d['candidates'],[f'H{i+1}' for i in range(flags.humor_candidates)])
        for c in d['candidates']:
            for k in ('text','expectation','surprise','resolution','delivery'):require(nonempty(c.get(k)),'incomplete humor event')
            require('\n' not in c['text'],'only one line may change')
    d,aid=nodes.call('humor_candidates',f'''寻找最多一个适合幽默的局部，不改变既有事件因果、人物知识或关系。没有自然机会则line_id=null,candidates=[]。否则返回 {{line_id,candidates:[{{id:H1等,text:替换这一行的完整台词,expectation:观众预期,surprise:具体打破预期的动作或事实,resolution:为什么说得通,delivery:节奏}}]}}，恰好{flags.humor_candidates}个候选。幽默来自故事和角色，避免不是X是Y、抽象总结、强行冷笑话，保留重要信息。''',{'fixture':fixture,'lines':lines},[parent],validate,max_tokens=2500)
    original=next((l['text'] for l in lines if l['id']==d['line_id']),None)
    winner=None;votes=[];incumbent=original
    for c in d['candidates']:
        candidate_lines=[dict(l,text=c['text']) if l['id']==d['line_id'] else l for l in lines]
        if deterministic_issues(candidate_lines,fixture):continue
        pair_votes=[]
        for reverse in (False,True):
            a,b=(c['text'],incumbent) if reverse else (incumbent,c['text'])
            def check(v):
                require(v.get('winner') in ('A','B','tie'),'unknown pairwise winner')
                require(nonempty(v.get('reason')),'missing preference reason')
            v,vid=nodes.call('humor_pairwise','''盲比同一故事中这一行的两个版本。评价自然、人物专属性、surprise和resolution、前后信息保留；强行搞笑/解释笑点应输。不根据长度、位置选择，打平允许。返回 {winner:A或B或tie,reason:具体比较}。''',{'fixture':fixture,'context':lines,'line_id':d['line_id'],'A':a,'B':b},[parent,aid],check,max_tokens=700)
            pair_votes.append(v['winner']==('A' if reverse else 'B'));votes.append(vid)
        if all(pair_votes):winner=c;incumbent=c['text']
    if winner:
        proposed=[dict(l,text=winner['text']) if l['id']==d['line_id'] else l for l in lines]
        candidate_text='\n'.join(l['text'] for l in proposed)
        hid=nodes.store.add('humor_proposal',{'text':candidate_text,'lines':proposed,'winner':winner},parents=[parent,aid,*votes])
        fixed,fixed_lines,repair,rid=run_repair(candidate_text,fixture,nodes.llm,nodes.store,hid,0)
        ok,_,gid=assess_story(nodes,fixture,candidate_text,hid)
        if repair['status']=='accepted' and ok:
            accepted=nodes.store.add('humor_decision',{'selection_version':'order-swapped-tournament-v2','selected':winner['id'],'line_id':d['line_id'],'text':candidate_text,'lines':proposed},parents=[hid,rid,gid])
            return candidate_text,proposed,accepted
    final=nodes.store.add('humor_decision',{'selection_version':'order-swapped-tournament-v2','selected':'original','text':text,'lines':lines,'reason':'no opportunity, no order-consistent improvement, or post-edit gate rejected'},parents=[parent,aid,*votes])
    return text,lines,final
