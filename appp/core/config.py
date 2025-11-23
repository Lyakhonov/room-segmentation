import os

class Settings:
    SECRET_KEY = os.getenv("SECRET_KEY", "CHANGE_ME_PLEASE")
    ALGORITHM = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24
    DATABASE_URL = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@db:5433/room_segmentation"
    )

settings = Settings()