"""Opt-in real LLM/TTS verification; no platform, S3 upload, or microphone playback.

Run from agent root: python scripts/verify_topic_evolution_live.py --env-file /local/.env
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-file', required=True)
    parser.add_argument('--output', default='output/topic-evolution-live.json')
    args = parser.parse_args()
    load_dotenv(args.env_file)
    from echuu.core.unit import Show, Unit, ScriptLine, AcousticHint
    from echuu.core.story_core import StoryCore
    from echuu.core.persona_model import RichPersona
    from echuu.live.engine import EchuuLiveEngine
    from echuu.live.state import Danmaku, PerformanceState
    engine = EchuuLiveEngine()
    persona = '节俭、温和、有点爱吐槽的年轻上班族，关心家人；用自然中文口语。'
    topic = '把换手机的预算用在哪里'
    initial = [
        '刚发工资，我正琢磨要不要换手机，可旧手机明明还能用。',
        '拿起旧手机看看照片，想着换新的，也有点舍不得这笔钱。',
        '真要换的话，我就先列一张清单，看哪些功能是自己每天会用的。',
        '不过买东西之前先等几天，热情过去了，反而能想清楚。',
        '我准备把购物车先放着，看看过几天自己还想不想要。',
        '等想清楚了再决定，至少不会因为一时冲动花掉这笔钱。',
    ]
    show = Show(persona=RichPersona(identity=persona, belief='花钱要想清楚', flaw='偶尔冲动'), topic=topic,
                story_core=StoryCore(spine='认真想想自己的消费取舍'),
                units=[Unit(index=0, time_window=(0, 90), acoustic=AcousticHint(),
                            lines=[ScriptLine(id=f'L{i:03d}', text=t, stage='pad') for i, t in enumerate(initial)])])
    engine.state = PerformanceState(name='小秋', persona=persona, background='正在考虑消费预算，没有其他已知家庭经历。',
                                    topic=topic, show=show)
    engine._source_material = {'persona': persona, 'topic': topic, 'background': engine.state.background}
    engine.tuning_guidance = ()
    engine._remember_spoken(initial[0], 'L000', 'story')
    report = {'model': getattr(engine.llm, 'model', ''), 'source': 'seeded-script + real topic LLM + real TTS',
              'initial_topic': topic, 'initial_lines': initial, 'checks': [], 'events': []}
    cases = [
        ('greeting', Danmaku.from_text('晚上好呀，小秋！', user='阿晴')),
        ('rice_ball', Danmaku.from_input('Sent Pebble', user='阿晴', kind='gift', gift_id='white-pebble', amount=8)),
        ('new_topic', Danmaku.from_text('不如省下买手机的钱给妈妈买个蛋糕？顺着聊聊长大以后怎么关心父母吧。', user='小明')),
    ]
    for label, dm in cases:
        start = time.monotonic()
        note = engine._steer_remaining_show(dm)
        row = {'case': label, 'elapsed_s': round(time.monotonic()-start, 2), 'item': dm.to_public(),
               'note': note, 'continuity_review': engine.topic_evolution.last_review, 'review_history': engine.topic_evolution.review_history, 'remaining_lines': [l.text for _, _, l in engine._upcoming_script_lines()]}
        report['events'].append(row)
        print(json.dumps({'case': label, 'action': dm.action, 'outcome': dm.outcome, 'topic': engine.state.topic}, ensure_ascii=False), flush=True)
        # Preserve even failures, to distinguish provider errors from a successful pass.
        out = Path(args.output); out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    report['checks'].append({'name': 'greeting_reply', 'pass': report['events'][0]['item']['action'] == 'reply' and report['events'][0]['item']['outcome'] == 'accepted'})
    report['checks'].append({'name': 'rice_ball_identity', 'pass': report['events'][1]['item']['gift_name'] == '饭团'})
    report['checks'].append({'name': 'related_topic_adopted', 'pass': report['events'][2]['item']['action'] == 'transition' and report['events'][2]['item']['story_changed']})
    report['checks'].append({'name': 'past_preserved', 'pass': show.units[0].lines[0].text == initial[0]})
    report['checks'].append({'name': 'ending_rewritten', 'pass': show.units[0].lines[-1].text != initial[-1]})
    # Simulate the next *already delivered* line before the next interaction.
    next_line = show.units[0].lines[1]
    engine.state.current_line_in_unit = 1
    engine._remember_spoken(next_line.text, next_line.id, 'story')
    current_topic = engine.state.topic
    engine.state.danmaku_queue.append(Danmaku.from_text('晚上好，我继续听。', user='阿晴'))
    reply = engine._maybe_interleave_danmaku(0, next_line.text)
    report['reply_review'] = engine.topic_evolution.reply_review_history
    report['reply'] = {'speech': reply['speech'] if reply else None, 'audio_bytes': len(reply.get('audio') or b'') if reply else 0}
    report['checks'].append({'name': 'next_greeting_keeps_new_topic', 'pass': engine.state.topic == current_topic and current_topic != topic})
    # Synthesize one actual rewritten continuation; TTS's key includes the new text.
    audio = engine.tts.synthesize(next_line.text)
    report['rewritten_audio'] = {'speech': next_line.text, 'audio_bytes': len(audio or b'')}
    report['checks'].append({'name': 'real_reply_and_continuation_audio', 'pass': bool(report['reply']['audio_bytes'] and audio)})
    report['current_topic'] = engine.state.topic
    report['topic_history'] = engine.state.topic_history
    report['passed'] = all(c['pass'] for c in report['checks'])
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    engine.tts.close_preparation()
    print(json.dumps({'passed': report['passed'], 'output': str(out), 'checks': report['checks']}, ensure_ascii=False), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
