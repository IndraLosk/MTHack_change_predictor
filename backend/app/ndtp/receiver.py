import asyncio
from collections import deque

from .decoder import (
    NPL_STRUCT,
    SIGNATURE,
    TYPE_REALTIME,
    extract_nav00,
    split_frame,
)

RECEIVED = deque(maxlen=500)


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
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def start_receiver(port: int):
    return await asyncio.start_server(_handle_client, "0.0.0.0", port)


def recent(limit: int) -> list:
    return list(RECEIVED)[-limit:]