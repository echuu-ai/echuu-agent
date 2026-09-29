from concurrent.futures import Future
from copy import deepcopy
from time import perf_counter
from types import SimpleNamespace

from echuu.live.background_interactions import BackgroundInteractions, PendingInteraction
from echuu.live.state import Danmaku
from tests.test_danmaku_interleave import _engine_with, _make_show


def rig():
    engine = _engine_with(_make_show(), [])
    engine.state.spoken_history = []
    engine.state.interaction_history = []
    engine.state.topic_history = []
    clock = [0.0]
    bg = BackgroundInteractions(engine, clock=lambda: clock[0])
    engine.background_interactions = bg
    events = []
    engine.on_steering = events.append
    dm = Danmaku.from_text('周末带块蛋糕回去看看妈妈吧')
    lines = engine._upcoming_script_lines()[3:]
    job = PendingInteraction(dm, 0, [l.id for _, _, l in lines], [l.text for _, _, l in lines], 0,
                             reply=Future(), rewrite=Future())
    bg.pending = [job]
    return engine, bg, job, clock, events


def test_slow_rewrite_never_blocks_boundary_and_short_reply_comes_first():
    e, bg, job, _, events = rig()
    try:
        job.reply.set_result({'speech': '这个主意挺暖心的。', 'audio': b'ack'})
        start = perf_counter()
        reply = bg.boundary(0)
        assert perf_counter() - start < .05
        assert reply['audio'] == b'ack' and not job.rewrite.done()
        assert e.state.spoken_history[-1]['kind'] == 'reply'
        assert events[-1]['item']['status'] == 'replied'
        assert events[-1]['item']['rewrite_pending'] is True
        assert e.state.topic == '商稿稿费'
    finally:
        bg.close()


def test_timeout_continues_original_and_late_result_cannot_commit():
    e, bg, job, clock, events = rig()
    before = deepcopy(e.state.show)
    try:
        # Simulate a running, uncancellable provider call.
        job.rewrite.set_running_or_notify_cancel()
        clock[0] = 25.1
        assert bg.boundary(0) is None
        assert job.resolved and job.dm.outcome == 'fallback'
        assert job.dm.decision_trace[-1]['code'] == 'rewrite_timeout'
        job.rewrite.set_result(('late invalid result',))
        bg.boundary(0)
        assert e.state.show == before
    finally:
        bg.close()


def test_prepared_rewrite_commits_only_at_reserved_boundary_with_ready_audio():
    e, bg, job, clock, events = rig()
    try:
        proposal = deepcopy(e.state)
        proposal.topic = proposal.show.topic = '长大后怎么关心父母'
        proposal.topic_history = [{'topic': proposal.topic}]
        audio = {}
        for unit in proposal.show.units:
            for line in unit.lines:
                if line.id in job.target_ids:
                    line.text = '周末带块蛋糕回去看看她好了。'
                    audio[(line.id, 0, line.text)] = b'new'
        dm = deepcopy(job.dm)
        dm.story_changed, dm.action, dm.outcome, dm.current_topic = True, 'transition', 'accepted', proposal.topic
        job.rewrite.set_result((proposal, dm, '已准备', audio))
        bg.boundary(0)
        assert not job.resolved and e.state.topic == '商稿稿费'
        # First four original lines have played; next line is the reserved insertion point.
        e.state.current_unit_idx, e.state.current_line_in_unit = 1, 0
        bg.boundary(1)
        assert job.resolved and e.state.topic == proposal.topic
        assert e.state.show.units[0].lines[0].text == 'u0故事第0句'
        assert e._interaction_audio == audio
    finally:
        bg.close()


def test_passed_insertion_point_discards_rewrite_without_touching_spoken_lines():
    e, bg, job, _, _ = rig()
    try:
        job.rewrite.set_running_or_notify_cancel()
        e.state.current_unit_idx, e.state.current_line_in_unit = 1, 1
        before = deepcopy(e.state.show)
        bg.boundary(1)
        assert job.resolved and job.dm.decision_trace[-1]['code'] == 'stale_rewrite'
        assert e.state.show == before
    finally:
        bg.close()


def test_close_rejects_pending_interactions_and_late_callbacks_are_inert():
    e, bg, job, _, events = rig()
    job.reply.set_running_or_notify_cancel()
    job.rewrite.set_running_or_notify_cancel()
    bg.close()
    assert not e.accepting_interactions
    before = deepcopy(e.state.show)
    job.reply.set_result({'speech': '晚到', 'audio': b'late'})
    job.rewrite.set_result(None)
    assert bg.boundary(0) is None and e.state.show == before
    assert events[-1]['item']['status'] == 'rejected'


def test_ready_rewrite_waiting_for_boundary_is_not_a_preparation_timeout():
    e, bg, job, clock, _ = rig()
    try:
        proposal = deepcopy(e.state)
        dm = deepcopy(job.dm)
        dm.story_changed, dm.action, dm.outcome = True, 'expand', 'accepted'
        proposal.topic_history = []
        job.rewrite.set_result((proposal, dm, 'ready', {}))
        clock[0] = 30
        bg.boundary(0)
        assert not job.resolved  # reserved bridge still playing; preparation finished
    finally:
        bg.close()


def test_short_reply_timeout_does_not_fill_the_pending_queue_forever():
    e, bg, job, clock, events = rig()
    try:
        job.reply.set_running_or_notify_cancel()
        job.rewrite.set_running_or_notify_cancel()
        clock[0] = 26
        bg.boundary(0)
        assert bg.pending == []
        assert any(x['item']['status'] == 'rejected' for x in events)
        job.reply.set_result({'speech': 'too late', 'audio': b'late'})
        assert bg.boundary(0) is None
    finally:
        bg.close()


def test_reply_can_enter_between_sentence_chunks_without_rewriting_current_line():
    e = _engine_with(_make_show(), [])
    e.state.show.units[0].lines[0].text = '这是已经准备好的第一句话，今天想认真想想该怎么安排预算。还有下一句原稿仍要接着说，不能被后来的改稿覆盖掉。'
    calls = []
    response = e._build_danmaku_event(e.state.show.units[0], Danmaku.from_text('你好'), '你好呀', b'ack')
    def boundary(unit_idx, **kwargs):
        calls.append(kwargs)
        if kwargs.get('commit_allowed') is False and len(calls) == 1:
            return response
    e.background_interactions = SimpleNamespace(boundary=boundary)
    run = e.run(max_steps=99, save_audio=False)
    first, second, third = next(run), next(run), next(run)
    assert first['line']['chunk_last'] is False
    assert second['speech'] == '你好呀'
    assert third['line']['id'] == first['line']['id']
    assert third['line']['chunk_index'] == 1
    run.close()


def test_background_tangent_preserves_branch_and_return_behavior():
    from threading import Event
    from echuu.core.story_core import StoryCore
    e = _engine_with(_make_show(), [])
    e.state.show.story_core = StoryCore(spine='预算')
    e.story_steerer.rewrite_line = lambda **kw: '沿着观众的线索聊一小段。' if kw['role'] == 'branch' else '再接回刚才聊的预算。'
    def prepare(text):
        f = Future()
        f.set_result(b'voice')
        return f
    e.tts.prepare = prepare
    e.tts.close_preparation = lambda: None
    dm = Danmaku.from_input('跑毛卡', kind='tangent', entities=[])
    ids = [line.id for _, _, line in e._upcoming_script_lines()]
    state, result, note, audio = BackgroundInteractions._rewrite(e, dm, ids, 25, lambda: 0, Event())
    assert result.story_changed and result.action == 'expand'
    assert state.topic == '商稿稿费'
    assert audio and '沿着观众的线索' in state.show.units[0].lines[1].text


def test_worker_deadline_reports_timeout_not_generic_failure():
    e, bg, job, _, _ = rig()
    try:
        job.rewrite.set_exception(TimeoutError('provider deadline'))
        bg.boundary(0)
        assert job.dm.decision_trace[-1]['code'] == 'rewrite_timeout'
    finally:
        bg.close()
