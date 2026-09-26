"""Приём и накопление потока NDTP.

TCP-сервер, который принимает пакеты телеметрии от эмулятора,
раскодирует их и хранит в буферах: история (RECEIVED), последнее
состояние каждого ТС (LATEST) и очередь на персистентность (CSV_QUEUE).
"""

import asyncio
import csv
import os
import uuid
from collections import deque
from datetime import datetime, timezone

from .decoder import (
    NPL_STRUCT,
    SIGNATURE,
    TYPE_REALTIME,
    extract_nav00,
    split_frame,
)

RECEIVED = deque(maxlen=500)

LATEST: dict[int, dict] = {}
CSV_QUEUE = deque()
CSV_FIELDS = [
    "packet_id",
    "tr_id",
    "unit_id",
    "event_time",
    "device_event_id",
    "location_valid",
    "gps_time",
    "lon",
    "lat",
    "alt",
    "speed",
    "heading",
    "receive_time",
    "is_hist_data",
]


async def _handle_client(reader, writer):
    """Обслуживает TCP-соединение: читает байты и режет их на кадры."""
    buf = b""
    try:
        while True:
            chunk = await reader.read(65535)
            if not chunk:
                break
            buf += chunk
            while len(buf) >= NPL_STRUCT.size:
                sig, size, *_ = NPL_STRUCT.unpack_from(buf, 0)
                if sig != SIGNATURE:
                    buf = buf[1:]
                    continue
                total = NPL_STRUCT.size + size
                if len(buf) < total:
                    break
                frame = buf[:total]
                buf = buf[total:]
                peer, nph, body = split_frame(frame)
                if nph[1] == TYPE_REALTIME:
                    try:
                        row = extract_nav00(body)
                    except ValueError:
                        continue
                    row["unit_id"] = peer
                    row["tr_id"] = peer
                    row["raw_hex"] = frame.hex()
                    RECEIVED.append(row)
                    LATEST[peer] = row
                    CSV_QUEUE.append(row)
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def start_receiver(port: int):
    """Запускает TCP-приёмник NDTP на заданном порту."""
    return await asyncio.start_server(_handle_client, "0.0.0.0", port)


def recent(limit: int) -> list:
    """Возвращает последние декодированные строки из истории."""
    return list(RECEIVED)[-limit:]


def latest(limit: int = 500) -> list:
    """Возвращает последнее состояние по каждому ТС (одна строка на ТС)."""
    return list(LATEST.values())[-limit:]


def _to_traffic_csv_timestamp(ts) -> str:
    """Превращает unix-секунды в строку даты в формате traffic.csv."""
    if not isinstance(ts, (int, float)):
        return ""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S.%f")


def _row_to_traffic_format(row: dict, received_at: datetime) -> dict:
    """Приводит декодированную строку к колонкам traffic.csv."""
    event_time = _to_traffic_csv_timestamp(row.get("event_time"))
    return {
        "packet_id": str(uuid.uuid4()),
        "tr_id": str(row.get("tr_id")),
        "unit_id": str(row.get("unit_id")),
        "event_time": event_time,
        "device_event_id": str(row.get("device_event_id") or 0),
        "location_valid": row.get("location_valid"),
        "gps_time": event_time,
        "lon": row.get("lon"),
        "lat": row.get("lat"),
        "alt": row.get("alt"),
        "speed": row.get("speed"),
        "heading": row.get("heading"),
        "receive_time": received_at.strftime("%Y-%m-%d %H:%M:%S.%f"),
        "is_hist_data": False,
    }


def _drain_csv_queue() -> list:
    """Забирает и очищает очередь строк на запись."""
    rows = list(CSV_QUEUE)
    CSV_QUEUE.clear()
    return rows


def rows_to_traffic_format(rows: list, received_at: datetime) -> list:
    """Применяет traffic.csv-формат ко всем строкам пачки."""
    return [_row_to_traffic_format(r, received_at) for r in rows]


def flush_rows_to_csv(path: str, traffic_rows: list):
    """Дописывает пачку строк в конец CSV-файла."""
    if not traffic_rows:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_header = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        for row in traffic_rows:
            writer.writerow(row)


async def csv_writer_loop(path: str, interval: int, db=None):
    """Периодически сбрасывает накопленные строки в CSV и БД."""
    while True:
        await asyncio.sleep(interval)
        rows = _drain_csv_queue()
        if not rows:
            continue
        traffic_rows = rows_to_traffic_format(rows, datetime.now(timezone.utc))
        try:
            flush_rows_to_csv(path, traffic_rows)
        except Exception as exc:
            print(f"[csv] ошибка записи: {exc!r}", flush=True)
        if db is not None:
            try:
                await db.insert_ndtp(traffic_rows)
            except Exception as exc:
                print(f"[db] ошибка вставки ndtp: {exc!r}", flush=True)