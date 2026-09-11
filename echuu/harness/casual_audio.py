"""Measure speech duration separately from generation latency and live pauses."""
import json,io,wave,time,hashlib,argparse
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed

def render(directory, whole_clip=False):
    from dotenv import load_dotenv
    load_dotenv('.env')
    from echuu.live.tts_client import TTSClient
    directory=Path(directory);m=json.loads((directory/'run.json').read_text());f=m['character']
    if m['status']!='completed':raise ValueError('generation did not complete')
    if (directory/'audio.json').exists():return directory.name
    tts=TTSClient();tts.tts.voice=f['voice'];tts.update_session(speech_rate=f['speech_rate'])
    frames=[];params=None;timeline=[];elapsed=0;begin=time.perf_counter()
    full_text=(directory/'text.txt').read_text()
    texts=[full_text.replace('\n',' ')] if whole_clip else full_text.splitlines()
    for i,line in enumerate(texts):
        data=None
        for attempt in range(2):
            try:data=tts.tts.synthesize(line) if whole_clip else tts.synthesize(line)
            except Exception:data=None
            if data:break
        if not data:raise RuntimeError('TTS unavailable for line '+str(i))
        with wave.open(io.BytesIO(data),'rb') as w:
            current=(w.getnchannels(),w.getsampwidth(),w.getframerate());count=w.getnframes();pcm=w.readframes(count)
            if not count or not any(pcm):raise ValueError('empty PCM')
            if params and current!=params:raise ValueError('inconsistent format')
            params=current;seconds=count/w.getframerate()
        (directory/f'line-{i+1}.wav').write_bytes(data);frames.append(pcm)
        timeline.append({'text':line,'start':elapsed,'seconds':seconds,'file':f'line-{i+1}.wav','sha256':hashlib.sha256(data).hexdigest()});elapsed+=seconds
    with wave.open(str(directory/'audio.wav'),'wb') as w:
        w.setnchannels(params[0]);w.setsampwidth(params[1]);w.setframerate(params[2]);w.writeframes(b''.join(frames))
    (directory/'audio.json').write_text(json.dumps({'mode':'whole_clip' if whole_clip else 'per_line','seconds':elapsed,'synthesis_seconds':round(time.perf_counter()-begin,2),'voice':f['voice'],'timeline':timeline,'sha256':hashlib.sha256((directory/'audio.wav').read_bytes()).hexdigest(),'scope':'Actual synthesized speech, excludes live pauses and interaction. Nonempty PCM checked; no ASR.'},ensure_ascii=False,indent=2))
    return directory.name

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root');a=p.parse_args();root=Path(a.root)
    dirs=list(root.glob('*casual_60'));dirs.extend([root/'yukito-casual_90',root/'yukito-legacy_before'])
    with ProcessPoolExecutor(max_workers=3) as pool:
        for f in as_completed([pool.submit(render,d) for d in dirs]):print('AUDIO',f.result(),flush=True)
