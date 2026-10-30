const $=(s,r=document)=>r.querySelector(s);
const fmt=(x,d=2)=>x==null||isNaN(x)?'–':Number(x).toLocaleString('en-IN',{maximumFractionDigits:d,minimumFractionDigits:d});
const fint=x=>x==null||isNaN(x)?'–':Math.round(x).toLocaleString('en-IN');
const lakh=x=>x==null?'–':Math.abs(x)>=1e7?(x/1e7).toFixed(2)+'Cr':Math.abs(x)>=1e5?(x/1e5).toFixed(2)+'L':fint(x);
const cookie=n=>(document.cookie.split('; ').find(r=>r.startsWith(n+'='))||'').split('=')[1];
async function api(url,opt){const r=await fetch(url,opt);return r.json()}
const post=(url,body)=>api(url,{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':cookie('csrftoken')},body:JSON.stringify(body)});
function sigCard(s,mark){
 const ce=s.action==='BUY_CE',act=['OPEN','T1_HIT'].includes(s.status);
 const pnl=mark?`<span class="${mark.r>=0?'up':'dn'}">${mark.r>=0?'+':''}${mark.r}R @ ${fmt(mark.px)}</span>`:(s.r!=null?`<span class="${s.r>=0?'up':'dn'}">${s.r>=0?'+':''}${s.r}R</span>`:'');
 return `<div class="sig ${ce?'ce':'pe'}"><h4><span>${s.action.replace('_',' ')} · ${s.symbol} <span class="pill">${s.strategy}</span></span><span>${s.conf}/100</span></h4>
 <div class="mut">${new Date(s.t).toLocaleTimeString()} · ${s.status} ${pnl}</div>
 <div class="lv"><div><small>Entry (ask+slip)</small><b>${fmt(s.entry)}</b></div><div><small>Stop</small><b class="dn">${fmt(s.sl)}</b></div><div><small>T1 / T2</small><b class="up">${fmt(s.t1)} / ${fmt(s.t2)}</b></div>
 <div><small>Lot × risk</small><b>${s.lot} × ₹${fint(s.risk_rs)}</b></div><div><small>Δ / Γ / Θ / IV</small><b>${fmt(s.greeks.delta,2)} / ${fmt(s.greeks.gamma,4)} / ${fmt(s.greeks.theta,1)} / ${fmt(s.greeks.iv,1)}%</b></div></div>
 <ul>${s.reasons.map(r=>`<li>${r}</li>`).join('')}</ul>
 <details><summary class="mut">score breakdown · plan</summary><div class="mut">${Object.entries(s.ctx.score_parts||{}).map(([k,v])=>k+' '+v).join(' · ')}<br>Book half at T1 and move stop to cost; trail ${act?'':'(done)'}; exit by time-stop ${s.ctx.t1_r?'':''}or 15:15. Spot ${fmt(s.ctx.spot,0)} · needs ~${s.ctx.need_move_pts} pts to T1 · theta over hold ≈ ₹${s.ctx.theta_cost}</div>
 <button onclick="scen(${s.id},this)">What-if grid</button><div class="scen"></div></details></div>`}
async function scen(id,btn){const d=await api('/api/scenario/'+id),el=btn.nextElementSibling;
 el.innerHTML=`<table class="t"><tr><th>Spot move</th>${d.horizons.map(h=>`<th>${h?'+'+h+'m':'now'} P&L/lot</th>`).join('')}</tr>${d.grid.map(r=>`<tr><td>${r.move_pct}% (${r.spot})</td>${r.cells.map(c=>`<td class="${c.pnl>=0?'up':'dn'}">${fint(c.pnl)}</td>`).join('')}</tr>`).join('')}</table><div class="mut">Same IV held (${fmt(d.iv,1)}%). Max loss ₹${fint(d.max_loss_rs)}. Expiry breakeven ${fint(d.breakeven_at_expiry)}.</div>`}
function statsHtml(s){const o=s.overall;if(!o.n)return'No closed signals yet — outcomes appear here as paper trades hit stop / target / time-stop.';
 const row=(n,a)=>`<tr><td>${n}</td><td>${a.n}</td><td>${a.win_rate}%</td><td>${a.expectancy_r}</td><td>${a.profit_factor??'–'}</td><td>${a.total_r}</td><td>${a.max_drawdown_r}</td></tr>`;
 return `<table class="t"><tr><th></th><th>N</th><th>Win</th><th>Exp (R)</th><th>PF</th><th>Total R</th><th>Max DD</th></tr>${row('ALL',o)}${Object.entries(s.by_strategy).map(([k,v])=>row(k,v)).join('')}</table><div class="mut">Open now: ${s.open}. Expectancy &gt; 0 over a meaningful sample (50+ trades) is the only evidence a rule works.</div>`}
