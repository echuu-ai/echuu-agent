"""直播引擎服务"""
import os
import asyncio
import traceback
import shutil
from pathlib import Path
from typing import Any, Callable, Optional, List
from datetime import datetime
from sqlalchemy.orm import Session

from echuu.live.engine import EchuuLiveEngine
from echuu.live.state import Danmaku
from echuu.live.timeline import TimelineWriter, estimate_wav_duration

try:
    from ..config import SCRIPTS_DIR
    from ..models import LiveConfig
    from ..state import state
    from ..database.models import Character, VoiceConfig, LLMModel, LiveSession, SessionStatus
    from .s3_archive import archive_session_to_s3
except ImportError:
    from config import SCRIPTS_DIR
    from models import LiveConfig
    from state import state
    from database.models import Character, VoiceConfig, LLMModel, LiveSession, SessionStatus
    from services.s3_archive import archive_session_to_s3


def _load_prev_memory_summary(character_name: str, exclude_session: str = "") -> str:
    """读同角色最近一次 session 的记忆摘要（story_points/promises），供跨场 call-back。"""
    import json
    try:
        candidates = sorted(
            (p for p in SCRIPTS_DIR.glob("*/memory.json") if p.parent.name != exclude_session),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        for path in candidates:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("name") != character_name:
                continue
            parts = []
            points = [str(x) for x in (data.get("story_points") or [])[:3]]
            if points:
                parts.append("聊过：" + "；".join(points))
            promises = [str(p.get("content", p)) for p in (data.get("promises") or [])[:2]]
            if promises:
                parts.append("答应过观众：" + "；".join(promises))
            return "。".join(parts)
    except Exception as exc:  # noqa: BLE001 — 记忆注入失败不阻塞开播
        print(f"[memory] 读取上一场记忆失败（跳过）: {exc}")
    return ""


def _dump_session_memory(engine, character_name: str, session_dir: Path) -> None:
    """storytelling 结束后把记忆摘要落盘，供下一场注入。"""
    import json
    try:
        memory = engine.state.memory
        payload = {
            "name": character_name,
            "initial_topic": getattr(engine.state, "initial_topic", "") or engine.state.topic,
            "current_topic": engine.state.topic,
            "topic_history": getattr(engine.state, "topic_history", []),
            "spoken_history": getattr(engine.state, "spoken_history", []),
            "interaction_history": getattr(engine.state, "interaction_history", []),
            "story_points": memory.story_points.get("mentioned", []),
            "promises": [p for p in memory.promises if not p.get("fulfilled")],
        }
        (session_dir / "memory.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[memory] 记忆落盘失败（跳过）: {exc}")


def _compat_meta(session_dir: Path, session_id: str) -> dict:
    import json
    for name in (f"{session_id}.json", "session.json", "meta.json"):
        path = session_dir / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def _wav_time_range(session_dir: Path):
    wavs = [path for path in session_dir.glob("*.wav") if path.is_file()]
    if not wavs:
        return None, None
    times = [datetime.utcfromtimestamp(path.stat().st_mtime) for path in wavs]
    return min(times), max(times)


def _parse_meta_time(value: Any):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("Z", "")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _preview_speech_lines(opening_events, engine) -> list[str]:
    """剧本预览：开场已生成的台词 + 还没开演的主体行。"""
    lines: list[str] = []
    for event in opening_events or []:
        speech = str((event or {}).get("speech") or "").strip()
        if speech:
            lines.append(speech)
    show = getattr(getattr(engine, "state", None), "show", None)
    units = getattr(show, "units", None) or []
    for unit in units:
        for line in getattr(unit, "lines", None) or []:
            text = str(getattr(line, "text", "") or "").strip()
            if text:
                lines.append(text)
    return lines[:24]


def ensure_live_session_record(session_id: str, session_dir: Path, room_id: str | None = None) -> None:
    """对照服场次没有 LiveSession 时补一行，否则归档完写不出公开日记。"""
    try:
        from ..database.database import SessionLocal
        from ..database.models import User, Character, VoiceConfig, LLMModel
    except ImportError:
        from database.database import SessionLocal
        from database.models import User, Character, VoiceConfig, LLMModel

    db = SessionLocal()
    try:
        meta = _compat_meta(session_dir, session_id)
        existing = db.query(LiveSession).filter(LiveSession.session_id == session_id).first()
        if existing:
            if meta.get("topic"):
                existing.topic = str(meta["topic"])[:255]
                cached = dict(existing.session_metadata or {})
                diary = dict(cached.get("diary") or {})
                diary.pop("title", None)
                cached["diary"] = diary
                existing.session_metadata = cached
                try:
                    from sqlalchemy.orm.attributes import flag_modified
                    flag_modified(existing, "session_metadata")
                except Exception:
                    pass
            started = _parse_meta_time(meta.get("started_at"))
            ended = _parse_meta_time(meta.get("ended_at"))
            if started:
                existing.started_at = started
            if ended:
                existing.ended_at = ended
            db.commit()
            return
        inline_user = db.query(User).filter(User.username == "admin").first()
        llm_model = db.query(LLMModel).filter(LLMModel.is_default == True).first()
        if not inline_user or not llm_model:
            print(f"[diary] skip {session_id}: inline persistence is not initialized")
            return
        meta = _compat_meta(session_dir, session_id)
        character_name = str(meta.get("character_name") or "Echuu")
        topic = str(meta.get("topic") or "今晚这一场")[:255]
        voice_name = str(meta.get("voice") or "Cherry")
        character = db.query(Character).filter(
            Character.user_id == inline_user.id,
            Character.name == character_name,
        ).first()
        if not character:
            character = Character(
                user_id=inline_user.id,
                name=character_name,
                persona=str(meta.get("persona") or ""),
                background=str(meta.get("background") or ""),
                default_llm_model_id=llm_model.id,
            )
            db.add(character)
            db.flush()
        voice_config = db.query(VoiceConfig).filter(
            VoiceConfig.character_id == character.id,
            VoiceConfig.voice_name == voice_name,
        ).first()
        if not voice_config:
            voice_config = VoiceConfig(
                character_id=character.id,
                voice_name=voice_name,
                tts_model=os.getenv("TTS_MODEL", "qwen3-tts-flash-realtime"),
                is_default=False,
            )
            db.add(voice_config)
            db.flush()
        started_at = _parse_meta_time(meta.get("started_at"))
        ended_at = _parse_meta_time(meta.get("ended_at"))
        if started_at is None or ended_at is None:
            wav_start, wav_end = _wav_time_range(session_dir)
            started_at = started_at or wav_start
            ended_at = ended_at or wav_end
        db.add(LiveSession(
            session_id=session_id,
            user_id=inline_user.id,
            character_id=character.id,
            topic=topic,
            llm_model_id=llm_model.id,
            voice_config_id=voice_config.id,
            status=SessionStatus.COMPLETED,
            script_path=str(session_dir / "full_script.json"),
            audio_dir=str(session_dir),
            archive_status="pending",
            started_at=started_at or datetime.utcnow(),
            ended_at=ended_at,
            session_metadata={
                "source": "compat-archive",
                "room_id": room_id or meta.get("room_id"),
                "character_name": character_name,
                "persona": meta.get("persona") or "",
                "voice": voice_name,
                "diary": {"visibility": "public"},
            },
        ))
        db.commit()
    except Exception as exc:  # noqa: BLE001 — 补记录失败不挡归档
        print(f"[diary] ensure session failed: {exc}")
        db.rollback()
    finally:
        db.close()


def _finish_session_record(session_id: str, archive: dict | None = None, error: str | None = None) -> None:
    """Persist final status for both inline and DB-backed runs."""
    try:
        from ..database.database import SessionLocal
    except ImportError:
        from database.database import SessionLocal
    db = SessionLocal()
    try:
        record = db.query(LiveSession).filter(LiveSession.session_id == session_id).first()
        if not record:
            return
        if record.ended_at is None:
            record.ended_at = datetime.utcnow()
        record.status = SessionStatus.FAILED if error else SessionStatus.COMPLETED
        if archive:
            record.s3_prefix = archive.get("prefix")
            record.uploaded_count = int(archive.get("uploaded_count") or 0)
            record.archive_status = str(archive.get("status") or "failed")
            record.archive_error = archive.get("error")
        elif error:
            record.archive_status = "failed"
            record.archive_error = error[:2000]
        try:
            from .diary import persist_diary
        except ImportError:
            from services.diary import persist_diary
        try:
            persist_diary(record, db)
        except Exception as exc:  # noqa: BLE001 — 日记失败不回滚归档
            print(f"[diary] persist failed: {exc}")
            db.commit()
    finally:
        db.close()


class LiveService:
    """直播引擎服务"""

    @staticmethod
    async def archive_existing_session(session_id: str, room_id: str | None = None) -> dict:
        try:
            from .session_files import resolve_session_dir
        except ImportError:
            from services.session_files import resolve_session_dir
        session_dir = resolve_session_dir(session_id)
        ensure_live_session_record(session_id, session_dir, room_id=room_id)
        if room_id:
            try:
                from ..database.database import SessionLocal
            except ImportError:
                from database.database import SessionLocal
            db = SessionLocal()
            try:
                record = db.query(LiveSession).filter(LiveSession.session_id == session_id).first()
                recorded_room = (record.session_metadata or {}).get("room_id") if record else None
                # 对照服 / 房间已关的场次没有 LiveSession，不能因此拒绝重试。
                if record and recorded_room and recorded_room != room_id:
                    raise PermissionError("Session does not belong to this room")
            finally:
                db.close()
        archive = await archive_session_to_s3(session_id, session_dir)
        _finish_session_record(session_id, archive=archive)
        return archive

    @staticmethod
    async def run_live_engine_inline(
        character_name: str,
        persona: str,
        background: str,
        topic: str,
        voice: str,
        initial_danmaku: List[str],
        session_id: str,
        room_id: str,
        max_steps: int = 15,
        mode: str = "storytelling",
        source: Optional[dict] = None,
        persona_card: Optional[dict] = None,
    ):
        """
        运行直播引擎（内联模式）：跳过数据库，直接使用传入的角色配置。
        TTS 使用环境变量中配置的默认声音，voice 参数会覆盖 TTS_VOICE。

        mode: storytelling（默认）/ reaction / singing_learn。
        source: 模式专属输入，见 InlineStartRequest 注释。
        persona_card: 结构化人设卡（Phase 2-①，可选），贯穿剧本/开场收尾/弹幕回应。
        """
        state.is_running = True
        state.stop_requested = False
        state.reset_play_gate()
        session_dir = SCRIPTS_DIR / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        timeline = TimelineWriter(session_dir, mode, meta={
            "name": character_name,
            "topic": topic,
            "voice": voice,
            "source": source or {},
        })

        async def bcast(data: dict):
            await state.broadcast(data, room_id=room_id)

        spoken_lines: list[str] = []

        async def emit_step(result: dict, step_idx: int) -> float:
            """统一出口：音频落盘 → timeline 记录 → WS 广播。返回本句时长。"""
            audio_data = result.pop("audio", None)
            duration = result.pop("duration", None)
            if audio_data:
                response_format = os.getenv("TTS_RESPONSE_FORMAT", "pcm").lower()
                ext = ".wav" if response_format == "pcm" else f".{response_format}"
                audio_filename = f"step_{step_idx}{ext}"
                with open(session_dir / audio_filename, "wb") as f:
                    f.write(audio_data)
                result["audio_url"] = f"/audio/{session_id}/{audio_filename}"
                if duration is None:
                    duration = estimate_wav_duration(audio_data)
            ev = timeline.append(
                result,
                duration=duration or 0.0,
                t=result.pop("timeline_t", None),
            )
            await bcast({"type": "step", **ev})
            if ev.get("speech"):
                spoken_lines.append(str(ev["speech"]))
            return float(duration or 0.0)

        try:
            # 覆盖声音设置
            if voice:
                os.environ["TTS_VOICE"] = voice

            engine = EchuuLiveEngine()
            state.set_engine(engine)

            # 重置停止标志
            state.stop_requested = False

            # 预生产跑在 executor 线程里，on_phase 不能在线程内 get_event_loop()
            main_loop = asyncio.get_running_loop()
            engine.on_steering = lambda payload: asyncio.run_coroutine_threadsafe(
                bcast(payload), main_loop,
            )
            engine.on_token_hunt = lambda payload: asyncio.run_coroutine_threadsafe(
                bcast(payload), main_loop,
            )

            generation_active = True

            def on_phase(msg: str):
                if not generation_active:
                    return
                asyncio.run_coroutine_threadsafe(
                    bcast({"type": "reasoning", "content": msg}),
                    main_loop,
                )

            # Bound preparation only; the live performance may last longer.
            preparation_deadline = main_loop.time() + float(os.getenv('ECHUU_GENERATION_TIMEOUT', '40'))
            async with asyncio.timeout_at(preparation_deadline):
                # ============ 预生产阶段：opening 与主体并行，按模式产出事件 ============
                from itertools import chain
                from echuu.modes.rundown import produce_opening_events, produce_closing_events
                from echuu.modes.topic_brief import produce_topic_brief, merge_brief_into_background
                from echuu.core.persona_card import PersonaCard
                from echuu.live.tts_client import TTSClient

                loop = main_loop
                card = PersonaCard.from_dict(persona_card)

                current_background = background
                background = f"【本次用户设定·故事前提】{background}\n本次主题：{topic}\n保留用户人设与明确虚构设定；现实产品和新闻若与最新有来源的事实冲突，应更正旧背景，不得默认把真实事件当成虚构。"

                # 上一场记忆注入：同角色最近一次 session 的记忆摘要 →"上次说过…"是天然跨场 call-back
                prev_memory = _load_prev_memory_summary(character_name, exclude_session=session_id)
                if prev_memory:
                    background = f"{background}\n\n【历史记忆·低于本次设定，不能作为本次新经历】{prev_memory}" if background else \
                        f"【历史记忆·低于本次设定，不能作为本次新经历】{prev_memory}"
                    on_phase("已载入上一场直播的记忆")

                # 主题语义 grounding：联网确认网络流行语的真实用法（如"云养猫"），
                # 结果注入 background，opening 与三模式主体共享。失败/不支持时为空，不阻塞。
                on_phase("正在核实主题最新事实与来源…")
                topic_brief = await loop.run_in_executor(
                    None, lambda: produce_topic_brief(engine.llm, topic),
                )
                background = merge_brief_into_background(background, topic, topic_brief)
                if topic_brief:
                    on_phase(f"主题背景已就绪：{topic_brief[:48]}…")
                # rundown 用独立 TTS client：与主体预生产并行跑，共享 client 的
                # set_instruction 会串音
                rundown_tts = TTSClient()

                def produce_opening_safe():
                    try:
                        return produce_opening_events(
                            engine, character_name, persona, topic, mode,
                            on_phase=on_phase, tts=rundown_tts, card=card, background=merge_brief_into_background(current_background, topic, topic_brief),
                        )
                    except Exception as exc:  # opening 失败不阻塞开播
                        print(f"[rundown] opening 生成失败（跳过）: {exc}")
                        return []

                if mode == "reaction":
                    from echuu.modes.reaction import produce_reaction_events
                    opening_events, events = await asyncio.gather(
                        loop.run_in_executor(None, produce_opening_safe),
                        loop.run_in_executor(
                            None,
                            lambda: produce_reaction_events(
                                engine, character_name, persona, background, topic,
                                source or {}, on_phase=on_phase,
                            ),
                        ),
                    )
                    generator = iter(events)
                elif mode == "singing_learn":
                    from echuu.modes.singing import produce_singing_events
                    opening_events, events = await asyncio.gather(
                        loop.run_in_executor(None, produce_opening_safe),
                        loop.run_in_executor(
                            None,
                            lambda: produce_singing_events(
                                engine, character_name, persona, background, topic,
                                source or {}, session_dir, on_phase=on_phase,
                            ),
                        ),
                    )
                    generator = iter(events)
                else:
                    # storytelling（默认）：现有引擎路径，保留弹幕实时穿插
                    # engine.state 要等 setup() 之后才存在，弹幕必须在 setup 完成后注入
                    from echuu.modes.preparation import wait_for_script
                    opening_future = loop.run_in_executor(None, produce_opening_safe)
                    body_future = loop.run_in_executor(
                        None,
                        lambda: engine.setup(
                            name=character_name,
                            persona=persona,
                            background=background,
                            topic=topic,
                            on_phase_callback=on_phase,
                            persona_card=persona_card,
                        ),
                    )
                    # Reserve time for ready: optional greeting audio must not
                    # throw away a complete script when TTS is slow.
                    opening_events = await wait_for_script(
                        body_future, opening_future,
                        deadline=preparation_deadline,
                    )
                    if not opening_events:
                        on_phase("正文已就绪，直接进入主题")
                    for dm_text in (initial_danmaku or []):
                        dm = Danmaku.from_text(dm_text, user="观众")
                        engine.state.danmaku_queue.append(dm)

                    # 复制剧本到 session 目录
                    script_sources = sorted(engine.scripts_dir.glob("*.json"), key=os.path.getmtime)
                    if script_sources:
                        shutil.copy(script_sources[-1], session_dir / "full_script.json")

                    # 落盘人物小传（供回看/评测；失败不阻塞）
                    try:
                        if getattr(engine, "dossier", None):
                            import json as _json
                            from dataclasses import asdict as _asdict
                            (session_dir / "dossier.json").write_text(
                                _json.dumps(_asdict(engine.dossier), ensure_ascii=False, indent=2),
                                encoding="utf-8",
                            )
                    except Exception as exc:  # noqa: BLE001
                        print(f"[dossier] 落盘失败（跳过）: {exc}")

                    from echuu.live.background_interactions import BackgroundInteractions
                    engine.background_interactions = BackgroundInteractions(engine)
                    generator = engine.run(max_steps=max_steps, play_audio=False, save_audio=True)

                # 开场段先播（stage='opening'，前端按 stage 走 sequential 通道）
                generator = chain(opening_events, generator)

            if mode == "storytelling":
                on_phase("剧本已就绪，正在预生成原稿语音…")
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(engine.prepare_show_audio, on_phase), timeout=60,
                    )
                except TimeoutError as exc:
                    # Provider connection timeouts and the preparation deadline
                    # are speech failures, not script generation failures.
                    raise RuntimeError("语音服务连接或预生成超时，请重试") from exc
                on_phase("原稿语音已就绪，互动只重生成改写的句子")
            generation_active = False
            await bcast({
                "type": "ready",
                "content": f"剧本已生成，Session: {session_id}",
                "session_id": session_id,
                "mode": mode,
                "preview": _preview_speech_lines(opening_events, engine),
            })

            # 等房主点「开始直播」再往下演，否则整场会在预览期间提前生成完。
            await state.wait_for_play()
            if state.stop_requested:
                await bcast({
                    "type": "success",
                    "session_id": session_id,
                    "content": "表演已取消",
                })
                return

            # ============ 表演阶段：统一事件循环 ============
            step_idx = 0
            opening_reply = None
            while not state.stop_requested:
                if opening_reply is not None:
                    result, opening_reply = opening_reply, None
                else:
                    result = await asyncio.to_thread(next, generator, None)
                if result is None or state.stop_requested:
                    break

                spoken_for = await emit_step(result, step_idx)

                # 广播 memory 事件（storytelling 专属）
                if mode == "storytelling" and engine.state:
                    memory = engine.state.memory
                    await bcast({
                        "type": "memory",
                        "memory": {
                            "current_topic": engine.state.topic,
                            "topic_history": getattr(engine.state, "topic_history", []),
                            "story_points": memory.story_points.get("mentioned", []),
                            "promises": [p for p in memory.promises if not p.get("fulfilled")],
                            "emotion_trend": [e["level"] for e in memory.emotion_track[-5:]],
                        },
                    })

                step_idx += 1
                # 跟着台词时长走，留一点余量给前端排队，但不要整场提前演完。
                playback_until = loop.time() + max(0.2, (spoken_for or 1.2) * 0.92)
                while not state.stop_requested and loop.time() < playback_until:
                    background = getattr(engine, "background_interactions", None)
                    if background is not None:
                        background.tick()
                    await asyncio.sleep(min(0.1, max(0, playback_until - loop.time())))
                if result.get("stage") == "opening" and getattr(engine, "background_interactions", None):
                    opening_reply = engine.background_interactions.boundary(0, commit_allowed=False)

            background = getattr(engine, "background_interactions", None)
            if background is not None:
                background.close()
            engine.accepting_interactions = False
            # ============ 收尾段：自然结束才播，用户主动 stop 跳过 ============
            if not state.stop_requested:
                def produce_closing_safe():
                    try:
                        return produce_closing_events(
                            engine, character_name, persona, engine.state.topic if engine.state else topic, mode,
                            on_phase=on_phase, tts=rundown_tts, card=card,
                            background=merge_brief_into_background(current_background, topic, topic_brief), spoken_lines=spoken_lines,
                        )
                    except Exception as exc:  # closing 失败不阻塞收播
                        print(f"[rundown] closing 生成失败（跳过）: {exc}")
                        return []

                for result in await loop.run_in_executor(None, produce_closing_safe):
                    if state.stop_requested:
                        break
                    await emit_step(result, step_idx)
                    step_idx += 1

            await bcast({
                "type": "success",
                "session_id": session_id,
                "content": "表演圆满结束",
            })

            # 记忆落盘：下一场同角色开播时注入"上次说过…"
            if mode == "storytelling" and engine.state:
                _dump_session_memory(engine, character_name, session_dir)

            # 归档音频+脚本到 S3（失败只 log 不中断）
            archive = {
                "prefix": f"streaming_content/{session_id}/",
                "uploaded_count": 0,
                "status": "failed",
                "error": "archive did not run",
            }
            try:
                archive = await archive_session_to_s3(session_id, session_dir)
            except Exception as archive_err:
                print(f"[archive] S3 归档失败（不影响直播结束）: {archive_err}")
                archive = {
                    "prefix": f"streaming_content/{session_id}/",
                    "uploaded_count": 0,
                    "status": "failed",
                    "error": str(archive_err)[:2000],
                }
            _finish_session_record(session_id, archive=archive)
            await bcast({
                "type": "archived",
                "session_id": session_id,
                **archive,
            })

        except Exception as e:
            generation_active = False
            _finish_session_record(session_id, error=str(e))
            await bcast({"type": "error", "content": "剧本生成超时，请重试" if isinstance(e, TimeoutError) else str(e)})
            print(traceback.format_exc())
        finally:
            for tts_owner in (locals().get("engine"),):
                if tts_owner and getattr(tts_owner, "background_interactions", None):
                    tts_owner.background_interactions.close()
                if tts_owner and getattr(tts_owner, "tts", None):
                    tts_owner.tts.close_preparation()
            if locals().get("rundown_tts"):
                rundown_tts.close_preparation()
            state.is_running = False
            state.clear_engine()

    @staticmethod
    async def run_live_engine(
        character_id: str,
        config: LiveConfig,
        session_id: str,
        user_id: str
    ):
        """
        运行直播引擎任务（数据库模式）

        Args:
            character_id: 角色ID
            config: 直播配置
            session_id: Session ID
            user_id: 用户ID
        """
        state.is_running = True
        session_dir = SCRIPTS_DIR / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        try:
            from ..database.database import SessionLocal
        except ImportError:
            from database.database import SessionLocal

        db = SessionLocal()
        live_session = None

        room_id = state.current_room_id

        async def bcast(data: dict):
            await state.broadcast(data, room_id=room_id)

        try:
            character = db.query(Character).filter(Character.id == character_id).first()
            if not character:
                raise ValueError(f"角色不存在: {character_id}")

            voice_config_id = config.voice_config_id
            if voice_config_id:
                voice_config = db.query(VoiceConfig).filter(VoiceConfig.id == voice_config_id).first()
            else:
                voice_config = db.query(VoiceConfig).filter(
                    VoiceConfig.character_id == character_id,
                    VoiceConfig.is_default == True
                ).first()

            if not voice_config:
                raise ValueError("未找到声音配置")

            llm_model_id = config.llm_model_id or character.default_llm_model_id
            if llm_model_id:
                llm_model = db.query(LLMModel).filter(LLMModel.id == llm_model_id).first()
            else:
                llm_model = db.query(LLMModel).filter(LLMModel.is_default == True).first()

            if not llm_model:
                raise ValueError("未找到LLM模型")

            live_session = LiveSession(
                session_id=session_id,
                user_id=user_id,
                character_id=character_id,
                topic=config.topic,
                llm_model_id=llm_model.id,
                voice_config_id=voice_config.id,
                vtuber_3d_model_id=config.vtuber_3d_model_id,
                status=SessionStatus.RUNNING,
                script_path=str(session_dir / "full_script.json"),
                audio_dir=str(session_dir),
                started_at=datetime.utcnow(),
            )
            db.add(live_session)
            db.commit()
            db.refresh(live_session)

            os.environ["TTS_MODEL"] = voice_config.tts_model
            os.environ["TTS_VOICE"] = voice_config.voice_name

            if llm_model.api_key_env:
                api_key = os.getenv(llm_model.api_key_env)
                if api_key:
                    os.environ["DEFAULT_MODEL"] = llm_model.model_id

            engine = EchuuLiveEngine()
            state.set_engine(engine)

            db_main_loop = asyncio.get_running_loop()
            engine.on_steering = lambda payload: asyncio.run_coroutine_threadsafe(
                bcast(payload), db_main_loop,
            )
            engine.on_token_hunt = lambda payload: asyncio.run_coroutine_threadsafe(
                bcast(payload), db_main_loop,
            )

            def on_phase(msg: str):
                asyncio.run_coroutine_threadsafe(
                    bcast({"type": "reasoning", "content": msg}),
                    db_main_loop,
                )

            engine.setup(
                name=character.name,
                persona=character.persona,
                background=character.background or "",
                topic=config.topic,
                on_phase_callback=on_phase,
            )

            script_sources = sorted(engine.scripts_dir.glob("*.json"), key=os.path.getmtime)
            if script_sources:
                shutil.copy(script_sources[-1], session_dir / "full_script.json")

            await bcast({
                "type": "ready",
                "content": f"剧本已生成并归档至 {session_id}",
                "session_id": session_id,
            })

            state.stop_requested = False
            step_idx = 0
            for result in engine.run(
                max_steps=config.max_steps,
                play_audio=False,
                save_audio=True
            ):
                if state.stop_requested:
                    break

                audio_data = result.get("audio")
                if audio_data:
                    response_format = os.getenv("TTS_RESPONSE_FORMAT", "pcm").lower()
                    ext = ".wav" if response_format == "pcm" else f".{response_format}"
                    audio_filename = f"step_{step_idx}{ext}"
                    with open(session_dir / audio_filename, "wb") as f:
                        f.write(audio_data)
                    result["audio_url"] = f"/audio/{session_id}/{audio_filename}"
                    result.pop("audio", None)

                # 广播 step 事件（扁平化）
                await bcast({"type": "step", **result})

                # 广播 memory 事件
                memory = engine.state.memory
                await bcast({
                    "type": "memory",
                    "memory": {
                        "story_points": memory.story_points.get("mentioned", []),
                        "promises": [p for p in memory.promises if not p.get("fulfilled")],
                        "emotion_trend": [e["level"] for e in memory.emotion_track[-5:]],
                    },
                })

                step_idx += 1
                await asyncio.sleep(0.1)

            await bcast({
                "type": "success",
                "session_id": session_id,
                "content": "表演圆满结束",
            })

            # 归档音频+脚本到 S3（失败只 log 不中断）
            archive = {
                "prefix": f"streaming_content/{session_id}/",
                "uploaded_count": 0,
                "status": "failed",
                "error": "archive did not run",
            }
            try:
                archive = await archive_session_to_s3(session_id, session_dir)
            except Exception as archive_err:
                print(f"[archive] S3 归档失败（不影响直播结束）: {archive_err}")
                archive = {
                    "prefix": f"streaming_content/{session_id}/",
                    "uploaded_count": 0,
                    "status": "failed",
                    "error": str(archive_err)[:2000],
                }
            await bcast({
                "type": "archived",
                "session_id": session_id,
                **archive,
            })

            live_session.status = SessionStatus.COMPLETED
            live_session.ended_at = datetime.utcnow()
            live_session.s3_prefix = archive.get("prefix")
            live_session.uploaded_count = int(archive.get("uploaded_count") or 0)
            live_session.archive_status = str(archive.get("status") or "failed")
            live_session.archive_error = archive.get("error")
            try:
                from .diary import persist_diary
            except ImportError:
                from services.diary import persist_diary
            try:
                persist_diary(live_session, db)
            except Exception as exc:  # noqa: BLE001
                print(f"[diary] persist failed: {exc}")
                db.commit()

        except Exception as e:
            if live_session:
                live_session.status = SessionStatus.FAILED
                live_session.ended_at = datetime.utcnow()
                live_session.archive_status = "failed"
                live_session.archive_error = str(e)[:2000]
                db.commit()
            await bcast({"type": "error", "content": "剧本生成超时，请重试" if isinstance(e, TimeoutError) else str(e)})
            print(traceback.format_exc())

            if live_session:
                try:
                    live_session.status = SessionStatus.FAILED
                    live_session.ended_at = datetime.utcnow()
                    db.commit()
                except Exception:
                    db.rollback()
        finally:
            state.is_running = False
            state.clear_engine()
            if db:
                db.close()

    @staticmethod
    def inject_danmaku(
        text: str,
        user: str = "观众",
        *,
        kind: str = "chat",
        gift_id: str = "",
        client_id: str = "",
        amount: int = 0,
        entities: list | None = None,
    ):
        """注入弹幕、投喂、线索收集或跑毛卡。"""
        if not state.is_running or not state.current_engine:
            raise ValueError("直播未运行")

        engine = state.current_engine
        if not getattr(engine, "accepting_interactions", True):
            raise ValueError("直播正在收尾，已停止接收互动")
        if not getattr(engine, "state", None):
            raise ValueError("直播尚未就绪")

        dm = Danmaku.from_input(
            text,
            user=user,
            kind=kind,
            gift_id=gift_id,
            client_id=client_id,
            amount=amount,
            entities=entities,
        )
        if dm.kind == "collect":
            from echuu.live.story_tokens import normalize_token
            token = normalize_token(
                dm.entities[0] if dm.entities else None,
                dm.text,
            )
            if token:
                engine.collect_story_token(token)
            dm.status = "applied"
            return dm
        if dm.kind == "tangent":
            from echuu.live.story_tokens import compose_tangent_text
            card = engine.consume_tangent_card()
            if not card:
                raise ValueError("还没有跑毛点")
            dm.entities = card
            dm.text = compose_tangent_text(card)
        engine.state.danmaku_queue.append(dm)
        engine.emit_steering(dm, "queued")
        return dm
