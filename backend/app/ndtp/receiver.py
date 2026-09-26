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
                    CSV_QUEUE.append(row)
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def start_receiver(port: int):
    return await asyncio.start_server(_handle_client, "0.0.0.0", port)


def recent(limit: int) -> list:
    return list(RECEIVED)[-limit:]


def _to_traffic_csv_timestamp(ts) -> str:
    if not isinstance(ts, (int, float)):
        return ""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S.%f")


def _row_to_traffic_format(row: dict, received_at: datetime) -> dict:
    event_time = _to_traffic_csv_timestamp(row.get("event_time"))
    return {
        "packet_id": str(uuid.uuid4()),
        "tr_id": row.get("tr_id"),
        "unit_id": row.get("unit_id"),
        "event_time": event_time,
        "device_event_id": 0,
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


def flush_rows_to_csv(path: str):
    if not CSV_QUEUE:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_header = not os.path.exists(path) or os.path.getsize(path) == 0
    received_at = datetime.now(timezone.utc)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        while CSV_QUEUE:
            writer.writerow(_row_to_traffic_format(CSV_QUEUE.popleft(), received_at))


async def csv_writer_loop(path: str, interval: int):
    while True:
        await asyncio.sleep(interval)
        try:
            flush_rows_to_csv(path)
        except Exception:
            continue