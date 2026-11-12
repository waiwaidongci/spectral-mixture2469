from sqlalchemy import String,Text,JSON,ForeignKey
from sqlalchemy.orm import Mapped,mapped_column
from ..config.database import Base
class MixtureSample(Base):
    __tablename__="mixture_samples"
    id:Mapped[int]=mapped_column(primary_key=True)
    code:Mapped[str]=mapped_column(String(80),unique=True)
    library_id:Mapped[int]=mapped_column(ForeignKey("spectral_libraries.id"))
    name:Mapped[str]=mapped_column(String(120))
    observed:Mapped[list]=mapped_column(JSON)
    note:Mapped[str]=mapped_column(Text)
