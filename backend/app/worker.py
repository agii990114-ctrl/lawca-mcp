"""인제스트 워커 프로세스.  실행: python -m backend.app.worker  (또는 --once)

CPU 임베딩·OCR은 무겁기 때문에 API 프로세스와 분리해 별도로 띄운다. 필요하면 여러 개를 동시에 띄워도 된다(SKIP LOCKED).
"""
import argparse
import logging
import os
import socket
import time

from . import config
from .ingest import pipeline, storage


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="큐를 비운 뒤 종료")
    ap.add_argument("--poll", type=float, default=2.0)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    storage.ensure_dirs()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    log = logging.getLogger("lawca.worker")
    log.info("worker %s 시작 (저장소 %s)", worker_id, config.STORAGE_DIR)
    with pipeline.connect() as conn:
        while True:
            result = pipeline.process_one(conn, worker_id)
            if result is None:
                if args.once:
                    break
                time.sleep(args.poll)
            else:
                log.info("작업 처리 결과: %s", result)


if __name__ == "__main__":
    main()
