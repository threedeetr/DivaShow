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

# Protect the shared TikTok signing service: one LIVE socket, no rapid retries.
MIN_CONNECT_GAP = 45  # seconds between *new* signing attempts
RATE_LIMIT_FALLBACK = 3600  # only if the provider did not supply a retry time
last_connect_attempt_at = 0.0
rate_limit_retry_at = 0.0


def seconds_until(timestamp: float) -> int:
    return max(0, int(timestamp - time.time() + 0.999))


def retry_from_provider(exc: Exception) -> float:
    """Respect the TikTokLive provider's retry_after/reset_time if available."""
    now = time.time()
    deadlines = []
    raw_after = getattr(exc, "retry_after", None)
    try:
        if raw_after is not None and float(raw_after) > 0:
            deadlines.append(now + float(raw_after))
    except (TypeError, ValueError, OverflowError):
        pass
    raw_reset = getattr(exc, "reset_time", None)
    try:
        if raw_reset is not None and float(raw_reset) > now:
            deadlines.append(float(raw_reset))
    except (TypeError, ValueError, OverflowError):
        pass
    return max(deadlines) if deadlines else now + RATE_LIMIT_FALLBACK


def is_sign_rate_limit(exc: Exception) -> bool:
    name = type(exc).__name__
    detail = str(exc)
    return (name == "SignatureRateLimitError" or
            ("RATE_LIMIT" in detail and "TikTokLive" in detail))


def connection_status() -> dict:
    retry = seconds_until(rate_limit_retry_at)
    gap = seconds_until(last_connect_attempt_at + MIN_CONNECT_GAP)
    return {**state, "latest_id": sequence,
            "cooldown_seconds": retry,
            "reconnect_wait_seconds": max(retry, gap),
            "can_connect": retry == 0 and gap == 0 and state["phase"] not in ("connecting", "connected")}


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


def welcome_name(user: object) -> str:
    """A short pronounceable display name; never read arbitrary profile text aloud."""
    for source in (getattr(user, "nickname", ""), getattr(user, "unique_id", "")):
        raw = clean_text(source, 48)
        value = re.sub(r"[^A-Za-z0-9ÇĞİÖŞÜçğıöşü ._-]", "", raw)
        value = re.sub(r"\s+", " ", value).strip(" ._-")
        if 2 <= len(value) <= 22 and re.search(r"[A-Za-zÇĞİÖŞÜçğıöşü]{2,}", value):
            return value
    return ""


async def live_worker(name: str, my_generation: int) -> None:
    global client, rate_limit_retry_at
    try:
        from TikTokLive import TikTokLiveClient
        from TikTokLive.events import CommentEvent, ConnectEvent, DisconnectEvent, GiftEvent, JoinEvent, RoomUserSeqEvent

        live = TikTokLiveClient(unique_id=name)
        client = live

        # Per-LIVE welcome controls. Never confuse cumulative joins with current viewers.
        welcome = {"viewers": None, "viewer_at": 0.0, "last_at": 0.0, "started": time.monotonic()}
        welcomed: set[str] = set()
        recent_joins: deque[float] = deque(maxlen=30)

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

        @live.on(RoomUserSeqEvent)
        async def on_viewer_count(event: object) -> None:
            if my_generation != generation:
                return
            try:
                count = int(getattr(event, "total", None))
            except (TypeError, ValueError, OverflowError):
                return
            if 0 < count <= 100000:
                welcome["viewers"] = count
                welcome["viewer_at"] = time.monotonic()
                state["viewer_count"] = count

        @live.on(JoinEvent)
        async def on_join(event: object) -> None:
            if my_generation != generation or state["phase"] != "connected":
                return
            now = time.monotonic()
            recent_joins.append(now)
            while recent_joins and now - recent_joins[0] > 30:
                recent_joins.popleft()
            viewers = welcome["viewers"]
            # If the count is unavailable/stale, don't assume a small room.
            if (viewers is None or now - welcome["viewer_at"] > 100
                    or viewers > 25 or now - welcome["started"] < 15):
                return
            # A crowded entry burst should not trigger a stream of greetings.
            if len(recent_joins) >= 5:
                return
            interval = 50 if viewers <= 10 else 150
            if now - welcome["last_at"] < interval:
                return
            user = getattr(event, "user", None)
            if user is None:
                return
            uid = clean_text(getattr(user, "unique_id", ""), 40).casefold()
            if not uid or uid in welcomed:
                return
            display = welcome_name(user)
            if not display:
                return
            welcomed.add(uid)
            welcome["last_at"] = now
            add_event("welcome", username=display, viewer_count=viewers)

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
            if is_sign_rate_limit(exc):
                # Provider limit applies even if the user disconnects. Do not bypass it.
                rate_limit_retry_at = max(rate_limit_retry_at, retry_from_provider(exc))
                remaining = seconds_until(rate_limit_retry_at)
                message = ("TikTok bağlantı limiti doldu. "
                           f"Yaklaşık {(remaining + 59) // 60} dakika sonra yeniden dene.")
                state.update(phase="cooldown", connected=False, error=message)
            else:
                # Avoid leaking sensitive session/cookie or token details in logs/UI.
                message = clean_text(type(exc).__name__ + ": " + str(exc), 160)
                state.update(phase="error", connected=False, error=message)
            add_event("system", text="Bağlantı kurulamadı: " + message)
    finally:
        if my_generation == generation:
            client = None


class ConnectBody(BaseModel):
    username: str = Field(min_length=2, max_length=40)


class DemoBody(BaseModel):
    type: Literal["comment", "gift"] = "comment"


def enhanced_connection_page(html: str) -> str:
    """Small client guard in served HTML. Keep the original 6-mimic HTML untouched."""
    old = "const status=await bridgeRequest('/api/status');"
    assert html.count(old) == 1, "Cannot locate Diva status UI anchor"
    html = html.replace(old, old + r"""
    // Update the button from server truth, not from browser-side guesses.
    const connectButton=$('bridgeConnect');
    const held=Number(status.reconnect_wait_seconds||0);
    const connected=status.phase==='connected';
    const connecting=status.phase==='connecting';
    connectButton.disabled=connected||connecting||held>0;
    connectButton.style.opacity=connectButton.disabled?'0.56':'1';
    connectButton.style.cursor=connectButton.disabled?'not-allowed':'pointer';
    connectButton.textContent=connected?'✅ Bağlı':connecting?'⏳ Bağlanıyor…':held>0?'⏳ '+(held<60?held+' sn':Math.ceil(held/60)+' dk')+' bekle':'🔗 Canlıya bağlan';
""", 1)
    old = "else if(status.phase==='error')bridgeSetStatus('⚠ '+String(status.error||'Bağlanamadı').slice(0,100),'error');"
    assert html.count(old) == 1, "Cannot locate Diva error UI anchor"
    html = html.replace(old, "else if(status.phase==='cooldown')bridgeSetStatus('⏳ '+String(status.error||'TikTok bağlantı limiti').slice(0,130),'error');\n    " + old, 1)
    old = "try{await bridgeRequest('/api/connect','POST',{username:liveUserInput.value.trim()});bridge.lastId=0;bridge.joined=true;"
    assert html.count(old) == 1, "Cannot locate Diva connect UI anchor"
    html = html.replace(old, "try{const connection=await bridgeRequest('/api/connect','POST',{username:liveUserInput.value.trim()});bridge.lastId=Math.max(bridge.lastId,Number(connection.last_event_id)||0);bridge.joined=true;", 1)
    # Greet actual join events only when the viewer count is small and reliable.
    old = "function bridgeHandleEvent(item){\n  if(item.type==='system')"
    assert html.count(old) == 1, "Missing welcome event handler anchor"
    html = html.replace(old, """function bridgeHandleEvent(item){
  if(item.type==='welcome'){
    if(Date.now() - (Number(item.at)||0)*1000 > 22000)return;
    const who=String(item.username||'').trim().slice(0,24);
    if(!who)return;
    const options=[`Hoş geldin ${who}! Pembe krallığa buyur aşkım!`,
      `${who}, sefalar getirdin! Diva seni gördü!`,
      `Aaaa ${who} gelmiş! Hoş geldin şekerim!`];
    bridgeOffer({text:options[Math.floor(Math.random()*options.length)],mood:'wink',kind:'welcome'});
    return;
  }
  if(item.type==='system')""", 1)
    old = "function bridgeOffer(item,priority=0){"
    assert html.count(old) == 1, "Missing queue anchor"
    html = html.replace(old, """function bridgeOffer(item,priority=0){
  // A greeting never interrupts comments, gifts, or active speech.
  if(item.kind==='welcome'){
    if(bridge.queue.length || bridge.aiPending || diva.classList.contains('talking'))return;
  } else {
    bridge.queue=bridge.queue.filter(e=>e.kind!=='welcome');
  }""", 1)
    old = "const reply=await divaAskAI(item.source,item.username,item.kind||'comment',item.text);"
    assert html.count(old) == 1, "Missing AI queue anchor"
    html = html.replace(old, "const reply=item.kind==='welcome' ? item.text : await divaAskAI(item.source,item.username,item.kind||'comment',item.text);", 1)
    return html


@app.get("/")
def home():
    html = enhanced_ai_page(enhanced_page(str(ROOT / "index.html")))
    return HTMLResponse(enhanced_connection_page(html), headers={"Cache-Control": "no-store"})


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
    return connection_status()


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
    global runner, client, generation, last_connect_attempt_at
    authorize(x_diva_pin)
    name = body.username.strip().removeprefix("@")
    if not USERNAME_RE.fullmatch(name):
        raise HTTPException(422, "Yalnızca TikTok kullanıcı adını yaz (örnek: diva123).")
    async with connect_lock:
        # A double tap or page reload must not restart a healthy connection.
        if state["phase"] in ("connecting", "connected") and runner and not runner.done():
            if state["username"].lower() != name.lower():
                raise HTTPException(409, "Başka hesap bağlı. Önce Bağlantıyı kes düğmesini kullan.")
            return {"ok": True, "phase": state["phase"], "username": name,
                    "already_running": True, "last_event_id": sequence}

        remaining = seconds_until(rate_limit_retry_at)
        if remaining:
            raise HTTPException(429, f"TikTok bağlantı limiti: {(remaining+59)//60} dakika sonra tekrar dene.")
        gap = seconds_until(last_connect_attempt_at + MIN_CONNECT_GAP)
        if gap:
            raise HTTPException(429, f"Yeni bağlantı için {gap} saniye bekle. Tekrar tekrar basma.")

        generation += 1
        if client is not None:
            try:
                await asyncio.wait_for(client.disconnect(), timeout=5)
            except Exception:
                pass
        if runner is not None and not runner.done():
            runner.cancel()
        client = None
        last_connect_attempt_at = time.time()
        state.update(phase="connecting", username=name, connected=False, error="", viewer_count=None)
        last_event_id = sequence  # never replay old LIVE comments after reconnect
        runner = asyncio.create_task(live_worker(name, generation))
    return {"ok": True, "phase": "connecting", "username": name,
            "already_running": False, "last_event_id": last_event_id}


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
        if seconds_until(rate_limit_retry_at):
            state.update(phase="cooldown", username="", connected=False)
        else:
            state.update(phase="idle", username="", connected=False, error="")
    return {"ok": True, "phase": state["phase"]}


@app.post("/api/demo")
def api_demo(body: DemoBody, x_diva_pin: str | None = Header(None)):
    authorize(x_diva_pin)
    if body.type == "gift":
        event = add_event("gift", username="DemoIzleyici", gift="Rose", count=3, diamonds=None)
    else:
        event = add_event("comment", username="DemoIzleyici", text="Diva bugün nasılsın?")
    return {"ok": True, "event": event}
