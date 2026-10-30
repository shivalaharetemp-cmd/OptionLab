let S={sym:null,rows:[],m:null,charts:{},ws:null,sigv:-1,tab:'signals',full:false,hist:[]};
const COL={up:'#26a269',dn:'#e5484d',acc:'#4c8dff',warn:'#e0a030',mut:'#8b96a5'};
function chart(id,type,labels,datasets,opts={}){const c=S.charts[id];
 if(c){c.data.labels=labels;c.data.datasets.forEach((d,i)=>Object.assign(d,datasets[i]));c.update('none');return}
 S.charts[id]=new Chart($('#c-'+id),{type,data:{labels,datasets},options:Object.assign({animation:false,maintainAspectRatio:false,responsive:true,interaction:{mode:'index',intersect:false},
  plugins:{legend:{labels:{color:COL.mut,boxWidth:10}}},scales:{x:{ticks:{color:COL.mut,maxTicksLimit:14},grid:{color:'#1c222b'}},y:{ticks:{color:COL.mut},grid:{color:'#1c222b'}}}},opts)})}
function ds(l,d,c,x={}){return Object.assign({label:l,data:d,backgroundColor:c+'cc',borderColor:c,borderWidth:1.5,pointRadius:0,spanGaps:true},x)}
function win(){const m=S.m,s=m.series,st=m.step,lo=m.atm-14*st,hi=m.atm+14*st;const ix=s.strikes.map((k,i)=>k>=lo&&k<=hi?i:-1).filter(i=>i>=0);return{ix,pick:a=>ix.map(i=>a[i]),k:ix.map(i=>s.strikes[i])}}

function renderKpis(){const m=S.m;if(!m)return;const live=S.live||{};const chg=live.spot_chg;
 const k=[['Spot',fmt(live.spot??S.spot,2)+(chg!=null?` <small class="${chg>=0?'up':'dn'}">${chg>=0?'+':''}${fmt(chg,1)}</small>`:'')],['India VIX',fmt(S.vix,2)],['ATM',fint(m.atm)],
 ['PCR (OI)',fmt(m.pcr_oi,2)],['Max pain',fint(m.max_pain)],['Call wall',fint(m.call_wall)],['Put wall',fint(m.put_wall)],['ATM IV',fmt(m.atm_iv,1)+'%'],
 ['25Δ skew',m.skew25==null?'–':fmt(m.skew25,1)+' pts'],['Straddle',fmt(m.straddle,1)+` <small>±${fmt(m.expected_move_pct,2)}%</small>`],['Lot',fint(m.lot)],
 ['Net GEX proxy',(m.gex_proxy>=0?'+':'')+lakh(m.gex_proxy)]];
 $('#kpis').innerHTML=k.map(([a,b])=>`<div class="kpi"><small>${a}</small><b>${b}</b></div>`).join('')}

function renderChain(){const m=S.m,rows=S.rows;if(!m)return;const st=m.step,n=S.full?999:12;
 const vis=rows.filter(r=>Math.abs(r.strike-m.atm)<=n*st);const mx=Math.max(...vis.map(r=>Math.max(r.ce?.oi||0,r.pe?.oi||0)),1);
 const c=(s,f,d)=>s?fmt(s[f],d):'–',oi=(s,col,w)=>s?`<td class="bar ${w||''}"><i style="background:${col};width:${s.oi/mx*100}%"></i><span>${lakh(s.oi)}</span></td>`:'<td>–</td>';
 const doi=s=>s?`<td class="${s.oi-s.prev_oi>=0?'up':'dn'}">${lakh(s.oi-s.prev_oi)}</td>`:'<td>–</td>';
 let h=`<tr><th colspan=9>CALLS</th><th>Strike</th><th colspan=9>PUTS</th></tr><tr><th>OI</th><th>ΔOI</th><th>Vol</th><th>IV</th><th>Δ</th><th>Γ</th><th>Θ</th><th>Bid/Ask</th><th>LTP</th><th></th><th>LTP</th><th>Bid/Ask</th><th>Θ</th><th>Γ</th><th>Δ</th><th>IV</th><th>Vol</th><th>ΔOI</th><th>OI</th></tr>`;
 for(const r of vis){const a=r.strike===m.atm,ce=r.ce,pe=r.pe,w=(r.strike===m.call_wall?'wall':''),w2=(r.strike===m.put_wall?'wall':'');
  h+=`<tr class="${a?'atm':''}">${oi(ce,COL.dn,w)}${doi(ce)}<td>${lakh(ce?.vol)}</td><td>${c(ce,'iv',1)}</td><td>${c(ce,'delta',2)}</td><td>${c(ce,'gamma',4)}</td><td>${c(ce,'theta',1)}</td><td>${ce?fmt(ce.bid,1)+'/'+fmt(ce.ask,1):'–'}</td><td data-k="${ce?.key}"><b>${c(ce,'ltp',2)}</b></td>
  <td class="k">${fint(r.strike)}</td><td data-k="${pe?.key}"><b>${c(pe,'ltp',2)}</b></td><td>${pe?fmt(pe.bid,1)+'/'+fmt(pe.ask,1):'–'}</td><td>${c(pe,'theta',1)}</td><td>${c(pe,'gamma',4)}</td><td>${c(pe,'delta',2)}</td><td>${c(pe,'iv',1)}</td><td>${lakh(pe?.vol)}</td>${doi(pe)}${oi(pe,COL.up,w2)}</tr>`}
 $('#chain').innerHTML=h}

function renderCharts(){const m=S.m;if(!m||!m.series)return;const w=win(),s=m.series,k=w.k;
 if(S.tab==='oi'){chart('oi','bar',k,[ds('Call OI',w.pick(s.call_oi),COL.dn),ds('Put OI',w.pick(s.put_oi),COL.up)]);
  chart('doi','bar',k,[ds('Call ΔOI',w.pick(s.call_doi),COL.dn),ds('Put ΔOI',w.pick(s.put_doi),COL.up)]);
  chart('gex','bar',k,[ds('Net GEX',w.pick(s.gex),COL.acc)]);
  const near=S.rows.filter(r=>Math.abs(r.strike-m.atm)<=5*m.step),nm={long_buildup:'Long build-up',short_buildup:'Short build-up',long_unwinding:'Long unwinding',short_covering:'Short covering',neutral:'–'};
  const bu=s=>{if(!s)return'neutral';const dp=s.ltp-s.close,d=s.oi-s.prev_oi;return !dp||!d?'neutral':dp>0&&d>0?'long_buildup':dp<0&&d>0?'short_buildup':dp<0?'long_unwinding':'short_covering'};
  $('#build').innerHTML=`<table class="t"><tr><th>Strike</th><th>CE</th><th>PE</th></tr>${near.map(r=>`<tr><td>${fint(r.strike)}</td><td>${nm[bu(r.ce)]}</td><td>${nm[bu(r.pe)]}</td></tr>`).join('')}</table>`}
 if(S.tab==='greeks'){chart('iv','line',k,[ds('Call IV',w.pick(s.call_iv),COL.dn),ds('Put IV',w.pick(s.put_iv),COL.up)]);
  chart('gam','line',k,[ds('Call Γ',w.pick(s.call_gamma),COL.dn),ds('Put Γ',w.pick(s.put_gamma),COL.up)]);
  chart('the','line',k,[ds('Call Θ',w.pick(s.call_theta),COL.dn),ds('Put Θ',w.pick(s.put_theta),COL.up)]);
  chart('veg','line',k,[ds('Call vega',w.pick(s.call_vega),COL.dn),ds('Put vega',w.pick(s.put_vega),COL.up)])}
 if(S.tab==='time'&&S.hist.length){const L=S.hist.map(h=>new Date(h.ts*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}));
  chart('spot','line',L,[ds('Spot',S.hist.map(h=>h.spot),COL.acc),ds('Max pain',S.hist.map(h=>h.max_pain),COL.warn,{borderDash:[5,4]})]);
  chart('pcr','line',L,[ds('PCR',S.hist.map(h=>h.pcr),COL.up)]);chart('aiv','line',L,[ds('ATM IV',S.hist.map(h=>h.atm_iv),COL.warn)])}}

async function loadSignals(){const [a,b,c]=await Promise.all([api('/api/signals?active=1&symbol='+S.sym),api('/api/signals?limit=12&symbol='+S.sym),api('/api/stats')]);
 S.active=a.signals;const act=new Set(a.signals.map(s=>s.id));const rest=b.signals.filter(s=>!act.has(s.id));
 $('#sigs').innerHTML=(a.signals.map(s=>sigCard(s,(S.live?.marks||{})[s.id])).join('')||'<span class="mut">No active signal right now.</span>')+(rest.length?'<h3 class="mut" style="margin-top:12px">Recent closed</h3>'+rest.map(s=>sigCard(s)).join(''):'');
 $('#stats').innerHTML=statsHtml(c)}

function applyChain(d){if(!d.ready){$('#err').style.display='block';$('#err').innerHTML='Waiting for the first option-chain snapshot… '+((d.errors||[]).map(e=>`${e.where}: ${e.msg}`).join(' | '));return}
 $('#err').style.display='none';S.rows=d.rows;S.m=d.metrics;S.spot=d.spot;S.vix=d.vix;S.hist=d.history||[];
 $('#notes').innerHTML=(d.notes||[]).map(n=>`${n.t} ${n.sym}: ${n.msg}`).join('<br>')||'Nothing skipped recently.';
 const se=$('#sym');if(!se.options.length)d.symbols.forEach(x=>se.add(new Option(x,x)));se.value=d.symbol;
 const ee=$('#exp');ee.innerHTML='';d.expiries.forEach(x=>ee.add(new Option(x,x)));ee.value=d.expiry;
 renderKpis();renderChain();renderCharts()}

function applyLive(d){S.live=d;$('#clock').textContent=new Date(d.now).toLocaleTimeString()+(d.now&&S.virtual?' (sim)':'');
 const f=d.feed;$('#feed').className='pill '+(f.connected?'ok':'bad');$('#feed').textContent=f.connected?`ws ${f.subscribed} keys`:'ws down'+(f.error?': '+f.error.slice(0,40):'');
 for(const[key,t]of Object.entries(d.ticks)){const td=document.querySelector(`td[data-k="${key}"]`);if(td&&t[0]!=null){const o=td.dataset.p;td.firstChild.textContent=fmt(t[0],2);if(o&&o!=t[0]){td.classList.remove('flash');void td.offsetWidth;td.classList.add('flash')}td.dataset.p=t[0]}}
 if(S.m){renderKpis()}
 if(d.sigv!==S.sigv){S.sigv=d.sigv;loadSignals()}else{for(const[id,mk]of Object.entries(d.marks||{})){}}
 $('#eng').className='pill ok';$('#eng').textContent='engine on'}

function connect(){const ws=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/ws/live/');S.ws=ws;
 ws.onopen=()=>ws.send(JSON.stringify({symbol:S.sym}));
 ws.onmessage=e=>{const d=JSON.parse(e.data);d.t==='chain'?(applyChain(d),S.sigv=-1):applyLive(d)};
 ws.onclose=()=>{$('#feed').className='pill bad';$('#feed').textContent='browser ws reconnecting…';setTimeout(connect,2000)}}

api('/api/profile').then(p=>{const e=$('#prof'),t=p.token?.expires_in_days!=null?` · token ${p.token.expires_in_days}d left`:'';
 if(p.ok){const u=p.profile;e.className='pill ok';e.textContent=(u.user_name||u.user_id||'account')+t;e.title=JSON.stringify({id:u.user_id,email:u.email,broker:u.broker,exchanges:u.exchanges,products:u.products},null,1)}
 else{e.className='pill warn';e.textContent='profile: '+(p.reason==='demo mode'?'demo':'needs static IP')+t;e.title=(p.hint||p.reason||'')}});
(async()=>{const d=await api('/api/state');S.sym=d.symbol;S.virtual=d.virtual_clock;applyChain(d);loadSignals();connect();
 $('#sym').onchange=e=>{S.sym=e.target.value;S.ws.send(JSON.stringify({symbol:S.sym}));S.sigv=-1};
 $('#exp').onchange=async e=>{await post('/api/select',{symbol:S.sym,expiry:e.target.value})};
 $('#tabs').onclick=e=>{const t=e.target.dataset?.t;if(!t)return;S.tab=t;document.querySelectorAll('#tabs button').forEach(b=>b.classList.toggle('on',b===e.target));
  document.querySelectorAll('main section').forEach(s=>s.style.display=s.id==='t-'+t?'':'none');renderCharts()};
 setInterval(()=>{if(S.tab==='signals')loadSignals()},15000)})();
