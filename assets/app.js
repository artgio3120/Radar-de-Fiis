(() => {
  const DATA = (window.FII_DATA && Array.isArray(window.FII_DATA.funds)) ? window.FII_DATA : {funds:[], meta:{}};
  let funds = DATA.funds.map((f,i)=>({...f,_id:i}));
  let filtered = [...funds];
  let page = 1;
  let sort = {key:'score', dir:'desc'};
  let viewMode = 'dashboard';
  let activePreset = '2em1'; // score 2em1 sempre ativo; este valor representa o perfil secundário ativo
  const $ = (s)=>document.querySelector(s);
  const $$ = (s)=>[...document.querySelectorAll(s)];
  const stateKey='fii-radar-favorites-v1';
  const favs = new Set(JSON.parse(localStorage.getItem(stateKey)||'[]'));

  const n = (v,d=0)=> Number.isFinite(+v)?+v:d;
  const fmtPct = v => v==null || !Number.isFinite(+v) ? '—' : `${(+v).toFixed(2).replace('.',',')}%`;
  const fmtPVP = v => v==null || !Number.isFinite(+v) ? '—' : (+v).toFixed(2).replace('.',',');
  const fmtBRL = v => v==null || !Number.isFinite(+v) ? '—' : new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL',maximumFractionDigits:2}).format(+v);
  const compactBRL = v => {
    if(v==null || !Number.isFinite(+v)) return '—';
    const x=+v; if(Math.abs(x)>=1e9)return `R$ ${(x/1e9).toFixed(2).replace('.',',')} bi`;
    if(Math.abs(x)>=1e6)return `R$ ${(x/1e6).toFixed(2).replace('.',',')} mi`;
    if(Math.abs(x)>=1e3)return `R$ ${(x/1e3).toFixed(1).replace('.',',')} mil`;
    return fmtBRL(x);
  };
  const median = arr => { const a=arr.filter(Number.isFinite).sort((x,y)=>x-y); if(!a.length)return null; const m=Math.floor(a.length/2); return a.length%2?a[m]:(a[m-1]+a[m])/2; };
  const esc = s => String(s??'').replace(/[&<>"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]));

  function init(){
    hydrateMeta(); populateSegments(); bind(); applyPreset('2em1');
  }

  function hydrateMeta(){
    const meta=DATA.meta||{};
    $('#sideUpdated').textContent=meta.updated_at||'snapshot local';
    $('#statusB3').textContent=meta.sources?.b3||'OK';
    $('#statusCVM').textContent=meta.sources?.cvm||'OK';
    $('#statusDiv').textContent=meta.sources?.dividends||'CVM';
    const banner=$('#dataBanner');
    if(banner){
      const awaiting=meta.state==='awaiting_update';
      const demo=!!meta.demo;
      const live=!awaiting&&!demo;
      banner.classList.toggle('live',live);
      if(awaiting){
        $('#dataBannerTitle').textContent='Aguardando primeira coleta oficial';
        $('#dataBannerText').textContent='Nenhum FII foi escolhido manualmente. Execute o workflow no GitHub para carregar automaticamente o universo completo B3 + CVM.';
        $('#dataBannerPill').textContent='PRONTO PARA SINCRONIZAR';
      }else if(demo){
        $('#dataBannerTitle').textContent='Base demonstrativa';
        $('#dataBannerText').textContent='Snapshot de interface.';
        $('#dataBannerPill').textContent='DEMO';
      }else{
        const c=meta.coverage||{};
        const coverageText=c.universe?` · universo ${c.universe} · preço ${c.with_price??'—'} · VP ${c.with_nav??'—'} · DY ${c.with_dy_12m??'—'}`:'';
        $('#dataBannerTitle').textContent='Universo automático B3 + CVM';
        $('#dataBannerText').textContent=`Última atualização: ${meta.updated_at||'—'} · ${meta.method||'2em1 Quant'}${coverageText}`;
        $('#dataBannerPill').textContent='LIVE';
      }
    }
  }

  function populateSegments(){
    const segs=[...new Set(funds.map(f=>f.segment).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'pt-BR'));
    $('#segmentFilter').innerHTML='<option value="">Todos os segmentos</option>'+segs.map(s=>`<option>${esc(s)}</option>`).join('');
  }

  function bind(){
    $('#applyFilters').addEventListener('click',()=>{activePreset='custom'; markPreset(); applyFilters();});
    $('#clearFilters').addEventListener('click',()=>applyPreset('2em1'));
    $('#refreshBtn').addEventListener('click',()=>{applyFilters(); flashButton($('#refreshBtn'),'✓ Recalculado');});
    $('#searchInput').addEventListener('input',()=>{page=1;renderTable();});
    $('#pageSize').addEventListener('change',()=>{page=1;renderTable();});
    $('#prevPage').addEventListener('click',()=>{page=Math.max(1,page-1);renderTable();});
    $('#nextPage').addEventListener('click',()=>{page++;renderTable();});
    $('#exportBtn').addEventListener('click',exportCSV);
    $('#drawerClose').addEventListener('click',closeDrawer);
    $('#drawerOverlay').addEventListener('click',closeDrawer);
    $$('.chip').forEach(b=>b.addEventListener('click',()=>applyPreset(b.dataset.preset)));
    $$('thead th[data-sort]').forEach(th=>th.addEventListener('click',()=>{ const k=th.dataset.sort; if(sort.key===k)sort.dir=sort.dir==='asc'?'desc':'asc'; else sort={key:k,dir:k==='ticker'||k==='segment'||k==='type'?'asc':'desc'}; renderTable(); }));
    $$('.nav-item').forEach(b=>b.addEventListener('click',()=>{ $$('.nav-item').forEach(x=>x.classList.remove('active')); b.classList.add('active'); viewMode=b.dataset.view; if(viewMode==='favorites'){filtered=funds.filter(f=>favs.has(f.ticker));page=1;renderAll(false);} else if(viewMode==='method'){document.querySelector('#methodPanel').scrollIntoView({behavior:'smooth'});} else {applyFilters();document.querySelector('.ranking-panel').scrollIntoView({behavior:'smooth'});} }));
  }

  function markPreset(){
    $$('.chip').forEach(b=>{
      const p=b.dataset.preset;
      const isActive = p==='2em1' ? true : p===activePreset || (p==='all' && activePreset==='2em1');
      b.classList.toggle('active', isActive);
    });
  }
  function setValues(v){ Object.entries(v).forEach(([id,val])=>{ const el=$('#'+id); if(el) el.type==='checkbox'?el.checked=!!val:el.value=val; }); }

  function applyPreset(name){
    const presets={
      // Base quantitativa 2em1: sempre ativa no score e como preset-base de triagem.
      '2em1':{dyMin:7,pvpMin:.70,pvpMax:1.05,liqMin:500000,plMin:0,vacMax:30,segmentFilter:'',typeFilter:'',excludeFlags:true},
      // Perfis enviados pelo usuário. O chip Método 2em1 permanece ativo junto com eles.
      anchor:{dyMin:7,pvpMin:.95,pvpMax:1.05,liqMin:1500000,plMin:0,vacMax:10,segmentFilter:'',typeFilter:'',excludeFlags:true},
      growth:{dyMin:8,pvpMin:.80,pvpMax:.94,liqMin:500000,plMin:0,vacMax:30,segmentFilter:'',typeFilter:'',excludeFlags:true},
      risk:{dyMin:8,pvpMin:.70,pvpMax:.84,liqMin:400000,plMin:0,vacMax:30,segmentFilter:'',typeFilter:'',excludeFlags:true},
      // "all" volta para a base 2em1, pois o método deve permanecer ativo.
      all:{dyMin:7,pvpMin:.70,pvpMax:1.05,liqMin:500000,plMin:0,vacMax:30,segmentFilter:'',typeFilter:'',excludeFlags:true}
    };
    activePreset = (name==='all' ? '2em1' : name);
    setValues(presets[name]||presets['2em1']);
    markPreset();
    applyFilters();
  }

  function applyFilters(){
    if(viewMode==='favorites') viewMode='dashboard';
    const dyMin=n($('#dyMin').value),pmin=n($('#pvpMin').value),pmax=n($('#pvpMax').value,99),liq=n($('#liqMin').value),pl=n($('#plMin').value),vac=n($('#vacMax').value,100);
    const seg=$('#segmentFilter').value,type=$('#typeFilter').value,exclude=$('#excludeFlags').checked;
    filtered=funds.filter(f=>{
      const v=f.vacancy==null?null:n(f.vacancy);
      return n(f.dy,-999)>=dyMin && n(f.pvp,-999)>=pmin && n(f.pvp,999)<=pmax && n(f.liquidity)>=liq && n(f.pl)>=pl && (v==null||v<=vac) && (!seg||f.segment===seg) && (!type||f.type===type) && (!exclude||!(f.flags||[]).some(x=>['vp_cota_diverge_pl_cotas','pvp_fora_da_faixa','vacancia_suspeita','vacancia_extrema','sem_preco'].includes(x)));
    });
    page=1; renderAll(true);
  }

  function renderAll(updateKpis=true){ if(updateKpis)renderKPIs(); renderTable(); }
  function renderKPIs(){
    $('#kpiUniverse').textContent=funds.length;
    $('#kpiApproved').textContent=filtered.length;
    $('#kpiApprovedPct').textContent=funds.length?`${((filtered.length/funds.length)*100).toFixed(1).replace('.',',')}% do universo`:'—';
    const dy=median(filtered.map(f=>n(f.dy,NaN))),pvp=median(filtered.map(f=>n(f.pvp,NaN))),scoreAvg=filtered.length?filtered.reduce((s,f)=>s+n(f.score),0)/filtered.length:null;
    $('#kpiDY').textContent=fmtPct(dy); $('#kpiPVP').textContent=fmtPVP(pvp); $('#kpiScore').textContent=scoreAvg==null?'—':scoreAvg.toFixed(1).replace('.',',');
  }

  function searched(){ const q=$('#searchInput').value.trim().toLowerCase(); return q?filtered.filter(f=>`${f.ticker} ${f.name} ${f.segment}`.toLowerCase().includes(q)):filtered; }
  function sortedRows(){
    const arr=[...searched()]; const dir=sort.dir==='asc'?1:-1;
    return arr.sort((a,b)=>{let x=a[sort.key],y=b[sort.key]; if(x==null)x=sort.dir==='asc'?Infinity:-Infinity;if(y==null)y=sort.dir==='asc'?Infinity:-Infinity;if(typeof x==='string')return x.localeCompare(y,'pt-BR')*dir;return (n(x)-n(y))*dir;});
  }

  function typeClass(t){return t==='Papel'?'paper':t==='Tijolo'?'brick':t==='Híbrido'?'hybrid':''}
  function classBadge(c){const map={'Ancoragem':'class-anchor','Oportunidade':'class-opportunity','Crescimento':'class-growth','Risco':'class-risk'};return `<span class="class-badge ${map[c]||'class-opportunity'}">${esc(c||'Oportunidade')}</span>`}

  function renderTable(){
    const arr=sortedRows(); const size=+$('#pageSize').value||20; const pages=Math.max(1,Math.ceil(arr.length/size)); page=Math.min(Math.max(1,page),pages); const start=(page-1)*size; const rows=arr.slice(start,start+size);
    $('#rankingBody').innerHTML=rows.map((f,idx)=>{
      const flags=(f.flags||[]); const flag=flags.length?`<span class="flag-icon" title="${esc(flags.join(', '))}">⚑</span>`:'';
      const fav=favs.has(f.ticker);
      return `<tr>
        <td class="rank-cell">#${start+idx+1}</td>
        <td><div class="fund-cell"><button class="fav ${fav?'active':''}" data-fav="${esc(f.ticker)}" aria-label="Favoritar">★</button><div class="ticker-wrap"><button class="ticker-btn" data-open="${esc(f.ticker)}">${esc(f.ticker)}</button><span class="fund-name">${esc(f.name||'')}</span></div></div></td>
        <td>${fmtBRL(f.price)}</td>
        <td><span class="badge">${esc(f.segment||'—')}</span></td>
        <td><span class="badge ${typeClass(f.type)}">${esc(f.type||'—')}</span></td>
        <td>${fmtPct(f.dy)}${flag}</td>
        <td>${fmtPVP(f.pvp)}</td>
        <td>${compactBRL(f.liquidity)}</td>
        <td>${compactBRL(f.pl)}</td>
        <td>${f.vacancy==null?'<span class="muted">N/A</span>':fmtPct(f.vacancy)}</td>
        <td><div class="score-cell"><div class="score-bar"><i style="width:${Math.max(0,Math.min(100,n(f.score)))}%"></i></div><strong>${n(f.score).toFixed(1).replace('.',',')}</strong></div></td>
        <td>${classBadge(f.classification)}</td>
      </tr>`;
    }).join('') || `<tr><td colspan="12" style="text-align:center;padding:34px;color:#6f8197">Nenhum FII encontrado com estes critérios.</td></tr>`;
    $('#resultsText').textContent=`Mostrando ${rows.length} de ${arr.length} FIIs`;
    $('#pageInfo').textContent=`${page} / ${pages}`; $('#prevPage').disabled=page<=1; $('#nextPage').disabled=page>=pages;
    $$('[data-fav]').forEach(b=>b.addEventListener('click',()=>toggleFav(b.dataset.fav,b)));
    $$('[data-open]').forEach(b=>b.addEventListener('click',()=>openDrawer(b.dataset.open)));
  }

  function toggleFav(ticker,button){ if(favs.has(ticker))favs.delete(ticker);else favs.add(ticker);localStorage.setItem(stateKey,JSON.stringify([...favs]));button.classList.toggle('active',favs.has(ticker));if(viewMode==='favorites'){filtered=funds.filter(f=>favs.has(f.ticker));renderAll(false);}}

  function openDrawer(ticker){
    const f=funds.find(x=>x.ticker===ticker); if(!f)return; const b=f.breakdown||{}; const flags=f.flags||[]; const score=n(f.score); $('#drawerContent').innerHTML=`
      <div class="drawer-head"><p class="eyebrow">FICHA QUANTITATIVA</p><div class="symbol">${esc(f.ticker)}</div><div class="full-name">${esc(f.name||'')}</div></div>
      <div class="drawer-score"><div class="score-ring" style="--scoredeg:${score*3.6}deg"><strong>${score.toFixed(0)}</strong></div><div class="score-meta"><strong>${esc(f.classification||'Oportunidade')}</strong><small>Score relativo para triagem. Serve para priorizar a análise qualitativa — não é recomendação de compra.</small></div></div>
      <div class="metric-grid">
        <div class="metric-card"><span>Cotação</span><strong>${fmtBRL(f.price)}</strong></div><div class="metric-card"><span>Valor patrimonial/cota</span><strong>${fmtBRL(f.nav)}</strong></div>
        <div class="metric-card"><span>P/VP</span><strong>${fmtPVP(f.pvp)}</strong></div><div class="metric-card"><span>DY 12m</span><strong>${fmtPct(f.dy)}</strong></div>
        <div class="metric-card"><span>Liquidez 30d</span><strong>${compactBRL(f.liquidity)}</strong></div><div class="metric-card"><span>Patrimônio</span><strong>${compactBRL(f.pl)}</strong></div>
        <div class="metric-card"><span>Vacância</span><strong>${f.vacancy==null?'N/A':fmtPct(f.vacancy)}</strong></div><div class="metric-card"><span>Cotistas</span><strong>${f.shareholders?new Intl.NumberFormat('pt-BR').format(f.shareholders):'—'}</strong></div>
      </div>
      <div class="breakdown"><h3>Composição do score</h3>${Object.entries({Valuation:b.valuation,Renda:b.income,Liquidez:b.liquidity,Patrimônio:b.size,Operacional:b.operational,Crescimento:b.growth}).map(([k,v])=>`<div class="break-row"><span>${k}</span><div class="break-track"><i style="width:${Math.max(0,Math.min(100,n(v)))}%"></i></div><strong>${n(v).toFixed(0)}</strong></div>`).join('')}</div>
      ${flags.length?`<div class="drawer-flags">${flags.map(x=>`<span>${esc(x)}</span>`).join('')}</div>`:''}
      <div class="source-box"><h3>Rastreabilidade</h3><p><code>B3</code> — cotação, ISIN e liquidez.</p><p><code>CVM</code> — patrimônio, VP/cota, cotistas, segmento e imóveis/vacância.</p><p><code>${esc(f.dividend_source||'CVM Informe Mensal')}</code> — DY mensal usado no acumulado de 12 meses.</p><p>Referência CVM: ${esc(f.report_date||'—')} · preço: ${esc(f.price_date||'—')}</p></div>`;
    $('#drawerOverlay').hidden=false; $('#detailDrawer').classList.add('open'); $('#detailDrawer').setAttribute('aria-hidden','false');
  }
  function closeDrawer(){ $('#detailDrawer').classList.remove('open'); $('#detailDrawer').setAttribute('aria-hidden','true'); setTimeout(()=>$('#drawerOverlay').hidden=true,180); }

  function exportCSV(){
    const rows=sortedRows(); const cols=['ticker','name','segment','type','price','dy','pvp','liquidity','pl','vacancy','score','classification'];
    const csv=[cols.join(';'),...rows.map(r=>cols.map(c=>String(r[c]??'').replaceAll(';',',')).join(';'))].join('\n');
    const blob=new Blob(['\ufeff'+csv],{type:'text/csv;charset=utf-8'}); const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='ranking_fiis.csv';a.click();URL.revokeObjectURL(a.href);
  }
  function flashButton(btn,text){const old=btn.textContent;btn.textContent=text;setTimeout(()=>btn.textContent=old,1300)}
  init();
})();
