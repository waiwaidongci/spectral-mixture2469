from pydantic import BaseModel,Field
class RunInput(BaseModel):
    name:str=Field(min_length=2,max_length=120)
    library_id:int
    sample_ids:list[int]=Field(min_length=1,max_length=6)
    noise_sigma:float=Field(default=.005,ge=0,le=.2,allow_inf_nan=False)
    repeats:int=Field(default=80,ge=20,le=120)
    seed:int=Field(default=2026,ge=0,le=2147483647)
