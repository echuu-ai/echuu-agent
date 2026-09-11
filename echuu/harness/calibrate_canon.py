"""Synthetic positive/negative controls; these are NOT generated story samples."""
import json
from pathlib import Path
from time import perf_counter
from dotenv import load_dotenv
from .canon import audit_prompt, parse_audit, AUDIT_VERSION
from .store import ArtifactStore


def main():
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[2]/'.env')
    from echuu.live.llm_factory import create_llm_client
    llm=create_llm_client()
    fixtures=json.loads(Path(__file__).with_name('fixtures-canon.json').read_text())
    controls=[
        (fixtures[0],'negative','桃矢，我是女孩子，也和月一样能直接施展魔法。我现在告诉你，我和月亮签订了契约，可以决定何时变成月。',True),
        (fixtures[0],'positive','桃矢，咖喱已经装好了。我刚才盛得多了一点，盒盖合不上。你的这份用新便当盒，我的换到旧的大盒里。别担心，我没有勉强自己少吃。',False),
        (fixtures[1],'negative','若叶，我本来就是男生。现在我留着电影开场那样的短发。我记得童年见过安茜，早就知道王子就是凤晓生，我真正的愿望一直是救安茜。',True),
        (fixtures[1],'positive','若叶，最后一朵你想自己挂？好，给你。我扶住凳子，你慢慢来。别急，我等你下来再一起看。',False),
    ]
    store=ArtifactStore(args.output/'audit-calibration','canon-audit-controls')
    results=[]
    for fixture,kind,text,should_fail in controls:
        prompt=audit_prompt(fixture,text)
        request_id=store.add('synthetic_control_request',{'prompt':prompt,'label':kind,'text':text},model=llm.model,prompt_version=AUDIT_VERSION)
        start=perf_counter()
        try:
            raw=llm.call(prompt,max_tokens=4000)
            response_id=store.add('audit_response',{'text':raw},parents=[request_id],duration_ms=round((perf_counter()-start)*1000))
            result=parse_audit(raw,text,fixture)
            matched=(result['overall']=='fail')==should_fail
            store.add('control_result',result,parents=[response_id],status='accepted' if matched else 'degraded')
            results.append({'character':fixture['name'],'kind':kind,'expected_failure':should_fail,'matched':matched,'result':result})
        except Exception as exc:
            results.append({'character':fixture['name'],'kind':kind,'matched':False,'error_type':type(exc).__name__})
    report={'prompt_version':AUDIT_VERSION,'scope':'4 authored controls only; not an estimate of canon-judge accuracy','results':results}
    (args.output/'calibration.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':
    main()
