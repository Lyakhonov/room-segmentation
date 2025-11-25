from sqlalchemy import Column, String, DateTime
from datetime import datetime, timezone
from app.core.database import Base
from app.core.utils import generate_uuid


class User(Base):
    __tablename__ = "users"
    id = Column(String(128), primary_key=True, default=generate_uuid)
    email = Column(String(256), unique=True, index=True, nullable=False)
    full_name = Column(String(128))
    password = Column(String(512), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.now(timezone.utc))