"""Append-only, content-addressed artifacts. Partial runs remain inspectable."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class ArtifactStore:
    def __init__(self, directory: Path, run_id: str):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        (self.directory / 'artifacts').mkdir()
        self.run_id = run_id
        self.ids = []

    def add(self, node, output, *, parents=(), inputs=None, status='accepted', **metadata):
        if any(parent not in self.ids for parent in parents):
            raise ValueError('unknown parent artifact')
        record = dict(run_id=self.run_id, node=node, node_version='1.0.0',
                      parent_artifact_ids=list(parents), input_hash=digest(inputs),
                      output=output, status=status, created_at=datetime.now(timezone.utc).isoformat(),
                      **metadata)
        artifact_id = digest(record)
        record['artifact_id'] = artifact_id
        with (self.directory / 'artifacts' / f'{artifact_id}.json').open('x') as f:
            f.write(canonical(record))
        with (self.directory / 'events.jsonl').open('a') as f:
            f.write(canonical(record) + '\n')
            f.flush()
        self.ids.append(artifact_id)
        return artifact_id

    def finish(self, manifest):
        with (self.directory / 'run.json').open('x') as f:
            f.write(json.dumps(dict(run_id=self.run_id, artifacts=self.ids, **manifest), ensure_ascii=False, indent=2))
