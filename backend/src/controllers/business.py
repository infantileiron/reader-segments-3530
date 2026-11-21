from fastapi import HTTPException
from src.models.entities import Segment,Assignment
def ensure_segment(s,i):
 row=s.get(Segment,i)
 if not row:raise HTTPException(404,'分群版本不存在')
 return row
def ensure_assignment(s,i):
 row=s.get(Assignment,i)
 if not row:raise HTTPException(404,'归群任务不存在')
 return row
