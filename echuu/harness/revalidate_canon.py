"""Revalidate existing judge responses without regenerating or overwriting run artifacts."""
import json
from pathlib import Path
from .canon import parse_audit, VALIDATOR_VERSION


def validate(raw,text,fixture):
    try:
        return {'validator_version':VALIDATOR_VERSION,'status':'valid','evaluation':parse_audit(raw,text,fixture)}
    except ValueError as exc:
        return {'validator_version':VALIDATOR_VERSION,'status':'invalid','reason':str(exc)}


def main():
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('root',type=Path)
    args=parser.parse_args()
    for p in args.root.glob('*/run.json'):
        run=json.loads(p.read_text())
        if not run['character'].get('canon_card'):
            continue
        events=[json.loads(line) for line in (p.parent/'events.jsonl').read_text().splitlines()]
        index=next((i for i,e in enumerate(events) if e['node']=='canon_evaluation'),None)
        if index is None:
            continue
        response=next(e for e in reversed(events[:index]) if e['node']=='llm_response')
        result=validate(response['output']['text'],(p.parent/'text.txt').read_text(),run['character'])
        result['source_artifact_id']=response['artifact_id']
        with (p.parent/'canon-validation-v2.json').open('x') as f:
            json.dump(result,f,ensure_ascii=False,indent=2)
    path=args.root/'audit-calibration/events.jsonl'
    if path.exists():
        events=[json.loads(line) for line in path.read_text().splitlines()]
        results=[]
        for response in events:
            if response['node']!='audit_response':
                continue
            request=next(e for e in events if e['artifact_id']==response['parent_artifact_ids'][0])['output']
            data=json.loads(request['prompt'].split('DATA:\n',1)[1])
            fixture={'canon_card':data['card'],'evidence':data['evidence']}
            result=validate(response['output']['text'],request['text'],fixture)
            result.update(kind=request['label'],source_artifact_id=response['artifact_id'])
            result['matched']=result['status']=='valid' and (result['evaluation']['overall']=='fail')==(request['label']=='negative')
            results.append(result)
        with (args.root/'calibration-revalidated-v2.json').open('x') as f:
            json.dump({'scope':'same raw responses, schema validation corrected to accept known source IDs alongside fact IDs','results':results},f,ensure_ascii=False,indent=2)


if __name__=='__main__':
    main()
