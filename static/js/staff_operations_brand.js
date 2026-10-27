/* Operations: copy today's PIN and keep anchored secondary sections reachable. */
(function(){
  'use strict';
  const root=document.querySelector('body.staff-workspace-page [data-operations-v2]');
  if(!root)return;
  const more=root.querySelector('#ops-brand-more');
  const openAnchor=function(){
    if(!more)return;
    const hash=decodeURIComponent(location.hash||'');
    if(['#staff-more','#staff-today','#ops-brand-more'].includes(hash)){more.open=true;}
  };
  window.addEventListener('hashchange',openAnchor);
  openAnchor();
  const copy=root.querySelector('[data-copy-guest-wifi]');
  const pin=root.querySelector('[data-guest-wifi-pin]');
  if(!copy||!pin)return;
  copy.addEventListener('click',async function(){
    const value=pin.textContent.trim();
    if(!value)return;
    const status=root.querySelector('[data-copy-guest-wifi-status]');
    try{
      if(navigator.clipboard && window.isSecureContext){
        await navigator.clipboard.writeText(value);
      }else{
        const select=document.createElement('textarea');
        select.value=value;
        select.setAttribute('readonly','');
        select.style.position='fixed';
        select.style.opacity='0';
        document.body.appendChild(select);
        select.select();
        const ok=document.execCommand('copy');
        select.remove();
        if(!ok)throw new Error('Clipboard unavailable');
      }
      if(status)status.textContent='تم نسخ رمز الإنترنت';
      copy.textContent='تم النسخ';
    }catch(error){
      if(status)status.textContent='تعذر النسخ التلقائي؛ انسخ الرمز المعروض يدوياً';
      copy.textContent='انسخ يدوياً';
    }
  });
})();
