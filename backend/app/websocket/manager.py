"""WebSocket connection manager."""

import asyncio
from typing import Dict, List
from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.event_buffers: Dict[str, List[dict]] = {}

    async def connect(self, job_id: str, ws: WebSocket):
        await ws.accept()
        self.active_connections[job_id] = ws
        buffer = self.event_buffers.pop(job_id, [])
        for event in buffer:
            try:
                await ws.send_json(event)
            except Exception:
                break

    def disconnect(self, job_id: str):
        self.active_connections.pop(job_id, None)

    async def send(self, job_id: str, data: dict):
        ws = self.active_connections.get(job_id)
        if ws:
            try:
                await ws.send_json(data)
            except Exception:
                self.disconnect(job_id)
        else:
            self.event_buffers.setdefault(job_id, []).append(data)

    def clear_buffer(self, job_id: str):
        self.event_buffers.pop(job_id, None)


manager = ConnectionManager()
