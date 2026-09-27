"""ML-воркер: live-инференс через общую БД.

Отвечает ТОЛЬКО за реальное время (поток NDTP-эмулятора):
окно прогноза считается от ЧАСОВ ПОТОКА (max event_time в БД),
а не от настенных — так воркеру безразлично, сдвигает ли эмулятор время.

Оффлайн-сабмит (points.csv -> submission.csv) воркером не делается:
для этого есть `python features.py` (блок __main__), который читает
CSV напрямую и не зависит от состояния БД.
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
SCHEDULE_CSV = os.getenv("SCHEDULE_CSV", "/app/data/schedule_plan.csv")
TRAFFIC_CSV = os.getenv("TRAFFIC_CSV", "/app/data/traffic.csv")  # только для маппинга unit_id->tr_id

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
CREATE TABLE IF NOT EXISTS stream_shift (
    id INTEGER PRIMARY KEY,
    offset_s DOUBLE PRECISION
);
CREATE TABLE IF NOT EXISTS unit_map (
    unit_id TEXT PRIMARY KEY,
    tr_id TEXT
);
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
    """Заполняет справочники из CSV, если пусты.

    schedule_stops — плановое расписание; unit_map — соответствие
    unit_id (устройство, его шлёт эмулятор) -> tr_id (ТС из расписания),
    источник маппинга — traffic.csv.
    """
    n_sched = await conn.fetchval("SELECT count(*) FROM schedule_stops")
    if n_sched == 0 and os.path.exists(SCHEDULE_CSV):
        df = pd.read_csv(SCHEDULE_CSV)
        for _, r in df.iterrows():
            await conn.execute(
                "INSERT INTO schedule_stops (tt_action_item_id,time_begin,tr_id,geom) "
                "VALUES ($1,$2,$3,$4) ON CONFLICT (tt_action_item_id) DO NOTHING",
                str(r["tt_action_item_id"]), r["time_begin"], str(r["tr_id"]), r["geom"],
            )
        print(f"[worker] залито расписание: {len(df)} строк", flush=True)

    n_map = await conn.fetchval("SELECT count(*) FROM unit_map")
    if n_map == 0 and os.path.exists(TRAFFIC_CSV):
        df = pd.read_csv(TRAFFIC_CSV, dtype=str)
        pairs = df[["unit_id", "tr_id"]].dropna().drop_duplicates("unit_id")
        for _, r in pairs.iterrows():
            await conn.execute(
                "INSERT INTO unit_map (unit_id, tr_id) VALUES ($1, $2) "
                "ON CONFLICT (unit_id) DO NOTHING",
                r["unit_id"], r["tr_id"],
            )
        print(f"[worker] залит маппинг unit_id->tr_id: {len(pairs)} пар", flush=True)


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
        "SELECT unit_id, event_time, lat, lon, speed, heading "
        "FROM ndtp_telemetry WHERE location_valid "
        "ORDER BY id DESC LIMIT 1500",
    )
    if not recent:
        return

    # в потоке unit_id устройства — переводим в tr_id расписания
    map_rows = await fetchtab(conn, "SELECT unit_id, tr_id FROM unit_map")
    unit2tr = {r["unit_id"]: r["tr_id"] for r in map_rows}
    for p in recent:
        p["tr_id"] = unit2tr.get(str(p["unit_id"]))
    recent = [p for p in recent if p["tr_id"] in order]
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
                # ON CONFLICT: не плодим дубли при повторных циклах;
                # считаем только реально вставленные строки (status = "INSERT 0 1")
                status = await conn.execute(
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
                if status.endswith("1"):
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


async def get_stream_offset(conn) -> float:
    """Сдвиг (в секундах) между шкалой времени расписания и потока.

    Эмулятор вещает в «настоящем» времени, а schedule_plan.csv исторический.
    Сдвиг = (старт живого потока) − (минимум расписания); вычисляется один
    раз, сохраняется в stream_shift и дальше переиспользуется. Само
    расписание в БД НЕ меняется — оффлайн-инференс (points.csv) остаётся
    на исходной шкале и не ломается.
    """
    row = await conn.fetchrow("SELECT offset_s FROM stream_shift WHERE id = 1")
    if row:
        return float(row["offset_s"])
    t0 = await conn.fetchval(
        "SELECT MIN(event_time)::timestamp FROM ndtp_telemetry "
        "WHERE receive_time > now() - interval '1 day'"
    )
    smin = await conn.fetchval("SELECT MIN(time_begin)::timestamp FROM schedule_stops")
    if not t0 or not smin:
        return 0.0  # поток ещё не пошёл — попробуем на следующем цикле
    offset = (pd.Timestamp(str(t0)) - pd.Timestamp(str(smin))).total_seconds()
    if abs(offset) < 60:
        offset = 0.0  # шкалы уже совпадают (или расписание сдвинуто вручную)
    await conn.execute(
        "INSERT INTO stream_shift (id, offset_s) VALUES (1, $1) "
        "ON CONFLICT (id) DO NOTHING",
        offset,
    )
    if offset:
        print(f"[live] шкала расписания сдвинута на {offset / 3600:.1f} ч "
              f"к времени потока", flush=True)
    return offset


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

    # --- авто-сдвиг расписания на шкалу потока (в памяти, БД не трогаем) ---
    offset_s = await get_stream_offset(conn)
    if offset_s:
        sched["time_begin"] += pd.to_timedelta(offset_s, unit="s")

    # --- активные ТС: в потоке лежит unit_id устройства, переводим в tr_id ---
    active = await fetchtab(
        conn, "SELECT DISTINCT unit_id FROM ndtp_telemetry WHERE location_valid")
    if not active:
        print("[live] нет валидной телеметрии", flush=True)
        return
    unit_ids = [str(r["unit_id"]) for r in active]
    map_rows = await fetchtab(
        conn, "SELECT unit_id, tr_id FROM unit_map WHERE unit_id = ANY($1)", unit_ids)
    unit2tr = {r["unit_id"]: r["tr_id"] for r in map_rows}
    tr2unit = {v: k for k, v in unit2tr.items()}
    if not unit2tr:
        print(f"[live] ни один unit_id потока не найден в unit_map "
              f"(active={len(unit_ids)})", flush=True)
        return
    active_ids = set(unit2tr.values())
    sched = sched[sched["tr_id"].isin(active_ids)]
    if sched.empty:
        print(
            f"[live] расписание не пересекается с активными ТС "
            f"(active={len(active_ids)} из {len(unit_ids)} юнитов): "
            f"эмулятор гоняет ТС не из расписания",
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
            "FROM ndtp_telemetry WHERE unit_id = $1 AND location_valid "
            "ORDER BY id DESC LIMIT 1",
            tr2unit.get(tr, tr),
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
        "SELECT event_time, unit_id, location_valid, lon, lat, speed "
        "FROM ndtp_telemetry ORDER BY id DESC LIMIT $1",
        TELEMETRY_LIMIT,
    ))
    if not tele.empty:
        # переводим unit_id потока в tr_id расписания — build_features группирует по tr_id
        tele["tr_id"] = tele["unit_id"].astype(str).map(unit2tr)
        tele = tele.dropna(subset=["tr_id"])

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


async def main():
    from asyncpg import connect

    while True:
        try:
            conn = await connect(DB_DSN)
            await init_db(conn)
            await seed_static(conn)
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
