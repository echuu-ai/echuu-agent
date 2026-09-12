"""Character-led clip planning, grounded review and bounded repair."""
import json
import hashlib
import re
import time
from .legacy_v4 import ScriptLineV4, SYSTEM_PROMPT, parse_object, issues_for

VOICE_DESIGNS = {
    '天上欧蒂娜': {'provenance':'中文改编设计，非原作口癖','habits':['爽快、坦率，以行动支持朋友','不频繁说王子/骑士口号，不为笑点把运动万能写成笨拙或怕普通小动物','不凭空加手机录像等未经时代设定确认的器材'], 'optional_phrases':['交给我。','等一下，这得问她。'], 'limits':'最多自然使用两次，不凑口癖'},
    '月城雪兔': {'provenance':'中文改编设计；参考对白语气，非原作固定口癖',
        'habits':['先注意对方具体状态，再自然回应','对桃矢可以温和地戳穿嘴硬，直呼桃矢；对小樱更照顾','短而直率，不装糊涂，不每句话卖萌'],
        'optional_phrases':['这样啊。','你刚才还说……','那我就放心了。'],
        'limits':'每段最多自然选用一个，全篇最多两个；不要求出现，不逐段套用；不把吃饭当唯一主题',
        'references':['https://www.lingq.com/en/learn-japanese-online/courses/878239/cardcaptor-sakura-s1e1-sakura-and-the-7517519/','https://www.animatetimes.com/news/details.php?id=1745139611']},
}
SYSTEM = """你是直播杂谈编剧。交付一个观众愿意转给朋友的中文短视频脚本，主播本人向观众讲刚发生的一件好笑的事。
人物经历和剧情围绕本次人设、主题及已收到的弹幕礼物展开；表达参考只能影响说话节奏，不得成为新的经历来源。
写说得出口的话。以具体的对话和事件推进为主，听众不用猜隐喻就听得懂。开头抛出有趣问题，中间误会加深或人物嘴硬被事实戳穿，最后有清楚的笑点回应。人物态度温和也可以好笑。
本次用户明确给定的事件和背景是不可推翻的前提，候选、正文、修复都必须保留；幽默只能来自之后的相容小插曲，不能把给定事件改成没发生、冒牌、做梦或夸口。不得虚构实际收到的弹幕或礼物。
人物资料决定身份、关系和措辞。可以新编符合设定的日常事件，不冒充原作剧情；设计的中文说话习惯标记为改编设计。资料中的秘密不对观众说。
每个段落必须给听众一个新信息。让笑点来自人物说了什么、做了什么和预期反差。故事始终用第一人称回顾，故事中的朋友不在直播现场。
不要文艺散文、物件传承、触景生情、身体特写、哲理总结；不要用不是X是Y推进表达，不加随机喵或弃讲尾句。禁止便当/饭盒主题。人物爱好不等于每次的选题。
只输出请求的JSON。"""


def validate_draft(obj, budget, config):
    lines=obj.get('lines',[])
    if not isinstance(lines,list) or not 5<=len(lines)<=10:raise ValueError('clip needs 5..10 beats')
    if [l.get('id') for l in lines]!=[f'L{i+1:03d}' for i in range(len(lines))]:raise ValueError('invalid clip IDs')
    if any(not isinstance(l.get('text'),str) or not l['text'].strip() for l in lines):raise ValueError('invalid text')
    chars=sum(len(l['text']) for l in lines)
    problems=issues_for(lines,config)
    if not budget['min_chars']<=chars<=budget['max_chars']:problems.append({'code':'duration','chars':chars})
    quotes=obj.get('quotables',[])
    if not isinstance(quotes,list) or not 1<=len(quotes)<=2:problems.append({'code':'quotable_count'})
    else:
        known={l['id']:l['text'] for l in lines}
        for q in quotes:
            if not isinstance(q,dict) or not isinstance(q.get('quote'),str) or not 4<=len(q['quote'])<=80 or q.get('line_id') not in known or q['quote'] not in known[q['line_id']]:problems.append({'code':'invented_quotable'})
    return problems


def validate_review(review, obj):
    if review.get('verdict') not in ('pass','revise'):raise ValueError('invalid review verdict')
    issues=review.get('issues');known={l['id']:l['text'] for l in obj['lines']}
    if not isinstance(issues,list):raise ValueError('missing review issues')
    for issue in issues:
        if issue.get('id') not in known or not issue.get('quote') or issue['quote'] not in known[issue['id']]:raise ValueError('invented review evidence')
    if (review['verdict']=='pass')!= (not issues):raise ValueError('contradictory review verdict')
    return issues


class ClipGenerationError(ValueError):
    """Raised when bounded, targeted repair rounds cannot clear every quality gate.

    Carries the full diagnostic trace (every round's before/after lines, the
    specific flagged problems/issues, and the reviews that rejected them) so a
    caller can persist it and act on *why* generation failed instead of just
    the fact that it did. The quality gates themselves are never relaxed to
    make this pass — see validate_draft/validate_review/issues_for.
    """

    def __init__(self, message, diagnostic):
        super().__init__(message)
        self.diagnostic = diagnostic


def _round_targets(problems, semantic):
    """Which line ids a repair round may touch, and whether it needs full latitude.

    Per-line problems/issues (regex style guards, semantic review issues, an
    invented quotable's source line) name a specific line id — repair for
    those is a scoped patch. Whole-draft problems (duration, quotable_count)
    have no single line at fault, so the round gets latitude over every line
    to adjust length/quotable selection; scoped rounds still get every line's
    *content* verbatim-preserved outside their targets (enforced by the
    caller, not just asked for in the prompt).
    """
    ids=set();wide=False
    for p in problems:
        pid=p.get('id')
        if pid:ids.add(pid)
        elif p.get('code')=='invented_quotable':wide=True
        else:wide=True  # duration, quotable_count: no single line at fault
    for s in semantic:
        sid=s.get('id')
        if sid:ids.add(sid)
        else:wide=True
    return ids,wide


def generate_clip(owner,name,persona,background,topic,language,config):
    start=time.perf_counter();rate=float(config.get('speech_rate',1))
    if not .7<=rate<=1.3:raise ValueError('speech_rate must be .7..1.3')
    target=float(config.get('target_seconds',90))
    if not 60<=target<=120:raise ValueError('shareable clip target must be 60..120 seconds')
    # Conservative planning band; only rendered audio can establish actual duration.
    cps=float(config.get('measured_chars_per_second',4.4*rate))
    if not 1<=cps<=20:raise ValueError('invalid speech calibration')
    max_rounds=int(config.get('max_repair_rounds',3))
    if not 1<=max_rounds<=6:raise ValueError('max_repair_rounds must be 1..6')
    call_budget=int(config.get('call_budget',4+max_rounds*3+2))
    budget={'target_seconds':target,'min_chars':round(60*cps),'max_chars':round(min(120,target+25)*cps),'estimate_scope':'planning only; final WAV must be 60..120 seconds'}
    trace={'version':'legacy-v4-shareable-v2','status':'generating','budget':budget,'calls':0,'max_repair_rounds':max_rounds,'raw_outputs':[]}
    owner.last_trace=trace
    def call(prompt, review_mode=False, repair_mode=False):
        if trace['calls']>=call_budget:raise ValueError('clip call budget exhausted')
        trace['calls']+=1
        call_start=time.perf_counter()
        raw=owner.llm.call(prompt,system=("你是证据审查器。对照DATA.input的人设、background和topic审查DATA.draft；草稿否定用户给定的已发生事件、替换其核心对象或虚构已收到的观众互动，必须引用原句并判revise，不能作为幽默豁免。不创作、不润色、不推测不存在的原作规则。id必须复制已有L001格式，quote必须逐字复制对应行中的连续子串，不加行号、引号或省略。时长由程序检查，禁止为时长创造虚拟行。人物正常提问、回顾时态、普通动作不自动构成问题。只判断叙述事实之间真实无法共存的矛盾。角色对话中的嘴硬、撒谎、误会、反话不是事实约束；例如他说顺路实际绕远正是笑点，绝不能当逻辑冲突。叙述者从对方行为推断动机是允许的，无需对方亲口承认。只判断明确事实冲突或听不懂的因果。资料没写某个普通细节不等于冲突；比喻不是物理事实，正常目测/触感、后来发生的事无需逐步铺垫。禁止把个人文风偏好当成错误，偏好写在preferences。每个reason最多60字。最多3个问题。返回指定JSON。" if review_mode else "你是口语脚本修复编辑。必须真正修改被指出的问题；禁止复述原稿或解释修改。按用户要求返回正文JSON和引用句。以DATA.input给定事实为准；草稿中与其冲突的情节必须改掉，不能保留为反转。事实和人物关系保留，删除花哨动作特写及不是X是Y句式，直接说事件。修复时长靠新增有因果的对话，不能重复和升华。" if repair_mode else SYSTEM),max_tokens=3000);trace['raw_outputs'].append(raw)
        trace.setdefault('call_details',[]).append({'review_mode':review_mode,'repair_mode':repair_mode,'seconds':round(time.perf_counter()-call_start,3),'input_sha256':hashlib.sha256(prompt.encode()).hexdigest()})
        return parse_object(raw)
    data={'name':name,'persona':persona,'background':background,'topic':topic,'language':language,'speech_tics':config.get('speech_tics',[]),'voice_design':config.get('voice_design',VOICE_DESIGNS.get(name,{})),'budget':budget,'candidate_count':config.get('candidate_count',2)}

    def review_prompt_for(obj):
        return '''作为挑剔的口语编辑审核实际台词，不能因元数据写了好笑就通过。检查：人设/关系/文化冲突、动作旁白、重复否定句式、无来由口癖、事件因果、金句是否硬凑、故事是否只是流水账、重复/抽象凑篇幅。相容虚构小事允许，不要求每个细节都有原作出处；不得把虚构当原作。先检查台词中是否真的实现预期变化和结果。每个问题必须引用对应行的精确原文。
返回 {verdict:"pass或revise",story_summary,share_reason,issues:[{id,quote,reason}]}。没有明确问题才pass。不要输出改稿。
DATA:\n'''+json.dumps({'input':data,'plan':trace.get('plan'),'draft':obj},ensure_ascii=False)

    def run_review(obj):
        prompt=review_prompt_for(obj)
        review=call(prompt, review_mode=True)
        try:
            return review,validate_review(review,obj)
        except ValueError:
            trace.setdefault('invalid_reviews',[]).append(review)
            review=call(prompt+'\n上次输出无效。只返回原文中可定位的问题，id只能L001等，quote不能添加行号。不评价字数时长。禁止改写引用。',review_mode=True)
            return review,validate_review(review,obj)

    try:
        plan=call('''设计一个值得分享的角色杂谈clip。按DATA.candidate_count生成不同事件候选，选择更有看点的。事件必须有人想得到什么、有人说了什么、实际做了什么。必须是轻松好笑的事件：具体对话中的可理解误会或嘴硬被揭穿，不做温情成长故事，禁止把物件修好当情感升华，禁止使用赋权/关系升维/静默成长这类解释。只安排两三个角色，日常小事无需陌生人和上一代往事。不能把人物爱好机械改成物件故障；避免饭盒装不下/换盒子这类无关系变化事件。用户指定事件必须保留，可扩展相容普通经历，不编造原作正史。不要预设观众在场发生过互动。
每个候选含id、premise、expectation、surprise、resolution、character_choice、share_reason。选择要解释为什么观众会继续听；落差必须能从行动和对话中理解。幽默不是强行比喻、网络术语或羞辱别人。
返回 {candidates:[...],selected_id,selection_reason,voice_design:{provenance:"中文改编设计，非原作口癖",habits:[],optional_phrases:[],frequency_limit:"全篇最多两次自然使用"}}。已有voice_design优先，没有就按人格设计不同的表达习惯。不要写正文。
DATA:\n'''+json.dumps(data,ensure_ascii=False))
        candidates=plan.get('candidates',[])
        if not 1<=len(candidates)<=3 or len({c.get('id') for c in candidates})!=len(candidates):raise ValueError('invalid candidate set')
        selected=next((c for c in candidates if c.get('id')==plan.get('selected_id')),None)
        if not selected or not all(selected.get(k) for k in ('premise','expectation','surprise','resolution','character_choice','share_reason')):raise ValueError('missing humor event')
        if data['voice_design']:
            plan['voice_design']=data['voice_design']
        if not isinstance(plan.get('voice_design'),dict) or not plan['voice_design'].get('provenance'):
            raise ValueError('missing voice design provenance')
        trace['plan']=plan
        prompt='''根据选中的事件写完整60–120秒中文口头clip。必须写7段，每段约50–65个汉字，总计约350–450字（以预算为准）。不能交一个150字的大纲。用实质对话和递进支撑，禁止重复铺垫、动作旁白、抽象感悟凑字数。开头有听下去的问题，中间至少两次新信息推进，结尾兑现开头。1–2句能单独引用的有趣话必须长在故事里，贴这个人物，不能只是给物件加人格。口吻是对直播观众聊趣事，可以有接话、复述对方的原话和轻轻吐槽；每个对话来回有实际信息，不要像默片只描写人的手、表情和动作。不要每段重新开场，不要介绍自己人设。
返回 {lines:[{id:"L001",text:"可直接说的台词"}...5至10段],quotables:[{line_id,quote:"对应正文精确原文",why_shareable}]}。
DATA:\n'''+json.dumps({'input':data,'plan':plan,'selected':selected},ensure_ascii=False)
        obj=call(prompt);problems=validate_draft(obj,budget,config);trace['initial_draft']=obj;trace['initial_issues']=problems
        review,semantic=run_review(obj)
        trace['review']=review

        # Bounded, targeted rewrite: each round only touches the lines a
        # structural or semantic problem actually named (verbatim-preserving
        # every other line by construction, not just by asking nicely), gets
        # re-validated against the same unrelaxed gates, and the full
        # before/after + reasons land in trace['repair_rounds'] so a failure
        # leaves a real diagnosis on disk instead of a bare error string.
        repair_rounds=[]
        round_num=0
        while (problems or semantic) and round_num<max_rounds:
            round_num+=1
            target_ids,wide=_round_targets(problems,semantic)
            before_lines=[dict(l) for l in obj['lines']]
            before_quotables=list(obj.get('quotables',[]))
            problems_before,semantic_before=problems,semantic
            scope_note=('本轮可调整任意行以满足时长/金句要求，但未受影响的行请保持原意不重写。'
                         if wide else
                         f'本轮只能重写以下行：{sorted(target_ids)}；其余行必须逐字保留，程序会强制忽略越界修改。')
            repair_payload={'input':data,'draft':obj,'structural_problems':problems,'semantic_issues':semantic,
                             'targets':sorted(target_ids) if not wide else 'all','round':round_num}
            round_record={'round':round_num,'targets':sorted(target_ids) if not wide else 'all','wide':wide,
                          'before_lines':before_lines,'before_quotables':before_quotables,
                          'structural_problems_before':problems_before,'semantic_issues_before':semantic_before}
            try:
                repaired=call(
                    f'第{round_num}/{max_rounds}轮定点修复，不是重写全篇。{scope_note}'
                    '输出{lines:[{id,text}](覆盖草稿全部行id，未改的行原样返回原文),quotables:[{line_id,quote,why_shareable}]}。'
                    '每个列出的问题都要真正解决，尤其不是X是Y必须改成直接表达；不能编造DATA.input之外的既成事实，也不能推翻本次已给的事件前提。'
                    '全文时长仍需在budget内。DATA:\n'+json.dumps(repair_payload,ensure_ascii=False),
                    repair_mode=True,
                )
                repaired_lines=repaired.get('lines') if isinstance(repaired,dict) else None
                if not isinstance(repaired_lines,list):raise ValueError('invalid repair output: missing lines')
                original_by_id={l['id']:l['text'] for l in before_lines}
                if not wide:
                    # Programmatic backstop: a scoped round may not add/drop/reorder
                    # line ids, and any edit outside its targets is ignored outright
                    # regardless of what the model returned.
                    if {l.get('id') for l in repaired_lines if isinstance(l,dict)}!=set(original_by_id):
                        raise ValueError('invalid repair output: line id set changed in a scoped round')
                    merged=[]
                    for line in repaired_lines:
                        lid=line.get('id')
                        merged.append(line if lid in target_ids else {'id':lid,'text':original_by_id[lid]})
                    repaired={**repaired,'lines':merged}
                candidate_obj=repaired
                candidate_problems=validate_draft(candidate_obj,budget,config)
                candidate_review,candidate_semantic=run_review(candidate_obj)
            except ValueError as round_exc:
                round_record.update(status='invalid_output',error=str(round_exc),
                                     after_lines=before_lines,after_quotables=before_quotables,
                                     structural_problems_after=problems_before,semantic_issues_after=semantic_before)
                repair_rounds.append(round_record)
                if str(round_exc)=='clip call budget exhausted':break
                continue  # obj/problems/semantic unchanged; next round retries from the same base
            obj,problems,semantic,review=candidate_obj,candidate_problems,candidate_semantic,candidate_review
            round_record.update(status='applied',after_lines=[dict(l) for l in obj['lines']],
                                 after_quotables=list(obj.get('quotables',[])),
                                 structural_problems_after=problems,semantic_issues_after=semantic,review=review)
            repair_rounds.append(round_record)
        trace['repair_rounds']=repair_rounds
        trace['review']=review

        if problems or semantic:
            trace.update(status='needs_review',final_structural_problems=problems,final_semantic_issues=semantic,
                          rounds_attempted=round_num)
            reason='clip constraints unresolved' if problems else 'semantic repair unresolved'
            raise ClipGenerationError(
                f'{reason} after {round_num} bounded targeted rewrite round(s)',
                diagnostic={
                    'reason':reason,'rounds_attempted':round_num,'max_repair_rounds':max_rounds,
                    'final_structural_problems':problems,'final_semantic_issues':semantic,
                    'repair_rounds':repair_rounds,'initial_draft':trace.get('initial_draft'),
                    'initial_issues':trace.get('initial_issues'),
                    'input':data,'plan':plan,
                },
            )
        trace.update(status='completed',final_draft=obj,chars=sum(len(l['text']) for l in obj['lines']),
                      semantic_status='reviewed',audio_status='not_measured',rounds_attempted=round_num)
        return [ScriptLineV4(id=l['id'],text=l['text'],stage='Hook' if i==0 else 'Resolution' if i==len(obj['lines'])-1 else 'Build-up',interruption_cost=.4,key_info=[]) for i,l in enumerate(obj['lines'])]
    except Exception as exc:
        trace.update(status='needs_review',error_type=type(exc).__name__);raise
    finally:trace['generation_seconds']=round(time.perf_counter()-start,3)
