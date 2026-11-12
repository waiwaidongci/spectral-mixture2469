from pydantic import BaseModel,Field
class SampleOutput(BaseModel):
    id:int
    code:str
    library_id:int
    name:str
    observed:list[float]
    note:str
class SampleAnnotation(BaseModel):
    note:str=Field(min_length=8,max_length=1500)
