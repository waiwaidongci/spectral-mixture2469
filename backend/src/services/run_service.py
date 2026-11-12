import csv,io
import numpy as np
from redis import Redis
from fastapi import HTTPException
from ..models import SpectralLibrary,Endmember,MixtureSample,UnmixRun,ReplicationNote
from ..config.settings import REDIS_URL
from ..utils.serialize import record
from .unmix_algorithm import matrix_diagnostics
from .audit_service import log
queue=Redis.from_url(REDIS_URL,decode_responses=True)
def list_runs(db):return [record(r) for r in db.query(UnmixRun).order_by(UnmixRun.id.desc())]
def get_run(db,rid):
    row=db.get(UnmixRun,rid)
    if not row:raise HTTPException(404,"解混运行不存在")
    return row

def create_snapshot(db,library,sample_ids):
    members=db.query(Endmember).filter_by(library_id=library.id).order_by(Endmember.code).all()
    samples=db.query(MixtureSample).filter(MixtureSample.id.in_(sample_ids),MixtureSample.library_id==library.id).order_by(MixtureSample.code).all()
    if len(samples)!=len(sample_ids):raise HTTPException(422,"样本重复、不存在或不属于所选光谱库")
    return {"library":record(library),"endmembers":[record(e) for e in members],"samples":[record(s) for s in samples]}
def create_run(db,data,user):
    library=db.get(SpectralLibrary,data.library_id)
    if not library:raise HTTPException(404,"光谱库不存在")
    if len(set(data.sample_ids))!=len(data.sample_ids) or len(data.sample_ids)*data.repeats>480:raise HTTPException(422,"样本不可重复，样本数×重复数不得超过480")
    snapshot=create_snapshot(db,library,data.sample_ids)
    try:
        _,d=matrix_diagnostics(np.array([e['spectrum'] for e in snapshot['endmembers']]).T)
        if not d['supported']:raise ValueError(d['reason'])
    except ValueError as e:raise HTTPException(422,str(e))
    row=UnmixRun(**data.model_dump(),input_snapshot=snapshot,owner=user.name)
    db.add(row);db.flush();log(db,user.email,"unmix.enqueue",row.id);db.commit()
    try:queue.rpush("spectral:jobs",row.id)
    except Exception:
        row.status="failed";row.error="队列不可用，请重提";db.commit();raise HTTPException(503,row.error)
    return record(row)
def export_run(db,rid):
    row=get_run(db,rid)
    if row.status!="completed" or not db.query(ReplicationNote).filter_by(run_id=rid,status="approved").first():raise HTTPException(403,"仅获批完成研究可导出")
    out=io.StringIO();w=csv.writer(out);w.writerow(['sample','endmember','weight','conditional_q025','conditional_q975','rss','rmse','noise_sigma','repeats','root_seed','input_hash'])
    for s in row.result['samples']:
        for i,e in enumerate(row.result['endmembers']):w.writerow([s['code'],e['code'],s['point_fit']['weights'][i],s['lower'][i],s['upper'][i],s['point_fit']['rss'],s['point_fit']['rmse'],row.noise_sigma,row.repeats,row.seed,row.result['input_hash']])
    return out.getvalue()
