from concurrent.futures import ThreadPoolExecutor
import threading
from echuu.live.tts_client import TTSClient
from unittest.mock import patch

def client():
    t = TTSClient.__new__(TTSClient)
    class Voice:
        voice = 'Bella'
        sample_rate = 24000
        speech_rate = 1.0
        def synthesize(self, text): return text.encode()
    t.tts = Voice(); t.enabled = True; t._recording = False; t._recording_buffer = []
    t._prepared = {}; t._prepare_lock = threading.Lock()
    t._prepare_pool = ThreadPoolExecutor(max_workers=2)
    return t

def test_reuses_audio_and_regenerates_only_changed_text_or_voice():
    t = client()
    with patch('echuu.live.breath.synthesize_with_breath', side_effect=lambda fn, text, **kw: fn(text)) as synth:
        f = t.prepare('原稿'); assert f.result() == '原稿'.encode()
        t.start_recording()
        assert t.synthesize('原稿') == '原稿'.encode()
        assert synth.call_count == 1
        assert len(t._recording_buffer) == 1
        assert t.synthesize('观众改写')
        assert synth.call_count == 2
        t.tts.voice = 'Cherry'
        assert t.synthesize('原稿')
        assert synth.call_count == 3
    t.close_preparation()

def test_retries_failed_audio_once_and_does_not_cache_silence():
    t = client()
    with patch('echuu.live.breath.synthesize_with_breath', side_effect=[b'', b'voice']) as synth:
        assert t.prepare('句子').result() == b'voice'
        assert synth.call_count == 2
    t.close_preparation()
