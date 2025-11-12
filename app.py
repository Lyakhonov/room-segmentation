import os
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.responses import JSONResponse, FileResponse
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from pydantic import BaseModel, EmailStr
from PIL import Image, ImageDraw
import uvicorn

# ==========================================
# CONFIGURATION
# ==========================================
SECRET_KEY = os.environ.get("SECRET_KEY", "CHANGE_ME_PLEASE")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 1 день

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5433/room_segmentation"
)

UPLOAD_DIR = "uploads"
RESULT_DIR = "results"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(RESULT_DIR, exist_ok=True)

# ==========================================
# DATABASE
# ==========================================
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(256), unique=True, index=True, nullable=False)
    full_name = Column(String(128))
    password = Column(String(512), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.now(timezone.utc))

class ImageRecord(Base):
    __tablename__ = "images"
    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    filename = Column(String(512), nullable=False)
    result_filename = Column(String(512))
    status = Column(String(32), default="processing")  # processing | done | failed
    created_at = Column(DateTime(timezone=True), default=datetime.now(timezone.utc))
    segmentation_meta = Column(Text)

engine = create_async_engine(DATABASE_URL, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

# ==========================================
# AUTH
# ==========================================
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_user_by_email(email: str, db: AsyncSession):
    result = await db.execute(select(User).where(User.email == email))
    return result.scalars().first()

async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=401,
        detail="Invalid token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = await get_user_by_email(email, db)
    if user is None:
        raise credentials_exception
    return user

# ==========================================
# SCHEMAS
# ==========================================
class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None

class UserResponse(BaseModel):
    id: int
    email: EmailStr
    full_name: Optional[str] = None

class Token(BaseModel):
    access_token: str
    token_type: str

# ==========================================
# FASTAPI
# ==========================================
app = FastAPI(title="Room Segmentation Backend")

# ---------- AUTH ----------
@app.post("/auth/register", response_model=UserResponse)
async def register_user(user: UserCreate, db: AsyncSession = Depends(get_db)):
    existing = await get_user_by_email(user.email, db)
    if existing:
        raise HTTPException(status_code=400, detail="User already exists")
    new_user = User(
        email=user.email,
        full_name=user.full_name,
        password=hash_password(user.password)
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return new_user

@app.post("/auth/login", response_model=Token)
async def login_user(form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    user = await get_user_by_email(form_data.username, db)
    if not user or not verify_password(form_data.password, user.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    access_token = create_access_token(
        {"sub": user.email},
        timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    return {"access_token": access_token, "token_type": "bearer"}

# ---------- CREATE TABLES ----------
@app.post("/create_tables")
async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return {"status": "ok"}

# ---------- IMAGE UPLOAD ----------
def simulate_segmentation(input_path: str, output_path: str):
    img = Image.open(input_path).convert("RGB")
    w, h = img.size
    mask = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(mask)

    draw.rectangle([0, int(h*0.65), w, h], fill=(255,0,0,120))  # floor
    draw.rectangle([0, int(h*0.15), int(w*0.25), int(h*0.65)], fill=(0,255,0,120))  # left wall
    draw.rectangle([int(w*0.75), int(h*0.15), w, int(h*0.65)], fill=(0,255,0,120))  # right wall
    draw.rectangle([int(w*0.1), int(h*0.45), int(w*0.22), int(h*0.65)], fill=(0,0,255,160))  # door
    draw.rectangle([int(w*0.55), int(h*0.18), int(w*0.78), int(h*0.36)], fill=(255,255,0,160))  # window

    overlaid = Image.alpha_composite(img.convert("RGBA"), mask)
    overlaid.save(output_path)

async def run_segmentation(input_path: str, output_path: str):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, simulate_segmentation, input_path, output_path)

@app.post("/images/upload")
async def upload_image(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not file.content_type.startswith("image/"):
        raise HTTPException(400, "Only image files supported")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    filename = f"{user.id}_{timestamp}_{file.filename}"
    path = os.path.join(UPLOAD_DIR, filename)
    with open(path, "wb") as f:
        f.write(await file.read())

    record = ImageRecord(owner_id=user.id, filename=path)
    db.add(record)
    await db.commit()
    await db.refresh(record)

    result_path = os.path.join(RESULT_DIR, f"result_{record.id}.png")
    await run_segmentation(path, result_path)

    record.result_filename = result_path
    record.status = "done"
    await db.commit()
    return {"id": record.id, "status": record.status}

@app.get("/images/{image_id}/result")
async def get_result(image_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ImageRecord).where(ImageRecord.id == image_id))
    record = result.scalars().first()
    if not record:
        raise HTTPException(404, "Not found")
    if record.owner_id != user.id:
        raise HTTPException(403, "Forbidden")
    if record.status != "done":
        return JSONResponse({"status": record.status}, status_code=202)
    return FileResponse(record.result_filename, media_type="image/png")

@app.get("/")
async def root():
    return {"service": "room-segmentation-backend", "status": "ok"}

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
