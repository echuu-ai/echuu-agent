"""Matched clip model comparison; unavailable providers remain unavailable, not losers."""
import argparse,json,time,hashlib,html
from functools import partial
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
from dotenv import load_dotenv
from echuu.harness.casual_experiment import fixtures
from echuu.generators.legacy_v4 import ScriptGeneratorV4
from echuu.live.llm_factory import create_llm_client
from echuu.harness.casual_audio import render

TOPICS={
'yukito':'桃矢声称只是顺路，却绕远确认妹妹平安；雪兔看穿后温和调侃，保留他的面子。不把桃矢写成笨蛋，不涉及医疗和食物。',
'utena':'若叶希望自己完成一件日常小事，欧蒂娜习惯性想帮忙；两人用轻松对话商量，欧蒂娜尊重她的选择。别用怕猫或笨拙丑化角色，不喊王子口号，不凭空添加现代手机录像。',
'lin_qiao':'朋友请帮忙取一个快递，到了发现是四个；林桥给箱子编号，让朋友来搬大的。笑点来自一个的计数标准及朋友嘴硬，不讲维修物件。',
'a_zhou':'约朋友观星，阿昼计算云量和时间，却忘记防蚊；朋友已经带了驱蚊用品。坦然认输，不编造天文知识，不做人生总结。',
'wu_tang':'朋友约散步，嘴上说随便走走，却对路线熟悉得像导游；追问后承认想找个安静地方练习向喜欢的人发出邀约。是朋友间帮忙排练，不跟踪第三人，不讨论食物。'}

class Routed:
 def __init__(self,writer,judge):self.writer=writer;self.judge=judge;self.calls=[]
 def call(self,prompt,system=None,max_tokens=3000):
  is_review=bool(system and system.startswith('你是证据审查器'))
  client=self.judge if is_review else self.writer;t=time.perf_counter()
  entry={'role':'judge' if is_review else 'writer','model':client.model,'input_sha256':hashlib.sha256(((system or '')+'\0'+prompt).encode()).hexdigest()}
  try:
   out=client.call(prompt,system=system,max_tokens=max_tokens);entry['status']='completed';return out
  except Exception as e:entry['status']='failed';entry['error_type']=type(e).__name__;raise
  finally:entry['seconds']=round(time.perf_counter()-t,3);self.calls.append(entry)

def run(root,model,f,thinking=None):
 d=Path(root)/(model+'--'+f['id']);d.mkdir(exist_ok=False)
 background=f['background']
 if 'canon_card' in f:
  card=dict(f['canon_card']);card['scene']={'audience':'直播观众','speech':'回顾新编经历，故事人物不是当前听众'}
  card['boundaries']=[dict(b,claim=b['claim'].replace('当前听者是桃矢；','不得对观众泄密；')) for b in card['boundaries'] if not b['id'].endswith('_fiction')]
  background+='\n'+json.dumps(card,ensure_ascii=False)
 topic='以此为事件方向，写可分享的60–120秒杂谈，1–2句贴角色的可引用话。这是创作种子，非原作剧情：'+TOPICS[f['id']]
 config={'output_format':'shareable_clip','target_seconds':90,'speech_rate':f['speech_rate'],'candidate_count':2}
 inp={'name':f['name'],'persona':f['persona'],'background':background,'topic':topic,'language':'zh','character_config':config}
 writer=create_llm_client(provider='claude' if model.startswith('claude') else 'qwen',model=model)
 judge=create_llm_client(provider='qwen',model='qwen3-max')
 for c in (writer,judge):
  c.client=c.client.with_options(timeout=90,max_retries=0)
  if hasattr(c,'seed'):c.seed=93
 if thinking is not None and not model.startswith('claude'):
  writer.client.chat.completions.create=partial(writer.client.chat.completions.create,extra_body={'enable_thinking':thinking})
 llm=Routed(writer,judge);gen=ScriptGeneratorV4(llm);t=time.perf_counter()
 m={'character':f,'model':model,'judge_model':'qwen3-max','enable_thinking':thinking,'request_timeout_seconds':90,'input':inp,'input_sha256':hashlib.sha256(json.dumps(inp,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),'status':'failed'}
 try:
  lines=gen.generate(**inp);(d/'text.txt').write_text('\n'.join(l.text for l in lines));m['status']='completed'
 except Exception as e:m['error_type']=type(e).__name__;m['error']=str(e)[:200]
 m.update(generation_seconds=round(time.perf_counter()-t,2),writer_trace=gen.last_trace,calls=llm.calls)
 (d/'run.json').write_text(json.dumps(m,ensure_ascii=False,indent=2));print(model,f['id'],m['status'],m['generation_seconds'],flush=True)
 if m['status']=='completed':
  try:render(d,whole_clip=True)
  except Exception as e:(d/'audio-error.txt').write_text(type(e).__name__)
 return m

def report(root):
 root=Path(root);rows=[];cards=[]
 for p in sorted(root.glob('*/run.json')):
  m=json.loads(p.read_text());d=p.parent;audio=json.loads((d/'audio.json').read_text()) if (d/'audio.json').exists() else None
  rows.append({'model':m['model'],'character':m['character']['name'],'enable_thinking':m.get('enable_thinking'),'failure_kind':('timeout' if 'timed out' in m.get('error','') else 'generation_or_validation') if m['status']=='failed' else None,'status':m['status'],'generation_seconds':m['generation_seconds'],'writer_seconds':sum(c['seconds'] for c in m['calls'] if c['role']=='writer'),'judge_seconds':sum(c['seconds'] for c in m['calls'] if c['role']=='judge'),'calls':len(m['calls']),'speech_seconds':audio['seconds'] if audio else None,'duration_pass':60<=audio['seconds']<=120 if audio else None,'input_sha256':m['input_sha256']})
  text=(d/'text.txt').read_text() if (d/'text.txt').exists() else m.get('error','failed')
  card=f'<article><h2>{html.escape(m["character"]["name"])} · {html.escape(m["model"])}</h2><p>{m["status"]} · 生成 {m["generation_seconds"]} 秒 · 朗读 {round(audio["seconds"],1) if audio else "未合成"} 秒</p><pre>{html.escape(text)}</pre>'
  if audio:card+=f'<audio controls preload="none" src="{d.name}/audio.wav"></audio>'
  cards.append(card+f'<details><summary>输入、初稿、审核和修复记录</summary><pre>{html.escape(json.dumps(m,ensure_ascii=False,indent=2))}</pre></details></article>')
 (root/'summary.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
 (root/'index.html').write_text('''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Clip 模型对照</title><style>body{font:17px/1.8 system-ui;max-width:1100px;margin:40px auto;padding:20px;background:#111b25;color:#eef4f8}article{border:1px solid #456;margin:25px 0;padding:22px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}audio{width:100%}a{color:#83ded5}</style><h1>同题 clip 模型对照</h1><p>Claude Fable 5.1：当前组织停用，未能实测。不能据此判断Claude内容质量。本页展示同题Qwen测试，各轮思考设置见原始记录。输入哈希一致，固定qwen3-max做审核，计入独立审核用时；不是无偏人类趣味评分。每人物每模型1次，失败保留，不补抽成功样本。</p><p><a href="RESULTS.md">结论与限制</a> · <a href="summary.json">原始统计</a></p>'''+''.join(cards))
 return rows

if __name__=='__main__':
 load_dotenv('.env');p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--models',nargs='+',default=['qwen3-max','qwen3.8-max']);p.add_argument('--thinking',choices=['default','on','off'],default='default');a=p.parse_args();root=Path(a.output);root.mkdir(exist_ok=False)
 with ThreadPoolExecutor(max_workers=3) as pool:
  futures=[pool.submit(run,root,model,f,{'default':None,'on':True,'off':False}[a.thinking]) for f in fixtures() for model in a.models]
  for future in as_completed(futures):future.result();report(root)
