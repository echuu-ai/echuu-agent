"""Render approved compiled stories through the real engine and persist playback events."""
import argparse,json,io,wave,hashlib
from pathlib import Path
from .runtime import to_state

def render(directory):
    from echuu.live.engine import EchuuLiveEngine
    directory=Path(directory);manifest=json.loads((directory/'run.json').read_text())
    if manifest['status']!='completed':raise ValueError('unapproved run cannot render')
    if (directory/'listening.json').exists():return
    records=[json.loads(x) for x in (directory/'events.jsonl').read_text().splitlines()]
    content=next(e['output'] for e in reversed(records) if e['node']=='compiled_content')
    fixture=content['fixture'];engine=EchuuLiveEngine();engine.state=to_state(content)
    engine._harness_content=content;engine._harness_directory=directory;engine._source_material=fixture
    engine.tts.tts.voice=fixture['voice']
    original_synthesize=engine.tts.synthesize;retries=[]
    def synthesize(text,*args,**kwargs):
        for attempt in range(2):
            try:data=original_synthesize(text,*args,**kwargs)
            except Exception:data=None
            if data:return data
            retries.append({'text':text,'attempt':attempt+1})
        return None
    engine.tts.synthesize=synthesize
    chunks=[];timeline=[];params=None;elapsed=0
    for event in engine.run(max_steps=100,play_audio=False):
        data=event.get('audio')
        if not data:raise ValueError('missing runtime audio')
        with wave.open(io.BytesIO(data),'rb') as wav:
            current=(wav.getnchannels(),wav.getsampwidth(),wav.getframerate())
            if params and params!=current:raise ValueError('audio format changed')
            params=current;frames=wav.readframes(wav.getnframes());seconds=wav.getnframes()/wav.getframerate()
            if not frames or not any(frames):raise ValueError('silent audio')
        chunks.append(frames);timeline.append({'text':event.get('speech',event.get('line',{}).get('text')),'start':elapsed,'seconds':seconds,'sha256':hashlib.sha256(data).hexdigest(),'line':event.get('line')});elapsed+=seconds
    with wave.open(str(directory/'listening.wav'),'wb') as wav:
        wav.setnchannels(params[0]);wav.setsampwidth(params[1]);wav.setframerate(params[2]);wav.writeframes(b''.join(chunks))
    (directory/'listening.json').write_text(json.dumps({'file':'listening.wav','audio_sha256':hashlib.sha256((directory/'listening.wav').read_bytes()).hexdigest(),'retries':retries,'voice':fixture['voice'],'seconds':elapsed,'timeline':timeline,'scope':'Real engine/TTS, waveform checked; no human listening score or ASR'},ensure_ascii=False,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('directory');args=p.parse_args();render(args.directory)
