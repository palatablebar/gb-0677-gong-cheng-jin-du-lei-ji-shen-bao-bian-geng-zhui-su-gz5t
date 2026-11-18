const $=id=>document.getElementById(id);
const state={run:null,certs:[]};
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function api(path,options){
  const r=await fetch(path,options);
  const b=await r.json().catch(()=>({}));
  if(!r.ok)throw new Error(b.message||("请求失败 "+r.status));
  return b;
}
function say(t){$("notice").textContent=t||"";}
function chip(status){
  const cls=status==="可签认"?"chip--ok":status==="冻结"?"chip--warn":"chip--bad";
  return `<span class="chip ${cls}">${esc(status)}</span>`;
}
async function reconcile(sample){
  say("对账中…");
  try{
    const period=$("period").value.trim();
    const run=await api("/api/visa/runs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({sample,period:period||undefined})});
    state.run=run;
    await refreshCerts();
    render();
    say(run.stored?"对账完成。":"同一批次已存在，沿用原对账结果，未重复放行。");
  }catch(e){say(e.message);}
}
async function refreshCerts(){
  if(!state.run)return;
  const data=await api("/api/visa/certificates?period="+encodeURIComponent(state.run.period));
  state.certs=data.certificates;
}
function render(){
  const run=state.run,m=run.metrics;
  $("run-meta").innerHTML=`批次 ${esc(run.run_id)}<br>规则 ${esc(run.manifest.rule_version)}`;
  $("kpis").innerHTML=[["本期申报",m.amount.declared],["可签认",m.amount.signable],["冻结待检",m.amount.frozen],["不予签认",m.amount.rejected],["待决事项",m.unresolved]]
    .map(([n,v])=>`<div class="kpi">${n}<strong>${esc(v)}</strong></div>`).join("");
  const banner=$("banner");
  banner.hidden=false;
  if(m.issuable){
    banner.className="banner banner--ok";
    banner.innerHTML=`<span>可签认范围已对平：申报 = 可签认 + 冻结 + 不予签认，且无待决事项。</span><b>可签发正式签证</b>`;
  }else{
    banner.className="banner banner--bad";
    banner.innerHTML=`<span>${m.balanced?"数量金额已对平，但存在待决事项。":"可签认范围未对平。"} 待决 ${m.unresolved} 项（申报隔离 ${m.declarations.quarantine} / 检验隔离 ${m.inspections.quarantine} / 检验冲突 ${m.conflicts}）。</span><b>不能形成正式签证</b>`;
  }
  $("line-count").textContent=`${m.lines.declared} 条`;
  $("lines").innerHTML=run.lines.map(l=>`<tr data-status="${esc(l.status)}"><td>${esc(l.decl_id)}</td><td>${esc(l.material)}</td><td>${esc(l.canonical)}</td><td>${esc(l.batch_no)}</td><td>${esc(l.item)}</td><td>${esc(l.qty)}</td><td>${esc(l.unit)}</td><td>${esc(l.amount)}</td><td>${esc(l.conclusion||"—")}</td><td>${esc(l.insp_id||"—")}</td><td>${chip(l.status)}${l.reason?" "+esc(l.reason):""}</td></tr>`).join("")||`<tr><td class="empty">无对账记录。</td></tr>`;
  $("by-item").innerHTML=run.by_item.map(it=>`<div class="audit-line"><span>${esc(it.item)}（${it.signable_lines}/${it.lines} 项可签）</span><b>${esc(it.signable_amount)} / ${esc(it.declared_amount)}</b></div>`).join("")||`<div class="empty">无汇总。</div>`;
  renderCerts();
  const pending=[];
  run.quarantine.forEach(q=>pending.push(`<div class="reason"><b>申报隔离 ${esc(q.reason)}</b><span>行 ${esc(q._line)} / ${esc(q.detail)}</span></div>`));
  run.inspection_quarantine.forEach(q=>pending.push(`<div class="reason"><b>检验隔离 ${esc(q.reason)}</b><span>行 ${esc(q._line)} / ${esc(q.detail)}</span></div>`));
  run.unmatched_inspections.forEach(u=>pending.push(`<div class="reason"><b>检验无对应申报</b><span>${esc(u.insp_id)} / ${esc(u.material)} / 批 ${esc(u.batch_no)}</span></div>`));
  run.conflicts.forEach(c=>pending.push(`<div class="reason"><b>检验结论冲突</b><span>${esc(c)}</span></div>`));
  $("pending-count").textContent=pending.length;
  $("pending").innerHTML=pending.join("")||`<div class="empty">无待决事项。</div>`;
  $("export").href=`/api/visa/runs/${run.run_id}/archive`;
  $("issue").disabled=!m.issuable;
  $("sig-decl").textContent=`申报 ${m.lines.declared} 项`;
  const cur=state.certs.find(c=>c.status==="现行");
  $("sig-cert").textContent=cur?`现行签证 ${cur.certificate_id}`:"尚未签发";
  $("sig-ver").textContent=cur?`版本 V${cur.version}`:"";
  $("status-right").textContent=run.manifest.generated_at.slice(0,19)+" UTC";
}
function renderCerts(){
  const cur=state.certs.find(c=>c.status==="现行");
  $("cert-tag").textContent=cur?`V${cur.version}`:"—";
  const head=cur?`<div class="audit-line"><span>${esc(cur.certificate_id)}（现行）</span><b>${esc(cur.signable_amount)}</b></div>`:`<div class="empty">本期尚未形成正式签证。</div>`;
  const hist=state.certs.map(c=>`<div class="audit-line"><span>V${c.version} ${esc(c.status)} ${esc(c.issued_at.slice(0,19))}</span><b>${esc(c.signable_amount)}</b></div>`).join("");
  $("certificate").innerHTML=head+hist;
}
$("reconcile").addEventListener("click",()=>reconcile($("dataset").value));
$("issue").addEventListener("click",async()=>{
  if(!state.run)return;
  say("签发中…");
  try{
    const cert=await api(`/api/visa/runs/${state.run.run_id}/issue`,{method:"POST"});
    await refreshCerts();
    render();
    say(cert.idempotent_replay?`${cert.certificate_id} 已放行过，未重复签发。`:`已签发 ${cert.certificate_id}（V${cert.version}）。`);
  }catch(e){say(e.message);}
});
$("print").addEventListener("click",()=>window.print());
$("upload").addEventListener("click",async()=>{
  const d=$("decl-file").files[0],i=$("insp-file").files[0];
  if(!d||!i){say("请同时选择申报记录和检验记录。");return;}
  const form=new FormData();
  form.append("declarations",d);
  form.append("inspections",i);
  const p=$("period").value.trim();
  if(p)form.append("period",p);
  say("导入并对账中…");
  try{
    const r=await fetch("/api/visa/upload",{method:"POST",body:form});
    const b=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(b.message||"导入失败");
    state.run=b;
    await refreshCerts();
    render();
    say("导入完成。");
  }catch(e){say(e.message);}
});
reconcile("story");
