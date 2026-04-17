from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

env_file_override = os.getenv("ENV_FILE", "").strip()
if env_file_override:
    env_path = Path(env_file_override)
    if not env_path.is_absolute():
        env_path = BASE_DIR / env_path
    load_dotenv(env_path, override=False)
else:
    # Carga local por defecto y permite configuracion especifica para detector.
    load_dotenv(BASE_DIR / ".env", override=False)
    load_dotenv(BASE_DIR / ".env.detector", override=False)


@dataclass(frozen=True)
class Settings:
    model_path: Path
    frontend_path: Path
    host: str
    port: int
    zone_names_env: list[str]
    internal_api_token: str
    allow_insecure_internal: bool
    run_local_api: bool
    zone_occupy_seconds: float
    zone_release_seconds: float
    mongo_uri: str
    mongo_db_name: str
    mongo_collection: str
    mongo_flush_interval: float
    video_path_env: str
    remote_ingest_url: str
    remote_state_interval: float
    remote_video_enabled: bool
    remote_video_fps: float
    remote_jpeg_quality: int
    remote_frame_max_width: int
    inference_max_width: int
    inference_every_n_frames: int
    yolo_imgsz: int
    yolo_conf: float
    yolo_max_det: int


def _parse_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_zone_names(raw: str) -> list[str]:
    return [z.strip() for z in raw.split(",") if z.strip()]


settings = Settings(
    model_path=(BASE_DIR / os.getenv("YOLO_MODEL", "yolo26n.pt")).resolve(),
    frontend_path=BASE_DIR / "index.html",
    host=os.getenv("HOST", os.getenv("APP_HOST", "0.0.0.0")),
    port=int(os.getenv("PORT", os.getenv("APP_PORT", "8000"))),
    zone_names_env=_parse_zone_names(os.getenv("ZONE_NAMES", "")),
    internal_api_token=os.getenv("INTERNAL_API_TOKEN", "").strip(),
    allow_insecure_internal=_parse_bool(os.getenv("ALLOW_INSECURE_INTERNAL", "false"), default=False),
    run_local_api=_parse_bool(os.getenv("RUN_LOCAL_API", "true"), default=True),
    zone_occupy_seconds=float(os.getenv("TIEMPO_PARA_OCUPAR", "10")),
    zone_release_seconds=float(os.getenv("TIEMPO_PARA_DESOCUPAR", "5")),
    mongo_uri=os.getenv("MONGO_URI", ""),
    mongo_db_name=os.getenv("MONGO_DB_NAME", "trackny"),
    mongo_collection=os.getenv("MONGO_COLLECTION", "occupancy_daily"),
    mongo_flush_interval=float(os.getenv("MONGO_FLUSH_INTERVAL", "86400")),
    video_path_env=os.getenv("VIDEO_PATH", "").strip(),
    remote_ingest_url=os.getenv("REMOTE_INGEST_URL", "").strip().rstrip("/"),
    remote_state_interval=max(0.2, float(os.getenv("REMOTE_STATE_INTERVAL", "1.0"))),
    remote_video_enabled=_parse_bool(os.getenv("REMOTE_VIDEO_ENABLED", "false"), default=False),
    remote_video_fps=max(1.0, float(os.getenv("REMOTE_VIDEO_FPS", "5.0"))),
    remote_jpeg_quality=max(40, min(95, int(os.getenv("REMOTE_JPEG_QUALITY", "70")))),
    remote_frame_max_width=max(320, int(os.getenv("REMOTE_FRAME_MAX_WIDTH", "960"))),
    inference_max_width=max(320, int(os.getenv("INFERENCE_MAX_WIDTH", "960"))),
    inference_every_n_frames=max(1, int(os.getenv("INFERENCE_EVERY_N_FRAMES", "2"))),
    yolo_imgsz=max(320, int(os.getenv("YOLO_IMGSZ", "480"))),
    yolo_conf=max(0.05, min(0.9, float(os.getenv("YOLO_CONF", "0.25")))),
    yolo_max_det=max(1, int(os.getenv("YOLO_MAX_DET", "20"))),
)
