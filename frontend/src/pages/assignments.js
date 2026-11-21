import {state} from '../stores/session.js';import {esc} from '../utils/html.js';import {badge} from '../components/common/status.js';import {table} from '../components/common/table.js';import {select} from '../components/common/fields.js';import {ASSIGN_STATE_LABELS,ASSIGN_MEMBER_LABELS} from '../constants/enums.js';
const label=s=>ASSIGN_STATE_LABELS[s]||s;
const retryButton=a=>state.user.role==='viewer'?'':` <button data-assignment-retry="${a.id}">按当前数据重试归群</button>`;
function detail(a){
 let head=`<h3>任务 #${a.id} · 基于版本#${a.segment_id} ${badge(label(a.state))}</h3><div class="metrics"><div><strong>${a.candidates}</strong>候选会员</div><div><strong>${a.result?a.result.assigned_count:'—'}</strong>归入</div><div><strong>${a.result?a.result.isolated_count:'—'}</strong>隔离</div><div><strong>R${a.source_revision}</strong>数据版本 / 当前R${a.current_revision}</div></div>`;
 if(a.state==='failed')return head+`<p class="notice">${esc(a.error||'任务失败')}</p><p>${retryButton(a)}</p>`;
 if(a.state!=='done')return head+'<p class="notice">任务待归群或计算中，请稍后刷新。</p>';
 let s=head;
 if(!a.valid)s+=`<p class="notice">归群结果已失效：源数据从 R${a.source_revision} 修订至 R${a.current_revision}。本结果不能再用于触达签核，可重试按当前数据重新归群。</p>`;
 s+=table(['会员编号','结果','群','标准化距离','训练最大半径','说明'],a.result.members.map(m=>[m.code,ASSIGN_MEMBER_LABELS[m.status]||m.status,m.cluster_id===null?'—':'群'+(m.cluster_id+1),m.distance.toFixed(4),m.radius.toFixed(4),m.reason||'—']));
 s+=`<p class="notice">${esc(a.result.method)}</p>`;
 if(!a.valid)s+=`<p>${retryButton(a)}</p>`;
 return s;
}
export function render(){
 const done=state.segments.filter(s=>s.state==='done');
 const a=state.assignments.find(x=>x.id===state.assignment)||state.assignments[0]||null;
 return `<div class="split"><section class="panel"><div class="result-head"><h2>新会员批量归群</h2><button id="refresh">刷新归群状态</button></div><p class="notice">沿用所选成功版本的特征、标准化尺度与群中心，把未参训会员按欧氏距离归入最近群，不重训全体分群；与多个群中心等距或距离超出该群训练最大半径的会员会被隔离并说明。归入与隔离人数合计始终等于候选数。</p>${state.assignments.map(t=>`<button class="study" data-assignment="${t.id}">#${t.id} · 版本#${t.segment_id} · ${label(t.state)} · 候选${t.candidates}${t.result?` · 归入${t.result.assigned_count} 隔离${t.result.isolated_count}`:''} · R${t.source_revision}${t.state==='done'&&!t.valid?' · 已失效':''}</button>`).join('')||'<p>尚无归群任务。出现未参训会员后，在右侧选择成功版本发起。</p>'}${a?detail(a):''}</section><aside class="side"><h2>发起归群任务</h2><form id="assignment">${select('基准成功版本','segment_id',done.map(s=>[s.id,'#'+s.id+' '+s.name]))}<button class="primary">提交批量归群</button></form><p>仅成功版本可作为基准；没有未参训会员时会被拒绝。任务失败或结果失效后可重试，重试按当前数据整体重算，不会重复入群。源数据修订会使结果失效，计算中发生修订不会发布旧结论。</p></aside></div>`;
}
