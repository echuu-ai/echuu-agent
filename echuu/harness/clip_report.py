"""Read-only report of clip runs, including failed drafts and measured audio gates."""
import json,html,hashlib,wave,argparse
from pathlib import Path

def report(root):
 root=Path(root);cards=[];summary=[]
 for p in sorted(root.glob('*/run.json'),key=lambda p:(not (p.parent/'audio.json').exists(),p.parent.name!='yukito-focused-v2-direct-tts','focused' not in p.parent.name,p.parent.name)):
  m=json.loads(p.read_text());t=m['writer_trace'];d=p.parent
  audio=json.loads((d/'audio.json').read_text()) if (d/'audio.json').exists() else None
  audio_valid=False
  if audio:
   assert hashlib.sha256((d/'audio.wav').read_bytes()).hexdigest()==audio['sha256']
   with wave.open(str(d/'audio.wav'),'rb') as w:seconds=w.getnframes()/w.getframerate()
   assert abs(seconds-audio['seconds'])<.001
   audio_valid=60<=seconds<=120
  result={'id':d.name,'name':m['character']['name'],'text_status':m['status'],'generation_seconds':m['generation_seconds'],'calls':t['calls'],'speech_seconds':audio['seconds'] if audio else None,'tts_seconds':audio['synthesis_seconds'] if audio else None,'duration_gate':audio_valid,'shareability':'awaiting human preference'}
  summary.append(result)
  text=(d/'text.txt').read_text() if (d/'text.txt').exists() else m.get('error','failed')
  quotes=t.get('final_draft',{}).get('quotables',[])
  body=f'<article><h2>{html.escape(result["name"])} · {"给定事件种子" if "focused" in d.name else "自由选题对照"}</h2><p>生成 {result["generation_seconds"]} 秒 · {result["calls"]} 次调用 · TTS合成 {audio["synthesis_seconds"] if audio else "未生成"} 秒 · 实际音频 {round(audio["seconds"],1) if audio else "未生成"} 秒 · 60–120秒门槛 {"通过" if audio_valid else "未通过"}</p><pre>{html.escape(text)}</pre>'
  if audio:body+=f'<audio controls preload="none" src="{d.name}/audio.wav"></audio>'
  body+='<h3>可引用句（模型提议，等待你的评价）</h3><pre>'+html.escape(json.dumps(quotes,ensure_ascii=False,indent=2))+'</pre>'
  body+='<details><summary>候选、口癖设计和审核/修复记录</summary><pre>'+html.escape(json.dumps(t,ensure_ascii=False,indent=2))+'</pre></details></article>';cards.append(body if m['status']=='completed' else '<details><summary>未通过：'+html.escape(result['name']+' / '+d.name+' / '+m.get('error','failed'))+'</summary>'+body+'</details>')
 (root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
 (root/'index.html').write_text('''<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>角色 clip 实测</title><style>body{max-width:1060px;margin:40px auto;padding:20px;background:#111b25;color:#e8eef4;font:17px/1.85 system-ui}article{border:1px solid #456;padding:24px;margin:28px 0;border-radius:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}a{color:#86ded6}audio{width:100%}</style><h1>1–2 分钟角色 clip：事件、口癖与可引用句</h1><p><a href="../legacy-casual-v4-2026-09-06/">上一版短稿与音频对照</a></p><p>本页是新编情境，非原作剧情或声优复刻。给定事件种子组由人工提供事件方向、模型扩写和审核；自由选题组由模型选择事件，二者分开呈现，不能把定向样本通过称为自由选题成功。口癖为中文改编设计。音频长度由WAV帧数实测；生成与TTS用时分别记录。自动检查通过不代表已经有趣，仍需你的分享意愿评价。</p><p><a href="RESULTS.md">结果、修复与剩余问题</a> · <a href="summary.json">用时数据</a> · <a href="tests.log">回归测试</a></p>'''+''.join(cards)+'</html>')
 return summary
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('root');a=p.parse_args();print(json.dumps(report(a.root),ensure_ascii=False,indent=2))
