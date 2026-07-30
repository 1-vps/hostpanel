(function(){
  'use strict';

  const byId = id => document.getElementById(id);
  const clamp = value => Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0));
  const percentFromText = text => {
    const match = String(text || '').match(/(-?\d+(?:\.\d+)?)\s*%/);
    return match ? clamp(Number(match[1])) : 0;
  };

  function setMeter(valueId, meterId){
    const value = byId(valueId);
    const meter = byId(meterId);
    if(!value || !meter) return;
    const update = () => meter.style.setProperty('--hp-value', `${percentFromText(value.textContent)}%`);
    new MutationObserver(update).observe(value,{childList:true,characterData:true,subtree:true});
    update();
  }

  function updateServiceHealth(){
    const body = byId('svcBody');
    const count = byId('hpServiceCount');
    const percent = byId('hpHealthPercent');
    const ring = byId('hpHealthRing');
    const summary = byId('hpServiceSummary');
    if(!body || !count || !percent || !ring || !summary) return;
    const rows = [...body.querySelectorAll('tr')].filter(row => !row.querySelector('.empty'));
    const running = rows.filter(row => row.querySelector('.tag.ok')).length;
    const total = rows.length;
    const healthy = total ? Math.round((running / total) * 100) : 0;
    count.textContent = total ? `${running} / ${total}` : '—';
    percent.textContent = total ? `${healthy}%` : '—';
    ring.style.setProperty('--hp-health-angle', `${healthy * 3.6}deg`);
    summary.textContent = total && running === total ? `${running} running` : total ? `${running} of ${total} running` : 'Waiting for services';
    summary.dataset.state = total && running === total ? 'ok' : 'warn';
  }

  function mirrorText(sourceId,targetId){
    const source = byId(sourceId);
    const target = byId(targetId);
    if(!source || !target) return;
    const update = () => { target.textContent = source.textContent || '—'; };
    new MutationObserver(update).observe(source,{childList:true,characterData:true,subtree:true});
    update();
  }

  function updateMailState(){
    const count = byId('mq');
    const state = byId('hpMailState');
    if(!count || !state) return;
    const update = () => {
      const n = Number.parseInt(count.textContent,10);
      state.textContent = !Number.isFinite(n) ? 'Waiting for queue status' : n > 0 ? 'Queue requires attention' : 'Delivery queue healthy';
      state.dataset.state = !Number.isFinite(n) ? 'pending' : n > 0 ? 'warn' : 'ok';
    };
    new MutationObserver(update).observe(count,{childList:true,characterData:true,subtree:true});
    update();
  }

  function bindPageLinks(){
    document.querySelectorAll('[data-hp-page]').forEach(control => {
      const page = control.getAttribute('data-hp-page');
      const navLink = page ? document.querySelector(`#nav a[data-page="${CSS.escape(page)}"]`) : null;
      if(!page || !navLink || navLink.hidden){
        control.hidden = true;
        return;
      }
      control.addEventListener('click', event => {
        event.preventDefault();
        location.hash = `#/panel/${page}`;
      });
    });
  }

  function improveToolbar(){
    const search = byId('globalSearch');
    if(search){
      search.setAttribute('enterkeyhint','search');
      search.setAttribute('autocomplete','off');
    }
    const refresh = byId('dashboardRetry');
    if(refresh){
      refresh.setAttribute('aria-label','Refresh dashboard data');
      refresh.setAttribute('title','Refresh dashboard data');
    }
  }

  function boot(){
    document.body.classList.add('hp-redesign');
    document.body.dataset.uiVersion = '3.0.0';
    setMeter('cpu','cpuMeter');
    setMeter('ram','ramMeter');
    setMeter('disk','diskMeter');
    mirrorText('uptime','dashboardUptime');
    mirrorText('uptime','dashboardUptimeRail');
    bindPageLinks();
    improveToolbar();

    const services = byId('svcBody');
    if(services){
      new MutationObserver(updateServiceHealth).observe(services,{childList:true,subtree:true,attributes:true,attributeFilter:['class']});
      updateServiceHealth();
    }
    updateMailState();
  }

  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded',boot,{once:true});
  else boot();
})();
