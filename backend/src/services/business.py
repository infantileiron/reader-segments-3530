from datetime import datetime,timezone
from src.models.entities import Audit,Meta
def audit(s,u,action):
 s.add(Audit(actor=u.email,action=action,created=datetime.now(timezone.utc).isoformat()));s.commit()
def dump(obj):return {c.name:getattr(obj,c.name) for c in obj.__table__.columns if c.name!='password'}
def _meta(s,lock):
 q=s.query(Meta).filter_by(key='readers')
 if lock and s.get_bind().dialect.name=='postgresql':q=q.with_for_update()
 row=q.first()
 if not row:row=Meta(key='readers',value=0);s.add(row);s.flush()
 return row
def current_revision(s):
 row=s.get(Meta,'readers');return row.value if row else 0
def locked_revision(s):return _meta(s,True).value
def bump_revision(s):
 row=_meta(s,True);row.value+=1;return row.value
