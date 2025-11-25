import asyncio
from datetime import datetime, timedelta, timezone
from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from PIL import Image, ImageDraw

from app.ML.segmentation import run_segmentation
from app.core.database import get_db
from app.core.security import get_current_user
from app.core.utils import generate_uuid
from app.models.user import User
from app.models.imageRecord import ImageRecord
from app.core.config import minio_client, MINIO_BUCKET


router = APIRouter()


@router.post("/upload")
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
    record = ImageRecord(
        id=generate_uuid(),
        owner_id=user.id,
        filename=object_name)
    db.add(record)
    await db.commit()
    await db.refresh(record)

    # 3) Выполняем сегментацию в памяти
    try:
        loop = asyncio.get_event_loop()
        result_bytes = await loop.run_in_executor(None, run_segmentation, file_bytes)

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



@router.get("/{image_id}/result")
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



@router.get("/history")
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
