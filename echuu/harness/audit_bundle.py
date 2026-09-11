"""Verify a complete experiment bundle, including post-experiment and runtime runs."""
import json,hashlib,wave
from pathlib import Path
from .store import digest

def audit(root):
    root=Path(root);errors=[];runs=0;artifacts=0;audio=[]
    for p in root.rglob('run.json'):
        m=json.loads(p.read_text());seen=[];runs+=1
        for line in (p.parent/'events.jsonl').read_text().splitlines():
            e=json.loads(line);aid=e['artifact_id'];artifacts+=1
            if digest({k:v for k,v in e.items() if k!='artifact_id'})!=aid:errors.append(str(p)+' hash')
            if json.loads((p.parent/'artifacts'/f'{aid}.json').read_text())!=e:errors.append(str(p)+' snapshot')
            if not set(e['parent_artifact_ids'])<=set(seen):errors.append(str(p)+' parents')
            seen.append(aid)
        if seen!=m['artifacts']:errors.append(str(p)+' index')
    for p in root.rglob('listening.json'):
        m=json.loads(p.read_text());file=p.parent/m['file']
        with wave.open(str(file),'rb') as w:
            frames=w.getnframes();rate=w.getframerate();pcm=w.readframes(frames)
            ok=bool(frames and any(pcm)) and abs(sum(x['seconds'] for x in m['timeline'])*rate-frames)<1
        sha=hashlib.sha256(file.read_bytes()).hexdigest()
        if m.get('audio_sha256') and m['audio_sha256']!=sha:ok=False
        audio.append({'directory':str(p.parent),'voice':m['voice'],'seconds':m['seconds'],'chunks':len(m['timeline']),'passed':ok,'sha256':sha})
    result={'runs_including_supplements_and_runtime':runs,'artifacts':artifacts,'artifact_errors':errors,'audio':audio,'scope':'Hash/parent/index integrity and nonzero WAV/frame coverage; not semantic correctness, ASR or human listening.'}
    (root/'final-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result

if __name__=='__main__':
    import sys
    r=audit(sys.argv[1]);print(json.dumps({'runs':r['runs_including_supplements_and_runtime'],'artifacts':r['artifacts'],'errors':r['artifact_errors'],'audio':len(r['audio']),'audio_passed':all(a['passed'] for a in r['audio'])}))
