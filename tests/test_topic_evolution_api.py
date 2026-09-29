"""Real REST/WS contract with a deterministic engine; no external services."""
import asyncio
import importlib
import json
from pathlib import Path
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

from echuu.live.topic_evolution import TopicEvolution
from tests.test_danmaku_interleave import _engine_with, _make_show, FakeLLM
from tests.test_topic_evolution import proposal


def test_rest_gift_identity_reaches_ws_and_new_topic_survives_next_chat(monkeypatch, tmp_path):
    backend = Path(__file__).resolve().parents[1] / 'echuu-web' / 'backend'
    monkeypatch.syspath_prepend(str(backend))
    # Routers imported without main: do not initialize or migrate any real DB.
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + str(tmp_path / 'test.db'))
    api = importlib.import_module('routers.api')
    ws = importlib.import_module('routers.websocket')
    state = api.state
    e = _engine_with(_make_show(), [])
    e.topic_evolution = TopicEvolution(FakeLLM(json.dumps(proposal(e), ensure_ascii=False)))
    monkeypatch.setattr(state, 'is_running', True)
    monkeypatch.setattr(state, 'current_engine', e)
    app = FastAPI()
    app.include_router(api.router, prefix='/api/v1')
    app.include_router(ws.router)
    with TestClient(app) as client:
        room = client.post('/api/v1/room').json()
        with client.websocket_connect('/ws?room_id=' + room['room_id']) as socket:
            def broadcast(event):
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    client.portal.call(state.broadcast, event, room['room_id'])
                else:
                    loop.create_task(state.broadcast(event, room_id=room['room_id']))
            e.on_steering = broadcast
            result = client.post('/api/v1/danmaku', json={
                'room_id': room['room_id'], 'client_id': 'gift-1', 'user': '小明',
                'text': 'Sent Pebble', 'kind': 'gift', 'gift_id': 'white-pebble', 'amount': 8,
            })
            assert result.status_code == 200
            assert result.json()['item']['gift_name'] == '饭团'
            queued = socket.receive_json()
            assert queued['item']['status'] == 'queued'
            response = e._maybe_interleave_danmaku(0, '刚才讲到预算')
            states = [socket.receive_json() for _ in range(3)]
            assert [s['item']['status'] for s in states] == ['processing', 'applied', 'replied']
            assert states[1]['item']['story_changed'] is True
            assert states[1]['item']['action'] == 'transition'
            assert states[1]['item']['current_topic'] == '怎么关心家人'
            assert response['danmaku']['text'] == 'Sent 饭团'
            e.topic_evolution = TopicEvolution(FakeLLM(json.dumps(proposal(e, 'reply'), ensure_ascii=False)))
            assert client.post('/api/v1/danmaku', json={'room_id': room['room_id'], 'text': '晚上好', 'user': '阿晴'}).status_code == 200
            assert socket.receive_json()['item']['status'] == 'queued'
            e._maybe_interleave_danmaku(0, '继续聊家人')
            states = [socket.receive_json() for _ in range(3)]
            assert states[-1]['item']['current_topic'] == '怎么关心家人'
            assert states[-1]['item']['action'] == 'reply'
            assert states[-1]['item']['story_changed'] is False


def test_closing_rejects_new_interactions_instead_of_queueing_forever(monkeypatch, tmp_path):
    backend = Path(__file__).resolve().parents[1] / 'echuu-web' / 'backend'
    monkeypatch.syspath_prepend(str(backend))
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + str(tmp_path / 'closing.db'))
    api = importlib.import_module('routers.api')
    e = _engine_with(_make_show(), [])
    e.accepting_interactions = False
    monkeypatch.setattr(api.state, 'is_running', True)  # archive is still running
    monkeypatch.setattr(api.state, 'current_engine', e)
    app = FastAPI()
    app.include_router(api.router, prefix='/api/v1')
    with TestClient(app) as client:
        room = client.post('/api/v1/room').json()
        response = client.post('/api/v1/danmaku', json={
            'room_id': room['room_id'], 'text': '再聊一会儿', 'user': '阿晴'})
        assert response.status_code == 400
        assert '收尾' in response.json()['detail']
        assert e.state.danmaku_queue == []
