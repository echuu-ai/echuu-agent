"""Join each character's first complete TTS line for a quick voice comparison."""
import json
from pathlib import Path
import wave


def build_preview(root, character_ids, variant='refactor_no_dossier', filename='five-voices-preview.wav'):
    root = Path(root)
    runs = [json.loads(p.read_text()) | {'directory':p.parent} for p in root.glob('*/run.json')]
    selected = [next(r for r in runs if r['character']['id']==character_id and r['variant']==variant and r.get('audio')) for character_id in character_ids]
    chunks, entries = [], []
    elapsed = 0.0
    params = None
    for run in selected:
        entry = run['audio']['timeline'][0]
        source = run['directory']/entry['file']
        with wave.open(str(source),'rb') as wav:
            current = (wav.getnchannels(),wav.getsampwidth(),wav.getframerate())
            if params and params!=current:
                raise ValueError('voice formats differ')
            params = current
            frames = wav.readframes(wav.getnframes())
            seconds = wav.getnframes()/wav.getframerate()
        entries.append({'name':run['character']['name'],'voice':run['audio']['voice'],'start_seconds':round(elapsed,2),
                        'duration_seconds':round(seconds,2),'source':str(source.relative_to(root)),'text':entry['text'],'audio_file':filename})
        chunks.extend([frames,bytes(round(.7*params[2])*params[0]*params[1])])
        elapsed += seconds+.7
    with wave.open(str(root/filename),'wb') as wav:
        wav.setnchannels(params[0]);wav.setsampwidth(params[1]);wav.setframerate(params[2]);wav.writeframes(b''.join(chunks))
    (root/'preview.json').write_text(json.dumps(entries,ensure_ascii=False,indent=2))
    return entries
