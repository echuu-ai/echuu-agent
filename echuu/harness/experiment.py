"""Fixed-fixture ablation; humor branches reuse the exact approved pattern story."""
import argparse,json,random
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
from dataclasses import replace
from .models import HarnessFlags
from .production import run_production
from .canon import generation_material
from .runner import TOPIC,EVIDENCE

def case(args):
    fixture,arm,root,seed=args
    from dotenv import load_dotenv
    load_dotenv()
    from echuu.live.llm_factory import create_llm_client
    llm=create_llm_client();llm.seed=seed
    flags=HarnessFlags(reference_mode='none' if arm=='none' else 'pattern',seed=seed)
    baseline=None
    if arm=='legacy':
        def baseline(metered):
            from echuu.generators.legacy_v4 import ScriptGeneratorV4_1
            random.seed(seed)
            topic,evidence,background=generation_material(fixture,TOPIC,EVIDENCE)
            generator=ScriptGeneratorV4_1(metered)
            lines=generator.generate(fixture['name'],fixture['persona'],background,topic,character_config={'min_units':5,'max_units':5})
            return '\n'.join(l.text for l in lines if l.text)
    path,manifest,_=run_production(fixture,llm,root,flags,baseline=baseline)
    results=[{'path':str(path),'manifest':manifest}]
    if arm=='pattern' and manifest['status']=='completed':
        hp,hm,_=run_production(fixture,llm,root,replace(flags,humor=True),source=path)
        results.append({'path':str(hp),'manifest':hm})
    return results

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--workers',type=int,default=3);p.add_argument('--seed',type=int,default=42);p.add_argument('--characters',nargs='*');a=p.parse_args()
    root=Path(a.output);root.mkdir(parents=True,exist_ok=False)
    here=Path(__file__).parent
    fixtures=json.loads((here/'fixtures.json').read_text());canon={f['id']:f for f in json.loads((here/'fixtures-creative.json').read_text())}
    fixtures=[canon.get(f['id'],f) for f in fixtures]
    fixtures=[dict(f,topic=f.get('topic',TOPIC),evidence=f.get('evidence',EVIDENCE)) for f in fixtures if not a.characters or f['id'] in a.characters]
    protocol={'title':'Echuu production harness ablation','seed':a.seed,'fixtures':fixtures,'arms':['legacy_v4','harness_none','harness_pattern','harness_humor'],'target_chars':[400,700],'design':'Same fixtures/model/seed. Humor paired on exact accepted pattern source. Legacy frozen generator without repair; other arms include layered gates/repair. No raw public corpus imported. One seed is exploratory, not significance evidence.'}
    (root/'experiment.json').write_text(json.dumps(protocol,ensure_ascii=False,indent=2))
    results=[]
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        jobs=[pool.submit(case,(f,arm,str(root),a.seed)) for f in fixtures for arm in ('legacy','none','pattern')]
        for future in as_completed(jobs):
            try:batch=future.result();results.extend(batch)
            except Exception as exc:print('WORKER FAILED',type(exc).__name__,flush=True);continue
            (root/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
            for r in batch:print('RESULT',r['manifest']['character']['id'],r['manifest']['variant'],r['manifest']['status'],flush=True)
    from .comparison import build_report
    build_report(root)

if __name__=='__main__':main()
