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

  const UX_TRANSLATIONS=Object.freeze({
    "da":{"serviceHealth":"Tjenestestatus","dataLoaded":"Kontrolpaneldata indlæst","messagesQueued":"{count} beskeder i kø","start":"Start","restart":"Genstart","confirm":"Bekræft","cancel":"Annuller","confirmServiceAction":"Vil du {action} tjenesten {service}?"},
    "de":{"serviceHealth":"Dienststatus","dataLoaded":"Dashboarddaten geladen","messagesQueued":"{count} Nachrichten in der Warteschlange","start":"Starten","restart":"Neu starten","confirm":"Bestätigen","cancel":"Abbrechen","confirmServiceAction":"Soll der Dienst {service} wirklich {action} werden?"},
    "en":{"serviceHealth":"Service health","dataLoaded":"Dashboard data loaded","messagesQueued":"{count} messages queued","start":"Start","restart":"Restart","confirm":"Confirm","cancel":"Cancel","confirmServiceAction":"Do you want to {action} the {service} service?"},
    "es":{"serviceHealth":"Estado de los servicios","dataLoaded":"Datos del panel cargados","messagesQueued":"{count} mensajes en cola","start":"Iniciar","restart":"Reiniciar","confirm":"Confirmar","cancel":"Cancelar","confirmServiceAction":"¿Quieres {action} el servicio {service}?"},
    "fi":{"serviceHealth":"Palvelujen tila","dataLoaded":"Hallintapaneelin tiedot ladattu","messagesQueued":"{count} viestiä jonossa","start":"Käynnistä","restart":"Käynnistä uudelleen","confirm":"Vahvista","cancel":"Peruuta","confirmServiceAction":"Haluatko suorittaa toiminnon {action} palvelulle {service}?"},
    "fr":{"serviceHealth":"État des services","dataLoaded":"Données du tableau de bord chargées","messagesQueued":"{count} messages en attente","start":"Démarrer","restart":"Redémarrer","confirm":"Confirmer","cancel":"Annuler","confirmServiceAction":"Voulez-vous utiliser l’action {action} pour le service {service} ?"},
    "nb":{"serviceHealth":"Tjenestestatus","dataLoaded":"Kontrollpaneldata lastet","messagesQueued":"{count} meldinger i kø","start":"Start","restart":"Start på nytt","confirm":"Bekreft","cancel":"Avbryt","confirmServiceAction":"Vil du utføre {action} for tjenesten {service}?"},
    "nl":{"serviceHealth":"Servicestatus","dataLoaded":"Dashboardgegevens geladen","messagesQueued":"{count} berichten in de wachtrij","start":"Starten","restart":"Opnieuw starten","confirm":"Bevestigen","cancel":"Annuleren","confirmServiceAction":"Wil je de actie {action} uitvoeren voor de service {service}?"},
    "pl":{"serviceHealth":"Stan usług","dataLoaded":"Dane panelu zostały załadowane","messagesQueued":"W kolejce: {count}","start":"Uruchom","restart":"Uruchom ponownie","confirm":"Potwierdź","cancel":"Anuluj","confirmServiceAction":"Czy wykonać działanie {action} dla usługi {service}?"},
    "sv":{"serviceHealth":"Tjänstehälsa","dataLoaded":"Paneldata har lästs in","messagesQueued":"{count} meddelanden i kö","start":"Starta","restart":"Starta om","confirm":"Bekräfta","cancel":"Avbryt","confirmServiceAction":"Vill du {action} tjänsten {service}?"},
    "ja":{"serviceHealth":"サービスの状態","dataLoaded":"ダッシュボードのデータを読み込みました","messagesQueued":"{count} 件のメッセージがキューにあります","start":"開始","restart":"再起動","confirm":"確認","cancel":"キャンセル","confirmServiceAction":"サービス {service} で {action} を実行しますか？"},
    "pt":{"serviceHealth":"Estado dos serviços","dataLoaded":"Dados do painel carregados","messagesQueued":"{count} mensagens na fila","start":"Iniciar","restart":"Reiniciar","confirm":"Confirmar","cancel":"Cancelar","confirmServiceAction":"Deseja executar {action} no serviço {service}?"},
    "zh":{"serviceHealth":"服务状态","dataLoaded":"仪表板数据已加载","messagesQueued":"队列中有 {count} 条消息","start":"启动","restart":"重启","confirm":"确认","cancel":"取消","confirmServiceAction":"是否对服务 {service} 执行“{action}”？"}
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
    const message=UX_TRANSLATIONS[language]?.[key]??UX_TRANSLATIONS.en[key]??ALL_REDESIGN_TRANSLATIONS[language][key]??REDESIGN_TRANSLATIONS.en[key]??key;
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

  function setLoadMeter(){
    const load=byId('loadv');
    const cpuDetail=byId('cpud');
    const meter=byId('loadMeter');
    if(!load||!cpuDetail||!meter)return;
    const update=()=>{
      const loadValue=Number.parseFloat(load.textContent);
      const cpuCount=Number.parseFloat(cpuDetail.textContent);
      const percent=Number.isFinite(loadValue)&&Number.isFinite(cpuCount)&&cpuCount>0
        ?clamp((loadValue/cpuCount)*100):0;
      meter.style.setProperty('--hp-value',`${percent}%`);
    };
    new MutationObserver(update).observe(load,{childList:true,characterData:true,subtree:true});
    new MutationObserver(update).observe(cpuDetail,{childList:true,characterData:true,subtree:true});
    update();
  }

  function syncDashboardFreshness(){
    const status=byId('dashboardUpdated');
    const error=byId('dashboardError');
    const services=byId('svcBody');
    if(!status||!services||error&&!error.hidden)return;
    const metrics=['cpu','ram','disk','loadv'].map(byId);
    const metricsReady=metrics.every(node=>node&&node.textContent.trim()&&!['—','Loading…'].includes(node.textContent.trim()));
    const servicesReady=[...services.querySelectorAll('tr')].some(row=>!row.querySelector('.empty'));
    if(metricsReady&&servicesReady){
      status.removeAttribute('data-i18n');
      status.textContent=copy('dataLoaded');
      status.dataset.state='loaded';
    }
  }

  function syncServiceActions(rows){
    rows=rows||[...(byId('svcBody')?.querySelectorAll('tr')??[])];
    rows.forEach(row=>{
      if(row.querySelector('.empty'))return;
      const button=row.querySelector('td:last-child button');
      if(!button)return;
      const action=row.querySelector('.tag.ok')?'restart':'start';
      button.dataset.hpServiceAction=action;
      button.removeAttribute('data-i18n');
      const label=copy(action);
      if(button.textContent.trim()!==label)button.textContent=label;
    });
  }

  function bindServiceConfirmation(){
    const body=byId('svcBody');
    if(!body||byId('hpServiceDialog'))return;
    const dialog=document.createElement('dialog');
    dialog.id='hpServiceDialog';
    dialog.className='hp-service-dialog';
    dialog.setAttribute('aria-labelledby','hpServiceDialogTitle');
    const form=document.createElement('form');
    form.method='dialog';
    const title=document.createElement('h3');
    title.id='hpServiceDialogTitle';
    const message=document.createElement('p');
    const actions=document.createElement('div');
    actions.className='hp-service-dialog-actions';
    const cancel=document.createElement('button');
    cancel.className='btn';
    cancel.value='cancel';
    const confirm=document.createElement('button');
    confirm.className='btn primary';
    confirm.value='confirm';
    actions.append(cancel,confirm);
    form.append(title,message,actions);
    dialog.append(form);
    document.body.append(dialog);

    const bypass=new WeakSet();
    let pending=null;
    body.addEventListener('click',event=>{
      const button=event.target.closest('button[data-hp-service-action]');
      if(!button||!body.contains(button))return;
      if(bypass.has(button)){
        bypass.delete(button);
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      pending=button;
      const action=button.dataset.hpServiceAction;
      const service=button.closest('tr')?.querySelector('td')?.textContent.trim()||'';
      title.textContent=copy(action);
      message.textContent=copy('confirmServiceAction',{action:copy(action).toLocaleLowerCase(currentLanguage()),service});
      cancel.textContent=copy('cancel');
      confirm.textContent=copy('confirm');
      dialog.showModal();
    },true);
    dialog.addEventListener('close',()=>{
      const button=pending;
      pending=null;
      if(dialog.returnValue==='confirm'&&button&&button.isConnected){
        bypass.add(button);
        button.click();
      }else if(button&&button.isConnected){
        button.focus();
      }
    });
  }

  function updateServiceHealth(){
    const body=byId('svcBody');
    const count=byId('hpServiceCount');
    const percent=byId('hpHealthPercent');
    const ring=byId('hpHealthRing');
    const summary=byId('hpServiceSummary');
    const dot=byId('hpServicesDot');
    const alert=byId('hpHealthAlert');
    if(!body||!count||!percent||!ring||!summary)return;
    const rows=[...body.querySelectorAll('tr')].filter(row=>!row.querySelector('.empty'));
    const running=rows.filter(row=>row.querySelector('.tag.ok')).length;
    const total=rows.length;
    const healthy=total?Math.round((running/total)*100):0;
    count.textContent=total?`${running} / ${total}`:'—';
    percent.textContent=total?`${healthy}%`:'—';
    const state=!total?'unknown':running===total?'ok':healthy>=50?'degraded':'critical';
    const message=!total?copy('waitingForServices'):running===total?copy('runningCount',{running}):copy('runningOf',{running,total});
    ring.style.setProperty('--hp-health-angle',`${healthy*3.6}deg`);
    ring.dataset.state=state;
    ring.setAttribute('aria-valuenow',String(healthy));
    ring.setAttribute('aria-valuetext',message);
    ring.setAttribute('aria-label',message);
    summary.textContent=message;
    summary.dataset.state=state;
    if(dot)dot.dataset.state=state;
    if(alert){
      alert.hidden=!total||running===total;
      alert.dataset.state=state;
      alert.textContent=alert.hidden?'':message;
    }
    syncServiceActions(rows);
    syncDashboardFreshness();
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
    state.textContent=!Number.isFinite(n)?copy('waitingForQueueStatus'):n>0?copy('messagesQueued',{count:n}):copy('deliveryQueueHealthy');
    state.dataset.state=!Number.isFinite(n)?'pending':n>0?'queued':'ok';
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
    if(key&&currentLanguage()!=='en'){
      const localized=crumb.textContent.trim();
      if(localized&&localized!==key){
        const title=`HostPanel — ${localized}`;
        if(document.title!==title)document.title=title;
      }
      return;
    }
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
    if(!navLink||navLink.hidden||navLink.getAttribute('aria-hidden')==='true'||navLink.getAttribute('aria-disabled')==='true')return false;
    if(navLink.matches('[disabled],.disabled')||navLink.closest('[hidden],[aria-hidden="true"],[inert]'))return false;
    const style=getComputedStyle(navLink);
    return style.display!=='none'&&style.visibility!=='hidden'&&style.pointerEvents!=='none';
  }

  function syncPageLinkVisibility(){
    document.querySelectorAll('[data-hp-page]').forEach(control=>{
      const allowed=pageLinkAllowed(pageLinkFor(control));
      control.hidden=!allowed;
      if(allowed){
        control.style.removeProperty('display');
        control.removeAttribute('aria-disabled');
      }else{
        control.style.setProperty('display','none','important');
        control.setAttribute('aria-disabled','true');
      }
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
        attributeFilter:['hidden','aria-hidden','aria-disabled','disabled','inert','class','style']
      });
    }
    syncPageLinkVisibility();
    const crumb=byId('crumb');
    if(crumb){
      new MutationObserver(scheduleRouteLabel).observe(crumb,{childList:true,characterData:true,subtree:true});
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
    const healthTitle=byId('hpHealthTitle');
    if(healthTitle)healthTitle.textContent=copy('serviceHealth');
    const open=document.querySelector('.hp-health-row [data-hp-page="security"]');
    if(open)open.textContent=copy('open');
    const overview=document.querySelector('.hp-dashboard-rail');
    if(overview)overview.setAttribute('aria-label',copy('dashboardOverview'));
    const live=byId('dashboardUptimeRail');
    if(live&&(!live.textContent||localizedLiveValues.has(live.textContent.trim())))live.textContent=copy('live');
    updateServiceHealth();
    syncDashboardFreshness();
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
    setLoadMeter();
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
    ['cpu','ram','disk','loadv'].forEach(id=>{
      const value=byId(id);
      if(value)new MutationObserver(syncDashboardFreshness).observe(value,{childList:true,characterData:true,subtree:true});
    });
    bindServiceConfirmation();
    bindMailState();
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});
  else boot();
})();
