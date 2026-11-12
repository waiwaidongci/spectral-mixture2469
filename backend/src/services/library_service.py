import numpy as np
from ..models import SpectralLibrary,Endmember,MixtureSample
from ..utils.serialize import record
from .unmix_algorithm import matrix_diagnostics

def list_libraries(db):
    rows=[]
    for library in db.query(SpectralLibrary).order_by(SpectralLibrary.code):
        members=db.query(Endmember).filter_by(library_id=library.id).order_by(Endmember.code).all()
        try:_,d=matrix_diagnostics(np.array([e.spectrum for e in members]).T)
        except ValueError as e:d={"supported":False,"condition_number":None,"reason":str(e)}
        rows.append(dict(record(library),endmember_count=len(members),sample_count=db.query(MixtureSample).filter_by(library_id=library.id).count(),supported=d['supported'],condition_number=d['condition_number'],reason=d['reason']))
    return rows
