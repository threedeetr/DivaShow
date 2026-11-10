"""Creator-controlled LIVE mode for Diva Show.

No platform evasion. When enabled, speech requires a creator tap,
gift-triggered effects are suppressed, and the gift goal is hidden.
It does not assert compliance or guarantee monetization eligibility.
"""

SAFETY_STYLE = r'''<style id="diva-creator-control-style">
html.diva-creator-control #divaGoalHud{display:none!important}
#divaCreatorQueue{position:fixed;right:12px;bottom:62px;z-index:240;
 max-width:min(340px,calc(100vw - 24px));padding:11px 12px;border-radius:17px;
 color:#fff;background:linear-gradient(150deg,rgba(79,11,60,.97),rgba(119,26,84,.96));
 border:1px solid #ffd5ed99;box-shadow:0 10px 30px #21001988;
 font-family:system-ui,Arial,sans-serif;box-sizing:border-box}
#divaCreatorQueue[hidden]{display:none!important}
#divaCreatorQueue .divaCreatorHeading{font-size:13px;font-weight:900;margin-bottom:4px}
#divaCreatorQueue .divaCreatorText{font-size:12px;line-height:1.3;
 white-space:normal;overflow-wrap:anywhere;max-height:60px;overflow:hidden;margin-bottom:9px}
#divaCreatorQueue .divaCreatorButtons{display:flex;gap:8px}
#divaCreatorQueue button{border-radius:10px;border:1px solid #ffd9e9aa;
 padding:8px 9px;flex:1;cursor:pointer;font-weight:800;font-size:12px;
 background:#ff81c5;color:#45142c}
#divaCreatorQueue button:last-child{background:#571c41;color:#fff6fc}
#divaCreatorHint{font-size:11px;opacity:.82;margin-top:5px;line-height:1.4}
</style>'''

SAFETY_JS = r'''<script id="diva-creator-control-script">
(() => {
  'use strict';
  const KEY='divaCreatorControlledModeV1';
  const choice=(() => {try{return localStorage.getItem(KEY);}catch(_){return null;}})();
  // Safe default: creator approval required after every reload, unless explicitly disabled.
  let controlled = choice !== 'off';
  const pending=[];
  const MAX_PENDING=4;
  const MAX_AGE_MS=30000;
  const timerId=setInterval(() => {trimQueue();render();}, 2500);
  window.divaCreatorControlledOn=() => controlled;

  const heading=document.createElement('div');
  heading.className='divaCreatorHeading';
  const detail=document.createElement('div');
  detail.className='divaCreatorText';
  const actions=document.createElement('div');
  actions.className='divaCreatorButtons';
  const approve=document.createElement('button');
  approve.type='button'; approve.textContent='🎙️ Okut';
  const ignore=document.createElement('button');
  ignore.type='button'; ignore.textContent='Geç';
  actions.append(approve,ignore);
  const hud=document.createElement('div');
  hud.id='divaCreatorQueue'; hud.hidden=true;
  hud.append(heading,detail,actions);document.body.append(hud);

  const control=document.createElement('div');
  control.style.cssText='margin-top:12px;padding:12px;border:1px solid #ffb6da7a;border-radius:12px;background:#45133255';
  const label=document.createElement('label');
  label.style.cssText='display:flex;gap:9px;align-items:center;font-weight:800;cursor:pointer';
  const check=document.createElement('input');check.type='checkbox';check.id='divaCreatorControlSwitch';
  const labelText=document.createTextNode(' 🛡️ Yayıncı kontrollü mod');
  label.append(check,labelText);control.append(label);
  const explanatory=document.createElement('div');
  explanatory.id='divaCreatorHint';
  explanatory.textContent='Açıkken hediye hedefi ve hediye animasyonları gizlenir, otomatik karşılama/otosohbet durur. Yorumları okumadan önce "Okut" düğmesine basman gerekir. Bu mod ihlal olmayacağını garanti etmez.';
  control.append(explanatory);
  const anchor=document.getElementById('aiTestBtn');
  if(anchor && anchor.parentElement){anchor.insertAdjacentElement('afterend',control);}
  else{document.body.append(control);}

  function trimQueue(){
    const now=Date.now();
    while(pending.length && now-pending[0].at>MAX_AGE_MS)pending.shift();
    while(pending.length>MAX_PENDING)pending.shift();
  }
  function render(){
    trimQueue();
    check.checked=controlled;
    document.documentElement.classList.toggle('diva-creator-control',controlled);
    const item=pending[0];
    hud.hidden=!controlled || !item;
    if(item){
      heading.textContent='🛡️ Sesli yanıt için yayıncı onayı ('+pending.length+')';
      const username=String(item.data.username||'İzleyici').slice(0,32);
      const preview=String(item.data.source||item.data.text||'').slice(0,115);
      detail.textContent=username+': '+preview;
    }
  }
  function setMode(on){
    controlled=Boolean(on);
    try{localStorage.setItem(KEY,controlled?'on':'off');}catch(_){ }
    window.divaCreatorControlEnabled=controlled;
    if(controlled){
      // Main Diva lives in a closure; use the narrow exported helper.
      window.divaCreatorQuiesce?.();
      try{
        const idleButton=document.getElementById('idleBtn');
        if(idleButton){
          idleButton.classList.remove('on');
          idleButton.setAttribute('aria-pressed','false');
          idleButton.textContent='⏳ Oto sohbet: Kapalı';
          idleButton.disabled=true;
          idleButton.title='Yayıncı kontrollü modda otosohbet devre dışıdır';
        }
      }catch(_){ }
    }else{
      pending.length=0;
      try{const b=document.getElementById('idleBtn');if(b){b.disabled=false;b.title='';}}catch(_){ }
    }
    render();
  }
  // Store only a small, short-lived queue of candidates; no API calls before approval.
  window.divaCreatorHold=(item,priority=0)=>{
    if(!controlled)return false;
    if(!item || !['comment','moderator'].includes(item.kind))return true;
    pending.push({data:{...item}, priority, at:Date.now()});
    trimQueue();render();return true;
  };
  check.addEventListener('change',()=>setMode(check.checked));
  approve.addEventListener('click',()=>{
    trimQueue();const saved=pending.shift();render();
    if(!saved)return;
    window.divaCreatorApproveOffer?.(saved.data,saved.priority);
  });
  ignore.addEventListener('click',()=>{pending.shift();render();});
  setMode(controlled);
})();
</script>'''


def enhanced_creator_control_page(html: str) -> str:
    """Inject into a fully rendered page, keeping all existing scripts intact."""
    def replace_once(before: str, after: str) -> None:
        nonlocal html
        if html.count(before) != 1:
            raise RuntimeError('Diva creator control insertion point changed: '+before[:70])
        html=html.replace(before,after,1)

    anchor='function bridgeOffer(item,priority=0){'
    # Main Diva runs in a private IIFE. Export two narrowly scoped callbacks.
    helpers=r'''window.divaCreatorApproveOffer=(data,priority=0)=>{
  bridgeOffer({...data,_divaCreatorApproved:true},priority);
  bridge.nextAt=0;
};
window.divaCreatorQuiesce=()=>{
  idleEnabled=false;
  divaIdleToken++;
  scheduleIdle();
  bridge.queue=bridge.queue.filter(q=>q._divaCreatorApproved);
};
'''
    replace_once(anchor,helpers+anchor+'''
  if(window.divaCreatorControlledOn?.()!==false){
    // Gifts, welcomes, automatic topics, and meme triggers are not voiced.
    if(['gift','welcome','idle','meme'].includes(item?.kind))return;
    if(!item?._divaCreatorApproved){
      window.divaCreatorHold?.(item,priority);
      return;
    }
  }''')

    # Keep gift notifications as text only, never trigger animated kisses or fireworks.
    gift_animation='divaBlowKiss(); // Gercek TikTok hediyesi: gorunur opucuk + goz kirpma\n    if(isBig)burst(55);'
    replace_once(gift_animation,'''if(window.divaCreatorControlledOn?.()===false){
      divaBlowKiss();
      if(isBig)burst(55);
    }''')

    replace_once('</head>',SAFETY_STYLE+'\n</head>')
    replace_once('</body>',SAFETY_JS+'\n</body>')
    return html
