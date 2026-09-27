"""ML-воркер: инференс через общую БД.

Окно live-прогноза считается от ЧАСОВ ПОТОКА (max event_time в БД),
а не от настенных — так воркеру безразлично, сдвигает ли эмулятор время.
"""

import asyncio
import os
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from features import FEATURES, build_features  # пайплайн признаков
from model_service import model  # singleton: грузится один раз
from map_matching import MapMatcher, decode_geom  # Map Matching (доп. фича ТЗ)

DB_DSN = os.getenv(
    "DB_DSN",
    "postgresql://mthack:mthack@db:5432/mthack",
)
POINTS_CSV = os.getenv("POINTS_CSV", "/app/data/points.csv")
SCHEDULE_CSV = os.getenv("SCHEDULE_CSV", "/app/data/schedule_plan.csv")
TRAFFIC_CSV = os.getenv("TRAFFIC_CSV", "/app/data/traffic.csv")

INTERVAL = int(os.getenv("INTERVAL", "20"))
TELEMETRY_LIMIT = int(os.getenv("TELEMETRY_LIMIT", "200000"))
STALE_AFTER_SECONDS = int(os.getenv("STALE_AFTER_SECONDS", "45"))

LIVE_WINDOW_MIN = int(os.getenv("LIVE_WINDOW_MIN", "10"))
LIVE_WINDOW_MAX = int(os.getenv("LIVE_WINDOW_MAX", "15"))
LIVE_MAX_VEHICLES = int(os.getenv("LIVE_MAX_VEHICLES", "200"))

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
CREATE TABLE IF NOT EXISTS route_features (
    id BIGSERIAL PRIMARY KEY,
    tr_id TEXT,
    event_time TEXT,
    lat DOUBLE PRECISION,
    lon DOUBLE PRECISION,
    speed DOUBLE PRECISION,
    heading DOUBLE PRECISION,
    matched_lat DOUBLE PRECISION,
    matched_lon DOUBLE PRECISION,
    route_progress_m DOUBLE PRECISION,
    route_progress_frac DOUBLE PRECISION,
    dist_to_route_m DOUBLE PRECISION,
    seg_index INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS route_features_tr_event_uidx
    ON route_features (tr_id, event_time);
"""


def load_csv(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path)


async def init_db(conn):
    await conn.execute(DDL)
    # Postgres не поддерживает ADD COLUMN IF NOT EXISTS в DDL — добавляем колонку безопасно
    has = await conn.fetchval(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name='predictions' AND column_name='stale'"
    )
    if not has:
        await conn.execute("ALTER TABLE predictions ADD COLUMN stale BOOLEAN DEFAULT false")


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
            except Exception:
                continue
        print(f"[worker] залито телеметрии: {inserted} строк", flush=True)


async def fetchtab(conn, sql, *args) -> list:
    """Выполнить SELECT и вернуть строки как list[dict] (для pandas)."""
    rows = await conn.fetch(sql, *args)
    return [dict(r) for r in rows]


async def map_match_telemetry(conn):
    """Map Matching: проецирует телеметрию на нить маршрута в route_features."""
    sched = await fetchtab(conn, "SELECT tr_id, geom FROM schedule_stops")
    from collections import OrderedDict
    order: dict[str, list[tuple]] = OrderedDict()
    for row in sched:
        order.setdefault(row["tr_id"], []).append(decode_geom(row["geom"]))
    has_route = [tr for tr, stops in order.items() if len(stops) >= 2]
    if not has_route:
        return

    recent = await fetchtab(
        conn,
        "SELECT tr_id, event_time, lat, lon, speed, heading "
        "FROM ndtp_telemetry WHERE location_valid AND tr_id = ANY($1) "
        "ORDER BY id DESC LIMIT 1500",
        has_route,
    )
    if not recent:
        return

    written = 0
    for tr in has_route:
        mm = MapMatcher(order[tr])
        for p in recent:
            if p["tr_id"] != tr:
                continue
            try:
                res = mm.match(p["lat"], p["lon"], heading=p["heading"], speed=p["speed"])
                # ON CONFLICT: не плодим дубли при повторных циклах
                await conn.execute(
                    """
                    INSERT INTO route_features
                        (tr_id,event_time,lat,lon,speed,heading,
                         matched_lat,matched_lon,route_progress_m,route_progress_frac,
                         dist_to_route_m,seg_index)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                    ON CONFLICT (tr_id, event_time) DO NOTHING
                    """,
                    tr, p["event_time"], p["lat"], p["lon"], p.get("speed"),
                    p.get("heading"), res["matched_lat"], res["matched_lon"],
                    res["route_progress_m"], res["route_progress_frac"],
                    res["dist_to_route_m"], res["seg_index"],
                )
                written += 1
            except Exception:
                continue
        if written:
            break  # за один цикл достаточно одной машины (демонстрация контура)
    print(f"[mapmatch] записано {written} записей Map Matching", flush=True)


def _current_dev_s(grp_sched, last, now_naive):
    """Оценка текущего отклонения ТС: задержка на последней уже пройденной остановке.

    now_naive — ВРЕМЯ ПОТОКА (max event_time), а не настенные часы.
    """
    pts = [decode_geom(g) for g in grp_sched["geom"]]
    if len(pts) < 2:
        return np.nan
    mm = MapMatcher(pts)
    res = mm.match(float(last["lat"]), float(last["lon"]))
    prog = res["route_progress_m"]
    offsets = [0.0]
    for i in range(len(pts) - 1):
        offsets.append(offsets[-1] + mm.seg_len[i])
    times = grp_sched["time_begin"].tolist()
    last_passed = None
    for off, tb in zip(offsets, times):
        if off <= prog + 5.0:
            last_passed = tb
    if last_passed is None or pd.isna(last_passed):
        return np.nan
    return (now_naive - last_passed).total_seconds()


async def live_early_warning(conn):
    """Живое раннее оповещение на потоке.

    «Сейчас» (T) — время последней телеметрии, реально поступившей в БД
    (max event_time), а не настенные часы сервера. Для каждого активного ТС
    ищется ближайшая плановая остановка с прибытием в окне T+10..15 мин,
    собираются те же 24 признака (только данные на момент T, анти-утечка),
    прогноз пишется в predictions с sample_id = live_<tr_id>.
    Прогноз подписан: '+' => опоздание, '-' => опережение.
    """
    if not model.ready:
        print(f"[live] модель не загружена: {model.error}", flush=True)
        return

    # --- часы потока: max event_time из поступившей телеметрии ---
    max_et = await conn.fetchval(
        "SELECT MAX(event_time) FROM ndtp_telemetry WHERE location_valid"
    )
    if not max_et:
        print("[live] телеметрии в БД нет — жду поток", flush=True)
        return
    T_dt = pd.Timestamp(str(max_et))
    now_naive = T_dt.to_pydatetime()
    lo = T_dt + pd.Timedelta(minutes=LIVE_WINDOW_MIN)
    hi = T_dt + pd.Timedelta(minutes=LIVE_WINDOW_MAX)

    sched = pd.DataFrame(await fetchtab(
        conn, "SELECT tt_action_item_id, time_begin, tr_id, geom FROM schedule_stops"))
    if sched.empty:
        print("[live] schedule_stops пустая — расписание не загружено", flush=True)
        return
    sched["time_begin"] = pd.to_datetime(sched["time_begin"])
    sched["tr_id"] = sched["tr_id"].astype(str)

    active = await fetchtab(
        conn, "SELECT DISTINCT tr_id FROM ndtp_telemetry WHERE location_valid")
    if not active:
        print("[live] нет валидной телеметрии", flush=True)
        return
    active_ids = {str(r["tr_id"]) for r in active}
    sched = sched[sched["tr_id"].isin(active_ids)]
    if sched.empty:
        print(
            f"[live] расписание не пересекается с активными ТС "
            f"(active={len(active_ids)}): проверьте маппинг tr_id",
            flush=True,
        )
        return

    nxt = sched[(sched["time_begin"] > lo) & (sched["time_begin"] <= hi)]
    if nxt.empty:
        print(
            f"[live] T={T_dt} — в окне +{LIVE_WINDOW_MIN}..{LIVE_WINDOW_MAX} мин "
            f"нет плановых остановок (расписание: "
            f"{sched['time_begin'].min()} .. {sched['time_begin'].max()})",
            flush=True,
        )
        return

    rows = []
    for tr, grp in nxt.groupby("tr_id"):
        if len(rows) >= LIVE_MAX_VEHICLES:
            break
        cand = grp.nsmallest(1, "time_begin").iloc[0]  # ближайшая в окне T+10..15 мин
        grp_sched = sched[sched["tr_id"] == tr].sort_values("time_begin")

        tele_tr = await fetchtab(
            conn,
            "SELECT event_time, location_valid, lon, lat, speed, heading "
            "FROM ndtp_telemetry WHERE tr_id = $1 AND location_valid "
            "ORDER BY id DESC LIMIT 1",
            tr,
        )
        if not tele_tr:
            continue
        last = tele_tr[0]
        cur_dev = _current_dev_s(grp_sched, last, now_naive)
        rows.append({
            "sample_id": f"live_{tr}",                       # стабильный id: текущий алерт по ТС
            "tr_id": tr,
            "T": now_naive,
            "target_stop_id": str(cand["tt_action_item_id"]),
            "target_time_begin": cand["time_begin"],
            "cur_dev_s": cur_dev,
        })

    if not rows:
        print("[live] нет активных ТС с телеметрией для точек в окне", flush=True)
        return

    points = pd.DataFrame(rows)
    tele = pd.DataFrame(await fetchtab(
        conn,
        "SELECT event_time, tr_id, location_valid, lon, lat, speed "
        "FROM ndtp_telemetry ORDER BY id DESC LIMIT $1",
        TELEMETRY_LIMIT,
    ))

    _t0 = time.perf_counter()                       # метрика latency (критерий 5)
    feats = build_features(points, tele, sched)
    preds = model.booster.predict(feats[FEATURES])
    infer_ms = (time.perf_counter() - _t0) * 1000.0

    # stale — по настенным часам записи бэкендом (это корректно, не путать с T)
    now = datetime.now(timezone.utc)
    last_received = await conn.fetchval("SELECT MAX(receive_time) FROM ndtp_telemetry")
    stale = False
    if last_received is not None:
        lr = last_received if last_received.tzinfo else last_received.replace(tzinfo=timezone.utc)
        stale = (now - lr).total_seconds() > STALE_AFTER_SECONDS

    n_late = 0
    for sid, val in zip(feats.index, preds):
        pred = float(round(val, 1))
        if pred > 0:
            n_late += 1
        await conn.execute(
            """
            INSERT INTO predictions (sample_id, prediction, stale)
            VALUES ($1, $2, $3)
            ON CONFLICT (sample_id) DO UPDATE
                SET prediction = EXCLUDED.prediction, stale = EXCLUDED.stale
            """,
            str(sid), pred, stale,
        )
    print(
        f"[live] T={T_dt} алертов={len(preds)} опозданий={n_late} "
        f"ранних/вовремя={len(preds) - n_late} stale={stale} "
        f"latency={infer_ms:.1f}ms ({infer_ms / len(preds):.1f}ms/алерт)",
        flush=True,
    )


async def infer(conn):
    if not model.ready:
        print(f"[worker] модель не загружена: {model.error}", flush=True)
        return

    points = pd.DataFrame(await fetchtab(conn, "SELECT * FROM forecast_points"))
    if points.empty:
        print("[worker] forecast_points пустая — points.csv не загружен, офлайн-прогноз пропущен", flush=True)
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

    # --- деградация: определяем свежесть последнего принятого пакета ---
    last_received = await conn.fetchval(
        "SELECT MAX(receive_time) FROM ndtp_telemetry"
    )
    stale = False
    if last_received is not None:
        now = datetime.now(timezone.utc)
        last = last_received if last_received.tzinfo else last_received.replace(tzinfo=timezone.utc)
        age = (now - last).total_seconds()
        stale = age > STALE_AFTER_SECONDS
    if stale:
        print(f"[worker] поток молчит >{STALE_AFTER_SECONDS}s — прогноз по последним данным (stale)", flush=True)

    feats = build_features(points, tele, schedule)
    preds = model.booster.predict(feats[FEATURES])
    for sid, val in zip(feats.index, preds):
        await conn.execute(
            """
            INSERT INTO predictions (sample_id, prediction, stale)
            VALUES ($1, $2, $3)
            ON CONFLICT (sample_id) DO UPDATE
                SET prediction = EXCLUDED.prediction, stale = EXCLUDED.stale
            """,
            str(sid), float(round(val, 1)), stale,
        )
    print(f"[worker] записано {len(preds)} предсказаний (stale={stale})", flush=True)


async def main():
    from asyncpg import connect

    while True:
        try:
            conn = await connect(DB_DSN)
            await init_db(conn)
            await seed_static(conn)
            await infer(conn)
            await live_early_warning(conn)
            await map_match_telemetry(conn)
            await conn.close()
        except Exception as exc:
            print(f"[worker] ошибка: {exc!r}", flush=True)
        await asyncio.sleep(INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass