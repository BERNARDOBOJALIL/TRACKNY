from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    model_path: Path
    frontend_path: Path
    host: str
    port: int
    zone_occupy_seconds: float
    zone_release_seconds: float
    mongo_uri: str
    mongo_db_name: str
    mongo_collection: str
    mongo_flush_interval: float
    video_path_env: str


settings = Settings(
    model_path=(BASE_DIR / os.getenv("YOLO_MODEL", "yolo26n.pt")).resolve(),
    frontend_path=BASE_DIR / "index.html",
    host=os.getenv("HOST", "127.0.0.1"),
    port=int(os.getenv("PORT", "8000")),
    zone_occupy_seconds=float(os.getenv("TIEMPO_PARA_OCUPAR", "10")),
    zone_release_seconds=float(os.getenv("TIEMPO_PARA_DESOCUPAR", "5")),
    mongo_uri=os.getenv("MONGO_URI", ""),
    mongo_db_name=os.getenv("MONGO_DB_NAME", "trackny"),
    mongo_collection=os.getenv("MONGO_COLLECTION", "occupancy_daily"),
    mongo_flush_interval=float(os.getenv("MONGO_FLUSH_INTERVAL", "86400")),
    video_path_env=os.getenv("VIDEO_PATH", "").strip(),
)
