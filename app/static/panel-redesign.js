(function(){
  'use strict';

  const REDESIGN_TRANSLATIONS=Object.freeze({
    "da":{"refreshDashboardData":"Opdater dashboarddata","waitingForServices":"Venter på tjenester","runningCount":"{running} kører","runningOf":"{running} af {total} kører","waitingForQueueStatus":"Venter på køstatus","queueRequiresAttention":"Køen kræver opmærksomhed","deliveryQueueHealthy":"Leveringskøen fungerer normalt","open":"Åbn","dashboardOverview":"Dashboardoversigt","live":"Live"},
    "de":{"refreshDashboardData":"Dashboard-Daten aktualisieren","waitingForServices":"Warten auf Dienste","runningCount":"{running} aktiv","runningOf":"{running} von {total} aktiv","waitingForQueueStatus":"Warten auf Warteschlangenstatus","queueRequiresAttention":"Die Warteschlange erfordert Aufmerksamkeit","deliveryQueueHealthy":"Die Zustellwarteschlange ist in Ordnung","open":"Öffnen","dashboardOverview":"Dashboard-Übersicht","live":"Live"},
    "en":{"refreshDashboardData":"Refresh dashboard data","waitingForServices":"Waiting for services","runningCount":"{running} running","runningOf":"{running} of {total} running","waitingForQueueStatus":"Waiting for queue status","queueRequiresAttention":"Queue requires attention","deliveryQueueHealthy":"Delivery queue healthy","open":"Open","dashboardOverview":"Dashboard overview","live":"Live"},
    "es":{"refreshDashboardData":"Actualizar los datos del panel","waitingForServices":"Esperando a los servicios","runningCount":"{running} en ejecución","runningOf":"{running} de {total} en ejecución","waitingForQueueStatus":"Esperando el estado de la cola","queueRequiresAttention":"La cola requiere atención","deliveryQueueHealthy":"La cola de entrega funciona correctamente","open":"Abrir","dashboardOverview":"Resumen del panel","live":"En vivo"},
    "fi":{"refreshDashboardData":"Päivitä hallintapaneelin tiedot","waitingForServices":"Odotetaan palveluita","runningCount":"{running} käynnissä","runningOf":"{running}/{total} käynnissä","waitingForQueueStatus":"Odotetaan jonon tilaa","queueRequiresAttention":"Jono vaatii huomiota","deliveryQueueHealthy":"Toimitusjono toimii normaalisti","open":"Avaa","dashboardOverview":"Hallintapaneelin yleiskatsaus","live":"Reaaliaikainen"},
    "fr":{"refreshDashboardData":"Actualiser les données du tableau de bord","waitingForServices":"En attente des services","runningCount":"{running} en cours d’exécution","runningOf":"{running} sur {total} en cours d’exécution","waitingForQueueStatus":"En attente de l’état de la file","queueRequiresAttention":"La file nécessite votre attention","deliveryQueueHealthy":"La file de livraison fonctionne normalement","open":"Ouvrir","dashboardOverview":"Vue d’ensemble du tableau de bord","live":"En direct"},
    "nb":{"refreshDashboardData":"Oppdater dataene i kontrollpanelet","waitingForServices":"Venter på tjenester","runningCount":"{running} kjører","runningOf":"{running} av {total} kjører","waitingForQueueStatus":"Venter på køstatus","queueRequiresAttention":"Køen krever oppmerksomhet","deliveryQueueHealthy":"Leveringskøen fungerer normalt","open":"Åpne","dashboardOverview":"Oversikt over kontrollpanelet","live":"Sanntid"},
    "nl":{"refreshDashboardData":"Dashboardgegevens vernieuwen","waitingForServices":"Wachten op services","runningCount":"{running} actief","runningOf":"{running} van {total} actief","waitingForQueueStatus":"Wachten op wachtrijstatus","queueRequiresAttention":"De wachtrij vereist aandacht","deliveryQueueHealthy":"De bezorgwachtrij is in orde","open":"Openen","dashboardOverview":"Dashboardoverzicht","live":"Live"},
    "pl":{"refreshDashboardData":"Odśwież dane panelu","waitingForServices":"Oczekiwanie na usługi","runningCount":"{running} działa","runningOf":"{running} z {total} działa","waitingForQueueStatus":"Oczekiwanie na stan kolejki","queueRequiresAttention":"Kolejka wymaga uwagi","deliveryQueueHealthy":"Kolejka dostarczania działa prawidłowo","open":"Otwórz","dashboardOverview":"Przegląd panelu","live":"Na żywo"},
    "sv":{"refreshDashboardData":"Uppdatera data i kontrollpanelen","waitingForServices":"Väntar på tjänster","runningCount":"{running} körs","runningOf":"{running} av {total} körs","waitingForQueueStatus":"Väntar på köstatus","queueRequiresAttention":"Kön kräver åtgärd","deliveryQueueHealthy":"Leveranskön fungerar normalt","open":"Öppna","dashboardOverview":"Översikt över kontrollpanelen","live":"Realtid"}
  });

  const byId=id=>document.getElementById(id);
  const clamp=value=>Math.max(0,Math.min(100,Number.isFinite(value)?value:0));
  const currentLanguage=()=>{
    const picker=byId('languageSelect');
    const candidate=String(picker?.value||document.documentElement.lang||'en').toLowerCase().split('-')[0];
    return Object.hasOwn(REDESIGN_TRANSLATIONS,candidate)?candidate:'en';
  };
  const copy=(key,vars={})=>{
    const language=currentLanguage();
    const message=REDESIGN_TRANSLATIONS[language][key]??REDESIGN_TRANSLATIONS.en[key]??key;
    return String(message).replace(/\{(\w+)\}/g,(_,name)=>Object.hasOwn(vars,name)?String(vars[name]):`{${name}}`);
  };
  const percentFromText=text=>{
    const match=String(text||'').match(/(-?\d+(?:\.\d+)?)\s*%/);
    return match?clamp(Number(match[1])):0;
  };

  function setMeter(valueId,meterId){
    const value=byId(valueId);
    const meter=byId(meterId);
    if(!value||!meter)return;
    const update=()=>meter.style.setProperty('--hp-value',`${percentFromText(value.textContent)}%`);
    new MutationObserver(update).observe(value,{childList:true,characterData:true,subtree:true});
    update();
  }

  function updateServiceHealth(){
    const body=byId('svcBody');
    const count=byId('hpServiceCount');
    const percent=byId('hpHealthPercent');
    const ring=byId('hpHealthRing');
    const summary=byId('hpServiceSummary');
    if(!body||!count||!percent||!ring||!summary)return;
    const rows=[...body.querySelectorAll('tr')].filter(row=>!row.querySelector('.empty'));
    const running=rows.filter(row=>row.querySelector('.tag.ok')).length;
    const total=rows.length;
    const healthy=total?Math.round((running/total)*100):0;
    count.textContent=total?`${running} / ${total}`:'—';
    percent.textContent=total?`${healthy}%`:'—';
    ring.style.setProperty('--hp-health-angle',`${healthy*3.6}deg`);
    summary.textContent=!total?copy('waitingForServices'):running===total?copy('runningCount',{running}):copy('runningOf',{running,total});
    summary.dataset.state=total&&running===total?'ok':'warn';
  }

  function mirrorText(sourceId,targetId){
    const source=byId(sourceId);
    const target=byId(targetId);
    if(!source||!target)return;
    const update=()=>{target.textContent=source.textContent||'—';};
    new MutationObserver(update).observe(source,{childList:true,characterData:true,subtree:true});
    update();
  }

  function renderMailState(){
    const count=byId('mq');
    const state=byId('hpMailState');
    if(!count||!state)return;
    const n=Number.parseInt(count.textContent,10);
    state.textContent=!Number.isFinite(n)?copy('waitingForQueueStatus'):n>0?copy('queueRequiresAttention'):copy('deliveryQueueHealthy');
    state.dataset.state=!Number.isFinite(n)?'pending':n>0?'warn':'ok';
  }

  function bindMailState(){
    const count=byId('mq');
    if(!count)return;
    new MutationObserver(renderMailState).observe(count,{childList:true,characterData:true,subtree:true});
    renderMailState();
  }

  function bindPageLinks(){
    document.querySelectorAll('[data-hp-page]').forEach(control=>{
      const page=control.getAttribute('data-hp-page');
      const navLink=page?document.querySelector(`#nav a[data-page="${CSS.escape(page)}"]`):null;
      if(!page||!navLink||navLink.hidden){
        control.hidden=true;
        return;
      }
      control.addEventListener('click',event=>{
        event.preventDefault();
        location.hash=`#/panel/${page}`;
      });
    });
  }

  function applyLocalizedCopy(){
    const refresh=byId('dashboardRetry');
    if(refresh){
      refresh.setAttribute('aria-label',copy('refreshDashboardData'));
      refresh.setAttribute('title',copy('refreshDashboardData'));
    }
    const open=document.querySelector('.hp-health-row [data-hp-page="security"]');
    if(open)open.textContent=copy('open');
    const overview=document.querySelector('.hp-dashboard-rail');
    if(overview)overview.setAttribute('aria-label',copy('dashboardOverview'));
    const live=byId('dashboardUptimeRail');
    if(live&&(!live.textContent||['Live','Realtid','En vivo','Reaaliaikainen','En direct','Sanntid','Na żywo'].includes(live.textContent.trim())))live.textContent=copy('live');
    updateServiceHealth();
    renderMailState();
  }

  function improveToolbar(){
    const search=byId('globalSearch');
    if(search){
      search.setAttribute('enterkeyhint','search');
      search.setAttribute('autocomplete','off');
    }
    applyLocalizedCopy();
  }

  function bindLanguage(){
    const picker=byId('languageSelect');
    if(picker)picker.addEventListener('change',()=>queueMicrotask(applyLocalizedCopy));
    new MutationObserver(()=>queueMicrotask(applyLocalizedCopy)).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});
  }

  function boot(){
    document.body.classList.add('hp-redesign');
    document.body.dataset.uiVersion='3.0.0';
    setMeter('cpu','cpuMeter');
    setMeter('ram','ramMeter');
    setMeter('disk','diskMeter');
    mirrorText('uptime','dashboardUptime');
    mirrorText('uptime','dashboardUptimeRail');
    bindPageLinks();
    bindLanguage();
    improveToolbar();

    const services=byId('svcBody');
    if(services){
      new MutationObserver(updateServiceHealth).observe(services,{childList:true,subtree:true,attributes:true,attributeFilter:['class']});
      updateServiceHealth();
    }
    bindMailState();
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});
  else boot();
})();
