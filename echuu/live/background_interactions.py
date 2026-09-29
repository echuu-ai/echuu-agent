"""Non-blocking preparation. Only the playback thread may commit live state.

Workers operate on snapshots; their results have deadlines and an exact future
insertion point. Missing that point keeps the original, already-prepared audio.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import copy, deepcopy
from dataclasses import dataclass, field
from time import monotonic
from threading import Event

from .topic_evolution import TopicEvolution


@dataclass
class PendingInteraction:
    dm: object
    started: float
    target_ids: list[str]
    originals: list[str]
    revision: int
    reply: object = None
    rewrite: object = None
    replied: bool = False
    resolved: bool = False
    response: str = ""
    rewrite_finished: float | None = None
    cancel_rewrite: Event = field(default_factory=Event)


class BackgroundInteractions:
    def __init__(self, engine, *, timeout=25.0, bridge_lines=3, clock=monotonic):
        self.engine, self.timeout, self.bridge_lines, self.clock = engine, timeout, bridge_lines, clock
        self.replies = ThreadPoolExecutor(max_workers=2, thread_name_prefix="interaction-reply")
        self.rewrites = ThreadPoolExecutor(max_workers=2, thread_name_prefix="interaction-rewrite")
        self.pending = []
        self.revision = 0
        self.closed = False
        engine.accepting_interactions = True
        engine._interaction_audio = {}

    def _snapshot(self):
        clone = copy(self.engine)
        clone.state = deepcopy(self.engine.state)
        clone.on_steering = None
        clone.background_interactions = None
        evolution = getattr(self.engine, "topic_evolution", None)
        if evolution is not None:
            clone.topic_evolution = TopicEvolution(evolution.llm)
        clone.tts = self.engine.tts.fork_preparation()
        return clone

    @staticmethod
    def _reply(clone, dm, last_line):
        try:
            # Acknowledge now. Never wait for or promise the pending rewrite.
            dm.action, dm.outcome = "reply", "accepted"
            dm.reason = "先回应当前互动；后续方向仍在准备"
            dm.current_topic = dm.previous_topic = clone.state.topic
            # Tangent cards still get an immediate acknowledgment; their
            # branch/return rewrite is handled by the independent worker.
            if dm.kind == "tangent":
                dm.kind = "chat"
            clone.state.danmaku_queue = [dm]
            clone._steer_remaining_show = lambda _: dm.reason
            return clone._maybe_interleave_danmaku(clone.state.current_unit_idx, last_line)
        finally:
            clone.tts.close_preparation()

    @staticmethod
    def _rewrite(clone, dm, ids, deadline, clock, cancelled):
        from dataclasses import asdict
        from .breath import chunk_text
        from .tts_instruction import build_instruction
        try:
            upcoming = [item for item in clone._upcoming_script_lines() if item[2].id in ids]
            clone._upcoming_script_lines = lambda: upcoming
            original_text = [line.text for _, _, line in upcoming]
            note = clone._steer_remaining_show(dm)
            if dm.kind == "tangent":
                dm.story_changed = original_text != [line.text for _, _, line in upcoming]
                dm.action, dm.outcome = ("expand" if dm.story_changed else "reply"), "accepted"
                dm.current_topic, dm.reason = clone.state.topic, note
                clone.state.topic_history = getattr(clone.state, "topic_history", [])
            if cancelled.is_set() or clock() > deadline:
                raise TimeoutError("改稿已过期")
            audio = {}
            if dm.story_changed:
                futures = []
                for unit, _, line in upcoming:
                    clone.tts.update_session(**asdict(unit.acoustic))
                    clone.tts.set_instruction(build_instruction(unit, line))
                    for index, chunk in enumerate(chunk_text(line.text) or [line.text]):
                        futures.append(((line.id, index, chunk), clone.tts.prepare(chunk)))
                for key, future in futures:
                    if cancelled.is_set():
                        raise TimeoutError("改稿已取消")
                    audio[key] = future.result(timeout=max(0, deadline - clock()))
                    if clone.tts.enabled and not audio[key]:
                        raise RuntimeError("改稿语音未就绪")
            if clock() > deadline:
                raise TimeoutError("互动准备超过期限")
            return clone.state, dm, note, audio
        finally:
            clone.tts.close_preparation()

    def tick(self):
        """Called during playback; starts jobs, never waits or changes the script."""
        if self.closed:
            return
        for job in self.pending:
            if self.clock() - job.started >= self.timeout and not job.replied and not job.reply.done():
                job.replied = True
                job.reply.cancel()
                self.engine.emit_steering(job.dm, "rejected", note="短回应准备超时，正文继续")
            if self.clock() - job.started >= self.timeout and not job.resolved and not job.rewrite.done():
                self._fallback(job, "rewrite_timeout", f"改稿超过{self.timeout:g}秒，继续已有内容")
        if len(self.pending) >= 4 or not self.engine.state.danmaku_queue:
            return
        dm = self.engine._pick_danmaku()
        dm.current_topic = dm.previous_topic = self.engine.state.topic
        dm.reason, dm.outcome = "短回应与后续内容正在分别准备", "accepted"
        # New substantive input supersedes unfinished work; greetings do not.
        from .topic_evolution import is_social_reply
        if not is_social_reply(dm.to_public()):
            for job in self.pending:
                if not job.resolved:
                    self._fallback(job, "superseded", "收到更新的互动，停止采用上一份未完成改稿")
        clone = self._snapshot()
        upcoming = clone._upcoming_script_lines()
        # Reserve roughly 18 seconds of existing speech, up to three lines.
        # This is continuing content, not a silence or a minimum response delay.
        bridge_count, estimated_seconds = 0, 0.0
        while bridge_count < min(self.bridge_lines, max(0, len(upcoming) - 2)) and estimated_seconds < 18:
            estimated_seconds += len(upcoming[bridge_count][2].text) / 5
            bridge_count += 1
        bridge, targets = upcoming[:bridge_count], upcoming[bridge_count:]
        clone._committed_bridge = [{"id": line.id, "text": line.text} for _, _, line in bridge]
        job = PendingInteraction(dm, self.clock(), [line.id for _, _, line in targets],
                                 [line.text for _, _, line in targets], self.revision)
        reply_clone = self._snapshot()
        history = getattr(self.engine.state, "spoken_history", [])
        last_line = history[-1]["text"] if history else ""
        job.reply = self.replies.submit(self._reply, reply_clone, deepcopy(dm), last_line)
        job.rewrite = self.rewrites.submit(self._rewrite, clone, deepcopy(dm), job.target_ids,
                                          job.started + self.timeout, self.clock, job.cancel_rewrite)
        job.rewrite.add_done_callback(lambda _: setattr(job, "rewrite_finished", self.clock()))
        self.pending.append(job)

    def _history(self, job):
        dm = job.dm
        history = [entry for entry in getattr(self.engine.state, "interaction_history", []) if entry["id"] != dm.id]
        history.append({"id": dm.id, "user": dm.user, "text": dm.text, "action": dm.action,
                        "outcome": dm.outcome, "story_changed": dm.story_changed,
                        "disposition": "adopted" if dm.story_changed else "responded" if dm.outcome == "accepted" else "not_adopted",
                        "pending": not job.resolved, "reason": dm.reason,
                        "topic": self.engine.state.topic, "reply": job.response})
        self.engine.state.interaction_history = history[-12:]

    def _fallback(self, job, code, reason):
        if job.resolved:
            return
        job.resolved = True
        job.cancel_rewrite.set()
        if job.rewrite:
            job.rewrite.cancel()
        dm = job.dm
        dm.action, dm.outcome, dm.story_changed = "reply", "fallback", False
        dm.current_topic = self.engine.state.topic
        dm.reason = reason
        dm.decision_trace = [*dm.decision_trace, {"attempt": 0, "code": code, "reason": reason}]
        self._history(job)
        self.engine.emit_steering(dm, "applied", elapsed_ms=round((self.clock() - job.started) * 1000),
                                  preparation_ms=round(((job.rewrite_finished or self.clock()) - job.started) * 1000))

    def boundary(self, unit_idx, *, reply_allowed=True, commit_allowed=True):
        """Poll only completed futures, insert at full-line boundaries, never wait."""
        if self.closed:
            return None
        self.tick()
        event = None
        for job in self.pending:
            if not job.replied and job.reply.done() and reply_allowed and event is None:
                job.replied = True
                try:
                    response = job.reply.result()
                except Exception:
                    response = None
                if response and (response.get("audio") or not self.engine.tts.enabled):
                    job.response = response["speech"]
                    event = self.engine._build_danmaku_event(self.engine.state.show.units[unit_idx], job.dm,
                                                            job.response, response.get("audio"))
                    self.engine._remember_spoken(job.response, "reply-" + job.dm.id, "reply")
                    if getattr(self.engine.tts, "_recording", False) and response.get("audio"):
                        self.engine.tts._recording_buffer.append(response["audio"])
                    self._history(job)
                    self.engine.emit_steering(job.dm, "replied", rewrite_pending=not job.resolved)
                else:
                    self.engine.emit_steering(job.dm, "rejected", note="短回应语音未就绪，正文继续")
            if not job.resolved and commit_allowed:
                self._try_commit(job)
        self.pending[:] = [job for job in self.pending if not (job.replied and job.resolved)]
        return event

    def _try_commit(self, job):
        upcoming = self.engine._upcoming_script_lines()
        remaining = {line.id: line for _, _, line in upcoming}
        if any(i not in remaining for i in job.target_ids):
            self._fallback(job, "stale_rewrite", "播放已越过改稿接入点，继续原稿")
            return
        if not job.rewrite.done():
            return
        try:
            proposal, dm, note, audio = job.rewrite.result()
        except TimeoutError:
            self._fallback(job, "rewrite_timeout", f"改稿超过{self.timeout:g}秒，继续已有内容")
            return
        except Exception:
            self._fallback(job, "preparation_failed", "改稿或语音准备失败，继续原稿")
            return
        if dm.story_changed and job.revision != self.revision:
            self._fallback(job, "stale_rewrite", "话题已改变，丢弃旧方向的改稿")
            return
        if dm.story_changed:
            # Play the reserved bridge first. Commit before the first rewritten line.
            if not upcoming or upcoming[0][2].id != job.target_ids[0]:
                return
            if [remaining[i].text for i in job.target_ids] != job.originals:
                self._fallback(job, "stale_rewrite", "原稿已改变，丢弃过期改稿")
                return
            changed = {line.id: line for unit in proposal.show.units for line in unit.lines}
            for line_id in job.target_ids:
                line = changed[line_id]
                target = remaining[line_id]
                target.text, target.cue, target.key_info, target.is_rupture = line.text, line.cue, line.key_info, line.is_rupture
            st = self.engine.state
            st.topic = st.show.topic = proposal.topic
            st.show.story_core = proposal.show.story_core
            st.topic_history = proposal.topic_history
            self.engine._interaction_audio.update(audio)
            self.revision += 1
        for name in ("action", "outcome", "story_changed", "reason", "current_topic", "previous_topic", "decision_trace"):
            setattr(job.dm, name, getattr(dm, name))
        job.resolved = True
        self._history(job)
        self.engine.emit_steering(job.dm, "applied", note=note, elapsed_ms=round((self.clock() - job.started) * 1000),
                                  preparation_ms=round(((job.rewrite_finished or self.clock()) - job.started) * 1000))

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.engine.accepting_interactions = False
        for job in self.pending:
            self._fallback(job, "session_ended", "本场已进入收尾，未完成改稿不再采用")
            if not job.replied:
                job.reply.cancel()
                self.engine.emit_steering(job.dm, "rejected", note="本场已收尾，回应未赶上播放")
        for dm in self.engine.state.danmaku_queue:
            self.engine.emit_steering(dm, "rejected", note="本场已收尾")
        self.engine.state.danmaku_queue.clear()
        self.replies.shutdown(wait=False, cancel_futures=True)
        self.rewrites.shutdown(wait=False, cancel_futures=True)
