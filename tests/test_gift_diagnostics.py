import json
from concurrent.futures import Future

from echuu.session_types import PerformanceStep
from tests.test_gift_runtime import rig
from tests.test_session_extensions import batch


def test_gift_uses_bounded_structured_call_and_waits_for_real_playback():
    runtime, engine, lines, response = rig()
    requests = []

    def call(prompt, **kwargs):
        requests.append(kwargs)
        return json.dumps(response)

    engine.llm_gen.call = call
    try:
        runtime.prepare(batch(), True)
        assert requests[0]["response_schema"]["properties"]["sentences"]
        assert requests[0]["max_tokens"] == 400
        choices = requests[0]["response_schema"]["oneOf"]
        assert choices[0]["properties"]["sentences"] == {"minItems": 2, "maxItems": 2}
        assert choices[-1]["properties"]["selected_gift_id"] == {"const": None}
        assert runtime.snapshot()[0]["status"] == "prepared"
        runtime.finish("gift", True)
        assert runtime.snapshot()[0]["status"] == "applied"
        for index, line in enumerate(lines):
            step = runtime.annotate(
                PerformanceStep(
                    step_id=str(index),
                    line_id=line.id,
                    speech=line.text,
                    payload={"line": {"chunk_last": True}},
                )
            )
            assert step.payload["gift_effect"]["batch_id"] == "gift"
            runtime.played(step)
            runtime.played(step)
        assert runtime.snapshot()[0]["status"] == "played"
        assert runtime.snapshot()[0]["played_lines"] == 2
        assert "第一句" not in json.dumps(runtime.snapshot(), ensure_ascii=False)
    finally:
        runtime.close()


def test_timeout_has_reason_and_late_result_cannot_apply():
    runtime, engine, lines, response = rig()
    future = Future()
    future.set_running_or_notify_cancel()

    class Pool:
        def submit(self, *_args):
            return future

        def shutdown(self, **_kwargs):
            pass

    runtime.pool.shutdown(wait=False)
    runtime.pool = Pool()
    try:
        runtime.prepare(batch().model_copy(update={"start_within": 0.01}), True)
        future.set_result(response)
        runtime.finish("gift", True)
        assert runtime.snapshot()[0]["reason"] == "model_timeout"
        assert runtime.snapshot()[0]["status"] == "not_applied"
        assert lines[0].text == "原句0。"
    finally:
        runtime.close()


def test_preview_and_stale_revision_do_not_report_effect_success():
    runtime, engine, lines, _ = rig()
    try:
        runtime.prepare(batch(), False)
        runtime.finish("gift", True)
        assert runtime.snapshot()[0]["reason"] == "preview"
        runtime.prepare(batch("live"), True)
        engine.background_interactions.revision += 1
        runtime.finish("live", True)
        assert runtime.snapshot()[-1]["reason"] == "revision_changed"
        assert lines[0].text == "原句0。"
    finally:
        runtime.close()


def test_unknown_selection_and_count_and_format_are_not_weakened():
    runtime, _, lines, response = rig()
    try:
        for key, value, reason in [
            ("selected_gift_id", "forged", "unknown_gift"),
            ("sentences", [], "sentence_count"),
            ("acknowledgement", "谢谢！收到啦！", "sentence_bounds"),
        ]:
            original = response[key]
            response[key] = value
            runtime.prepare(batch(reason), True)
            runtime.finish(reason, True)
            assert runtime.snapshot()[-1]["reason"] == reason
            assert runtime.snapshot()[-1]["status"] == "not_applied"
            assert lines[0].text == "原句0。"
            response[key] = original
    finally:
        runtime.close()


def test_last_chunk_alone_cannot_claim_whole_rewritten_line_played():
    from echuu.live.breath import chunk_text

    runtime, _engine, lines, response = rig()
    response["sentences"][0] = (
        "我先把场次创建好，再慢慢检查房间连接，仔细核对字幕和声音，确认这一轮准备都没有问题；最后还要认真确认播放完成后的回执确实已经送达。"
    )
    try:
        runtime.prepare(batch(), True)
        runtime.finish("gift", True)
        chunks = chunk_text(lines[0].text)
        assert len(chunks) > 1
        last = len(chunks) - 1
        runtime.played(
            runtime.annotate(
                PerformanceStep(
                    step_id="last",
                    line_id=lines[0].id,
                    chunk_index=last,
                    speech=chunks[last],
                    payload={},
                )
            )
        )
        assert runtime.snapshot()[0]["played_lines"] == 0
        for index, speech in enumerate(chunks[:-1]):
            runtime.played(
                runtime.annotate(
                    PerformanceStep(
                        step_id=str(index),
                        line_id=lines[0].id,
                        chunk_index=index,
                        speech=speech,
                        payload={},
                    )
                )
            )
        assert runtime.snapshot()[0]["played_lines"] == 1
        assert runtime.snapshot()[0]["status"] == "applied"
    finally:
        runtime.close()


def test_configured_model_budget_reserves_half_remaining_time_for_audio():
    from types import SimpleNamespace

    runtime, engine, _lines, _response = rig()
    engine.config = SimpleNamespace(gift_model_timeout_seconds=8)
    try:
        runtime.prepare(batch().model_copy(update={"start_within": 6}), True)
        assert 2.9 < runtime.snapshot()[0]["budget_seconds"] <= 3
        runtime.finish("gift", False)
        assert runtime.snapshot()[0]["reason"] == "ack_not_played"
    finally:
        runtime.close()


def test_invalid_schema_reports_field_and_type_without_raw_input():
    runtime, _engine, _lines, response = rig()
    response["sentences"] = "PRIVATE BODY"
    try:
        runtime.prepare(batch(), True)
        item = runtime.snapshot()[0]
        assert item["reason"] == "schema_invalid"
        assert item["validation_issues"] == [
            {"field": "sentences", "code": "list_type"}
        ]
        assert "PRIVATE" not in json.dumps(item)
    finally:
        runtime.close()


def test_gift_submits_all_audio_before_waiting_and_skips_unchanged_lines():
    runtime, engine, lines, response = rig()
    submitted = []
    closed = []
    response["sentences"][1] = lines[1].text

    class AudioFuture(Future):
        def result(self, *, timeout):
            assert submitted == [response["acknowledgement"], response["sentences"][0]]
            assert 0 < timeout <= 30
            return b"audio"

    class Fork:
        def prepare(self, text):
            submitted.append(text)
            future = AudioFuture()
            future.set_result(b"audio")
            return future

        def close_preparation(self):
            closed.append(True)

    engine.tts.fork_preparation = Fork
    engine.tts.synthesize = lambda _: (_ for _ in ()).throw(
        AssertionError("serial thanks")
    )
    try:
        runtime.prepare(batch(), True)
        item = runtime.snapshot()[0]
        assert item["changed_lines"] == 1
        assert item["audio_chunks"] == 2
        assert item["audio_seconds"] >= item["ack_audio_seconds"] >= 0
        assert closed == [True]
        runtime.finish("gift", True)
        assert lines[0].text == response["sentences"][0]
        assert lines[1].text == "原句1。"
    finally:
        runtime.close()


def test_parallel_audio_failure_keeps_original_and_closes_workers():
    runtime, engine, lines, response = rig()
    closed = []

    class Fork:
        def prepare(self, text):
            result = Future()
            if text == response["sentences"][1]:
                result.set_exception(OSError("tts failed"))
            else:
                result.set_result(b"audio")
            return result

        def close_preparation(self):
            closed.append(True)

    engine.tts.fork_preparation = Fork
    try:
        _, audio = runtime.prepare(batch(), True)
        assert audio == b"audio"
        runtime.finish("gift", True)
        assert runtime.snapshot()[0]["reason"] == "audio_failed"
        assert runtime.snapshot()[0]["changed_lines"] == 0
        assert all(line.text.startswith("原句") for line in lines)
        assert closed == [True]
    finally:
        runtime.close()
