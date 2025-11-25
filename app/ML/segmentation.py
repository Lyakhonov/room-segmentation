import cv2
import numpy as np
from ultralytics import YOLO

# Загружаем модель
room_model = YOLO("app/ML/best.pt")

COLOR_MAP = {
    "floor": (0, 255, 0),
    "ceiling": (255, 0, 0),
    "wall": (0, 0, 255),
    "door": (255, 255, 0),
    "window": (0, 255, 255)
}


def apply_segmentation_masks(image: np.ndarray, results):
    overlay = image.copy()

    for res in results:
        names = res.names

        if res.masks is None:
            continue

        # res.masks.xy — набор точек полигона
        for polygon_points, cls in zip(res.masks.xy, res.boxes.cls):
            cls_name = names[int(cls)]
            color = COLOR_MAP.get(cls_name, (255, 255, 255))

            polygon = np.array(polygon_points, dtype=np.int32)
            cv2.fillPoly(overlay, [polygon], color)

    return cv2.addWeighted(image, 0.6, overlay, 0.4, 0)


def run_segmentation(input_bytes: bytes) -> bytes:
    """Преобразует байты → делает сегментацию → возвращает байты PNG."""
    np_arr = np.frombuffer(input_bytes, np.uint8)
    img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    if img is None:
        raise ValueError("Failed to decode input image bytes")

    # YOLO предсказание
    room_res = room_model(img)[0]
    # dw_res = door_window_model(img)[0]

    # Наложение масок
    # masked = apply_segmentation_masks(img, [room_res, dw_res])
    masked = apply_segmentation_masks(img, [room_res])

    success, output_bytes = cv2.imencode(".png", masked)

    if not success:
        raise ValueError("Failed to encode output image")

    return output_bytes.tobytes()
