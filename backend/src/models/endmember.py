from sqlalchemy import String,JSON,ForeignKey,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column
from ..config.database import Base
class Endmember(Base):
    __tablename__="endmembers"
    __table_args__=(UniqueConstraint("library_id","code"),)
    id:Mapped[int]=mapped_column(primary_key=True)
    library_id:Mapped[int]=mapped_column(ForeignKey("spectral_libraries.id"))
    code:Mapped[str]=mapped_column(String(40))
    name:Mapped[str]=mapped_column(String(80))
    spectrum:Mapped[list]=mapped_column(JSON)
