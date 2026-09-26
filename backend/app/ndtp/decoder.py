"""Декодирование бинарных пакетов NDTP в колонки traffic.csv.

Слой протокола: не знает о FastAPI и HTTP. Только байты на входе,
словарь с колонками на выходе.
"""

import struct

NPL_STRUCT = struct.Struct("<HHHHBIH")
NPH_STRUCT = struct.Struct("<HHHI")
NAV00_STRUCT = struct.Struct("<IIIBBHHHHHBB")

SIGNATURE = 0x7E7E
TYPE_REALTIME = 101
CELL_NAV00 = 0


def decode_nav00(payload: bytes) -> dict:
    """Превращает 26 байт ячейки навигации в колонки traffic.csv."""
    (timestamp, lon_raw, lat_raw, extra_dop, bat_voltage,
     speed_avg, speed_max, course, track, altitude, nsat, pdop) = NAV00_STRUCT.unpack(payload)

    lat_sign = 1 if (extra_dop >> 5) & 1 else -1
    lon_sign = 1 if (extra_dop >> 6) & 1 else -1

    return {
        "event_time": timestamp,
        "location_valid": bool((extra_dop >> 7) & 1),
        "lon": lon_sign * lon_raw / 1e7,
        "lat": lat_sign * lat_raw / 1e7,
        "alt": altitude,
        "speed": speed_avg,
        "heading": course,
        "nsat": nsat,
        "pdop": pdop,
    }


def extract_nav00(payload: bytes) -> dict:
    """Достаёт ячейку навигации из начала тела пакета и раскодирует её."""
    cell_type, cell_number = payload[0], payload[1]
    if cell_type != CELL_NAV00:
        raise ValueError(f"not a NAV00 cell: type={cell_type}")
    return decode_nav00(payload[2:2 + NAV00_STRUCT.size])


def split_frame(frame: bytes):
    """Разбирает полный кадр на NPL/NPH/тело, возвращает peer_address."""
    signature, data_size, _flags, _crc, _pkt_type, peer_address, _req = NPL_STRUCT.unpack_from(frame, 0)
    if signature != SIGNATURE:
        raise ValueError("bad NPL signature")
    nph = NPH_STRUCT.unpack_from(frame, NPL_STRUCT.size)
    return peer_address, nph, frame[NPL_STRUCT.size + NPH_STRUCT.size:]