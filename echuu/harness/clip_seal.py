"""Seal completed clip experiments into the existing artifact store."""
import json,argparse
from pathlib import Path
from .store import ArtifactStore

def seal(root):
 for p in Path(root).glob('*/run.json'):
  d=p.parent
  if (d/'evidence').exists():continue
  m=json.loads(p.read_text());trace=m['writer_trace'];store=ArtifactStore(d/'evidence',d.name)
  parent=store.add('clip_input',{'character':m['character'],'topic':m['topic'],'model':m.get('model','qwen-plus'),'generation_background':m.get('generation_background'),'note':'For older experimental runs reconstruct normalized background using archived reproduction script.'})
  for i,raw in enumerate(trace.get('raw_outputs',[])):
   parent=store.add('model_response',{'index':i,'raw':raw},parents=[parent])
  parent=store.add('clip_result',{'status':m['status'],'trace':trace,'text':(d/'text.txt').read_text() if (d/'text.txt').exists() else None},parents=[parent],status='accepted' if m['status']=='completed' else 'blocked')
  if (d/'audio.json').exists():
   audio=json.loads((d/'audio.json').read_text())
   parent=store.add('measured_audio',audio,parents=[parent],status='accepted' if 60<=audio['seconds']<=120 else 'blocked')
  store.finish({'status':m['status'],'source':'clip experiment','final_artifact_id':parent})
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('root');seal(p.parse_args().root)
