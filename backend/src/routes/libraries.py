from fastapi import APIRouter,Depends
from sqlalchemy.orm import Session
from ..config.database import get_db
from ..controllers.dependencies import current_user
from ..schemas.library import LibraryOutput
from ..schemas.endmember import EndmemberOutput
from ..services.library_service import list_libraries
from ..services.endmember_service import list_endmembers
router=APIRouter(prefix="/libraries",tags=["libraries"])
@router.get("",response_model=list[LibraryOutput])
def index(db:Session=Depends(get_db),user=Depends(current_user)):return list_libraries(db)
@router.get("/{lid}/endmembers",response_model=list[EndmemberOutput])
def endmembers(lid:int,db:Session=Depends(get_db),user=Depends(current_user)):return list_endmembers(db,lid)
