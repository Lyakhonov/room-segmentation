import os
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional
from io import BytesIO

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, EmailStr
from PIL import Image, ImageDraw
import uvicorn
from minio import Minio
from minio.error import S3Error

from appp.core.database import Base, get_db, engine
from appp.models.user import User
from appp.models.imageRecord import ImageRecord

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


MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "admin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "admin123456")
MINIO_BUCKET = "room-segmentation"


minio_client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=False
)

try:
    if not minio_client.bucket_exists(MINIO_BUCKET):
        minio_client.make_bucket(MINIO_BUCKET)
except S3Error as err:
    print("MinIO error:", err)


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


# ---------- IMAGE UPLOAD ----------
def simulate_segmentation_bytes(input_bytes: bytes) -> bytes:
    img = Image.open(BytesIO(input_bytes)).convert("RGBA")
    w, h = img.size

    mask = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(mask)

    draw.rectangle([0, int(h*0.65), w, h], fill=(255,0,0,120))  # floor
    draw.rectangle([0, int(h*0.15), int(w*0.25), int(h*0.65)], fill=(0,255,0,120))  # left wall
    draw.rectangle([int(w*0.75), int(h*0.15), w, int(h*0.65)], fill=(0,255,0,120))  # right wall
    draw.rectangle([int(w*0.1), int(h*0.45), int(w*0.22), int(h*0.65)], fill=(0,0,255,160))  # door
    draw.rectangle([int(w*0.55), int(h*0.18), int(w*0.78), int(h*0.36)], fill=(255,255,0,160))  # window

    result_img = Image.alpha_composite(img, mask)

    output = BytesIO()
    result_img.save(output, format="PNG")
    output.seek(0)

    return output.read()


async def run_segmentation_bytes(input_bytes: bytes) -> bytes:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, simulate_segmentation_bytes, input_bytes)


@app.post("/images/upload")
async def upload_image(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not file.content_type.startswith("image/"):
        raise HTTPException(400, "Only image files supported")

    # читаем байты изображения
    file_bytes = await file.read()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    filename = f"{user.id}_{timestamp}_{file.filename}"
    object_name = f"uploads/{filename}"

    # 1) Загружаем оригинал в MinIO
    minio_client.put_object(
        MINIO_BUCKET,
        object_name,
        data=BytesIO(file_bytes),
        length=len(file_bytes),
        content_type=file.content_type
    )

    # 2) Создаём запись в БД
    record = ImageRecord(owner_id=user.id, filename=object_name)
    db.add(record)
    await db.commit()
    await db.refresh(record)

    # 3) Выполняем сегментацию в памяти
    try:
        result_bytes = await run_segmentation_bytes(file_bytes)

        result_filename = f"results/result_{record.id}.png"

        # 4) Загружаем сегментированную картинку в MinIO
        minio_client.put_object(
            MINIO_BUCKET,
            result_filename,
            data=BytesIO(result_bytes),
            length=len(result_bytes),
            content_type="image/png"
        )

        record.result_filename = result_filename
        record.status = "done"
        await db.commit()

    except Exception as e:
        record.status = "failed"
        await db.commit()
        raise HTTPException(500, f"Segmentation failed: {e}")

    return {"id": record.id, "status": "done"}



@app.get("/images/{image_id}/result")
async def get_result(image_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ImageRecord).where(ImageRecord.id == image_id))
    record = result.scalars().first()
    if not record:
        raise HTTPException(404, "Not found")
    if record.owner_id != user.id:
        raise HTTPException(403, "Forbidden")

    if record.status != "done":
        return {"status": record.status}

    try:
        signed_url = minio_client.presigned_get_object(
            MINIO_BUCKET,
            record.result_filename,
            expires=timedelta(hours=1)
        )
    except Exception as e:
        raise HTTPException(500, f"Could not generate signed URL: {e}")

    return {"status": "done", "url": signed_url}

from datetime import timedelta

@app.get("/images/history")
async def get_history(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    query = await db.execute(
        select(ImageRecord).where(ImageRecord.owner_id == user.id)
    )
    records = query.scalars().all()

    history = []

    for r in records:

        # Подписанная ссылка на оригинал
        original_url = minio_client.presigned_get_object(
            MINIO_BUCKET,
            r.filename,
            expires=timedelta(hours=1)
        )

        # Подписанная ссылка на результат (если есть)
        result_url = None
        if r.result_filename:
            result_url = minio_client.presigned_get_object(
                MINIO_BUCKET,
                r.result_filename,
                expires=timedelta(hours=1)
            )

        history.append({
            "id": r.id,
            "created_at": r.created_at.isoformat(),
            "status": r.status,
            "original_url": original_url,
            "result_url": result_url
        })

    return history



@app.get("/")
async def root():
    return {"service": "room-segmentation-backend", "status": "ok"}

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
