"""Verify stored lineage, hashes and WAV coverage, without judging listening quality."""
import argparse
import hashlib
import json
from pathlib import Path
import wave
from .store import digest


def verify(root, expected=None):
    root = Path(root)
    rows = []
    for manifest_path in sorted(root.glob('*/run.json')):
        directory = manifest_path.parent
        manifest = json.loads(manifest_path.read_text())
        errors = []
        seen = []
        audio_frames = 0
        for line in (directory/'events.jsonl').read_text().splitlines():
            event = json.loads(line)
            artifact_id = event['artifact_id']
            if digest({k:v for k,v in event.items() if k!='artifact_id'}) != artifact_id:
                errors.append('event hash mismatch')
            if json.loads((directory/'artifacts'/f'{artifact_id}.json').read_text()) != event:
                errors.append('snapshot mismatch')
            if any(parent not in seen for parent in event['parent_artifact_ids']):
                errors.append('missing or out-of-order parent')
            seen.append(artifact_id)
            if event['node']=='tts_line':
                audio = directory/event['output']['file']
                if hashlib.sha256(audio.read_bytes()).hexdigest() != event['audio_sha256']:
                    errors.append('audio hash mismatch')
                with wave.open(str(audio),'rb') as wav:
                    frames = wav.getnframes()
                    pcm = wav.readframes(frames)
                    if not frames or not any(pcm):
                        errors.append('empty or silent audio')
                    audio_frames += frames
        if seen != manifest['artifacts']:
            errors.append('manifest index mismatch')
        if manifest.get('audio'):
            with wave.open(str(directory/manifest['audio']['file']),'rb') as wav:
                if wav.getnframes() != audio_frames:
                    errors.append('merged audio coverage mismatch')
        rows.append({'run_id':manifest['run_id'],'status':manifest['status'], 'artifacts':len(seen),
                     'audio':bool(manifest.get('audio')), 'errors':errors})
    integrity=bool(rows) and all(not r['errors'] for r in rows) and (expected is None or len(rows)==expected)
    report = {'runs':rows, 'expected_runs':expected, 'integrity_passed':integrity,
              'release_ready_count':sum(r['status']=='completed' for r in rows),
              'passed':bool(rows) and all(not r['errors'] and r['status']=='completed' for r in rows)
                       and (expected is None or len(rows)==expected),
              'scope':'Artifact integrity, parent ordering, nonzero PCM and merged frame coverage; not ASR or listening quality.'}
    (root/'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('--expected', type=int)
    args = parser.parse_args()
    report = verify(args.root, args.expected)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report['passed'] else 1)
