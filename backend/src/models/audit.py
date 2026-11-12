from sqlalchemy import String
from sqlalchemy.orm import Mapped,mapped_column
from ..config.database import Base
from ..utils.clock import now
class Audit(Base):
    __tablename__="audits"
    id:Mapped[int]=mapped_column(primary_key=True)
    actor:Mapped[str]=mapped_column(String(160))
    action:Mapped[str]=mapped_column(String(100))
    target:Mapped[str]=mapped_column(String(120))
    created_at:Mapped[str]=mapped_column(String(64),default=now)
