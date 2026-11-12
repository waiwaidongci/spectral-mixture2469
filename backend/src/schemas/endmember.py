from pydantic import BaseModel
class EndmemberOutput(BaseModel):
    id:int
    library_id:int
    code:str
    name:str
    spectrum:list[float]
