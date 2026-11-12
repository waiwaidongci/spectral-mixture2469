from fastapi import HTTPException
from ..models import SpectralLibrary,MixtureSample
from ..utils.serialize import record
from .audit_service import log

def list_samples(db,lid):
    if not db.get(SpectralLibrary,lid):raise HTTPException(404,"光谱库不存在")
    return [record(s) for s in db.query(MixtureSample).filter_by(library_id=lid).order_by(MixtureSample.code)]
def annotate_sample(db,sid,data,user):
    row=db.get(MixtureSample,sid)
    if not row:raise HTTPException(404,"样本不存在")
    row.note=data.note;log(db,user.email,"sample.annotate",row.code);db.commit();return record(row)
