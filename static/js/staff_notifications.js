(function(){
  const root=document.getElementById('staff-notifications'); if(!root) return;
  const badge=document.getElementById('staff-notification-badge'), box=document.getElementById('staff-notification-dropdown');
  const status=document.getElementById('staff-notification-status');
  const toggle=document.getElementById('staff-notification-toggle'), soundBtn=document.getElementById('staff-sound-toggle'), browserBtn=document.getElementById('staff-browser-toggle');
  let lastId='', browser=false, audioContext=null, firstPoll=true, unreadCount=null;
  const seenIds=new Set();
  let sound=localStorage.getItem('staffNotificationSound')==='1';
  let interacted=sound;
  function csrf(){return (document.cookie.match(/csrftoken=([^;]+)/)||[])[1]||''}
  function post(url,data){return fetch(url,{method:'POST',headers:{'X-CSRFToken':csrf(),'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams(data)});}
  function setSoundLabel(){soundBtn.textContent=sound?'كتم الصوت':'تشغيل الصوت';}
  function ensureAudioContext(){
    if(!interacted) return null;
    const Ctx=window.AudioContext||window.webkitAudioContext; if(!Ctx) return null;
    if(!audioContext) audioContext=new Ctx();
    if(audioContext.state==='suspended') audioContext.resume().catch(()=>{});
    return audioContext;
  }
  function scheduleTone(ctx,frequency,start,duration,peak){
    const osc=ctx.createOscillator(), gain=ctx.createGain();
    osc.type='sine'; osc.frequency.setValueAtTime(frequency,start);
    gain.gain.setValueAtTime(0.0001,start);
    gain.gain.linearRampToValueAtTime(peak,start+0.012);
    gain.gain.exponentialRampToValueAtTime(0.0001,start+duration);
    osc.connect(gain); gain.connect(ctx.destination); osc.start(start); osc.stop(start+duration+0.02);
  }
  function playNotificationDing(){
    if(!sound) return;
    const ctx=ensureAudioContext(); if(!ctx) return;
    const now=ctx.currentTime+0.01;
    scheduleTone(ctx,880,now,0.09,0.075);
    scheduleTone(ctx,1320,now+0.11,0.12,0.065);
  }
  setSoundLabel();
  function openNotifications(){
    box.hidden=false;
    toggle.setAttribute('aria-expanded','true');
  }
  function closeNotifications(restoreFocus){
    box.hidden=true;
    toggle.setAttribute('aria-expanded','false');
    if(restoreFocus) toggle.focus();
  }
  toggle.addEventListener('click',()=>{if(box.hidden) openNotifications(); else closeNotifications(false);});
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape'&&!box.hidden){event.preventDefault(); closeNotifications(true);}
  });
  document.addEventListener('click',event=>{
    if(!box.hidden&&!root.contains(event.target)) closeNotifications(false);
  });
  soundBtn.onclick=()=>{interacted=true; sound=!sound; localStorage.setItem('staffNotificationSound',sound?'1':'0'); if(sound){ensureAudioContext(); playNotificationDing();} setSoundLabel(); post(root.dataset.prefUrl,{enable_sound:sound?'1':'0'});};
  browserBtn.onclick=()=>{
    if(!('Notification' in window)){browserBtn.textContent='تنبيهات المتصفح غير مدعومة'; return;}
    browserBtn.textContent='جارٍ طلب إذن تنبيهات المتصفح';
    Notification.requestPermission().then(permission=>{
      browser=permission==='granted';
      browserBtn.textContent=browser?'تنبيهات المتصفح مفعّلة':permission==='denied'?'تم رفض تنبيهات المتصفح':'لم يتم تفعيل تنبيهات المتصفح';
      post(root.dataset.prefUrl,{enable_browser_notifications:browser?'1':'0'});
    },()=>{browserBtn.textContent='تعذر تفعيل تنبيهات المتصفح';});
  };
  box.addEventListener('click',e=>{const b=e.target.closest('.staff-notification-read'); if(!b) return; post(root.dataset.markReadUrl,{id:b.dataset.notificationId}).then(poll);});
  function alertNew(items){ if(!items.length) return; playNotificationDing(); if(browser&&Notification.permission==='granted'){new Notification(items[0].title||'تنبيه جديد',{body:items[0].message||'تنبيه جديد'});} }
  function render(data){
    const nextUnread=Number(data.unread_count)||0;
    badge.textContent=nextUnread;
    badge.setAttribute('aria-label',nextUnread+' تنبيهات غير مقروءة');
    if(!firstPoll&&nextUnread!==unreadCount){
      status.textContent=nextUnread===0?'لا توجد تنبيهات غير مقروءة':'عدد التنبيهات غير المقروءة: '+nextUnread;
    }
    unreadCount=nextUnread;
    box.innerHTML=data.html||'<div class="staff-notification-empty">لا توجد تنبيهات جديدة</div>';
    const latest=data.latest||[], ids=data.latest_ids||latest.map(item=>String(item.id));
    const newIds=ids.filter(id=>!seenIds.has(String(id)));
    if(!firstPoll && newIds.length) alertNew(latest.filter(item=>newIds.includes(String(item.id))));
    ids.forEach(id=>seenIds.add(String(id)));
    const first=latest[0]; if(first) lastId=String(first.id);
    firstPoll=false;
  }
  function poll(){ if(document.hidden) return; fetch(root.dataset.pollUrl+'?after='+encodeURIComponent(lastId)+'&known='+encodeURIComponent(Array.from(seenIds).join(','))).then(r=>r.json()).then(render).catch(()=>{}); }
  poll(); setInterval(poll,5000);
})();
