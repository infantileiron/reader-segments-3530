import os,json
import redis
from fastapi import APIRouter,Depends,HTTPException,Response
from sqlalchemy.exc import IntegrityError
from src.config.database import db
from src.models.entities import User,Reader,Segment,Outreach,Audit,Assignment
from src.schemas.business import Login,ReaderInput,SegmentInput,AssignmentInput,OutreachInput
from src.middlewares.auth import current,editor,admin
from src.utils.security import verify,token
from src.services.business import audit,dump,current_revision,locked_revision,bump_revision
from src.controllers.business import ensure_segment,ensure_assignment
router=APIRouter()
def enqueue(key,i):
 try:redis.Redis.from_url(os.environ['REDIS_URL']).rpush(key,i);return True
 except redis.RedisError:return False
def lockable(q,s):
 return q.with_for_update() if s.get_bind().dialect.name=='postgresql' else q
def assignment_view(a,rev):return dict(dump(a),current_revision=rev,valid=a.state=='done' and a.source_revision==rev)
@router.post('/auth/login')
def login(data:Login,s=Depends(db)):
 u=s.query(User).filter_by(email=data.email).first()
 if not u or not verify(data.password,u.password):raise HTTPException(401,'账号或密码错误')
 return {'token':token(u),'user':dump(u)}
@router.get('/me')
def me(u=Depends(current)):return dump(u)
@router.get('/readers')
def readers(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Reader).order_by(Reader.id).all()]
@router.post('/readers')
def create(data:ReaderInput,u=Depends(editor),s=Depends(db)):
 bump_revision(s);r=Reader(**data.model_dump());s.add(r)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'会员编号重复')
 audit(s,u,'录入阅读档案 '+r.code);return dump(r)
@router.put('/readers/{i}')
def edit(i:int,data:ReaderInput,u=Depends(editor),s=Depends(db)):
 r=s.get(Reader,i)
 if not r:raise HTTPException(404,'会员档案不存在')
 bump_revision(s)
 for k,v in data.model_dump().items():setattr(r,k,v)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'会员编号重复')
 audit(s,u,'修订档案 '+str(i));return dump(r)
@router.get('/segments')
def segments(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Segment).order_by(Segment.id.desc()).all()]
@router.post('/segments')
def train(data:SegmentInput,u=Depends(editor),s=Depends(db)):
 rows=[dump(r) for r in s.query(Reader).order_by(Reader.id).all()];r=Segment(**data.model_dump(),result={'snapshot':rows});s.add(r);s.commit()
 if not enqueue('reader:segments',r.id):r.state='failed';r.error='队列暂不可用，请稍后重试';s.commit();raise HTTPException(503,r.error)
 audit(s,u,'提交分群 '+str(r.id));return dump(r)
@router.get('/segments/{i}')
def detail(i:int,u=Depends(current),s=Depends(db)):return dump(ensure_segment(s,i))
@router.get('/segments/{i}/export')
def export(i:int,u=Depends(current),s=Depends(db)):
 r=ensure_segment(s,i)
 if r.state!='done':raise HTTPException(409,'仅成功分群可导出')
 return Response(json.dumps(dump(r),ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="reader-segments-{i}.json"'})
@router.get('/assignments')
def assignments(u=Depends(current),s=Depends(db)):
 rev=current_revision(s)
 return [assignment_view(a,rev) for a in s.query(Assignment).order_by(Assignment.id.desc()).all()]
@router.post('/assignments')
def create_assignment(data:AssignmentInput,u=Depends(editor),s=Depends(db)):
 seg=ensure_segment(s,data.segment_id)
 if seg.state!='done':raise HTTPException(409,'仅成功分群版本可用于归群')
 trained={m['id'] for m in seg.result['snapshot']}
 candidates=[r for r in s.query(Reader).all() if r.id not in trained]
 if not candidates:raise HTTPException(422,'没有未参训会员，无需归群')
 a=Assignment(segment_id=seg.id,state='pending',source_revision=current_revision(s),candidates=len(candidates));s.add(a);s.commit()
 if not enqueue('reader:assignments',a.id):a.state='failed';a.error='队列暂不可用，请稍后重试';s.commit();raise HTTPException(503,a.error)
 audit(s,u,'提交归群任务 '+str(a.id)+'（基于版本#'+str(seg.id)+'）');return assignment_view(a,current_revision(s))
@router.get('/assignments/{i}')
def assignment_detail(i:int,u=Depends(current),s=Depends(db)):return assignment_view(ensure_assignment(s,i),current_revision(s))
@router.post('/assignments/{i}/retry')
def retry_assignment(i:int,u=Depends(editor),s=Depends(db)):
 a=lockable(s.query(Assignment).filter_by(id=i),s).first()
 if not a:raise HTTPException(404,'归群任务不存在')
 rev=current_revision(s)
 if a.state in ('pending','running'):raise HTTPException(409,'任务正在归群，请等待完成')
 if a.state=='done' and a.source_revision==rev:raise HTTPException(409,'归群结果仍有效，无需重试')
 seg=ensure_segment(s,a.segment_id)
 trained={m['id'] for m in seg.result['snapshot']}
 a.state='pending';a.error='';a.result=None;a.source_revision=rev
 a.candidates=len([r for r in s.query(Reader.id).all() if r.id not in trained]);s.commit()
 if not enqueue('reader:assignments',a.id):a.state='failed';a.error='队列暂不可用，请稍后重试';s.commit();raise HTTPException(503,a.error)
 audit(s,u,'重试归群任务 '+str(a.id));return assignment_view(a,rev)
@router.get('/assignments/{i}/export')
def export_assignment(i:int,u=Depends(current),s=Depends(db)):
 a=ensure_assignment(s,i)
 if a.state!='done':raise HTTPException(409,'仅完成的归群任务可导出')
 return Response(json.dumps(assignment_view(a,current_revision(s)),ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="reader-assignment-{i}.json"'})
@router.get('/outreaches')
def outreaches(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Outreach).order_by(Outreach.id.desc()).all()]
@router.post('/outreaches')
def outreach(data:OutreachInput,u=Depends(editor),s=Depends(db)):
 r=ensure_segment(s,data.segment_id)
 if r.state!='done':raise HTTPException(409,'分群完成后才能制定触达方案')
 members=[m['code'] for m in r.result['members'] if m['cluster_id']==data.cluster_id]
 if not members:raise HTTPException(422,'该版本没有所选群组')
 extra=[];arev=None
 if data.assignment_id is not None:
  a=ensure_assignment(s,data.assignment_id)
  if a.segment_id!=data.segment_id:raise HTTPException(422,'归群任务与所选分群版本不一致')
  if a.state!='done':raise HTTPException(409,'归群任务完成后才能引用其成员')
  rev=current_revision(s)
  if a.source_revision!=rev:raise HTTPException(409,f'归群结果已失效（基于数据版本R{a.source_revision}，当前R{rev}），请重新归群后再起草')
  extra=[m['code'] for m in a.result['members'] if m['status']=='assigned' and m['cluster_id']==data.cluster_id];arev=a.source_revision
 o=Outreach(segment_id=data.segment_id,cluster_id=data.cluster_id,title=data.title,note=data.note,members=members+extra,assignment_id=data.assignment_id,assignment_revision=arev,assigned_members=extra)
 s.add(o);s.commit();audit(s,u,'起草触达方案 '+str(o.id));return dump(o)
@router.post('/outreaches/{i}/approve')
def approve(i:int,u=Depends(admin),s=Depends(db)):
 r=lockable(s.query(Outreach).filter_by(id=i),s).first()
 if not r:raise HTTPException(404,'触达方案不存在')
 if r.status!='draft':raise HTTPException(409,'已签核方案不可重复签核')
 if r.assignment_id is not None:
  rev=locked_revision(s);a=s.get(Assignment,r.assignment_id)
  if not a or a.state!='done' or a.source_revision!=r.assignment_revision or r.assignment_revision!=rev:raise HTTPException(409,f'归群结果已失效（草案基于数据版本R{r.assignment_revision}，当前R{rev}），不能签核；请重新起草')
 r.status='approved';s.commit();audit(s,u,'签核触达方案 '+str(i));return dump(r)
@router.get('/outreaches/{i}/export')
def export_outreach(i:int,u=Depends(current),s=Depends(db)):
 r=s.get(Outreach,i)
 if not r:raise HTTPException(404,'触达方案不存在')
 return Response(json.dumps(dump(r),ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="reader-outreach-{i}.json"'})
@router.get('/audits')
def audits(u=Depends(admin),s=Depends(db)):return [dump(r) for r in s.query(Audit).order_by(Audit.id.desc()).limit(100)]
