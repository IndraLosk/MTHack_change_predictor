"""ML-воркер: инференс через общую БД (без HTTP).

Связующим «мостом» между backend и моделью выступает PostgreSQL:
- backend пишет телеметрию в ndtp_telemetry;
- воркер читает из БД прогнозные точки, расписание и телеметрию,
  строит признаки и пишет предсказания обратно в predictions.

Статические справочные данные (точки прогноза и расписание) воркер
один раз переносит в БД из смонтированных CSV, если таблицы пусты.
"""

import asyncio
import os
from datetime import datetime

import pandas as pd

from features import FEATURES, build_features  # пайплайн признаков
from model_service import model  # singleton: грузится один раз

DB_DSN = os.getenv(
    "DB_DSN",
    "postgresql://mthack:mthack@db:5432/mthack",
)
POINTS_CSV = os.getenv("POINTS_CSV", "/app/data/points.csv")
SCHEDULE_CSV = os.getenv("SCHEDULE_CSV", "/app/data/schedule_plan.csv")
TRAFFIC_CSV = os.getenv("TRAFFIC_CSV", "/app/data/traffic.csv")

INTERVAL = int(os.getenv("INTERVAL", "20"))
TELEMETRY_LIMIT = int(os.getenv("TELEMETRY_LIMIT", "200000"))

DDL = """
CREATE TABLE IF NOT EXISTS ndtp_telemetry (
    id BIGSERIAL PRIMARY KEY,
    packet_id TEXT,
    tr_id TEXT,
    unit_id TEXT,
    event_time TEXT,
    device_event_id TEXT,
    location_valid BOOLEAN,
    gps_time TEXT,
    lon DOUBLE PRECISION,
    lat DOUBLE PRECISION,
    alt DOUBLE PRECISION,
    speed DOUBLE PRECISION,
    heading DOUBLE PRECISION,
    receive_time TIMESTAMPTZ,
    is_hist_data BOOLEAN
);
CREATE TABLE IF NOT EXISTS predictions (
    sample_id TEXT PRIMARY KEY,
    prediction DOUBLE PRECISION
);
CREATE TABLE IF NOT EXISTS forecast_points (
    sample_id TEXT PRIMARY KEY,
    tr_id TEXT,
    T TEXT,
    target_stop_id TEXT,
    target_time_begin TEXT,
    cur_dev_s DOUBLE PRECISION
);
CREATE TABLE IF NOT EXISTS schedule_stops (
    tt_action_item_id TEXT PRIMARY KEY,
    time_begin TEXT,
    tr_id TEXT,
    geom TEXT
);
"""


def load_csv(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path)


async def init_db(conn):
    await conn.execute(DDL)


async def seed_static(conn):
    """Заполняет таблицы forecast_points/schedule_stops из CSV, если пусты."""
    n_points = await conn.fetchval("SELECT count(*) FROM forecast_points")
    if n_points == 0 and os.path.exists(POINTS_CSV):
        df = pd.read_csv(POINTS_CSV)
        for _, r in df.iterrows():
            await conn.execute(
                "INSERT INTO forecast_points (sample_id,tr_id,T,target_stop_id,target_time_begin,cur_dev_s) VALUES ($1,$2,$3,$4,$5,$6)",
                str(r["sample_id"]), str(r["tr_id"]), r["T"], str(r["target_stop_id"]),
                r["target_time_begin"], float(r["cur_dev_s"]),
            )

    n_sched = await conn.fetchval("SELECT count(*) FROM schedule_stops")
    if n_sched == 0 and os.path.exists(SCHEDULE_CSV):
        df = pd.read_csv(SCHEDULE_CSV)
        for _, r in df.iterrows():
            await conn.execute(
                "INSERT INTO schedule_stops (tt_action_item_id,time_begin,tr_id,geom) VALUES ($1,$2,$3,$4)",
                str(r["tt_action_item_id"]), r["time_begin"], str(r["tr_id"]), r["geom"],
            )

    has_tele = await conn.fetchval(
        "SELECT count(*) FROM ndtp_telemetry WHERE tr_id IN (SELECT DISTINCT tr_id FROM forecast_points)"
    )
    if has_tele == 0 and os.path.exists(TRAFFIC_CSV):
        ids = {str(r["tr_id"]) for _, r in pd.read_csv(POINTS_CSV).iterrows()}
        df = pd.read_csv(TRAFFIC_CSV)
        df["tr_id"] = df["tr_id"].astype(str)
        df = df[df["tr_id"].isin(ids)]
        inserted = 0
        for _, r in df.iterrows():
            recv = datetime.fromisoformat(str(r["receive_time"])) if pd.notna(r["receive_time"]) else None
            gps = "" if pd.isna(r["gps_time"]) else r["gps_time"]
            for col in ("lon", "lat", "alt", "speed"):
                if pd.isna(r[col]):
                    r = r.copy()
                    r[col] = 0.0
            try:
                await conn.execute(
                    """
                    INSERT INTO ndtp_telemetry
                        (packet_id,tr_id,unit_id,event_time,device_event_id,location_valid,
                         gps_time,lon,lat,alt,speed,heading,receive_time,is_hist_data)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                    """,
                    str(r.get("packet_id") or ""), str(r["tr_id"]), str(r["unit_id"] or ""),
                    r["event_time"], str(r.get("device_event_id") or 0),
                    bool(r["location_valid"]), gps,
                    float(r["lon"]), float(r["lat"]), float(r["alt"]),
                    float(r["speed"]), float(r["heading"]), recv,
                    bool(r.get("is_hist_data", False)),
                )
                inserted += 1
            except Exception as exc:
                continue
        print(f"[worker] залито телеметрии: {inserted} строк", flush=True)


async def fetchtab(conn, sql, *args) -> list:
    """Выполнить SELECT и вернуть строки как list[dict] (для pandas)."""
    rows = await conn.fetch(sql, *args)
    return [dict(r) for r in rows]


async def infer(conn):
    if not model.ready:
        print(f"[worker] модель не загружена: {model.error}", flush=True)
        return

    points = pd.DataFrame(await fetchtab(conn, "SELECT * FROM forecast_points"))
    if points.empty:
        return
    points.rename(columns={"t": "T"}, inplace=True)

    schedule = pd.DataFrame(await fetchtab(conn, "SELECT * FROM schedule_stops"))

    tele = pd.DataFrame(
        await fetchtab(
            conn,
            "SELECT event_time, tr_id, location_valid, lon, lat, speed "
            "FROM ndtp_telemetry ORDER BY id DESC LIMIT $1",
            TELEMETRY_LIMIT,
        )
    )

    feats = build_features(points, tele, schedule)
    preds = model.booster.predict(feats[FEATURES])
    for sid, val in zip(feats.index, preds):
        await conn.execute(
            """
            INSERT INTO predictions (sample_id, prediction)
            VALUES ($1, $2)
            ON CONFLICT (sample_id) DO UPDATE SET prediction = EXCLUDED.prediction
            """,
            str(sid), float(round(val, 1)),
        )
    print(f"[worker] записано {len(preds)} предсказаний", flush=True)


async def main():
    from asyncpg import connect

    while True:
        try:
            conn = await connect(DB_DSN)
            await init_db(conn)
            await seed_static(conn)
            await infer(conn)
            await conn.close()
        except Exception as exc:
            print(f"[worker] ошибка: {exc!r}", flush=True)
        await asyncio.sleep(INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass