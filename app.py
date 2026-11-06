"""Diva CANLI mobile live bridge.

Unrelated to TikTok/ByteDance. Handles a single broadcaster at a time.
The optional TikTokLive dependency is imported on a real connect request.
"""
from __future__ import annotations

import asyncio
import hmac
import secrets
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
from diva_ai import AiReplyRequest, generate_ai_reply, enhanced_ai_page, usage_snapshot
from diva_dashboard import enhanced_dashboard_page

ROOT = Path(__file__).resolve().parent
APP_PIN = os.environ.get("DIVA_PIN", "").strip()
USERNAME_RE = re.compile(r"^@?[a-zA-Z0-9._]{2,30}$")

app = FastAPI(title="Diva CANLI bridge", version="1.0")

feed: deque[dict] = deque(maxlen=400)
sequence = 0
backend_boot_id = secrets.token_hex(8)  # distinguishes Render restarts for the browser's gift ledger
gift_points_total = 0  # gift diamond_count x repeat_count, not TikTok coin balance
gift_unknown_value_events = 0
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
            "backend_boot_id": backend_boot_id,
            "gift_points_total": gift_points_total,
            "gift_unknown_value_events": gift_unknown_value_events,
            **usage_snapshot(),
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


# Filter only explicit standalone vulgar words and common inflections.
# Avoid false matches in "ama", "amca", "götür", "şikayet" and similar words.
import unicodedata

_BLOCKED_WORD = re.compile(
    r"^(?:"
    r"am(?:i|ina|ini|im|dan|da|cik|cigi|cigini)?|amk|amq|aq|aminakoy(?:im|ayim|sun|sunuz|arim)|"
    r"got(?:u|un|une|unu|um|umuz|ten|te|lu|luge|ler|leri|lerini|unden|unde|unun)?|"
    r"sik(?:mek|me|il|im|in|i|er|erim|eriz|erler|tik|tim|ti|tigi|tir|tire|tirin|tirsin|tiniz|tireyim|eyim|icem|iyorum|iyor)?|"
    r"meme(?:m|mi|me|n|ni|si|sini|sinde|ler|leri|lerini|lerin|nin|ye|de|den)?|orospu(?:su|lar|lugu|luk)?|yar+ak(?:i|im|in|ini|lar|lari)?|pic(?:ler|in|ligi)?"
    r")$"
)


def is_blocked_comment(text: object) -> bool:
    raw = str(text or "").casefold().replace("ı", "i")
    plain = "".join(ch for ch in unicodedata.normalize("NFKD", raw) if not unicodedata.combining(ch))
    return any(_BLOCKED_WORD.fullmatch(token) for token in re.findall(r"[a-z0-9]+", plain))


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
        # Visibility for troubleshooting unavailable TikTok member/count events.
        state.update(viewer_count=None, join_events_seen=0, greetings_sent=0,
                     welcome_last_skip="", last_join_at=None)
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
            if message and not is_blocked_comment(message):
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
            state["join_events_seen"] += 1
            state["last_join_at"] = time.time()
            recent_joins.append(now)
            while recent_joins and now - recent_joins[0] > 30:
                recent_joins.popleft()

            def skipped(why: str) -> None:
                state["welcome_last_skip"] = why

            viewers = welcome["viewers"]
            current_count = viewers is not None and now - welcome["viewer_at"] <= 100
            if current_count:
                if viewers > 25:
                    skipped("Kalabalik yayin (>25 izleyici)")
                    return
                # Verified small room. A quick burst still shouldn't start a greeting flood.
                if len(recent_joins) >= 5:
                    skipped("Hizli katilim yogunlugu")
                    return
                interval = 50 if viewers <= 10 else 150
            else:
                # TikTok sometimes omits RoomUserSeqEvent or pauses viewer counts.
                # Do NOT equate join count to current viewer count. Only allow a
                # very rare greeting when entrances themselves are sparse.
                # An old high viewer count is evidence of a busy room: never fall back.
                if viewers is not None and viewers > 25:
                    skipped("Son bilinen izleyici sayisi yuksek")
                    return
                if len(recent_joins) > 2:
                    skipped("Izleyici sayisi belirsiz; katilim yogun")
                    return
                interval = 100
            if now - welcome["last_at"] < interval:
                skipped("Karsilama araligi dolmadi")
                return
            user = getattr(event, "user", None)
            if user is None:
                skipped("TikTok katilan kisi bilgisini gondermedi")
                return
            uid = clean_text(getattr(user, "unique_id", ""), 40).casefold()
            if not uid or uid in welcomed:
                skipped("Kullanici adi eksik veya daha once karsilandi")
                return
            display = welcome_name(user)
            if not display:
                skipped("Seslendirilebilir isim bulunamadi")
                return
            welcomed.add(uid)
            welcome["last_at"] = now
            state["greetings_sent"] += 1
            state["welcome_last_skip"] = ""
            add_event("welcome", username=display,
                      viewer_count=viewers if current_count else None)

        @live.on(GiftEvent)
        async def on_gift(event: object) -> None:
            global gift_points_total, gift_unknown_value_events
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
            if diamonds is None:
                gift_unknown_value_events += 1
            else:
                gift_points_total += diamonds * count
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
    // A single brief greeting may wait for current speech; never stack greetings.
    if(bridge.queue.some(e=>e.kind==='welcome') || bridge.queue.length>2)return;
    item.welcomeQueuedAt=Date.now();
  } else {
    bridge.queue=bridge.queue.filter(e=>e.kind!=='welcome');
  }""", 1)
    # Do not read an old greeting after lengthy speech/AI backlogs.
    old = "const item=bridge.queue.shift();if(!item)return;"
    assert html.count(old) == 1, "Missing queue pop anchor"
    html = html.replace(old, old + "\n  if(item.kind==='welcome' && Date.now()-Number(item.welcomeQueuedAt||0)>20000)return;", 1)
    old = "const reply=await divaAskAI(item.source,item.username,item.kind||'comment',item.text);"
    assert html.count(old) == 1, "Missing AI queue anchor"
    html = html.replace(old, """const reaction=item.kind==='welcome'
      ? {reply:item.text,mood:item.mood||'wink'}
      : await divaAskAIReaction(item.source,item.username,item.kind||'comment',item.text,item.mood||'normal');
    const reply=reaction.reply;
    item.mood=reaction.mood||item.mood||'normal';""", 1)
    # Defense in depth: manual input and queued comments also skip these messages.
    js_filter = r"""function divaContainsBlockedWords(value){
  const cleaned=String(value||'').toLocaleLowerCase('tr-TR').replace(/ı/g,'i')
    .normalize('NFD').replace(/[\u0300-\u036f]/g,'');
  const words=cleaned.match(/[a-z0-9]+/g)||[];
  const bad=/^(?:am(?:i|ina|ini|im|dan|da|cik|cigi|cigini)?|amk|amq|aq|aminakoy(?:im|ayim|sun|sunuz|arim)|got(?:u|un|une|unu|um|umuz|ten|te|lu|luge|ler|leri|lerini|unden|unde|unun)?|sik(?:mek|me|il|im|in|i|er|erim|eriz|erler|tik|tim|ti|tigi|tir|tire|tirin|tirsin|tiniz|tireyim|eyim|icem|iyorum|iyor)?|meme(?:m|mi|me|n|ni|si|sini|sinde|ler|leri|lerini|lerin|nin|ye|de|den)?|orospu(?:su|lar|lugu|luk)?|yar+ak(?:i|im|in|ini|lar|lari)?|pic(?:ler|in|ligi)?)$/;
  return words.some(w=>bad.test(w));
}
"""
    hook="function bridgeHandleEvent(item){"
    assert html.count(hook)==1, "Diva comment guard anchor missing"
    html=html.replace(hook,js_filter+hook+"\n  if(item.type==='comment' && divaContainsBlockedWords(item.text))return;",1)
    manual="async function divaManualAI(msg,name,mood){"
    assert html.count(manual)==1, "Diva manual AI guard anchor missing"
    html=html.replace(manual,manual+"\n if(divaContainsBlockedWords(msg))return;",1)
    return html


SHOW_CSS = r"""<style id="diva-show-rocket-style">
/* Decorative cartoon missile: short-lived, pointer-events: none, zero layout impact. */
.diva-rocket-layer{position:absolute;inset:0;pointer-events:none;z-index:95;overflow:hidden}
.diva-rocket-flyer{position:absolute;left:108%;top:48%;width:clamp(230px,75vw,365px);
  filter:drop-shadow(0 9px 18px #540d4dcc);transform:translate(0,-50%);
  animation:diva-rocket-cross 2.45s cubic-bezier(.18,.14,.43,.99) forwards;will-change:left,transform}
.diva-rocket-flyer svg{display:block;width:100%;height:auto}
.diva-rocket-flash{position:absolute;inset:0;background:radial-gradient(circle at 50% 48%,#fff6ae88 0%,#ffc14718 45%,transparent 80%);
  opacity:0;animation:diva-rocket-glow 1.05s ease-out .2s both}
@keyframes diva-rocket-cross{0%{left:108%;transform:translate(0,-50%) rotate(-4deg)}
  100%{left:-5%;transform:translate(-100%,-50%) rotate(2deg)}}
@keyframes diva-rocket-glow{0%,100%{opacity:0}30%{opacity:.75}}
@media(prefers-reduced-motion:reduce){.diva-rocket-flyer{animation-duration:.5s}.diva-rocket-flash{animation:none}}
</style>"""

SHOW_JS = r'''<script id="diva-show-meme-script">
function divaNormalizeMeme(value){
 return String(value||'').toLocaleLowerCase('tr-TR').normalize('NFD')
  .replace(/[\u0300-\u036f]/g,'').replace(/ı/g,'i').replace(/[^a-z0-9]+/g,' ').trim().replace(/\s+/g,' ');
}
function divaIsAydinlatMeme(value){
 const s=divaNormalizeMeme(value);
 return /\baydinlat(?: hele)? (?:suralari|buralari|ortaligi)\b/.test(s)
   || /\b(?:suralari|buralari|ortaligi)\s+aydinlat\b/.test(s)
   || /\baydinlat hele\b/.test(s);
}
function divaIsNameQuestion(value){
 const s=divaNormalizeMeme(value);
 return /\b(?:adin ne|ismin ne|adi ne|ismi ne|sen kimsin|kimsin sen|kimsin)\b/.test(s);
}
const divaRocketReplies=[
 'Aydınlatıyorum aşkım! Bu ışıkta gözlüğünü tak, Diva füzesi geçiyor!',
 'Çekilin şekerler! Pembe krallığın aydınlatma müdürü füzesini yolladı!',
 'Ayy şuralar karanlık mı kalmış? Diva füzesini gönderiyorum, ayol!',
 'Işığı açmakla kalmam, gökyüzüne imzamı atarım! Yol verin Diva geliyor!'
];
let divaRocketLastAt=0;
function divaLaunchLightRocket(){
 const now=Date.now();
 if(now-divaRocketLastAt<12000)return;
 divaRocketLastAt=now;
 const root=document.getElementById('app');
 if(!root)return;
 const layer=document.createElement('div');layer.className='diva-rocket-layer';layer.setAttribute('aria-hidden','true');
 layer.innerHTML=`<div class="diva-rocket-flash"></div><div class="diva-rocket-flyer">
 <svg viewBox="0 0 380 160" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
 <defs><linearGradient id="divaRocketMetal" x1="0" y1="0" x2="0" y2="1">
 <stop stop-color="#fffaf8"/><stop offset=".53" stop-color="#becfd9"/><stop offset="1" stop-color="#6b7797"/></linearGradient>
 <linearGradient id="divaRocketFire" x1="0" y1="0" x2="1" y2="0"><stop stop-color="#ff3b92"/><stop offset=".5" stop-color="#ffca33"/><stop offset="1" stop-color="#fffadb"/></linearGradient></defs>
 <path d="M342 61 C370 51 372 27 380 13 L360 76 L378 147 C356 130 355 99 342 95Z" fill="#ffbe50" opacity=".9"/>
 <path d="M338 65 L382 80 338 94Z" fill="url(#divaRocketFire)"/>
 <path d="M319 60L361 11 337 68Z" fill="#ee438c" stroke="#fff5cb" stroke-width="3"/>
 <path d="M319 94L360 150 337 89Z" fill="#d32774" stroke="#fff5cb" stroke-width="3"/>
 <path d="M22 80Q75 22 165 52L324 52Q346 80 324 108L165 108Q75 138 22 80Z" fill="url(#divaRocketMetal)" stroke="#7a3966" stroke-width="4"/>
 <path d="M22 80Q75 22 165 52Q122 80 165 108Q72 133 22 80Z" fill="#f6e0ef"/>
 <path d="M160 56H306" stroke="#ffffff99" stroke-width="5" stroke-linecap="round"/>
 <path d="M169 56L170 104" stroke="#d12574" stroke-width="6"/><path d="M279 55L279 105" stroke="#d12574" stroke-width="6"/>
 <circle cx="221" cy="80" r="27" fill="#f9e9ff" stroke="#f458ad" stroke-width="5"/>
 <path d="M221 61L225 72L237 72L229 81L232 93L221 86L210 93L213 81L205 72L217 72Z" fill="#e33790"/>
 <text x="249" y="86" font-size="16" font-family="sans-serif" fill="#7e2554" font-weight="900" text-anchor="middle">DIVA</text>
 <path d="M124 52L91 13L168 50Z" fill="#e53186" stroke="#fff3ce" stroke-width="3"/>
 <path d="M124 108L91 148L168 110Z" fill="#c61b73" stroke="#fff3ce" stroke-width="3"/>
 </svg></div>`;
 root.appendChild(layer);
 setTimeout(()=>layer.remove(),2900);
}
</script>'''


def enhanced_show_page(html: str) -> str:
    """Progressive web app metadata and one special TikTok meme; leave existing images untouched."""
    head_tag = "</head>"
    if html.count(head_tag) != 1:
        raise RuntimeError("Diva head anchor missing")
    html=html.replace(head_tag, '''<link rel="manifest" href="/manifest.webmanifest" />
<meta name="application-name" content="Diva Show" />
<meta name="mobile-web-app-capable" content="yes" />
<meta name="apple-mobile-web-app-capable" content="yes" />
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
''' + SHOW_CSS + head_tag, 1)
    original = "    addChat(user,msg);\n    if(!$('autoReplyComments').checked)return;"
    if html.count(original)!=1:
        raise RuntimeError("Diva TikTok comment anchor missing")
    # Animations display for the meme even if voice auto replies were disabled.
    html=html.replace(original,"    addChat(user,msg);\n    if(divaIsAydinlatMeme(msg))divaLaunchLightRocket();\n    if(!$('autoReplyComments').checked)return;",1)
    original = "    const reply=react(msg,user);\n    const mood=/(sinir|laf sok|saçma)/i.test(msg)?'angry':"
    if html.count(original)!=1:
        raise RuntimeError("Diva live chat reply anchor missing")
    html=html.replace(original,'''    if(divaIsAydinlatMeme(msg)){
      const line=divaRocketReplies[Math.floor(Math.random()*divaRocketReplies.length)];
      bridgeOffer({text:line,mood:'surprised',kind:'meme',source:msg,username:user},1);
      return;
    }
    const reply=divaIsNameQuestion(msg)?'Ben Diva! Hamam böceklerinin tek divası benim, aşkım!':react(msg,user);
    const mood=/(sinir|laf sok|saçma)/i.test(msg)?'angry':''',1)
    # Scripted replies bypass Groq (and its quota); keep original welcome and Groq flow intact.
    old="const reaction=item.kind==='welcome'\n      ? {reply:item.text,mood:item.mood||'wink'}"
    if html.count(old)!=1:
        raise RuntimeError("Diva AI queue anchor missing")
    html=html.replace(old,"const reaction=(item.kind==='welcome'||item.kind==='meme')\n      ? {reply:item.text,mood:item.mood||'wink'}",1)
    end_tag="</body>"
    if html.count(end_tag)!=1:
        raise RuntimeError("Diva body anchor missing")
    html=html.replace(end_tag,SHOW_JS+"\n"+end_tag,1)
    return html

@app.get("/")
def home():
    html = enhanced_ai_page(enhanced_page(str(ROOT / "index.html")))
    return HTMLResponse(enhanced_dashboard_page(enhanced_show_page(enhanced_connection_page(html))), headers={"Cache-Control": "no-store"})


@app.post("/api/tts")
async def tts(body: SpeechRequest, x_diva_pin: str | None = Header(None)):
    authorize(x_diva_pin)
    return await synthesize_emel(body)


@app.post("/api/ai/reply")
async def ai_reply(body: AiReplyRequest, x_diva_pin: str | None = Header(None)):
    authorize(x_diva_pin)
    if body.kind == "comment" and is_blocked_comment(body.message):
        raise HTTPException(422, "Bu yorum otomatik cevap kapsamına alınmadı.")
    return await generate_ai_reply(body)


@app.get("/manifest.webmanifest")
def app_manifest():
    from fastapi.responses import FileResponse
    return FileResponse(ROOT / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/icon-192.png")
def pwa_icon_192():
    return FileResponse(ROOT / "icon-192.png", media_type="image/png")


@app.get("/icon-512.png")
def pwa_icon_512():
    return FileResponse(ROOT / "icon-512.png", media_type="image/png")


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
