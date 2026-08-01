if(typeof window.storageGet!=='function')window.storageGet=()=>null;
if(typeof window.storageSet!=='function')window.storageSet=()=>{};

(function(){
  'use strict';

  const REDESIGN_TRANSLATIONS=Object.freeze({
    "da":{"refreshDashboardData":"Opdater dataene i kontrolpanelet","waitingForServices":"Venter på tjenester","runningCount":"{running} kører","runningOf":"{running} af {total} kører","waitingForQueueStatus":"Venter på køstatus","queueRequiresAttention":"Køen kræver opmærksomhed","deliveryQueueHealthy":"Leveringskøen fungerer normalt","open":"Åbn","dashboardOverview":"Oversigt over kontrolpanelet","live":"Live"},
    "de":{"refreshDashboardData":"Dashboarddaten aktualisieren","waitingForServices":"Warten auf Dienste","runningCount":"{running} aktiv","runningOf":"{running} von {total} aktiv","waitingForQueueStatus":"Warten auf Warteschlangenstatus","queueRequiresAttention":"Die Warteschlange erfordert Aufmerksamkeit","deliveryQueueHealthy":"Die Zustellwarteschlange funktioniert ordnungsgemäß","open":"Öffnen","dashboardOverview":"Dashboardübersicht","live":"Live"},
    "en":{"refreshDashboardData":"Refresh dashboard data","waitingForServices":"Waiting for services","runningCount":"{running} running","runningOf":"{running} of {total} running","waitingForQueueStatus":"Waiting for queue status","queueRequiresAttention":"Queue requires attention","deliveryQueueHealthy":"Delivery queue is healthy","open":"Open","dashboardOverview":"Dashboard overview","live":"Live"},
    "es":{"refreshDashboardData":"Actualizar los datos del panel","waitingForServices":"A la espera de los servicios","runningCount":"{running} en ejecución","runningOf":"{running} de {total} en ejecución","waitingForQueueStatus":"A la espera del estado de la cola","queueRequiresAttention":"La cola requiere atención","deliveryQueueHealthy":"La cola de entrega funciona correctamente","open":"Abrir","dashboardOverview":"Resumen del panel","live":"En vivo"},
    "fi":{"refreshDashboardData":"Päivitä hallintapaneelin tiedot","waitingForServices":"Odotetaan palveluja","runningCount":"{running} käynnissä","runningOf":"{running}/{total} käynnissä","waitingForQueueStatus":"Odotetaan jonon tilaa","queueRequiresAttention":"Jono vaatii huomiota","deliveryQueueHealthy":"Toimitusjono toimii normaalisti","open":"Avaa","dashboardOverview":"Hallintapaneelin yleiskatsaus","live":"Reaaliajassa"},
    "fr":{"refreshDashboardData":"Actualiser les données du tableau de bord","waitingForServices":"En attente des services","runningCount":"{running} en cours d’exécution","runningOf":"{running} sur {total} en cours d’exécution","waitingForQueueStatus":"En attente de l’état de la file d’attente","queueRequiresAttention":"La file d’attente nécessite une intervention","deliveryQueueHealthy":"La file d’attente de distribution fonctionne normalement","open":"Ouvrir","dashboardOverview":"Vue d’ensemble du tableau de bord","live":"En direct"},
    "nb":{"refreshDashboardData":"Oppdater dataene i kontrollpanelet","waitingForServices":"Venter på tjenester","runningCount":"{running} kjører","runningOf":"{running} av {total} kjører","waitingForQueueStatus":"Venter på køstatus","queueRequiresAttention":"Køen krever oppmerksomhet","deliveryQueueHealthy":"Leveringskøen fungerer normalt","open":"Åpne","dashboardOverview":"Oversikt over kontrollpanelet","live":"Sanntid"},
    "nl":{"refreshDashboardData":"Dashboardgegevens vernieuwen","waitingForServices":"Wachten op services","runningCount":"{running} actief","runningOf":"{running} van {total} actief","waitingForQueueStatus":"Wachten op de wachtrijstatus","queueRequiresAttention":"De wachtrij vereist aandacht","deliveryQueueHealthy":"De verzendwachtrij werkt normaal","open":"Openen","dashboardOverview":"Dashboardoverzicht","live":"Live"},
    "pl":{"refreshDashboardData":"Odśwież dane panelu","waitingForServices":"Oczekiwanie na usługi","runningCount":"Aktywne: {running}","runningOf":"Aktywne: {running} z {total}","waitingForQueueStatus":"Oczekiwanie na stan kolejki","queueRequiresAttention":"Kolejka wymaga uwagi","deliveryQueueHealthy":"Kolejka wysyłkowa działa prawidłowo","open":"Otwórz","dashboardOverview":"Przegląd panelu","live":"Na żywo"},
    "sv":{"refreshDashboardData":"Uppdatera data i kontrollpanelen","waitingForServices":"Väntar på tjänster","runningCount":"{running} körs","runningOf":"{running} av {total} körs","waitingForQueueStatus":"Väntar på köstatus","queueRequiresAttention":"Kön kräver åtgärd","deliveryQueueHealthy":"E-postkön fungerar normalt","open":"Öppna","dashboardOverview":"Översikt över kontrollpanelen","live":"Realtid"}
  });

  const FUTURE_REDESIGN_TRANSLATIONS=Object.freeze({
    "ja":{"refreshDashboardData":"ダッシュボードのデータを更新","waitingForServices":"サービスを待機しています","runningCount":"{running} 件稼働中","runningOf":"{total} 件中 {running} 件稼働中","waitingForQueueStatus":"キューの状態を待機しています","queueRequiresAttention":"キューの確認が必要です","deliveryQueueHealthy":"配信キューは正常です","open":"開く","dashboardOverview":"ダッシュボードの概要","live":"リアルタイム"},
    "pt":{"refreshDashboardData":"Atualizar os dados do painel","waitingForServices":"Aguardando serviços","runningCount":"{running} em execução","runningOf":"{running} de {total} em execução","waitingForQueueStatus":"Aguardando o status da fila","queueRequiresAttention":"A fila requer atenção","deliveryQueueHealthy":"A fila de entrega está normal","open":"Abrir","dashboardOverview":"Visão geral do painel","live":"Em tempo real"},
    "zh":{"refreshDashboardData":"刷新仪表板数据","waitingForServices":"正在等待服务","runningCount":"{running} 个正在运行","runningOf":"{total} 个中有 {running} 个正在运行","waitingForQueueStatus":"正在等待队列状态","queueRequiresAttention":"队列需要处理","deliveryQueueHealthy":"投递队列运行正常","open":"打开","dashboardOverview":"仪表板概览","live":"实时"}
  });

  const ALL_REDESIGN_TRANSLATIONS=Object.freeze({
    ...REDESIGN_TRANSLATIONS,
    ...FUTURE_REDESIGN_TRANSLATIONS
  });

  const byId=id=>document.getElementById(id);
  const clamp=value=>Math.max(0,Math.min(100,Number.isFinite(value)?value:0));
  const localizedLiveValues=new Set(Object.values(ALL_REDESIGN_TRANSLATIONS).map(messages=>messages.live));
  const currentLanguage=()=>{
    const picker=byId('languageSelect');
    const candidate=String(picker?.value||document.documentElement.lang||'en').toLowerCase().split('-')[0];
    return Object.hasOwn(ALL_REDESIGN_TRANSLATIONS,candidate)?candidate:'en';
  };
  const copy=(key,vars={})=>{
    const language=currentLanguage();
    const message=ALL_REDESIGN_TRANSLATIONS[language][key]??REDESIGN_TRANSLATIONS.en[key]??key;
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

  function routeLink(){
    const match=location.hash.match(/^#\/panel\/([^/?#]+)/);
    const active=document.querySelector('#nav a.active[data-page]');
    const page=match?.[1]||active?.getAttribute('data-page')||'dashboard';
    return document.querySelector(`#nav a[data-page="${CSS.escape(page)}"]`);
  }

  function syncRouteLabel(){
    const navLink=routeLink();
    const label=navLink?.textContent?.trim();
    const crumb=byId('crumb');
    if(!label||!crumb)return;
    const key=navLink.querySelector('[data-i18n]')?.getAttribute('data-i18n')||navLink.getAttribute('data-i18n');
    if(key&&crumb.getAttribute('data-i18n')!==key)crumb.setAttribute('data-i18n',key);
    if(crumb.textContent.trim()!==label)crumb.textContent=label;
    const title=`HostPanel — ${label}`;
    if(document.title!==title)document.title=title;
  }

  function scheduleRouteLabel(){
    queueMicrotask(syncRouteLabel);
    setTimeout(syncRouteLabel,0);
  }

  function pageLinkFor(control){
    const page=control.getAttribute('data-hp-page');
    return page?document.querySelector(`#nav a[data-page="${CSS.escape(page)}"]`):null;
  }

  function pageLinkAllowed(navLink){
    return Boolean(navLink&&!navLink.hidden&&navLink.getAttribute('aria-hidden')!=='true');
  }

  function syncPageLinkVisibility(){
    document.querySelectorAll('[data-hp-page]').forEach(control=>{
      const allowed=pageLinkAllowed(pageLinkFor(control));
      control.hidden=!allowed;
      if(allowed)control.removeAttribute('aria-disabled');
      else control.setAttribute('aria-disabled','true');
    });
  }

  function bindPageLinks(){
    document.querySelectorAll('[data-hp-page]').forEach(control=>{
      const page=control.getAttribute('data-hp-page');
      control.addEventListener('click',event=>{
        const navLink=pageLinkFor(control);
        if(!page||!pageLinkAllowed(navLink)){
          event.preventDefault();
          return;
        }
        event.preventDefault();
        navLink.click();
        if(location.hash!==`#/panel/${page}`)location.hash = `#/panel/${page}`;
        scheduleRouteLabel();
      });
    });
    const nav=byId('nav');
    if(nav){
      new MutationObserver(syncPageLinkVisibility).observe(nav,{
        subtree:true,
        childList:true,
        attributes:true,
        attributeFilter:['hidden','aria-hidden','class','style']
      });
    }
    syncPageLinkVisibility();
    const crumb=byId('crumb');
    if(crumb){
      new MutationObserver(()=>{
        const label=routeLink()?.textContent?.trim();
        if(label&&crumb.textContent.trim()!==label)scheduleRouteLabel();
      }).observe(crumb,{childList:true,characterData:true,subtree:true});
    }
    window.addEventListener('hashchange',scheduleRouteLabel);
    scheduleRouteLabel();
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
    if(live&&(!live.textContent||localizedLiveValues.has(live.textContent.trim())))live.textContent=copy('live');
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
    let activeLanguage=currentLanguage();
    if(picker){
      picker.value=activeLanguage;
      picker.addEventListener('change',()=>{
        activeLanguage=currentLanguage();
        queueMicrotask(applyLocalizedCopy);
      });
    }
    new MutationObserver(()=>{
      const nextLanguage=currentLanguage();
      if(nextLanguage===activeLanguage)return;
      activeLanguage=nextLanguage;
      queueMicrotask(()=>{
        if(picker&&picker.value!==activeLanguage)picker.value=activeLanguage;
        applyLocalizedCopy();
        syncRouteLabel();
      });
    }).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});
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