"""Приём и накопление потока телеметрии NDTP (TCP-сервер + буферы)."""

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
CSV_QUEUE = deque(maxlen=5000)
CSV_DROPPED = 0
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
    """Обрабатывает TCP-соединение: читает кадры NDTP и раскладывает по буферам."""
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
                    _append_csv_queue(row)
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def start_receiver(port: int):
    """Запускает TCP-сервер приёма NDTP на указанном порту."""
    return await asyncio.start_server(_handle_client, "0.0.0.0", port)


def recent(limit: int) -> list:
    """Возвращает последние limit строк из буфера истории."""
    return list(RECEIVED)[-limit:]


def total() -> int:
    """Возвращает общее число строк в буфере истории."""
    return len(RECEIVED)


def latest(limit: int = 500) -> list:
    """Возвращает актуальное состояние каждого ТС (по одной последней строке)."""
    return list(LATEST.values())[-limit:]


def _to_traffic_csv_timestamp(ts) -> str:
    """Форматирует unix-время в строку даты, как в traffic.csv."""
    if not isinstance(ts, (int, float)):
        return ""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S.%f")


def _row_to_traffic_format(row: dict, received_at: datetime) -> dict:
    """Приводит строку к колонкам traffic.csv (packet_id, gps_time, receive_time)."""
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


def _append_csv_queue(row: dict):
    """Кладёт строку в очередь на запись, считая отброшенные при переполнении."""
    global CSV_DROPPED
    if len(CSV_QUEUE) >= CSV_QUEUE.maxlen:
        CSV_DROPPED += 1
    CSV_QUEUE.append(row)


def csv_dropped() -> int:
    """Возвращает число строк, отброшенных из-за переполнения очереди."""
    return CSV_DROPPED


def _drain_csv_queue() -> list:
    """Забирает и очищает очередь строк, ожидающих персистентной записи."""
    rows = list(CSV_QUEUE)
    CSV_QUEUE.clear()
    return rows


def rows_to_traffic_format(rows: list, received_at: datetime) -> list:
    """Применяет traffic.csv-формат к пачке строк (используется при сбросе)."""
    return [_row_to_traffic_format(r, received_at) for r in rows]


def flush_rows_to_csv(path: str, traffic_rows: list):
    """Дописывает строки в CSV-файл (режим append), создавая заголовок при первом сбросе."""
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
    """Фоновая задача: периодически выгружает накопленные строки в CSV и БД."""
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