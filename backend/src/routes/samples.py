from fastapi import APIRouter,Depends
from sqlalchemy.orm import Session
from ..config.database import get_db
from ..controllers.dependencies import current_user,roles
from ..constants.roles import Role
from ..schemas.sample import SampleOutput,SampleAnnotation
from ..services.sample_service import list_samples,annotate_sample
router=APIRouter(tags=["samples"])
@router.get("/libraries/{lid}/samples",response_model=list[SampleOutput])
def index(lid:int,db:Session=Depends(get_db),user=Depends(current_user)):return list_samples(db,lid)
@router.patch("/samples/{sid}")
def annotate(sid:int,data:SampleAnnotation,db:Session=Depends(get_db),user=Depends(roles(Role.ADMIN,Role.ANALYST))):return annotate_sample(db,sid,data,user)
