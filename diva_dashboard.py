"""Diva Show: compact live quota and gift-goal overlay, without touching character assets.

All display preferences and cumulative gift progress are stored in this browser only.
Render's memory-only counters may reset on service restart, and the HUD safely
reconciles each new server instance without double counting observed gifts.
"""

DASHBOARD_CSS = r'''<style id="diva-dashboard-css">
/* A compact pair of HUD elements. Avoid touching the Diva figure or the original face sprites. */
#divaAiChip{position:absolute;top:122px;right:16px;z-index:9;display:flex;align-items:center;gap:6px;
  padding:5px 10px;border:1px solid #ffeab592;border-radius:17px;color:#fff7e9;
  background:linear-gradient(120deg,#71133edb,#a72e6bdc);box-shadow:0 3px 12px #53103575;
  font-size:12px;font-weight:900;line-height:1.15;pointer-events:auto}
#divaAiChip .lips{font-size:18px;line-height:1}
#divaAiChip .sub{font-size:9px;font-weight:700;color:#ffebcf}
#divaGoalHud{position:absolute;z-index:8;bottom:82px;left:13px;right:13px;max-width:425px;margin-inline:auto;
  padding:9px 12px 8px;border-radius:16px;border:1px solid #ffead69c;
  background:linear-gradient(130deg,#601438dc,#8f245ad6);box-shadow:0 4px 19px #58133a66,inset 0 1px #ffffff32;color:white;
  font-size:11px;overflow:hidden;pointer-events:none}
.diva-goal-head{display:flex;align-items:center;justify-content:space-between;gap:8px;font-weight:850}
#divaGoalTitle{display:block;max-width:70%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#divaGoalAmount{font-variant-numeric:tabular-nums;white-space:nowrap;color:#ffe6b0;font-size:11px}
.diva-goal-track{height:9px;background:#300b29dd;border:1px solid #ffd4ec61;border-radius:20px;margin-top:7px;overflow:hidden}
#divaGoalFill{width:0%;height:100%;background:linear-gradient(90deg,#ff84bf,#ffcf79,#fff4bc);border-radius:18px;transition:width .6s ease}
#divaGoalHud.done{border-color:#fff4aa;box-shadow:0 0 18px #ffc36f80}
.diva-goal-foot{display:flex;justify-content:space-between;gap:6px;margin-top:3px;font-size:9px;color:#ffe2f0}
.diva-goal-settings{margin:12px 0;padding:13px;border:1px solid #ffe0b6ad;border-radius:15px;background:#3f0e36a8}
.diva-goal-settings h3{margin:0 0 6px;font-size:16px;color:#fff0b7}
.diva-goal-settings label{display:block;font-size:12px;margin-top:9px;color:#ffe7f6;font-weight:800}
.diva-goal-settings input{display:block;width:100%;min-width:0;margin-top:5px;padding:10px 12px;border:1px solid #ffffff93;
  border-radius:11px;color:#4b103a;background:#fff2f9;font-size:14px}
.diva-goal-settings .diva-goal-actions{display:flex;gap:9px;margin-top:12px}
.diva-goal-settings button{flex:1;padding:9px 7px;border-radius:11px;border:1px solid #fff5cc9f;font-size:12px;font-weight:900;
  color:#5d143e;background:#ffe7b6}
.diva-goal-settings button.secondary{background:#6d215a;color:#fff5f5}
.diva-goal-settings p{font-size:11px;line-height:1.45;color:#ffddea;margin:9px 0 0}
#divaGoalSettingsStatus{min-height:15px;font-weight:800;color:#f6e3a3}
@media(max-width:355px){#divaAiChip{top:110px;right:9px;padding:4px 8px;font-size:11px}#divaGoalHud{bottom:80px;left:8px;right:8px;padding:7px 10px;font-size:10px}}
@media(max-height:700px){#divaAiChip{top:108px}#divaGoalHud{bottom:78px;padding:7px 10px}.diva-goal-track{height:7px}}
</style>'''

DASHBOARD_HUD = r'''<aside id="divaAiChip" aria-label="Başarılı Groq cevap sayısı" title="Bu Render oturumunda bugün üretilen başarılı Groq cevapları. Ücretsiz Groq hesap bakiyesi değildir.">
  <span class="lips" aria-hidden="true">💋</span><strong id="divaAiCount">—</strong><span class="sub">AI cevap</span>
</aside>
<section id="divaGoalHud" aria-label="Canlı yayın hediye hedefi">
  <div class="diva-goal-head"><span id="divaGoalTitle">👠 Marka ayakkabı almak</span><span id="divaGoalAmount">0 / 1.000</span></div>
  <div class="diva-goal-track" role="progressbar" aria-label="Hediye hedefi ilerlemesi" aria-valuemin="0" aria-valuemax="1000" aria-valuenow="0" id="divaGoalTrack"><div id="divaGoalFill"></div></div>
  <div class="diva-goal-foot"><span>🎁 Hediye puanı</span><span id="divaGoalPercent">%0</span></div>
</section>'''

DASHBOARD_SETTINGS = r'''<section class="diva-goal-settings" aria-label="Yayın hedefi ayarları">
  <h3>🎯 Yayın hedefi</h3>
  <label for="divaGoalInputTitle">Hedefin adı</label>
  <input id="divaGoalInputTitle" type="text" maxlength="65" placeholder="Örn: Marka ayakkabı almak" autocomplete="off" />
  <label for="divaGoalInputTarget">Hedef için gereken jeton / hediye puanı</label>
  <input id="divaGoalInputTarget" type="number" min="1" max="100000000" step="1" inputmode="numeric" value="1000" />
  <div class="diva-goal-actions">
    <button id="divaGoalSave" type="button">✓ Hedefi kaydet</button>
    <button id="divaGoalReset" type="button" class="secondary">↺ İlerlemeyi sıfırla</button>
  </div>
  <p id="divaGoalSettingsStatus" role="status" aria-live="polite"></p>
  <p id="divaAiUsageDetails">💋 AI cevapları: bağlanınca gösterilecek.</p>
  <p>İlerleme, gerçek TikTok hediyelerinin bildirilen <b>elmas değeri × adet</b> üzerinden hesaplanır. Bu, gerçek jeton bakiyesi veya para kazancı değildir. Değeri bildirilmeyen hediyeler sayılmaz. Ayarlar ve biriken ilerleme bu telefonun tarayıcısında tutulur; tarayıcı verileri silinirse kaybolabilir.</p>
</section>'''

DASHBOARD_JS = r'''<script id="diva-dashboard-js">
(function(){
'use strict';
const $d=id=>document.getElementById(id);
const get=(key, fallback)=>{try{const s=localStorage.getItem(key);return s===null?fallback:s;}catch{return fallback;}};
const put=(key, value)=>{try{localStorage.setItem(key,String(value));}catch{}};
const fmt=n=>Math.max(0,Math.floor(n)).toLocaleString('tr-TR');
const safeNum=(value, fallback=0)=>{const n=Number(value);return Number.isFinite(n)&&n>=0?n:fallback;};
const readJson=(key,fallback)=>{try{return JSON.parse(get(key,JSON.stringify(fallback)))||fallback;}catch{return fallback;}};
const goalTitleKey='divaGoalTitleV1';
const goalTargetKey='divaGoalTargetV1';
const goalBaseKey='divaGoalStartV1';
const ledgerKey='divaGiftLedgerV1';
const aiLedgerKey='divaAiLedgerV1';
let title=get(goalTitleKey,'👠 Marka ayakkabı almak');
let target=Math.max(1,Math.min(100000000,Math.round(safeNum(get(goalTargetKey,'1000'),1000))));
let ledger=readJson(ledgerKey,{boot:'',last:0,total:0});
if(typeof ledger!=='object'||!ledger)ledger={boot:'',last:0,total:0};
ledger={boot:String(ledger.boot||''),last:safeNum(ledger.last),total:safeNum(ledger.total)};
let goalBase=safeNum(get(goalBaseKey,'0'));
let aiLedger=readJson(aiLedgerKey,{day:'',boot:'',last:0,total:0});
if(typeof aiLedger!=='object'||!aiLedger)aiLedger={day:'',boot:'',last:0,total:0};
aiLedger={day:String(aiLedger.day||''),boot:String(aiLedger.boot||''),last:safeNum(aiLedger.last),total:safeNum(aiLedger.total)};
const titleInput=$d('divaGoalInputTitle');
const targetInput=$d('divaGoalInputTarget');
const notice=$d('divaGoalSettingsStatus');
function draw(){
 const progress=Math.max(0,Math.floor(ledger.total-goalBase));
 const percentage=Math.min(100,Math.round(progress/target*100));
 $d('divaGoalTitle').textContent=(progress>=target?'🎉 ':'🎯 ')+title;
 $d('divaGoalAmount').textContent=fmt(progress)+' / '+fmt(target);
 $d('divaGoalFill').style.width=(Math.min(100,100*progress/target))+'%';
 $d('divaGoalPercent').textContent=progress>=target?'HEDEF TAMAMLANDI':'%'+percentage;
 $d('divaGoalHud').classList.toggle('done',progress>=target);
 const track=$d('divaGoalTrack');
 track.setAttribute('aria-valuemax',String(target));
 track.setAttribute('aria-valuenow',String(Math.min(progress,target)));
 track.setAttribute('aria-valuetext',fmt(progress)+' / '+fmt(target)+' hediye puanı');
}
function update(status){
 if(!status||typeof status!=='object')return;
 const boot=String(status.backend_boot_id||'');
 if(boot && typeof status.ai_success_replies==='number'){
  const day=String(status.ai_count_day||'');
  const value=Math.max(0,Math.floor(safeNum(status.ai_success_replies)));
  if(day && aiLedger.day!==day){
    aiLedger={day,boot,last:value,total:value};
  }else if(!aiLedger.boot){
    aiLedger={day,boot,last:value,total:value};
  }else if(aiLedger.boot!==boot){
    aiLedger.boot=boot;aiLedger.last=value;aiLedger.total+=value;
  }else if(value>=aiLedger.last){
    aiLedger.total+=value-aiLedger.last;aiLedger.last=value;
  }else{
    // A counter reset within the same boot is treated as a new segment.
    aiLedger.total+=value;aiLedger.last=value;
  }
  put(aiLedgerKey,JSON.stringify(aiLedger));
  $d('divaAiCount').textContent=fmt(aiLedger.total);
  const requestPart=fmt(safeNum(status.ai_requests_today))+' / '+fmt(safeNum(status.ai_request_cap));
  $d('divaAiChip').title='Bugün başarılı Groq yanıtları (bu tarayıcının kaydı): '+fmt(aiLedger.total)+' · Sunucudaki API denemesi: '+requestPart+' · UTC: '+day+' · Resmî Groq ücretsiz kota bakiyesi değildir.';
  $d('divaAiUsageDetails').textContent='💋 Bugün '+fmt(aiLedger.total)+' başarılı Groq cevabı · Sunucuda '+requestPart+' API denemesi (UTC). Bunlar Groq hesabının resmî kalan kotası değildir.';
 }
 const total=safeNum(status.gift_points_total,NaN);
 if(!boot||!Number.isFinite(total))return;
 const n=Math.max(0,Math.floor(total));
 if(!ledger.boot){
  // First link to an existing server starts the *new* goal from the current amount.
  ledger.boot=boot;ledger.last=n;
 }else if(ledger.boot!==boot){
  // Render restarted: the new backend's counter starts from zero.
  ledger.boot=boot;ledger.last=n;ledger.total+=n;
 }else if(n>ledger.last){
  ledger.total+=n-ledger.last;ledger.last=n;
 }else if(n<ledger.last){
  // Defensive recovery if the same boot id reported a smaller value.
  ledger.last=n;
 }
 put(ledgerKey,JSON.stringify(ledger));
 draw();
}
window.divaDashboardUpdate=update;
titleInput.value=title.replace(/^👠\s*/,'');
targetInput.value=String(target);
$d('divaGoalSave').addEventListener('click',()=>{
 const proposed=titleInput.value.trim().replace(/[\x00-\x1f]/g,'').slice(0,65);
 const v=Number(targetInput.value);
 if(!proposed||!Number.isSafeInteger(v)||v<1||v>100000000){notice.textContent='⚠ Hedef adı ve 1–100.000.000 arasında tam sayı gir.';return;}
 title=proposed;target=v;
 put(goalTitleKey,title);put(goalTargetKey,target);
 notice.textContent='✓ Hedef kaydedildi. Mevcut ilerleme korundu.';
 draw();
});
$d('divaGoalReset').addEventListener('click',()=>{
 goalBase=ledger.total;
 put(goalBaseKey,goalBase);
 notice.textContent='↺ Hedef ilerlemesi sıfırlandı. Yeni hediyeler sayılacak.';
 draw();
});
draw();
})();
</script>'''


def enhanced_dashboard_page(html: str) -> str:
    """Inject using unique verified anchors; keep all six character images byte-identical."""
    meta = '<div class="top-meta"><span class="tag">💗 Pembe krallık</span></div>'
    if html.count(meta) != 1:
        raise RuntimeError('Diva HUD location anchor missing')
    html = html.replace(meta, meta + '\n' + DASHBOARD_HUD, 1)

    settings = '<div class="field"><label for="argo">Diva\'nın dili</label>'
    if html.count(settings) != 1:
        raise RuntimeError('Diva settings anchor missing')
    html = html.replace(settings, DASHBOARD_SETTINGS + '\n  ' + settings, 1)

    status = "const status=await bridgeRequest('/api/status');"
    if html.count(status) != 1:
        raise RuntimeError('Diva status poll anchor missing')
    html = html.replace(status, status + "\n    if(window.divaDashboardUpdate)window.divaDashboardUpdate(status);", 1)

    head = '</head>'
    body = '</body>'
    if html.count(head) != 1 or html.count(body) != 1:
        raise RuntimeError('Diva HTML top-level anchors missing')
    html = html.replace(head, DASHBOARD_CSS + '\n' + head, 1)
    html = html.replace(body, DASHBOARD_JS + '\n' + body, 1)
    return html
