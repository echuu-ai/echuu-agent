"""Portable, escaped HTML inspector; works over file:// without a backend."""
import html
import json
from pathlib import Path


def canon_explanation(run,validation):
    if validation and validation.get('status')=='invalid':
        reason=validation.get('reason','')
        reasons={'invented canon quote':'Judge 提供的引文不是剧本中的连续原文，可能拼接了分散句子，或混入输入设定。',
                 'invented canon evidence':'Judge 引用了不存在的事实或来源编号。',
                 'invented character choice':'Judge 给出的人物行为引文无法在剧本中精确定位。'}
        return '审计响应校验未通过：'+reasons.get(reason,reason)+' 本次审计结论未被采用；这不等于剧本已被判为人设错误。'
    if run.get('repair'):
        return '本次使用自动修复中的分行审查与争议复核；旧版原作审计未单独运行。'
    if run.get('canon_judge_error'):
        return '原作审计没有得到可用结果（'+str(run['canon_judge_error'])+'）。请查看响应与校验详情，不能据此判断人物是否合格。'
    return '原作审计结果如下。' if run.get('canon_evaluation') else '本次未运行原作审计。'


def build_report(root):
    root = Path(root)
    cards = []
    summaries = []
    for path in sorted(root.glob('*/run.json')):
        run = json.loads(path.read_text())
        directory = path.parent
        prefix = html.escape(directory.name)
        text = (directory/'text.txt').read_text() if (directory/'text.txt').exists() else ''
        events = [json.loads(line) for line in (directory/'events.jsonl').read_text().splitlines()]
        details = ''.join('<details><summary>'+html.escape(e['node']+' · '+e['status'])+'</summary><pre>'+html.escape(json.dumps(e, ensure_ascii=False, indent=2))+'</pre></details>' for e in events)
        lineages = next((e['output'] for e in events if e['node']=='lineage_index'), [])
        lineage_html = ''.join('<details><summary>'+html.escape(str(row['line'])+'. '+row['text'])+'</summary><p>精确文本匹配来源（非语义归因）：</p>'+(' · '.join('<a href="'+prefix+'/artifacts/'+artifact_id+'.json">'+artifact_id[:12]+'</a>' for artifact_id in row['exact_match_response_ids']) or '<p>无精确匹配；请查看 compiled_show 与阶段 trace。</p>')+'</details>' for row in lineages)
        audio = f'<audio controls preload="none" src="{prefix}/audio.wav"></audio>' if run.get('audio') else '<p>音频未完成；已生成的分句音频可在 trace 中定位。</p>'
        evaluation = html.escape(json.dumps(run.get('evaluation', {'unavailable':run.get('judge_error','disabled')}), ensure_ascii=False, indent=2))
        validation_path = directory/'canon-validation-v2.json'
        validation = json.loads(validation_path.read_text()) if validation_path.exists() else None
        validation_html = html.escape(json.dumps(validation, ensure_ascii=False, indent=2))
        canon_note=html.escape(canon_explanation(run,validation))
        from .style import contrast_patterns
        from .repair import line_items
        patterns=contrast_patterns(line_items(text))
        style_note=('<p>句式提示：检测到 '+str(len(patterns))+' 处“不是…是…”结构，需结合上下文检查重复辩解；这不是人设判错。</p>') if len(patterns)>=3 else ''
        canon_html = html.escape(json.dumps(run.get('canon_evaluation', {'status':'not_available', 'error':run.get('canon_judge_error')}), ensure_ascii=False, indent=2))
        repair=run.get('repair')
        draft=(directory/'draft.txt').read_text() if (directory/'draft.txt').exists() else ''
        repair_html = '<p>自动修复：'+html.escape(repair['status'])+' · '+str(len(repair['rounds']))+' 轮</p><details><summary>修复前文本</summary><pre>'+html.escape(draft)+'</pre></details><details><summary>修复决策 / 允许保留的扩写</summary><pre>'+html.escape(json.dumps(repair,ensure_ascii=False,indent=2))+'</pre></details>' if repair else ''
        persona = run['character']
        summaries.append({'run_id':run['run_id'], 'character':persona['name'], 'variant':run['variant'], 'status':run['status'], 'chars':len(text), 'lines':len(text.splitlines()), 'voice':persona['voice'], 'audio_seconds':run.get('audio',{}).get('duration_seconds'), 'gates':run.get('evaluation',{}).get('gates'), 'judge_error':run.get('judge_error'), 'canon_evaluation':run.get('canon_evaluation'), 'cost_usd':run.get('cost_usd'),'repair':repair})
        cards.append(f'''<article><h2>{html.escape(persona['name'])} / {html.escape(run['variant'])}</h2>
<p>{html.escape(run['status'])} · {run['duration_seconds']}s · {run['llm_call_count']} calls · {html.escape(persona['voice'])}</p>
<p>{html.escape(persona['persona'])}</p><p>{html.escape(persona['background'])}</p>
{repair_html}{audio}{style_note}<details open><summary>完整剧本</summary><pre>{html.escape(text)}</pre></details>
<details><summary>逐句来源（点击台词展开）</summary>{lineage_html}</details>
<details><summary>自动评审（待人工校准）</summary><pre>{evaluation}</pre></details>
<details><summary>原作一致性审计</summary><p>{canon_note}</p><details><summary>原始状态与错误码</summary><pre>{canon_html}</pre></details></details>
<details><summary>原作审计引文/来源重新校验（同一模型响应）</summary><pre>{validation_html}</pre></details>
<details><summary>Run Overview</summary><pre>{html.escape(json.dumps(run, ensure_ascii=False, indent=2))}</pre></details>
<details><summary>Artifact Timeline / Lineage · {len(events)} artifacts</summary>{details}</details>
<p><a href="{prefix}/run.json">Run JSON</a> · <a href="{prefix}/events.jsonl">完整事件</a></p></article>''')
    document = '''<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Echuu Harness · 五角色实测</title>
<style>body{font:16px/1.6 system-ui;background:#10151f;color:#e6edf6;max-width:1600px;margin:auto;padding:32px}h1{font-size:32px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,560px),1fr));gap:24px}article{background:#1b2432;border:1px solid #36465d;border-radius:16px;padding:24px;min-width:0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.7 system-ui}summary{cursor:pointer;color:#8fdad3;padding:10px 0}audio{width:100%}a{color:#8fdad3}</style>
<h1>Echuu Harness · 五角色实测</h1><p>Phase 0 · 真实模型生成与 TTS。两位动漫角色使用独立合成音色演绎；事件为测试创作的非原作设定。自动评审仅供定位，不能替代人工偏好与听感评测。</p>
<p>每个人设使用同一事件素材和 seed。记录执行依赖与原文匹配，不虚构逐句语义来源。费用未知显示 null。当前测试不包含实时弹幕。</p><main>''' + ''.join(cards) + '</main></html>'
    experiment_path = root/'experiment.json'
    if experiment_path.exists():
        experiment = json.loads(experiment_path.read_text())
        document = document.replace('Echuu Harness · 五角色实测', html.escape(experiment['title']))
        if any(item.get('repair') for item in summaries):
            document = document.replace('Phase 0 · 真实模型生成与 TTS。','局部自动修复 · 相容扩写保留，修复后复审，未通过则暂缓 TTS。')
        document = document.replace('每个人设使用同一事件素材和 seed。', html.escape(experiment['comparison_note']))
    (root/'summary.json').write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding='utf-8')
    preview = root/'preview.json'
    if preview.exists():
        entries = json.loads(preview.read_text())
        preview_html = '<h2>'+str(len(entries))+' 种声线快速试听</h2><audio controls preload="none" src="'+html.escape(entries[0].get('audio_file','five-voices-preview.wav'))+'"></audio><p>'+html.escape(' / '.join(str(e['start_seconds'])+'s '+e['name']+' ('+e['voice']+')' for e in entries))+'</p>'
        document = document.replace('<main>', preview_html+'<main>')
    review = root/'review.md'
    if review.exists():
        document = document.replace('<main>', '<details open><summary>文本复核与实测结论</summary><pre>'+html.escape(review.read_text())+'</pre></details><main>')
    (root/'index.html').write_text(document, encoding='utf-8')
    return root/'index.html'
