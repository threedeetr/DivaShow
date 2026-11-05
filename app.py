"""Diva CANLI mobile live bridge.

Unrelated to TikTok/ByteDance. Handles a single broadcaster at a time.
The optional TikTokLive dependency is imported on a real connect request.
"""
from __future__ import annotations

import asyncio
import hmac
import os
import re
import time
from collections import deque
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from pydantic import BaseModel, Field
from diva_voice import SpeechRequest, synthesize_emel, enhanced_page
from diva_ai import AiReplyRequest, generate_ai_reply, enhanced_ai_page

ROOT = Path(__file__).resolve().parent
APP_PIN = os.environ.get("DIVA_PIN", "").strip()
USERNAME_RE = re.compile(r"^@?[a-zA-Z0-9._]{2,30}$")

app = FastAPI(title="Diva CANLI bridge", version="1.0")

feed: deque[dict] = deque(maxlen=400)
sequence = 0
state = {"phase": "idle", "username": "", "error": "", "connected": False}
client = None
runner: asyncio.Task | None = None
generation = 0
connect_lock = asyncio.Lock()


def authorize(pin: str | None) -> None:
    # Never silently expose a writable API on a public URL.
    if not APP_PIN:
        raise HTTPException(503, "Sunucuda DIVA_PIN ayarlanmamış. Render Environment bölümüne ekleyin.")
    if not pin or not hmac.compare_digest(str(pin), APP_PIN):
        raise HTTPException(401, "Yönetici kodu yanlış.")


def clean_text(value: object, length: int) -> str:
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()[:length]


def add_event(event_type: str, **fields: object) -> dict:
    global sequence
    sequence += 1
    event = {"id": sequence, "type": event_type, "at": time.time(), **fields}
    feed.append(event)
    return event


def username_of(user: object) -> str:
    return clean_text(getattr(user, "unique_id", "") or getattr(user, "nickname", "") or "Izleyici", 34)


async def live_worker(name: str, my_generation: int) -> None:
    global client
    try:
        from TikTokLive import TikTokLiveClient
        from TikTokLive.events import CommentEvent, ConnectEvent, DisconnectEvent, GiftEvent

        live = TikTokLiveClient(unique_id=name)
        client = live

        @live.on(ConnectEvent)
        async def on_connect(_event: object) -> None:
            if my_generation == generation:
                state.update(phase="connected", connected=True, error="")
                add_event("system", text=f"@{name} yayınına bağlantı kuruldu.")

        @live.on(DisconnectEvent)
        async def on_disconnect(_event: object) -> None:
            if my_generation == generation:
                state.update(phase="disconnected", connected=False)
                add_event("system", text="TikTok bağlantısı kesildi.")

        @live.on(CommentEvent)
        async def on_comment(event: object) -> None:
            if my_generation != generation:
                return
            message = clean_text(getattr(event, "comment", ""), 240)
            if message:
                add_event("comment", username=username_of(getattr(event, "user", None)), text=message)

        @live.on(GiftEvent)
        async def on_gift(event: object) -> None:
            if my_generation != generation or bool(getattr(event, "streaking", False)):
                return
            gift = getattr(event, "gift", None)
            if gift is None:
                return
            try:
                count = max(1, min(999, int(getattr(event, "repeat_count", 1) or 1)))
            except (ValueError, TypeError):
                count = 1
            diamond_raw = getattr(gift, "diamond_count", None)
            try:
                diamonds = max(0, int(diamond_raw)) if diamond_raw is not None else None
            except (ValueError, TypeError):
                diamonds = None
            add_event(
                "gift",
                username=username_of(getattr(event, "user", None)),
                gift=clean_text(getattr(gift, "name", "Hediye"), 70),
                count=count,
                diamonds=diamonds,
            )

        # This blocks for the duration of the LIVE connection.
        await live.connect()
        if my_generation == generation:
            state.update(phase="disconnected", connected=False)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if my_generation == generation:
            # Do not leak authentication details / cookies in error strings.
            message = clean_text(type(exc).__name__ + ": " + str(exc), 180)
            state.update(phase="error", connected=False, error=message)
            add_event("system", text="Bağlantı kurulamadı: " + message)
    finally:
        if my_generation == generation:
            client = None


class ConnectBody(BaseModel):
    username: str = Field(min_length=2, max_length=40)


class DemoBody(BaseModel):
    type: Literal["comment", "gift"] = "comment"


@app.get("/")
def home():
    return HTMLResponse(enhanced_ai_page(enhanced_page(str(ROOT / "index.html"))), headers={"Cache-Control": "no-store"})


@app.post("/api/tts")
async def tts(body: SpeechRequest, x_diva_pin: str | None = Header(None)):
    authorize(x_diva_pin)
    return await synthesize_emel(body)


@app.post("/api/ai/reply")
async def ai_reply(body: AiReplyRequest, x_diva_pin: str | None = Header(None)):
    authorize(x_diva_pin)
    return await generate_ai_reply(body)


@app.get("/health")
def health():
    return {"ok": True, "service": "Diva CANLI"}


@app.get("/api/status")
def api_status(x_diva_pin: str | None = Header(None)):
    authorize(x_diva_pin)
    return {**state, "latest_id": sequence}


@app.get("/api/events")
def api_events(
    after: int = Query(0, ge=0),
    x_diva_pin: str | None = Header(None),
):
    authorize(x_diva_pin)
    selected = [evt for evt in feed if evt["id"] > after]
    return {"events": selected[:100], "latest_id": sequence, "phase": state["phase"]}


@app.post("/api/connect")
async def api_connect(body: ConnectBody, x_diva_pin: str | None = Header(None)):
    global runner, client, generation
    authorize(x_diva_pin)
    name = body.username.strip().removeprefix("@")
    if not USERNAME_RE.fullmatch(name):
        raise HTTPException(422, "Yalnızca TikTok kullanıcı adını yaz (örnek: diva123).")
    async with connect_lock:
        generation += 1
        if client is not None:
            try:
                await asyncio.wait_for(client.disconnect(), timeout=5)
            except Exception:
                pass
        if runner is not None and not runner.done():
            runner.cancel()
        client = None
        state.update(phase="connecting", username=name, connected=False, error="")
        runner = asyncio.create_task(live_worker(name, generation))
    return {"ok": True, "phase": "connecting", "username": name}


@app.post("/api/disconnect")
async def api_disconnect(x_diva_pin: str | None = Header(None)):
    global runner, client, generation
    authorize(x_diva_pin)
    async with connect_lock:
        generation += 1
        if client is not None:
            try:
                await asyncio.wait_for(client.disconnect(), timeout=5)
            except Exception:
                pass
        if runner is not None and not runner.done():
            runner.cancel()
        runner = None
        client = None
        state.update(phase="idle", username="", connected=False, error="")
    return {"ok": True, "phase": "idle"}


@app.post("/api/demo")
def api_demo(body: DemoBody, x_diva_pin: str | None = Header(None)):
    authorize(x_diva_pin)
    if body.type == "gift":
        event = add_event("gift", username="DemoIzleyici", gift="Rose", count=3, diamonds=None)
    else:
        event = add_event("comment", username="DemoIzleyici", text="Diva bugün nasılsın?")
    return {"ok": True, "event": event}
