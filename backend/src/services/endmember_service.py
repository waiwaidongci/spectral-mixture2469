from fastapi import HTTPException
from ..models import SpectralLibrary,Endmember
from ..utils.serialize import record

def list_endmembers(db,lid):
    if not db.get(SpectralLibrary,lid):raise HTTPException(404,"光谱库不存在")
    return [record(e) for e in db.query(Endmember).filter_by(library_id=lid).order_by(Endmember.code)]
