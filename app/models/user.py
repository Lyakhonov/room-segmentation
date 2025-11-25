from sqlalchemy import Column, Integer, String, DateTime
from datetime import datetime, timezone
from app.core.database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(256), unique=True, index=True, nullable=False)
    full_name = Column(String(128))
    password = Column(String(512), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.now(timezone.utc))