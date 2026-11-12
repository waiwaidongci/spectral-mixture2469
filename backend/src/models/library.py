from sqlalchemy import String,Text,JSON
from sqlalchemy.orm import Mapped,mapped_column
from ..config.database import Base
class SpectralLibrary(Base):
    __tablename__="spectral_libraries"
    id:Mapped[int]=mapped_column(primary_key=True)
    code:Mapped[str]=mapped_column(String(40),unique=True)
    name:Mapped[str]=mapped_column(String(120))
    description:Mapped[str]=mapped_column(Text)
    wavelengths:Mapped[list]=mapped_column(JSON)
    source:Mapped[str]=mapped_column(String(160))
