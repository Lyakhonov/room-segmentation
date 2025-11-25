from minio import S3Error
import uvicorn
from fastapi import FastAPI

from app.routers import auth, images, root
from app.core.config import minio_client, MINIO_BUCKET

app = FastAPI(title="Room Segmentation Backend")

app.include_router(root.router, tags=["health-check"])
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(images.router, prefix="/images", tags=["images"])

try:
    if not minio_client.bucket_exists(MINIO_BUCKET):
        minio_client.make_bucket(MINIO_BUCKET)
except S3Error as err:
    print("MinIO error:", err)

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)