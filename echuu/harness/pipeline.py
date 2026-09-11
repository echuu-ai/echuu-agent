"""Input → intents → outlines → units → whole-story gates → optional humor → compiled show."""
from dataclasses import asdict
from .models import GateBlocked
from .nodes import require,nonempty,exact_ids
from .repair import line_items,deterministic_issues,run_repair
from .retrieval import retrieve

GATES=('complete','clear','persona','concrete','shareable')

def known_evidence(fixture):return {e['id'] for e in fixture.get('evidence',[])}

def evidence_refs(refs,known):
    require(isinstance(refs,list) and all(isinstance(x,str) for x in refs) and set(refs)<=known,'unknown evidence ID')

def assess_story(nodes,fixture,text,parent):
    lines=line_items(text); ids={l['id'] for l in lines}
    def validate(d):
        require(isinstance(d,dict) and set(d.get('gates',{}))==set(GATES),'missing story gates')
        for gate in d['gates'].values():
            require(type(gate.get('pass')) is bool and nonempty(gate.get('reason')),'invalid gate verdict')
            require(isinstance(gate.get('line_ids'),list) and set(gate['line_ids'])<=ids,'invalid gate line IDs')
        require(nonempty(d.get('premise')),'missing retellable premise')
    result,aid=nodes.call('whole_story_gate','''检查完整故事，不打总分。返回 {premise:一句话复述,gates:{complete:{pass:bool,reason:具体因果分析,line_ids:[]},clear:{同结构},persona:{同结构},concrete:{同结构},shareable:{同结构}}。
complete 检查起因/行动/结果，clear 检查顺序、重复、指代，persona 只拒绝真实设定冲突，不因新小经历未给出而拒绝；concrete 检查具体场景与动作；shareable 判断是否能复述一个有具体变化的小故事，不要求宏大/强笑点。无笑点不影响完整性。假设不等于事实断言；不要因可能暗示秘密而臆测泄密。line_ids 必须来自输入；不能编造引文。''',{'fixture':fixture,'lines':lines},[parent],validate,max_tokens=1600)
    hard=deterministic_issues(lines,fixture)
    accepted=all(g['pass'] for g in result['gates'].values()) and not hard
    decision=nodes.store.add('whole_story_decision',{'accepted':accepted,'hard_issues':hard,'gates':result['gates'],'premise':result['premise']},parents=[parent,aid],status='accepted' if accepted else 'rejected')
    return accepted,result,decision


def choose(nodes,name,candidates,fixture,parent):
    ids=[c['id'] for c in candidates]
    def validate(d):
        exact_ids(d.get('assessments'),ids)
        for row in d['assessments']:
            require(type(row.get('pass')) is bool and nonempty(row.get('reason')),'invalid candidate judgment')
        require(d.get('selected_id') is None or d['selected_id'] in ids,'unknown selection')
        if d['selected_id'] is not None:
            require(next(r for r in d['assessments'] if r['id']==d['selected_id'])['pass'],'selected rejected candidate')
    d,aid=nodes.call(name,'''比较所有候选的因果完整性、人物选择、当前场景和可复述性。不要用辞藻打分，不要求强行反转；普通相容经历允许增补。返回 {assessments:[{id,pass:bool,reason:具体理由}],selected_id:最好的合格候选ID或null}。所有ID恰好评一次。''',{'fixture':fixture,'candidates':candidates},[parent],validate,max_tokens=1500)
    ranking=[d['selected_id']] if d['selected_id'] else []
    ranking += [r['id'] for r in d['assessments'] if r['pass'] and r['id'] not in ranking]
    selected=nodes.store.add(name+'_selection',{'ranking':ranking,'assessment':d},parents=[parent,aid],status='accepted' if ranking else 'rejected')
    return [next(c for c in candidates if c['id']==i) for i in ranking],selected


def compile_content(nodes,fixture,text,lines,parent,line_sources,flags):
    from .delivery import prepare_delivery
    text,lines,delivery_id=prepare_delivery(nodes,fixture,text,lines,parent)
    if delivery_id!=parent:
        line_sources=[dict(s,pre_delivery_artifact_id=s['artifact_id'],artifact_id=delivery_id) for s in line_sources]
        parent=delivery_id
    char_count=sum(len(l['text']) for l in lines)
    preflight={'release_ready':True,'chars':char_count,'length_matched':flags.min_chars<=char_count<=flags.max_chars,
               'target_chars':[flags.min_chars,flags.max_chars],'line_count':len(lines),'hard_issues':deterministic_issues(lines,fixture)}
    preflight['release_ready']=bool(lines) and not preflight['hard_issues']
    pid=nodes.store.add('runtime_preflight',preflight,parents=[parent],status='accepted' if preflight['release_ready'] else 'rejected')
    if not preflight['release_ready']:raise GateBlocked('runtime preflight failed')
    units=[]
    for index in range(4):
        beat_ids={s['id'] for s in line_sources if s.get('beat_id')==f'B{index+1}'}
        part=[l for l in lines if l['id'] in beat_ids]
        units.append({'index':index,'purpose':'故事进展','lines':part,'delivery':{'speech_rate':fixture.get('speech_rate',1.0)}})
    content={'version':'compiled-show-v1','text':text,'lines':lines,'units':units,'release_ready':True,
             'lineage':line_sources,'fixture':fixture,'flags':flags.to_dict(),'preflight':preflight,
             'provenance_scope':'writer-declared evidence/beat references validated for existence, not independently proven semantic causation'}
    aid=nodes.store.add('compiled_content',content,parents=[parent,pid,*list(dict.fromkeys(s['artifact_id'] for s in line_sources if s.get('artifact_id')))])
    return content,aid


def produce(nodes,fixture,flags,parent,raw_examples=()):
    known=known_evidence(fixture)
    eid=nodes.store.add('evidence_pack_v1',{'fixture':fixture,'evidence':fixture.get('evidence',[]),'creative_scope':'compatible everyday invention is run-only; canon/time/knowledge are constraints'},parents=[parent],inputs=fixture)
    intent_ids=[f'I{i+1}' for i in range(flags.intent_candidates)]
    def intent_validator(d):
        exact_ids(d.get('candidates'),intent_ids)
        for c in d['candidates']:
            for key in ('premise','desire','obstacle','action','outcome'):require(nonempty(c.get(key)),'missing intent '+key)
            evidence_refs(c.get('evidence_ids'),known)
    intent,ia=nodes.call('story_intents',f'''为当前角色和事件生成 {len(intent_ids)} 个不同讲述切入点，ID={intent_ids}。不能改掉已给的因果和结局，差异来自人物关注点和表达。返回 {{candidates:[{{id,premise,desire,obstacle,action,outcome,evidence_ids:[]}}]}}。保持日常、可讲完；不发明重大背景。''',{'fixture':fixture},[eid],intent_validator)
    intents,isa=choose(nodes,'intent_judge',intent['candidates'],fixture,ia)
    if not intents:raise GateBlocked('no viable story intent')
    attempts=0; feedback=[]
    for selected_intent in intents:
        outline_ids=[f'O{i+1}' for i in range(flags.outline_candidates)]
        def outline_validator(d):
            exact_ids(d.get('candidates'),outline_ids)
            for c in d['candidates']:
                require(nonempty(c.get('premise')),'missing outline premise')
                require(isinstance(c.get('mechanisms'),list) and all(isinstance(x,str) for x in c['mechanisms']),'invalid mechanisms')
                beats=c.get('beats');exact_ids(beats,['B1','B2','B3','B4'])
                for b in beats:
                    for key in ('action','change','purpose'):require(nonempty(b.get(key)),'missing beat '+key)
                    evidence_refs(b.get('evidence_ids'),known)
        outlines,oa=nodes.call('outlines',f'''为选中的故事意图生成 {len(outline_ids)} 个大纲，ID={outline_ids}。每个恰好4个beat（B1..B4），按因果推进到已有结局。返回 {{candidates:[{{id,premise,mechanisms:[从misunderstanding/contrast/reversal/callback/everyday选],beats:[{{id,action,change,purpose,evidence_ids:[]}}]}}]}}。最后一段必须有行动结果；普通真诚故事也可，不要强加秘密坦白、代价或人生总结。''',{'fixture':fixture,'intent':selected_intent,'previous_failures':feedback},[isa],outline_validator)
        ranked,osa=choose(nodes,'outline_judge',outlines['candidates'],fixture,oa)
        for outline in ranked:
            if attempts>=flags.max_plan_attempts:break
            attempts+=1
            chosen_id=nodes.store.add('outline_attempt',{'attempt':attempts,'intent':selected_intent,'outline':outline},parents=[isa,osa],inputs=outline)
            references,refid=retrieve(outline,flags.reference_mode,nodes.store,chosen_id,raw_examples)
            lines=[];lineage=[];unit_ids=[]
            for index,beat in enumerate(outline['beats']):
                candidate_ids=[f'U{index+1}C{i+1}' for i in range(flags.unit_candidates)]
                def unit_validator(d):
                    exact_ids(d.get('candidates'),candidate_ids)
                    for c in d['candidates']:
                        require(isinstance(c.get('lines'),list) and 1<=len(c['lines'])<=3,'unit needs 1..3 lines')
                        for line in c['lines']:
                            require(nonempty(line.get('text')) and '\n' not in line['text'],'one spoken line per record')
                            evidence_refs(line.get('evidence_ids'),known)
                            require(line.get('origin') in {'evidence','compatible_invention'},'missing invention provenance')
                unit,uid=nodes.call('unit_writer',f'''只写当前beat的口语台词，产生 {len(candidate_ids)} 个候选，ID={candidate_ids}。每个候选这一段约{flags.min_chars//4}至{flags.max_chars//4}字，1至3句记录。保留legacy擅长的具体物件、自然语感和完整小故事，但不强行跑题/自我辩解/身体反应。不复述已说内容，不提前说后续beat，不写舞台括号。不复制参考里的名字、经历。返回 {{candidates:[{{id,lines:[{{text,evidence_ids:[],origin:evidence或compatible_invention}}]}}]}}。已有故事为连续前文，最后一段自然收尾而非总结道理。''',{'fixture':fixture,'outline':outline,'beat':beat,'spoken_so_far':lines,'references':references},[chosen_id,refid,*unit_ids[-1:]],unit_validator,max_tokens=1900)
                if len(unit['candidates'])>1:
                    rank,sid=choose(nodes,'unit_candidate_judge',unit['candidates'],fixture,uid)
                    if not rank:raise GateBlocked('no viable unit')
                    selected=rank[0];uid=sid
                else:selected=unit['candidates'][0]
                for row in selected['lines']:
                    lid=f'L{len(lines)+1:03d}'
                    lines.append({'id':lid,'text':row['text']})
                    lineage.append({'id':lid,'beat_id':beat['id'],'evidence_ids':row['evidence_ids'],'origin':row['origin'],'artifact_id':uid})
                unit_ids.append(uid)
            text='\n'.join(l['text'] for l in lines)
            draft_id=nodes.store.add('unit_draft',{'text':text,'lines':lines,'lineage':lineage},parents=unit_ids)
            accepted,review,rid=assess_story(nodes,fixture,text,draft_id)
            # Complete/clear failures return to another outline before cosmetic repair.
            if not review['gates']['complete']['pass'] or not review['gates']['clear']['pass']:
                feedback.append(review)
                nodes.store.add('outline_rollback',{'attempt':attempts,'reason':review},parents=[chosen_id,rid],status='rejected')
                continue
            selected_id=draft_id
            if flags.local_repair:
                text,lines,repair,selected_id=run_repair(text,fixture,nodes.llm,nodes.store,draft_id,flags.max_repairs)
                if repair['status']!='accepted':
                    feedback.append(repair)
                    nodes.store.add('repair_blocked',{'attempt':attempts,'repair':repair},parents=[selected_id],status='rejected')
                    continue
                accepted,review,rid=assess_story(nodes,fixture,text,selected_id)
            if not accepted:
                feedback.append(review);continue
            # Same line IDs remain stable after local edits; a repair changes the declared provenance scope.
            if selected_id!=draft_id:
                lineage=[dict(s,pre_repair_artifact_id=s['artifact_id'],artifact_id=selected_id,origin='repaired_'+s['origin']) for s in lineage if any(l['id']==s['id'] and l['text'] for l in lines)]
            base=nodes.store.add('approved_story',{'text':text,'lines':lines,'lineage':lineage,'gate':review},parents=[selected_id,rid])
            if flags.humor:
                from .humor import humor_pass
                text,lines,base=humor_pass(nodes,fixture,text,lines,base,flags)
                lineage=[dict(s,pre_humor_artifact_id=s['artifact_id'],artifact_id=base) for s in lineage]
            return compile_content(nodes,fixture,text,lines,base,lineage,flags)
        if attempts>=flags.max_plan_attempts:break
    raise GateBlocked('no approved story within bounded planning/repair attempts')
