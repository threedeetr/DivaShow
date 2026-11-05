"""Optional Emel Neural Turkish voice for Diva CANLI.

The UI is injected at render time to preserve the 6-mimic inline HTML exactly.
Uses the unofficial Edge Read Aloud interface through edge-tts; this may become
unavailable without notice. Device TTS is retained as a fallback.
"""
from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)
VOICE = "tr-TR-EmelNeural"
VOICE_SEMAPHORE = asyncio.Semaphore(2)


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=280)
    rate: float = Field(default=0.94, ge=0.75, le=1.2)
    pitch: float = Field(default=1.02, ge=0.85, le=1.25)


async def synthesize_emel(data: SpeechRequest) -> Response:
    """Create an in-memory MP3 response; don't write speech or PINs to disk."""
    clean = " ".join(data.text.split())
    if not clean:
        raise HTTPException(status_code=422, detail="Seslendirilecek metin boş olamaz.")
    rate_pct = max(-25, min(20, round((data.rate - 1.0) * 100)))
    pitch_hz = max(-15, min(25, round((data.pitch - 1.0) * 90)))
    try:
        import edge_tts
        async with VOICE_SEMAPHORE:
            async def build_audio():
                tts = edge_tts.Communicate(
                    clean, voice=VOICE,
                    rate=f"{rate_pct:+d}%", pitch=f"{pitch_hz:+d}Hz",
                )
                chunks = []
                total = 0
                async for part in tts.stream():
                    if part.get("type") == "audio":
                        sound = part.get("data", b"")
                        total += len(sound)
                        if total > 1_500_000:
                            raise ValueError("Generated voice is too large")
                        chunks.append(sound)
                return b"".join(chunks)
            audio = await asyncio.wait_for(build_audio(), timeout=15)
        if not audio:
            raise ValueError("TTS did not return audio")
        return Response(
            content=audio,
            media_type="audio/mpeg",
            headers={"Cache-Control":"no-store", "X-Voice":"tr-TR-EmelNeural"},
        )
    except Exception:
        # Keep exception specifics out of public responses and don't print text.
        log.warning("Emel TTS unavailable", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Emel Neural şu an kullanılamıyor. Telefon sesi yedeği seçilebilir.",
        )


# The original speech logic remains present and is renamed to sayPhoneText.
# Only the small speech function is replaced at request time; the actual
# index.html with images, animation, TikTok event handlers is NOT edited.
CLOUD_JS = r'''
const cloudEngine = $('divaVoiceEngine');
const cloudNotice = $('divaCloudStatus');
cloudEngine.value = store.get('divaVoiceEngine','emel');
cloudEngine.addEventListener('change',()=>{
 store.set('divaVoiceEngine',cloudEngine.value);
 cloudNotice.textContent=cloudEngine.value==='emel'
 ? '☁️ Emel Neural seçildi. Sunucuya bağlanacak; çalışmazsa telefon sesini dener.'
 : '📱 Telefonun kendi konuşma motoru seçildi.';
});
cloudNotice.textContent=cloudEngine.value==='emel'
 ? '☁️ Emel Neural (sunucu) · Türkçe kadın sesi. Test etmek için PIN girip sesi dene.'
 : '📱 Telefonun kendi konuşma motoru seçildi.';
let cloudAudio=null, cloudAudioUrl=null, cloudAbort=null;
function stopCloudAudio(){
 if(cloudAbort){cloudAbort.abort();cloudAbort=null;}
 if(cloudAudio){cloudAudio.onended=null;cloudAudio.onerror=null;cloudAudio.pause();cloudAudio.removeAttribute('src');cloudAudio.load();cloudAudio=null;}
 if(cloudAudioUrl){URL.revokeObjectURL(cloudAudioUrl);cloudAudioUrl=null;}
}
function sayText(txt){
 const serial=++speechSerial;
 stopCloudAudio();
 try{window.speechSynthesis?.cancel();}catch(e){}
 if(!voiceEnabled){endTalkLater();return;}
 if(cloudEngine.value!=='emel' || location.protocol==='file:' || !$('livePin').value.trim()){
   if(cloudEngine.value==='emel') {cloudNotice.textContent='⚠️ Bulut sesi için aynı Render sitesinde DIVA_PIN girilmeli. Telefon sesi deneniyor.';toast('Emel sesi için önce DIVA_PIN gir.');}
   sayPhoneText(txt);
   return;
 }
 clearInterval(lipTimer);lipTimer=null;
 if(!shockUntil&&!angryUntil&&!winkUntil)setFace(0);
 const ctl=new AbortController();cloudAbort=ctl;
 let deadline=setTimeout(()=>ctl.abort(),16500);
 const payload={text:txt.slice(0,280), rate:Number(speed.value),pitch:Number(pitch.value)};
 fetch('/api/tts',{method:'POST',headers:{'Content-Type':'application/json','X-Diva-Pin':$('livePin').value.trim()},body:JSON.stringify(payload),signal:ctl.signal,cache:'no-store'})
  .then(async res=>{
    if(!res.ok)throw new Error('Bulut ses servisi: '+res.status);
    const data=await res.blob();
    if(data.size<80)throw new Error('Boş ses kaydı');
    return data;
  })
  .then(async data=>{
    if(serial!==speechSerial)return;
    cloudAudioUrl=URL.createObjectURL(data);
    const audio=new Audio(); cloudAudio=audio;
    audio.preload='auto';audio.src=cloudAudioUrl;
    audio.onended=()=>{if(serial===speechSerial){endTalk();cloudNotice.textContent='✅ Emel Neural Türkçe ses tamamlandı.';}stopCloudAudio();};
    audio.onerror=()=>{if(serial!==speechSerial)return;stopCloudAudio();cloudNotice.textContent='⚠️ Emel sesi açılamadı. Telefon sesi deneniyor.';toast('Emel sesi açılamadı; telefon sesi deneniyor.');startLipAnimation();sayPhoneText(txt);};
    try{
      await audio.play();
      if(serial===speechSerial){startLipAnimation();cloudNotice.textContent='🔊 Emel Neural konuşuyor.';clearTimeout(speechStopTimer);speechStopTimer=setTimeout(()=>{if(serial===speechSerial){stopCloudAudio();endTalk();}},24000);}
    }catch(err){
      if(serial!==speechSerial)return;
      stopCloudAudio();
      cloudNotice.textContent='⚠️ Telefon otomatik sesi engelledi. “Sesi aç / Test et” düğmesine dokun.';
      startLipAnimation();sayPhoneText(txt);
    }
  })
  .catch(err=>{
    if(serial!==speechSerial)return;
    stopCloudAudio();
    cloudNotice.textContent='⚠️ Emel Neural erişilemedi ('+String(err.message).slice(0,52)+'). Telefon sesi deneniyor.';
    toast('Emel Neural erişilemedi; telefon sesi deneniyor.');
    startLipAnimation();sayPhoneText(txt);
  })
  .finally(()=>clearTimeout(deadline));
}
function sayPhoneText(txt){const serial=++speechSerial;
'''


@lru_cache(maxsize=1)
def enhanced_page(index_path: str) -> str:
    html = Path(index_path).read_text(encoding="utf-8")
    anchor = '<div class="voice-info" id="voiceInfo" aria-live="polite">'
    if html.count(anchor) != 1:
        raise RuntimeError("Voice panel marker was not found exactly once")
    new_panel = (
        '<div class="field voice-field"><label for="divaVoiceEngine">Ses kaynağı</label>'
        '<select id="divaVoiceEngine"><option value="emel">☁️ Emel Neural · Türkçe kadın (sunucu)</option>'
        '<option value="phone">📱 Telefon sesi (yedek)</option></select></div>'
        '<div class="voice-info" id="divaCloudStatus" aria-live="polite">'
        'Bulut sesi hazırlanıyor…</div>\n     '
    )
    html = html.replace(anchor, new_panel + anchor, 1)
    speech_marker = 'function sayText(txt){const serial=++speechSerial;'
    if html.count(speech_marker) != 1:
        raise RuntimeError("Speech marker was not found exactly once")
    html = html.replace(speech_marker, CLOUD_JS, 1)
    # Before microphone-free mimic tests/manual expression picks, stop playing
    # any previous cloud recording just as the old code cancels phone TTS.
    js_marker = 'speechSerial++;if(\'speechSynthesis\' in window)window.speechSynthesis.cancel();'
    if js_marker in html:
        html = html.replace(js_marker,"speechSerial++;stopCloudAudio();if('speechSynthesis' in window)window.speechSynthesis.cancel();")
    other_marker = 'speechSerial++;if(window.speechSynthesis)window.speechSynthesis.cancel();'
    if other_marker in html:
        html = html.replace(other_marker, 'speechSerial++;stopCloudAudio();if(window.speechSynthesis)window.speechSynthesis.cancel();')
    html = html.replace('Diva CANLI •', 'Diva CANLI • Emel Neural +', 1)
    return html
