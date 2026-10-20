from concurrent.futures import Future

from tests.test_gift_runtime import rig
from tests.test_session_extensions import batch


class DeferredAudio(Future):
    def result(self, *args, **kwargs):
        assert self.done(), "candidate awaited before thanks"
        return super().result(*args, **kwargs)


def deferred_rig():
    runtime, engine, lines, response = rig()
    futures = []
    closed = []

    class Fork:
        def prepare(self, text):
            future = DeferredAudio()
            if text == response["acknowledgement"]:
                future.set_result(b"thanks")
            else:
                futures.append(future)
            return future

        def close_preparation(self):
            closed.append(True)

    engine.tts.fork_preparation = Fork
    return runtime, engine, lines, response, futures, closed


def test_thanks_returns_before_rewrite_audio_and_receipt_does_not_claim_effect():
    runtime, _, lines, response, futures, closed = deferred_rig()
    try:
        assert runtime.prepare(batch(), True)[1] == b"thanks"
        assert runtime.snapshot()[0]["status"] == "preparing"
        assert not closed and len(futures) == 2
        assert runtime.finish("gift", True) is False
        assert runtime.snapshot()[0]["acknowledgement_played"]
        assert all(line.text.startswith("原句") for line in lines)
        for future in futures:
            future.set_result(b"rewrite")
        runtime.poll()
        assert runtime.finish("gift", True) is True
        assert lines[0].text == response["sentences"][0]
        assert runtime.snapshot()[0]["status"] == "applied"
        assert runtime.snapshot()[0]["played_lines"] == 0
        assert closed == [True]
    finally:
        runtime.close()


def test_close_drops_background_candidates_and_late_completion_cannot_adopt():
    runtime, _, lines, _, futures, closed = deferred_rig()
    runtime.prepare(batch(), True)
    runtime.close()
    for future in futures:
        future.set_result(b"late")
    runtime.poll()
    assert runtime.snapshot()[0]["reason"] == "cancelled"
    assert all(line.text.startswith("原句") for line in lines)
    assert closed == [True]


def test_expired_background_audio_and_changed_revision_keep_thanks_separate(
    monkeypatch,
):
    from echuu import gift_runtime

    clock = [10.0]
    monkeypatch.setattr(gift_runtime, "monotonic", lambda: clock[0])
    for failure in ("audio_timeout", "revision_changed"):
        runtime, engine, lines, _, futures, closed = deferred_rig()
        try:
            runtime.prepare(batch(), True)
            if failure == "audio_timeout":
                clock[0] += 11
            else:
                engine.background_interactions.revision += 1
            for future in futures:
                future.set_result(b"candidate")
            assert runtime.finish("gift", True) is True
            assert runtime.snapshot()[0]["reason"] == failure
            assert runtime.snapshot()[0]["acknowledgement_played"]
            assert runtime.snapshot()[0]["changed_lines"] == 0
            assert all(line.text.startswith("原句") for line in lines)
            assert closed == [True]
        finally:
            runtime.close()


def test_audio_ready_before_deadline_may_adopt_after_long_played_thanks(monkeypatch):
    from echuu import gift_runtime

    clock = [10.0]
    monkeypatch.setattr(gift_runtime, "monotonic", lambda: clock[0])
    runtime, _, lines, response, futures, _ = deferred_rig()
    try:
        runtime.prepare(batch(), True)
        clock[0] += 2
        for future in futures:
            future.set_result(b"candidate")
        clock[0] += 20
        assert runtime.finish("gift", True) is True
        assert lines[0].text == response["sentences"][0]
        assert runtime.snapshot()[0]["effect_preparation_seconds"] == 2
    finally:
        runtime.close()


def test_cancelled_thanks_and_replacement_discard_unfinished_candidates():
    for action in ("cancel", "replace"):
        runtime, _, lines, _, futures, _ = deferred_rig()
        try:
            runtime.prepare(batch(), True)
            old = list(futures)
            if action == "cancel":
                assert runtime.finish("gift", False) is True
                reason = "ack_not_played"
            else:
                assert runtime.finish("gift", True) is False
                runtime.prepare(batch("next"), True)
                reason = "replaced"
            for future in old:
                future.set_result(b"late")
            runtime.poll()
            assert runtime.snapshot()[0]["reason"] == reason
            assert runtime.snapshot()[0]["changed_lines"] == 0
            assert all(line.text.startswith("原句") for line in lines)
        finally:
            runtime.close()
