from sqlalchemy import Column, ForeignKey, Integer, String, DateTime
from datetime import datetime, timezone
from appp.core.database import Base

class ImageRecord(Base):
    __tablename__ = "images"
    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    filename = Column(String(512), nullable=False)
    result_filename = Column(String(512))
    status = Column(String(32), default="processing")  # processing | done | failed
    created_at = Column(DateTime(timezone=True), default=datetime.now(timezone.utc))