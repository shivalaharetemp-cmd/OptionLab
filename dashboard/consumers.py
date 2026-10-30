import asyncio
import json

from channels.generic.websocket import AsyncWebsocketConsumer

from analytics.state import STATE
from . import payload


class LiveConsumer(AsyncWebsocketConsumer):
    """Pushes a light `live` frame every second and a full `chain` frame whenever a new option-chain snapshot arrives."""

    async def connect(self):
        self.sym, self.cv, self.task = None, -1, None
        await self.accept()
        self.task = asyncio.create_task(self._loop())

    async def disconnect(self, code):
        if self.task:
            self.task.cancel()

    async def receive(self, text_data=None, bytes_data=None):
        try:
            msg = json.loads(text_data)
        except ValueError:
            return
        if msg.get("symbol"):
            self.sym, self.cv = msg["symbol"], -1      # force a fresh chain frame

    async def _loop(self):
        try:
            while True:
                if self.sym:
                    live = payload.live_payload(self.sym)
                    if live["chain_version"] != self.cv:
                        self.cv = live["chain_version"]
                        await self.send(json.dumps({"t": "chain", **payload.chain_payload(self.sym)}, default=str))
                    await self.send(json.dumps(live, default=str))
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
