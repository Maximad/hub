(function(){
  const root=document.getElementById('staff-notifications'); if(!root) return;
  const badge=document.getElementById('staff-notification-badge'), box=document.getElementById('staff-notification-dropdown');
  const toggle=document.getElementById('staff-notification-toggle'), soundBtn=document.getElementById('staff-sound-toggle'), browserBtn=document.getElementById('staff-browser-toggle');
  let lastId='', sound=false, interacted=false, browser=false, audioContext=null;
  function csrf(){return (document.cookie.match(/csrftoken=([^;]+)/)||[])[1]||''}
  function post(url,data){return fetch(url,{method:'POST',headers:{'X-CSRFToken':csrf(),'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams(data)});}
  function ensureAudioContext(){
    if(!interacted) return null;
    const Ctx=window.AudioContext||window.webkitAudioContext; if(!Ctx) return null;
    if(!audioContext) audioContext=new Ctx();
    if(audioContext.state==='suspended') audioContext.resume().catch(()=>{});
    return audioContext;
  }
  function playNotificationBeep(){
    if(!sound) return;
    const ctx=ensureAudioContext(); if(!ctx) return;
    const osc=ctx.createOscillator(), gain=ctx.createGain(), now=ctx.currentTime;
    osc.type='sine'; osc.frequency.setValueAtTime(880,now);
    gain.gain.setValueAtTime(0.0001,now); gain.gain.exponentialRampToValueAtTime(0.08,now+0.02); gain.gain.exponentialRampToValueAtTime(0.0001,now+0.18);
    osc.connect(gain); gain.connect(ctx.destination); osc.start(now); osc.stop(now+0.2);
  }
  toggle.onclick=()=>{box.hidden=!box.hidden};
  soundBtn.onclick=()=>{interacted=true; sound=!sound; if(sound) ensureAudioContext(); soundBtn.textContent=sound?'كتم الصوت':'تشغيل الصوت'; post(root.dataset.prefUrl,{enable_sound:sound?'1':'0'});};
  browserBtn.onclick=()=>{ if(!('Notification' in window)) return; Notification.requestPermission().then(p=>{browser=p==='granted'; post(root.dataset.prefUrl,{enable_browser_notifications:browser?'1':'0'});}); };
  box.addEventListener('click',e=>{const b=e.target.closest('.staff-notification-read'); if(!b) return; post(root.dataset.markReadUrl,{id:b.dataset.notificationId}).then(poll);});
  function alertNew(items){ if(!items.length) return; playNotificationBeep(); if(browser&&Notification.permission==='granted'){new Notification(items[0].title,{body:items[0].message||'تنبيه جديد'});} }
  function render(data){badge.textContent=data.unread_count||0; box.innerHTML=data.html||'<div class="staff-notification-empty">لا توجد تنبيهات جديدة</div>'; const first=(data.latest||[])[0]; if(data.has_new && first && first.id!=lastId) alertNew(data.latest); if(first) lastId=String(first.id);}
  function poll(){ if(document.hidden) return; fetch(root.dataset.pollUrl+'?after='+encodeURIComponent(lastId)).then(r=>r.json()).then(render).catch(()=>{}); }
  poll(); setInterval(poll,5000);
})();
