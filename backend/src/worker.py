import os,time,json,logging
from pathlib import Path
import redis
from src.config.database import Session
from src.models.entities import Segment,Assignment,Reader
from src.services.analysis import cluster,assign
from src.services.business import locked_revision
r=redis.Redis.from_url(os.environ['REDIS_URL'])
def recover():
 with Session() as s:
  for pending in s.query(Segment).filter(Segment.state.in_(['queued','running'])).all():
   pending.state='queued';r.rpush('reader:segments',pending.id)
  for pending in s.query(Assignment).filter(Assignment.state.in_(['pending','running'])).all():
   pending.state='pending';r.rpush('reader:assignments',pending.id)
  s.commit()
def run_segment(i):
 with Session() as s:
  row=s.get(Segment,i)
  if not row or row.state!='queued':return
  snapshot=row.result['snapshot'];row.state='running';s.commit()
  try:
   row.result={**cluster(snapshot,row.features,row.k),'snapshot':snapshot};row.state='done'
   Path('/artifacts/'+str(row.id)+'.json').write_text(json.dumps(row.result,ensure_ascii=False))
  except Exception as e:row.state='failed';row.error=str(e)
  s.commit()
def run_assignment(i):
 with Session() as s:
  row=s.get(Assignment,i)
  if not row or row.state!='pending':return
  row.state='running';s.commit()
  try:
   seg=s.get(Segment,row.segment_id)
   if not seg or seg.state!='done':raise ValueError('基准分群版本不存在或未成功，无法归群')
   trained={m['id'] for m in seg.result['snapshot']}
   rows=[dict(id=x.id,code=x.code,weekly_reading=x.weekly_reading,completion=x.completion,night_share=x.night_share) for x in s.query(Reader).order_by(Reader.id).all() if x.id not in trained]
   result=assign(rows,seg.result)
   if result['assigned_count']+result['isolated_count']!=result['candidates'] or len({m['id'] for m in result['members']})!=result['candidates']:raise ValueError('归入与隔离合计不等于候选数，结论未发布')
   rev=locked_revision(s)
   if rev!=row.source_revision:row.state='failed';row.error=f'计算期间源数据已修订（数据版本R{row.source_revision}→R{rev}），旧结论未发布，请重试归群'
   else:
    row.result=result;row.candidates=result['candidates'];row.state='done'
    Path('/artifacts/assignment-'+str(row.id)+'.json').write_text(json.dumps(dict(result,assignment_id=row.id,segment_id=row.segment_id,source_revision=row.source_revision),ensure_ascii=False))
  except Exception as e:row.state='failed';row.error=str(e)
  s.commit()
if __name__=='__main__':
 recover()
 while True:
  Path('/tmp/heartbeat').touch()
  try:
   item=r.blpop(['reader:segments','reader:assignments'],timeout=3)
   if not item:continue
   key=item[0].decode() if isinstance(item[0],bytes) else item[0]
   if key=='reader:segments':run_segment(int(item[1]))
   else:run_assignment(int(item[1]))
  except Exception:logging.exception('worker');time.sleep(2)
