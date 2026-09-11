import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from echuu.harness.store import ArtifactStore
from echuu.harness.runner import parse_judgment, run_case
from echuu.harness.report import build_report


def test_append_only_and_parents(tmp_path):
    store = ArtifactStore(tmp_path/'run', 'run')
    first = store.add('input', {'topic':'hello'})
    second = store.add('output', {'text':'world'}, parents=[first])
    assert json.loads((store.directory/'artifacts'/f'{second}.json').read_text())['parent_artifact_ids']==[first]
    with pytest.raises(ValueError):
        store.add('bad', {}, parents=['missing'])
    with pytest.raises(FileExistsError):
        ArtifactStore(tmp_path/'run', 'run')
    store.finish({'status':'failed'})
    with pytest.raises(FileExistsError):
        store.finish({'status':'completed'})


def test_judge_rejects_hallucinated_spans():
    judgment = {'gates':{g:{'passed':True,'reason':'ok'} for g in ['completeness','clarity','shareability','persona','humor']},
                'issues':[{'text_span':'not in script'}]}
    with pytest.raises(ValueError):
        parse_judgment(json.dumps(judgment), 'actual script')
    judgment['issues'] = []
    assert parse_judgment(json.dumps(judgment), 'actual script') == judgment
    judgment['gates']['humor']['passed'] = 'yes'
    with pytest.raises(ValueError):
        parse_judgment(json.dumps(judgment), 'actual script')


def test_failed_call_keeps_request_and_inspectable_report(tmp_path):
    def failing(prompt, *args, **kwargs):
        raise RuntimeError('fake-secret-must-not-be-written')
    class Engine:
        def __init__(self):
            self.llm_gen=SimpleNamespace(model='fake',call=failing,generate=failing)
            self.llm=SimpleNamespace()
            self.debug_trace={'stages':[]}
        def setup(self, **kwargs):
            self.llm_gen.call('<script>alert(1)</script>')
    fixture={'id':'test','name':'<script>','persona':'test','background':'test','voice':'Ethan'}
    directory, result=run_case(fixture,'legacy_v4',tmp_path,engine_factory=Engine,audio=False,judge=False)
    events=(directory/'events.jsonl').read_text()
    assert result['status']=='failed'
    assert 'llm_request' in events and 'llm_response' in events
    assert 'fake-secret' not in events
    report=build_report(tmp_path).read_text()
    assert '<script>' not in report
    assert '&lt;script&gt;' in report


def test_verifier_detects_mutated_artifact(tmp_path):
    from echuu.harness.verify import verify
    store = ArtifactStore(tmp_path/'run', 'run')
    artifact_id = store.add('input', {'text':'original'})
    store.finish({'status':'completed'})
    assert verify(tmp_path, expected=1)['passed']
    artifact_path = store.directory/'artifacts'/f'{artifact_id}.json'
    record = json.loads(artifact_path.read_text())
    record['output']['text'] = 'silently changed'
    artifact_path.write_text(json.dumps(record))
    report = verify(tmp_path, expected=1)
    assert not report['passed']
    assert 'snapshot mismatch' in report['runs'][0]['errors']


def test_canon_material_keeps_scene_and_version_separate():
    from echuu.harness.canon import generation_material
    fixture=json.loads(Path('echuu/harness/fixtures-canon.json').read_text())[0]
    topic,evidence,background=generation_material(fixture,'wrong topic',[])
    assert topic==fixture['topic'] and evidence==fixture['evidence']
    assert '尚未告诉桃矢' in background and 'author_only' in background
    assert '义卖' not in topic


def test_canon_audit_rejects_false_sources_and_unobserved_passes():
    from echuu.harness.canon import parse_audit, CATEGORIES
    fixture=json.loads(Path('echuu/harness/fixtures-canon.json').read_text())[0]
    data={'checks':[{'category':c,'verdict':'pass','text_span':'','fact_ids':[],'reason':'unobserved'} for c in CATEGORIES], 'character_specific_choices':[], 'overall':'pass'}
    result=parse_audit(json.dumps(data),'台词',fixture)
    assert result['overall']=='uncertain'
    data['checks'][0].update(verdict='fail',text_span='台词',fact_ids=['made_up_source'])
    with pytest.raises(ValueError):
        parse_audit(json.dumps(data),'台词',fixture)
    data['checks'][0]['fact_ids']=['yb_identity']
    assert parse_audit(json.dumps(data),'台词',fixture)['overall']=='fail'


def test_canon_known_source_can_support_but_not_replace_a_fact():
    from echuu.harness.canon import parse_audit, CATEGORIES
    fixture=json.loads(Path('echuu/harness/fixtures-canon.json').read_text())[0]
    checks=[{'category':c,'verdict':'uncertain','text_span':'','fact_ids':[],'reason':'unobserved'} for c in CATEGORIES]
    checks[0].update(verdict='fail',text_span='我是女孩子',fact_ids=['yb_identity','user_excerpt_2026_09_06'])
    data={'checks':checks,'character_specific_choices':[]}
    assert parse_audit(json.dumps(data),'我是女孩子',fixture)['overall']=='fail'
    checks[0]['fact_ids']=['user_excerpt_2026_09_06']
    with pytest.raises(ValueError):
        parse_audit(json.dumps(data),'我是女孩子',fixture)
