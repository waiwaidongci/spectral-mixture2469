from fastapi import HTTPException
from ..models import UnmixRun,ReplicationNote
from ..utils.serialize import record
from .audit_service import log

def list_notes(db):return [record(n) for n in db.query(ReplicationNote).order_by(ReplicationNote.id.desc())]
def create_note(db,data,user):
    run=db.get(UnmixRun,data.run_id)
    if not run or run.status!="completed":raise HTTPException(409,"只能对已完成解混研究提交复配建议")
    row=ReplicationNote(**data.model_dump(),owner=user.name,status="submitted")
    db.add(row);db.flush();log(db,user.email,"replication.submit",row.id);db.commit();return record(row)
def review_note(db,nid,data,user):
    row=db.get(ReplicationNote,nid)
    if not row:raise HTTPException(404,"复配建议不存在")
    if row.status!="submitted":raise HTTPException(409,"已审阅建议不可重复审批")
    row.status=data.status;row.reviewer=user.name;row.review_note=data.review_note
    log(db,user.email,"replication."+data.status,row.id);db.commit();return record(row)
