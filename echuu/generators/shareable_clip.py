"""Character-led clip planning, grounded review and bounded repair."""
import json
import hashlib
import re
import time
import os
from .clip_schema import PLAN_SCHEMA, DRAFT_SCHEMA
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
保留用户人设与明确虚构设定；现实新闻以联网参考中有来源的最新事实为准，纠正背景里的过时信息，区分发布、预购和发售。其余已给定的故事事件，候选、正文、修复都必须保留；幽默只能来自之后的相容小插曲，不能把给定事件改成没发生、冒牌、做梦或夸口。不得虚构实际收到的弹幕或礼物。
现实产品的价格、型号、配色、参数、发布日期只能引用主题简报中有来源的明确事实；不得从训练记忆补充其他产品的价格变化、规格或销售情况。缺失事实就略去，用打工人的预算取舍制造笑点，不用编造产品消息推进剧情。
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


def generate_clip(owner,name,persona,background,topic,language,config):
    start=time.perf_counter();rate=float(config.get('speech_rate',1))
    if not .7<=rate<=1.3:raise ValueError('speech_rate must be .7..1.3')
    target=float(config.get('target_seconds',90))
    if not 60<=target<=120:raise ValueError('shareable clip target must be 60..120 seconds')
    # Conservative planning band; only rendered audio can establish actual duration.
    cps=float(config.get('measured_chars_per_second',4.4*rate))
    if not 1<=cps<=20:raise ValueError('invalid speech calibration')
    budget={'target_seconds':target,'min_chars':round(60*cps),'max_chars':round(min(120,target+25)*cps),'estimate_scope':'planning only; final WAV must be 60..120 seconds'}
    trace={'version':'legacy-v4-shareable-v1','status':'generating','budget':budget,'calls':0,'raw_outputs':[]}
    owner.last_trace=trace
    def call(prompt, review_mode=False, repair_mode=False):
        if trace['calls']>=6:raise ValueError('clip call budget exhausted')
        trace['calls']+=1
        call_start=time.perf_counter()
        caller = getattr(owner.llm, 'call_structured', None)
        options = {'response_schema': DRAFT_SCHEMA if 'plan' in trace else PLAN_SCHEMA} if callable(caller) else {}
        caller = caller if callable(caller) else owner.llm.call
        raw=caller(prompt,system=("你是证据审查器。对照DATA.input的人设、background和topic审查DATA.draft；草稿否定用户给定的已发生事件、替换其核心对象或虚构已收到的观众互动，必须引用原句并判revise，不能作为幽默豁免。不创作、不润色、不推测不存在的原作规则。id必须复制已有L001格式，quote必须逐字复制对应行中的连续子串，不加行号、引号或省略。时长由程序检查，禁止为时长创造虚拟行。人物正常提问、回顾时态、普通动作不自动构成问题。只判断叙述事实之间真实无法共存的矛盾。角色对话中的嘴硬、撒谎、误会、反话不是事实约束；例如他说顺路实际绕远正是笑点，绝不能当逻辑冲突。叙述者从对方行为推断动机是允许的，无需对方亲口承认。只判断明确事实冲突或听不懂的因果。资料没写某个普通细节不等于冲突；比喻不是物理事实，正常目测/触感、后来发生的事无需逐步铺垫。禁止把个人文风偏好当成错误，偏好写在preferences。每个reason最多60字。最多3个问题。返回指定JSON。" if review_mode else "你是口语脚本修复编辑。必须真正修改被指出的问题；禁止复述原稿或解释修改。按用户要求返回正文JSON和引用句。以DATA.input给定事实为准；草稿中与其冲突的情节必须改掉，不能保留为反转。事实和人物关系保留，删除花哨动作特写及不是X是Y句式，直接说事件。修复时长靠新增有因果的对话，不能重复和升华。" if repair_mode else SYSTEM),max_tokens=3000,**options);trace['raw_outputs'].append(raw)
        trace.setdefault('call_details',[]).append({'review_mode':review_mode,'seconds':round(time.perf_counter()-call_start,3),'input_sha256':hashlib.sha256(prompt.encode()).hexdigest()})
        return parse_object(raw)
    data={'name':name,'persona':persona,'background':background,'topic':topic,'language':language,'speech_tics':config.get('speech_tics',[]),'voice_design':config.get('voice_design',VOICE_DESIGNS.get(name,{})),'budget':budget,'candidate_count':config.get('candidate_count',2)}
    try:
        if os.getenv('ECHUU_FAST_CLIP') == '1':
            trace['plan'] = {'mode': 'single_pass'}
            obj=call('直接写本场完整中文直播剧本JSON，不另做候选规划。7段，共350到450字，段落id依次L001到L007。每段提供新信息，以用户人设和本次话题为准，不能编造已发生的观众互动。未确认新品只当传闻，不编造购买、预售或实测事实。返回 {"lines":[{"id":"L001","text":"台词"}],"quotables":[{"line_id":"L001","quote":"正文中逐字一致的短句","why_shareable":"原因"}]}。JSON内部的双引号必须转义。DATA:\n'+json.dumps(data,ensure_ascii=False))
            problems=validate_draft(obj,budget,config)
            trace['initial_issues']=problems
            # Fast live mode reports quality/duration advice without another model call.
            # validate_draft still raises on malformed lines, missing text or invalid IDs.
            trace['quality_warnings'] = problems
            trace.update(status='completed',final_draft=obj,chars=sum(len(l['text']) for l in obj['lines']),semantic_status='skipped',audio_status='not_measured')
            return [ScriptLineV4(id=l['id'],text=l['text'],stage='Hook' if i==0 else 'Resolution' if i==len(obj['lines'])-1 else 'Build-up',interruption_cost=.4,key_info=[]) for i,l in enumerate(obj['lines'])]
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
        # Semantic review is temporarily disabled for live recording. Keep the
        # deterministic draft constraints and one bounded repair for those only.
        if problems:
            obj=call('修复草稿的程序校验问题。输出{lines:[{id,text}],quotables:[{line_id,quote,why_shareable}]}。只修复issues列出的格式、字数、引用或表达问题，保留本次人设与事实。长度在budget内。DATA:\n'+json.dumps({'input':data,'draft':obj,'issues':problems},ensure_ascii=False),repair_mode=True)
            problems=validate_draft(obj,budget,config)
        if problems:raise ValueError('clip constraints unresolved: '+json.dumps(problems,ensure_ascii=False))
        trace.update(status='completed',final_draft=obj,chars=sum(len(l['text']) for l in obj['lines']),semantic_status='skipped',audio_status='not_measured')
        return [ScriptLineV4(id=l['id'],text=l['text'],stage='Hook' if i==0 else 'Resolution' if i==len(obj['lines'])-1 else 'Build-up',interruption_cost=.4,key_info=[]) for i,l in enumerate(obj['lines'])]
    except Exception as exc:
        trace.update(status='needs_review',error_type=type(exc).__name__);raise
    finally:trace['generation_seconds']=round(time.perf_counter()-start,3)
