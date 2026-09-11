"""Frozen legacy vs revised 60/90-second spoken stories on the same retelling fixtures."""
import json,random,time,importlib.util,argparse
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
from .store import ArtifactStore
from .production import MeteredLLM
from .canon import generation_material
from .runner import TOPIC,EVIDENCE
from .style import contrast_patterns
from .repair import line_items

ROOT=Path('output/legacy-casual-2026-09-06')

def fixtures():
    here=Path(__file__).parent
    data=json.loads((here/'fixtures.json').read_text());canonical={f['id']:f for f in json.loads((here/'fixtures-creative.json').read_text())}
    out=[]
    for f in data:
        f=canonical.get(f['id'],f)
        if f['id'] in canonical:
            other='桃矢' if f['id']=='yukito' else '若叶'
            f['background']=f['background'].split('情境在')[0].split('情境发生')[0]
            f['background']='本次直播杂谈回顾一件已经发生的小事。原作人物边界以事实卡为准；不要把台词当成正在演示动作。'+ ('日本日常生活背景。' if f['id']=='yukito' else '凤学园日常生活背景。')
            f['canon_card']['scene']={'audience':'直播观众','setting':'回顾刚才和'+other+'发生的小事','speech':'自然口头讲述；'+other+'是故事中人物，不是当前听众'}
        else:f['background']+=' 现在在直播中回顾这件已发生的小事。'
        f['topic']='向直播观众用中文第一人称讲清楚下列一件小事：出了什么问题、自己怎么处理、最后怎样。自然杂谈，不是当场动作描写；按目标时长写。'
        f['evidence']=f.get('evidence',EVIDENCE)
        out.append(f)
    return out

def frozen_generator():
    def load(name,path):
        spec=importlib.util.spec_from_file_location(name,path);mod=importlib.util.module_from_spec(spec)
        import sys
        sys.modules[name]=mod;spec.loader.exec_module(mod);return mod
    sb=load('echuu.core._frozen_sb',ROOT/'structure_breaker_before.py')
    gen=load('echuu.generators._frozen_legacy',ROOT/'legacy_v4_before.py');gen.StructureBreaker=sb.StructureBreaker
    return gen.ScriptGeneratorV4

def run(args):
    fixture,variant,root=args
    global ROOT
    ROOT=Path(root)
    from dotenv import load_dotenv
    load_dotenv('.env')
    from echuu.live.llm_factory import create_llm_client
    from echuu.generators.legacy_v4 import ScriptGeneratorV4
    target=60 if variant=='casual_60' else 90 if variant=='casual_90' else 75
    rid=fixture['id']+'-'+variant;store=ArtifactStore(ROOT/rid,rid)
    llm=create_llm_client();llm.seed=42;metered=MeteredLLM(llm,store,2)
    generator=(frozen_generator() if variant=='legacy_before' else ScriptGeneratorV4)(metered)
    topic,_,background=generation_material(fixture,TOPIC,EVIDENCE)
    input_id=store.add('input',{'fixture':fixture,'target_seconds':target,'seed':42})
    config={'target_seconds':target,'speech_rate':fixture['speech_rate'],'min_units':4,'max_units':4}
    if variant=='legacy_before':config.update(min_units=5,max_units=5)
    config['cultural_context']='依据人物背景的日常生活；无须增加精确年级或学校经历。'
    started=time.perf_counter();manifest={'character':fixture,'variant':variant,'target_seconds':target,'status':'failed','model':llm.model,'seed':42};usage=[]
    orig=llm.client.chat.completions.create
    def measured(*a,**kw):
        r=orig(*a,**kw);usage.append(r.usage.model_dump() if r.usage else None);return r
    llm.client.chat.completions.create=measured
    try:
        random.seed(42);lines=generator.generate(fixture['name'],fixture['persona'],background,topic,character_config=config)
        text='\n'.join(l.text for l in lines);(store.directory/'text.txt').write_text(text)
        store.add('script',{'text':text,'lines':[vars(l) for l in lines],'writer_trace':getattr(generator,'last_trace',{})},parents=[input_id])
        manifest.update(status='completed',chars=sum(len(l.text) for l in lines),contrast_count=len(contrast_patterns(line_items(text))),cat_tail='喵' in text,writer_trace=getattr(generator,'last_trace',{}))
    except Exception as e:manifest.update(error_type=type(e).__name__,writer_trace=getattr(generator,'last_trace',{}))
    manifest.update(generation_seconds=round(time.perf_counter()-started,2),llm_calls=metered.calls,token_usage=usage)
    store.finish(manifest);return manifest

def report():
    import html
    rows=[]
    for p in ROOT.glob('*/run.json'):
        m=json.loads(p.read_text());text=(p.parent/'text.txt').read_text() if (p.parent/'text.txt').exists() else ''
        rows.append((p.parent.name,m,text))
    head='<!doctype html><meta charset="utf-8"><title>Legacy 杂谈与用时重测</title><style>body{font:16px/1.7 system-ui;background:#111b25;color:#e8eef4;max-width:1200px;margin:40px auto;padding:20px}td,th{border-bottom:1px solid #345;padding:10px;text-align:left}table{width:100%}article{border:1px solid #345;padding:20px;margin:24px 0}pre{white-space:pre-wrap}a{color:#87d7ce}</style><h1>Legacy：杂谈、人设语气与用时</h1><p>相同事件、相同直播回顾视角。冻结旧版75秒请求（旧版内部仍强制5段×80–120字），新版目标上限60/90秒，不要求凑满。生成用时是实际值；朗读用时只对已合成音频显示。词数与生成预算校验不等于语义审查通过。</p><table><tr><th>角色/组</th><th>状态</th><th>字数</th><th>生成秒/调用</th><th>不是…是…/喵</th><th>实际音频秒</th></tr>'
    body='';summary=[]
    for rid,m,text in rows:
        audio_path=ROOT/rid/'audio.json';audio=json.loads(audio_path.read_text()) if audio_path.exists() else None
        head+=f'<tr><td>{html.escape(m["character"]["name"])} / {m["variant"]}</td><td>{m["status"]}</td><td>{m.get("chars","—")}</td><td>{m["generation_seconds"]} / {m["llm_calls"]}</td><td>{m.get("contrast_count","—")} / {m.get("cat_tail","—")}</td><td>{round(audio["seconds"],1) if audio else "未合成"}</td></tr>'
        body+=f'<article><h2>{html.escape(m["character"]["name"])} · {m["variant"]}</h2><pre>{html.escape(text or str(m.get("error_type")))}</pre>'
        if audio:body+=f'<audio controls src="{rid}/audio.wav"></audio>'
        body+=f'<details><summary>说话方式、故事主线与用时</summary><pre>{html.escape(json.dumps(m.get("writer_trace",{}),ensure_ascii=False,indent=2))}</pre></details><a href="{rid}/events.jsonl">原始调用</a></article>'
        summary.append({k:v for k,v in m.items() if k not in ('writer_trace','character','token_usage')}|{'character':m['character']['name']})
    (ROOT/'index.html').write_text(head+'</table><p><a href="RESULTS.md">实验结论与剩余问题</a> · <a href="tests.log">测试记录</a></p>'+body)
    (ROOT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default=str(ROOT));parser.add_argument('--variants',nargs='+',default=['legacy_before','casual_60','casual_90']);args=parser.parse_args();ROOT=Path(args.output);ROOT.mkdir(parents=True,exist_ok=True)
    with ProcessPoolExecutor(max_workers=3) as pool:
        for f in as_completed([pool.submit(run,(fixture,v,str(ROOT))) for fixture in fixtures() for v in args.variants]):
            r=f.result();print(r['character']['id'],r['variant'],r['status'],r['generation_seconds'],flush=True);report()
