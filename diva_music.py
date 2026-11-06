"""Optional background music controls for Diva Show.

The track is uploaded by the broadcaster and served as a separate static MP3.
Browser audio begins only after the broadcaster taps the music button.
The Diva's speech temporarily ducks the background track (it never stops it).
"""

MUSIC_CSS = r'''<style id="diva-music-style">
/* Both live controls share the existing bottom dock; no face or HUD edits. */
.idle-dock{display:flex;align-items:stretch;gap:7px;width:min(375px,calc(100% - 24px))}
.idle-dock #idleBtn{width:auto;min-width:0;flex:1 1 53%;padding:9px 6px;font-size:clamp(10px,2.85vw,12px);min-height:44px}
.idle-dock #divaMusicBtn{
  width:auto;min-width:0;flex:1 1 47%;min-height:44px;padding:9px 6px;
  border-radius:28px;border:1.5px solid rgba(255,233,177,.9);
  background:linear-gradient(120deg,rgba(88,24,82,.96),rgba(147,44,115,.95));
  box-shadow:0 5px 23px rgba(53,8,43,.35),inset 0 1px 0 rgba(255,255,255,.26);
  color:#fff4fb;font-size:clamp(10px,2.85vw,12px);font-weight:850;white-space:nowrap;
  backdrop-filter:blur(12px);cursor:pointer
}
.idle-dock #divaMusicBtn.on{background:linear-gradient(115deg,#ffe0b7,#ffbadd);border-color:#fff7df;color:#6e194c}
@media(max-width:345px){.idle-dock{gap:5px;width:calc(100% - 15px)}
 .idle-dock #idleBtn,.idle-dock #divaMusicBtn{font-size:10px;padding-inline:4px}}
</style>'''

MUSIC_JS = r'''<script id="diva-music-script">
(()=>{
 'use strict';
 const button=document.getElementById('divaMusicBtn');
 const diva=document.getElementById('diva');
 if(!button||!diva)return;
 const music=new Audio('/media/diva-background.mp3');
 music.loop=true;
 music.preload='metadata';
 music.volume=0.19;
 let enabled=false;
 let startAttempt=0;
 const fullVolume=0.19; // keep speech clearly in the foreground
 const speakingVolume=0.035;
 function syncMusicVolume(){
   music.volume=diva.classList.contains('talking')?speakingVolume:fullVolume;
 }
 function setButton(){
   button.classList.toggle('on',enabled);
   button.setAttribute('aria-pressed',String(enabled));
   button.textContent=enabled?'🎵 Müzik: Açık':'🎵 Müzik: Kapalı';
 }
 function turnOff(){
   startAttempt++;
   enabled=false;
   music.pause(); // resume from where it stopped if re-enabled
   setButton();
 }
 button.addEventListener('click',async()=>{
   if(enabled){turnOff();return;}
   const request=++startAttempt;
   enabled=true;
   setButton();
   syncMusicVolume();
   try{
     await music.play(); // called in direct response to the user's tap (mobile autoplay policy)
     if(request!==startAttempt||!enabled)music.pause();
   }catch(_err){
     if(request!==startAttempt)return;
     turnOff();
     if(typeof toast==='function')toast('Müzik başlatılamadı. Telefonun medya sesini ve bağlantını kontrol et.');
   }
 });
 music.addEventListener('error',()=>{
   if(!enabled)return;
   turnOff();
   if(typeof toast==='function')toast('Arka plan müzik dosyası yüklenemedi. diva-background.mp3 dosyasını GitHub’a yükle.');
 });
 const observer=new MutationObserver(syncMusicVolume);
 observer.observe(diva,{attributes:true,attributeFilter:['class']});
 setButton();
})();
</script>'''


def enhanced_music_page(html: str) -> str:
    """Append a music button without touching other existing buttons or face images."""
    original = '<div class="idle-dock"><button class="action" id="idleBtn" type="button" aria-pressed="false">⏳ Oto sohbet: Kapalı</button></div>'
    if html.count(original) != 1:
        raise RuntimeError('Diva bottom control anchor not found exactly once')
    new = original.replace('</button></div>', '</button><button class="action" id="divaMusicBtn" type="button" aria-pressed="false">🎵 Müzik: Kapalı</button></div>')
    html = html.replace(original, new, 1)
    if html.count('</head>') != 1 or html.count('</body>') != 1:
        raise RuntimeError('Diva page tags invalid')
    html=html.replace('</head>',MUSIC_CSS+'\n</head>',1)
    html=html.replace('</body>',MUSIC_JS+'\n</body>',1)
    return html
