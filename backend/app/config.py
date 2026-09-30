"""설정: 루트 .env 와 LAWCA_* 환경변수. 코드에 비밀값을 두지 않는다."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_env() -> dict:
    env = {}
    f = ROOT / ".env"
    if f.exists():
        env = dict(l.strip().split("=", 1) for l in f.read_text(encoding="utf-8").splitlines() if "=" in l and not l.startswith("#"))
    env.update({k: v for k, v in os.environ.items() if k.startswith("LAWCA_")})
    return env


ENV = _load_env()


def _dsn(user: str, pw_key: str) -> str:
    return (f"host={ENV.get('LAWCA_DB_HOST', '127.0.0.1')} port={ENV['LAWCA_DB_PORT']} dbname={ENV['LAWCA_DB_NAME']} "
            f"user={user} password={ENV[pw_key]}")


APP_DSN = _dsn("lawca_app", "LAWCA_APP_PASSWORD")          # API: RLS 적용, 최소 권한
WORKER_DSN = _dsn("lawca_worker", "LAWCA_WORKER_PASSWORD")  # 인제스트 워커: RLS 적용, 큐 전체 접근
AUDIT_DSN = _dsn("lawca_audit", "LAWCA_AUDIT_PASSWORD")     # 감사 로그 INSERT 전용
OWNER_DSN = _dsn("lawca_owner", "LAWCA_OWNER_PASSWORD")     # 관리 스크립트·테스트 전용(서버 런타임에서는 쓰지 않는다)

# ---- 저장소·업로드 한도
STORAGE_DIR = Path(ENV.get("LAWCA_STORAGE_DIR", str(ROOT / "backend" / "data")))
MAX_FILE_MB = int(ENV.get("LAWCA_MAX_FILE_MB", "200"))                 # 파일 하나(ZIP 내부 항목 포함)
MAX_REQUEST_MB = int(ENV.get("LAWCA_MAX_REQUEST_MB", "1024"))          # 요청 하나
MAX_FILES_PER_REQUEST = int(ENV.get("LAWCA_MAX_FILES_PER_REQUEST", "500"))
MAX_ZIP_ENTRIES = int(ENV.get("LAWCA_MAX_ZIP_ENTRIES", "5000"))
MAX_ZIP_UNCOMPRESSED_MB = int(ENV.get("LAWCA_MAX_ZIP_UNCOMPRESSED_MB", "8192"))
MAX_ZIP_RATIO = int(ENV.get("LAWCA_MAX_ZIP_RATIO", "200"))              # 항목별 압축률 상한(큰 파일에만 적용)
ALLOWED_EXT = {".pdf", ".hwp", ".hwpx", ".docx", ".png", ".jpg", ".jpeg", ".webp"}
ARCHIVE_EXT = {".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz"}

# ---- 인증
SESSION_ABSOLUTE_HOURS = int(ENV.get("LAWCA_SESSION_HOURS", "12"))
SESSION_IDLE_MINUTES = int(ENV.get("LAWCA_SESSION_IDLE_MINUTES", "120"))
COOKIE_NAME = "lawca_session"
COOKIE_SECURE = ENV.get("LAWCA_COOKIE_SECURE", "0") == "1"             # https 뒤에서는 반드시 1
LOGIN_MAX_FAILURES = int(ENV.get("LAWCA_LOGIN_MAX_FAILURES", "5"))
LOGIN_LOCK_MINUTES = int(ENV.get("LAWCA_LOGIN_LOCK_MINUTES", "15"))
MIN_PASSWORD_LEN = 10

# ---- 워커
KORDOC_CLI = ROOT / "backend" / "tools" / "node_modules" / "kordoc" / "dist" / "cli.js"
PARSE_TIMEOUT_SEC = int(ENV.get("LAWCA_PARSE_TIMEOUT_SEC", "600"))
MAX_JOB_ATTEMPTS = 3
EMBED_MODEL = "intfloat/multilingual-e5-large"
