"""Order-swapped same-model pairwise comparison; never substitutes for human votes."""
import json,argparse
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
from .store import ArtifactStore
from .production import MeteredLLM
from .nodes import NodeRunner,require
from .models import HarnessFlags

def compare(args):
    root,left,right=args
    from dotenv import load_dotenv
    load_dotenv()
    from echuu.live.llm_factory import create_llm_client
    a,b=Path(left),Path(right);ma=json.loads((a/'run.json').read_text());mb=json.loads((b/'run.json').read_text())
    key=ma['run_id']+'__'+mb['run_id'];dest=Path(root)/'pairwise'/key
    if (dest/'run.json').exists():return json.loads((dest/'run.json').read_text())
    store=ArtifactStore(dest,key);llm=create_llm_client();llm.seed=42
    nodes=NodeRunner(MeteredLLM(llm,store,4),store,HarnessFlags(max_calls=4))
    texts=[(a/'text.txt').read_text(),(b/'text.txt').read_text()];votes=[];status='completed'
    try:
        for reverse in (False,True):
            order=[1,0] if reverse else [0,1];versions={label:texts[i] for label,i in zip(('A','B'),order)}
            def validate(d):
                require(d.get('winner') in ('A','B','tie'),'bad winner')
                require(isinstance(d.get('reason'),str),'missing reason')
                require(isinstance(d.get('quotes'),list) and len(d['quotes'])>=2,'quote both texts')
                require({q.get('version') for q in d['quotes']}=={'A','B'},'both sides required')
                for q in d['quotes']:require(bool(q.get('text')) and q['text'] in versions.get(q.get('version'),''),'invented quote')
            d,_=nodes.call('blind_pairwise','''比较两篇角色口语故事。依次考虑人物具体选择、事件可理解性、自然语感、有趣程度和重复套路。字数长或门禁术语不加分。不知道分组，不猜版本。返回 {winner:A或B或tie,reason:具体比较,quotes:[{version:A或B,text:该版本精确连续原文}]}。每边至少一句证据。普通经历允许相容扩写。不要将温和故事强改成搞笑段子。''',{'fixture':ma['character'],**versions},[],validate,max_tokens=1400)
            votes.append({'order':order,'winner':d['winner'],'original_index':None if d['winner']=='tie' else order[0 if d['winner']=='A' else 1],'reason':d['reason'],'quotes':d['quotes']})
    except Exception as exc:status='failed';error=type(exc).__name__
    winners=[v['original_index'] for v in votes]
    final=winners[0] if len(winners)==2 and winners[0]==winners[1] and winners[0] is not None else None
    result={'status':status,'left':ma['run_id'],'right':mb['run_id'],'character':ma['character']['id'],'left_variant':ma['variant'],'right_variant':mb['variant'],'winner':('left' if final==0 else 'right') if final is not None else 'inconclusive','votes':votes,'scope':'same model, order-swapped calls; not human or independent-model evidence'}
    if status=='failed':result['error_type']=error
    store.finish(result);return result

def main():
    p=argparse.ArgumentParser();p.add_argument('root');p.add_argument('--workers',type=int,default=4);a=p.parse_args();root=Path(a.root)
    groups={}
    for f in root.glob('*/run.json'):
        m=json.loads(f.read_text())
        if (f.parent/'text.txt').exists():groups.setdefault(m['character']['id'],{})[m['variant']]=str(f.parent)
    pairs=[]
    for group in groups.values():
        for l,r in [('legacy_v4','harness_pattern'),('harness_none','harness_pattern'),('harness_pattern','harness_humor')]:
            if l in group and r in group:pairs.append((str(root),group[l],group[r]))
    results=[]
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        for future in as_completed([pool.submit(compare,x) for x in pairs]):
            results.append(future.result());(root/'pairwise.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print('PAIR',results[-1]['character'],results[-1]['left_variant'],results[-1]['right_variant'],results[-1]['winner'],flush=True)

if __name__=='__main__':main()
