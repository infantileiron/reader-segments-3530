import {state} from '../stores/session.js';import {esc} from '../utils/html.js';import {badge} from '../components/common/status.js';import {field,select} from '../components/common/fields.js';
function card(o){
 const linked=o.assignment_id?state.assignments.find(a=>a.id===o.assignment_id):null;
 const broken=o.assignment_id&&(!linked||!linked.valid);
 const extra=o.assignment_id?` · 归群#${o.assignment_id} 基于R${o.assignment_revision}（含归群成员${(o.assigned_members||[]).length}位）`:'';
 return `<article class="rec"><h3>${esc(o.title)} ${badge(o.status)}</h3><p>分群版本 #${o.segment_id} · 群${o.cluster_id+1} · ${o.members.length}位会员${extra}</p>${broken&&o.status==='draft'?`<p class="notice">归群依据已失效：${linked?`源数据已修订至 R${linked.current_revision}`:'归群任务不存在'}，本草案不能签核，请重新起草。</p>`:''}<p>${esc(o.note)}</p><p class="member-line">${o.members.slice(0,12).map(esc).join(' / ')}${o.members.length>12?' …':''}</p>${state.user.role==='admin'&&o.status==='draft'&&!broken?`<button data-approve="${o.id}">负责人签核</button>`:''} <button data-outreach-export="${o.id}">导出方案与成员</button></article>`;
}
export function render(){
 const successful=state.segments.filter(s=>s.state==='done');
 const usable=state.assignments.filter(a=>a.valid);
 return `<div class="split"><section class="panel"><h2>内容建议，回到具体读者群</h2><p class="notice">触达方案只在站内起草与签核，不自动外发。成员在保存时冻结，与分群版本绑定；可选择并入有效归群成员，归群失效后草案不能签核，已签核名单保持冻结。避免把群号理解成会员等级。</p>${state.outreaches.map(card).join('')||'<p>尚无触达方案。先完成分群，在这里写明内容主题、渠道假设与频次限制。</p>'}</section><aside class="side"><h2>起草触达方案</h2><form id="outreach">${select('分群版本','segment_id',successful.map(s=>[s.id,s.name]))}${select('群组号','cluster_id',[0,1,2,3,4,5].map(n=>[n,'群'+(n+1)]))}${select('归群任务（可选）','assignment_id',[['','不并入归群成员'],...usable.map(a=>[a.id,`归群#${a.id} · 版本#${a.segment_id} · 归入${a.result.assigned_count}位`])])}${field('方案标题','title','text','required')}<label>内容主题、渠道与频次<textarea name="note" minlength="8" required rows="6"></textarea></label><button class="primary">保存站内草案</button></form><p>群组必须存在于所选版本；归群任务必须属于同一版本且结果有效。成员来自冻结版本与归群结果，不随档案修订漂移。</p></aside></div>`;
}
