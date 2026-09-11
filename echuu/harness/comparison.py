"""Self-contained comparison inspector and human preference export (no invented votes)."""
import json,html
from pathlib import Path
from .style import contrast_patterns
from .repair import line_items

def build_report(root):
    root=Path(root);rows=[]
    for p in sorted(root.glob('*/run.json')):
        m=json.loads(p.read_text());events=[json.loads(x) for x in (p.parent/'events.jsonl').read_text().splitlines()]
        text=(p.parent/'text.txt').read_text() if (p.parent/'text.txt').exists() else ''
        drafts=[r for r in events if r['node']=='unit_draft']
        if not text and drafts:text=drafts[-1]['output']['text']
        usage=m.get('token_usage') or []
        metrics={'calls':m['llm_call_count'],'seconds':m['duration_seconds'],'tokens':sum((u or {}).get('total_tokens',0) for u in usage) if usage else None,'chars':sum(len(x) for x in text.splitlines()),'contrast_count':len(contrast_patterns(line_items(text))),'repair_rejections':sum(r['node'] in ('repair_blocked','outline_rollback') for r in events)}
        audio=json.loads((p.parent/'listening.json').read_text()) if (p.parent/'listening.json').exists() else None
        supplement=[]
        for version in ('delivery-v2','repair-v8'):
            sp=p.parent/version/'run.json'
            if sp.exists():
                sm=json.loads(sp.read_text());supplement.append({'version':version,'status':sm['status'],'path':p.parent.name+'/'+version,'text':(sp.parent/'text.txt').read_text() if (sp.parent/'text.txt').exists() else '', 'audio':(sp.parent/'listening.wav').exists()})
        for sp in (p.parent/'humor-v2').glob('*/run.json'):
            sm=json.loads(sp.read_text());supplement.append({'version':'humor-v2','status':sm['status'],'path':str(sp.parent.relative_to(root)),'text':(sp.parent/'text.txt').read_text() if (sp.parent/'text.txt').exists() else '', 'audio':False})
        rows.append({'supplement':supplement,'audio':audio,'run_id':m['run_id'],'character':m['character']['name'],'character_id':m['character']['id'],'variant':m['variant'],'status':m['status'],'preflight':m.get('preflight',{}),'metrics':metrics,'text':text,'directory':p.parent.name,'events':events,'error':m.get('blocked_reason',m.get('error_type'))})
    protocol=json.loads((root/'experiment.json').read_text()) if (root/'experiment.json').exists() else {}
    observed={(r['character_id'],r['variant']) for r in rows}
    missing=[{'character':f['id'],'variant':v,'reason':'not run; paired humor requires an approved pattern source'} for f in protocol.get('fixtures',[]) for v in protocol.get('arms',[]) if (f['id'],v) not in observed]
    arms={}
    for r in rows:
        a=arms.setdefault(r['variant'],{'runs':0,'accepted':0,'length_matched':0,'calls':0,'seconds':0,'tokens':0})
        a['runs']+=1;a['accepted']+=r['status']=='completed';a['length_matched']+=bool(r['preflight'].get('length_matched'));a['calls']+=r['metrics']['calls'];a['seconds']+=r['metrics']['seconds'];a['tokens']+=r['metrics']['tokens'] or 0
    summary={'missing_arms':missing,'arms':arms,'runs':[{k:v for k,v in r.items() if k not in ('events','text')} for r in rows],'caveat':'Single fixed seed per character. Gate acceptance is not human enjoyment. Same-model critics are not independent human evidence. Length mismatch reported, not silently discarded.'}
    (root/'comparison.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    payload=json.dumps(rows,ensure_ascii=False).replace('<','\\u003c')
    pairwise=json.loads((root/'pairwise.json').read_text()) if (root/'pairwise.json').exists() else []
    source='''<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Echuu Harness 对比实验</title><style>
body{font:16px/1.7 system-ui;background:#101720;color:#e7edf4;max-width:1400px;margin:36px auto;padding:0 24px}h1{font-size:30px}h2{font-size:21px;color:#91dbd6}table{border-collapse:collapse;width:100%}td,th{padding:10px;text-align:left;border-bottom:1px solid #334253}button,select{font:inherit;background:#25384b;color:white;border:1px solid #54708a;border-radius:6px;padding:6px 12px;margin:4px}article{border:1px solid #334253;padding:20px;margin:18px 0;border-radius:10px;background:#18232f}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.6 monospace;max-height:550px;overflow:auto}.story{white-space:pre-wrap}.muted{color:#a6b4c4}.pair{display:grid;grid-template-columns:1fr 1fr;gap:24px}.bad{color:#ffb291}a{color:#91dbd6}@media(max-width:700px){.pair{grid-template-columns:1fr}}</style>
<h1>Echuu Harness · 内容生产对比</h1><p>固定 5 个人设与场景；legacy_v4 默认保留。Humor 从同一份通过审核的 Pattern 故事分支，可回退到原稿。</p><p class="muted">单 seed 探索实验。通过门禁不等于好笑或好听；Judge 使用同一个模型的不同调用。公开数据尚未导入，Pattern 为项目原创编写。长度不匹配会显式显示。</p><div id="summary"></div><h2>匿名双稿比较</h2><p class="muted">先读 A/B 再选择。人类选择仅保存到当前浏览器，可导出 JSON；不会把模型评价计为你的偏好。</p><select id="who"></select><select id="left"></select><select id="right"></select><button id="show">生成 A/B</button><div id="pair"></div><button id="export">导出我的偏好</button><h2>模型盲比（非人类反馈）</h2><div id="modelpairs"></div><h2>运行与完整链路</h2><div id="runs"></div><script>
const rows=PAYLOAD; const pairs=PAIRWISE; const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const variants=[...new Set(rows.map(r=>r.variant))];const people=[...new Map(rows.map(r=>[r.character_id,r.character])).entries()];
for(const [id,name] of people)who.add(new Option(name,id));for(const v of variants){left.add(new Option(v,v));right.add(new Option(v,v))}right.value='harness_pattern';left.value='legacy_v4';
document.getElementById('summary').innerHTML='<table><tr><th>角色 / 分组</th><th>结果</th><th>字数</th><th>调用 / 秒 / tokens</th><th>不是…是…</th></tr>'+rows.map(r=>`<tr><td>${esc(r.character)} / ${esc(r.variant)}</td><td>${esc(r.status)}</td><td>${r.metrics.chars}${r.preflight.length_matched?'':' ⚠'}</td><td>${r.metrics.calls} / ${r.metrics.seconds} / ${r.metrics.tokens??'未知'}</td><td>${r.metrics.contrast_count}</td></tr>`).join('')+'</table>';
modelpairs.innerHTML=pairs.map(p=>`<details><summary>${esc(p.character)} · ${esc(p.left_variant)} / ${esc(p.right_variant)}：${esc(p.winner)}</summary><pre>${esc(JSON.stringify(p,null,2))}</pre></details>`).join('');
runs.innerHTML=rows.map(r=>`<article><h2>${esc(r.character)} · ${esc(r.variant)}</h2><p>${esc(r.status)} · ${esc(r.error??'')} <a href="${encodeURIComponent(r.directory)}/run.json">manifest</a> · <a href="${encodeURIComponent(r.directory)}/events.jsonl">全部事件</a></p><div>${r.audio?`<audio controls preload="none" src="${encodeURIComponent(r.directory)}/listening.wav"></audio><p>${esc(r.audio.voice)} · ${Math.round(r.audio.seconds)} 秒</p>`:''}</div><div class="story">${esc(r.text||'没有可展示的完整稿件')}</div>${r.supplement.map(s=>`<details><summary>实验后补修 · ${esc(s.version)} · ${esc(s.status)}（不覆盖原成绩）</summary><a href="${s.path}/run.json">补修记录</a><div class="story">${esc(s.text)}</div>${s.audio?`<audio controls preload="none" src="${s.path}/listening.wav"></audio>`:''}</details>`).join('')}<details><summary>节点、候选、门禁、回退与来源（${r.events.length}）</summary>${r.events.map(e=>`<details><summary>${esc(e.node)} ${esc(e.node_name??'')} · ${esc(e.status)}</summary><pre>${esc(JSON.stringify(e,null,2))}</pre></details>`).join('')}</details></article>`).join('');
let selected=null;let prefs=JSON.parse(localStorage.getItem('echuu-harness-preferences-v1')||'[]');
show.onclick=()=>{let a=rows.find(r=>r.character_id===who.value&&r.variant===left.value),b=rows.find(r=>r.character_id===who.value&&r.variant===right.value);if(!a||!b||!a.text||!b.text||a.run_id===b.run_id){pair.textContent='请选择存在完整稿件的不同分组。';return}if(Math.random()<.5)[a,b]=[b,a];selected=[a,b];pair.innerHTML=`<div class="pair"><article><h2>A</h2><div class="story">${esc(a.text)}</div></article><article><h2>B</h2><div class="story">${esc(b.text)}</div></article></div><button data-v="A">A 更自然有趣</button><button data-v="B">B 更自然有趣</button><button data-v="tie">相当</button><button data-v="neither">都不好</button>`;pair.querySelectorAll('button').forEach(btn=>btn.onclick=()=>{prefs.push({version:1,source:'human_browser',timestamp:new Date().toISOString(),A:a.run_id,B:b.run_id,winner:btn.dataset.v});localStorage.setItem('echuu-harness-preferences-v1',JSON.stringify(prefs));btn.textContent='已保存';});};
document.getElementById('export').onclick=()=>{const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(prefs,null,2)],{type:'application/json'}));a.download='echuu-human-preferences.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)};
</script></html>'''.replace('PAYLOAD',payload).replace('PAIRWISE',json.dumps(pairwise,ensure_ascii=False).replace('<','\\u003c'))
    (root/'index.html').write_text(source)
    return summary

if __name__=='__main__':
    import sys
    print(json.dumps(build_report(Path(sys.argv[1]))['arms'],ensure_ascii=False,indent=2))
