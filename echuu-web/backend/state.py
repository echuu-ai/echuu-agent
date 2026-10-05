"""全局状态管理"""
import json
import asyncio
import uuid
from typing import Optional, Dict, Set, Tuple
from fastapi import WebSocket

from echuu.live.engine import EchuuLiveEngine


class GlobalStateManager:
    """全局状态管理器"""

    def __init__(self):
        self.current_session = None
        self.is_running = False
        self.active_session_id: Optional[str] = None
        self.active_connections: Dict[str, WebSocket] = {}
        self.realtime_danmaku_queue = asyncio.Queue()
        self.current_engine: Optional[EchuuLiveEngine] = None

        # Room management
        self.rooms: Dict[str, Set[str]] = {}          # room_id → set of ws_ids
        self.room_tokens: Dict[str, str] = {}          # room_id → owner_token
        self.current_room_id: Optional[str] = None     # the active broadcast room

        # Stop signal — set to True to request graceful early stop
        self.stop_requested: bool = False
        # Host must click 开始直播 before the engine consumes the show.
        # Without this gate the generator races ahead of playback and later
        # gifts / danmaku / tangents hit "直播未运行".
        self._play_gate: Optional[asyncio.Event] = None

    # ------------------------------------------------------------------
    # Room management
    # ------------------------------------------------------------------

    def create_room(self) -> Tuple[str, str]:
        """创建新房间，返回 (room_id, owner_token)"""
        room_id = str(uuid.uuid4())
        owner_token = str(uuid.uuid4())
        self.rooms[room_id] = set()
        self.room_tokens[room_id] = owner_token
        return room_id, owner_token

    def room_exists(self, room_id: str) -> bool:
        return room_id in self.rooms

    def verify_owner(self, room_id: str, owner_token: str) -> bool:
        return self.room_tokens.get(room_id) == owner_token

    def get_online_count(self, room_id: str) -> int:
        ws_ids = self.rooms.get(room_id, set())
        # Only count open connections
        count = 0
        for ws_id in ws_ids:
            ws = self.active_connections.get(ws_id)
            if ws is not None:
                count += 1
        return count

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def add_connection(self, websocket: WebSocket, room_id: Optional[str] = None) -> str:
        """添加 WebSocket 连接；如果 room_id 有效则加入房间"""
        ws_id = str(uuid.uuid4())
        self.active_connections[ws_id] = websocket
        if room_id and room_id in self.rooms:
            self.rooms[room_id].add(ws_id)
        return ws_id

    def remove_connection(self, ws_id: str):
        """移除 WebSocket 连接，同时从所有房间中清除"""
        self.active_connections.pop(ws_id, None)
        for ws_ids in self.rooms.values():
            ws_ids.discard(ws_id)

    # ------------------------------------------------------------------
    # Broadcasting
    # ------------------------------------------------------------------

    async def broadcast(self, data: dict, room_id: Optional[str] = None):
        """向指定房间（或所有连接）广播消息；发送失败的连接自动忽略"""
        clean_data = json.loads(json.dumps(data, default=str))

        if room_id:
            ws_ids = self.rooms.get(room_id, set())
            connections = {k: v for k, v in self.active_connections.items() if k in ws_ids}
        else:
            connections = self.active_connections

        dead = []
        for ws_id, ws in list(connections.items()):
            try:
                await ws.send_json(clean_data)
            except Exception:
                dead.append(ws_id)

        # Clean up dead connections
        for ws_id in dead:
            self.remove_connection(ws_id)

    async def broadcast_to_room(self, data: dict):
        """广播到 current_room_id（由 start_live 设置）"""
        await self.broadcast(data, room_id=self.current_room_id)

    # ------------------------------------------------------------------
    # Engine management
    # ------------------------------------------------------------------

    def set_engine(self, engine: EchuuLiveEngine):
        self.current_engine = engine

    def clear_engine(self):
        self.current_engine = None

    def reset_play_gate(self) -> None:
        self._play_gate = asyncio.Event()

    def request_play(self) -> None:
        if self._play_gate is not None:
            self._play_gate.set()

    def play_requested(self) -> bool:
        return bool(self._play_gate and self._play_gate.is_set())

    async def wait_for_play(self) -> None:
        if self._play_gate is None:
            return
        await self._play_gate.wait()


# 全局状态实例
state = GlobalStateManager()
