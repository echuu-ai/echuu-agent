"""Reproducible character clip generation and measured speech experiment."""
import json,time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
from echuu.harness.casual_experiment import fixtures
from echuu.generators.legacy_v4 import ScriptGeneratorV4
from echuu.live.llm_factory import create_llm_client
from echuu.harness.casual_audio import render
load_dotenv('.env')
ROOT=Path('output/shareable-clips-final-2026-09-06')
MODEL='qwen3-max'
def run(f):
 d=ROOT/f['id'];d.mkdir(exist_ok=False)
 # Character facts retained; prescribed trivial event evidence removed for topic selection.
 background=f['background']
 if 'canon_card' in f:
  card=dict(f['canon_card']);card['scene']={'audience':'直播观众','speech':'对观众回顾过去的小事，桃矢等只是故事中的人，不是当前听者'}
  card['boundaries']=[dict(b,claim=b['claim'].replace('当前听者是桃矢；','不得对直播观众公开秘密；')) for b in card['boundaries'] if not b['id'].endswith('_fiction')]
  background+='\n人物边界：'+json.dumps(card,ensure_ascii=False)
 topic='本次不讲食物、厨具或维修小物件，选朋友相处或生活误会。设计可分享的1–2分钟杂谈。讲述一件符合性格的虚构日常经历，重点在人物关系、误会和选择。不要讲便当盒，也不使用之前义卖贴错标签的事件。'
 llm=create_llm_client(model=MODEL);llm.seed=57;g=ScriptGeneratorV4(llm);start=time.perf_counter()
 m={'model':llm.model,'generation_background':background,'character':f,'status':'failed','variant':'shareable_clip','topic':topic}
 try:
  lines=g.generate(f['name'],f['persona'],background,topic,character_config={'output_format':'shareable_clip','target_seconds':90,'speech_rate':f['speech_rate']})
  (d/'text.txt').write_text('\n'.join(l.text for l in lines));m['status']='completed'
 except Exception as e:m['error']=str(e)
 m.update(writer_trace=g.last_trace,generation_seconds=round(time.perf_counter()-start,2));(d/'run.json').write_text(json.dumps(m,ensure_ascii=False,indent=2))
 print(f['id'],m['status'],m.get('error',''),flush=True)
 if m['status']=='completed':
  try:render(d,whole_clip=True);print('audio',f['id'],flush=True)
  except Exception as e:(d/'audio-error.txt').write_text(str(e))
if __name__=='__main__':
 import argparse
 parser=argparse.ArgumentParser();parser.add_argument('--output',default=str(ROOT));parser.add_argument('--model',default=MODEL);args=parser.parse_args();MODEL=args.model;ROOT=Path(args.output);ROOT.mkdir(exist_ok=False)
 with ThreadPoolExecutor(max_workers=3) as p:list(p.map(run,fixtures()))
 from echuu.harness.clip_report import report
 report(ROOT)
