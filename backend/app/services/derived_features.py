"""Производные признаки парка по телеметрии и расписанию (без pandas, stdlib)."""

import math
from collections import defaultdict

_R = 6371000.0


def _hav(lat1, lon1, lat2, lon2):
    """Гаверсинусное расстояние между точками в метрах."""
    p1a, p1b = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1a) * math.cos(p1b) * math.sin(dlon / 2) ** 2
    return 2 * _R * math.asin(math.sqrt(a))


def build_routes(schedule_stops_rows):
    """Группирует остановки по tr_id как [(lat, lon, time_begin), ...] (>=2 шт)."""
    routes = defaultdict(list)
    for r in schedule_stops_rows:
        routes[r["tr_id"]].append((r["stop_lat"], r["stop_lon"], r.get("time_begin")))
    return {k: v for k, v in routes.items() if len(v) >= 2}


def _route_metrics(lat, lon, pts):
    """Позиция точки на нити: (progress_m, route_len_m, next_stop_index, plan_next)."""
    cum = [0.0]
    for i in range(len(pts) - 1):
        cum.append(cum[-1] + _hav(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]))
    route_len = cum[-1]

    best_dist = float("inf")
    best_stop = 0
    for i, (slat, slon, _plan) in enumerate(pts):
        d = _hav(lat, lon, slat, slon)
        if d < best_dist:
            best_dist, best_stop = d, i

    next_idx = min(best_stop + 1, len(pts) - 1)
    return cum[best_stop], route_len, next_idx, pts[next_idx][2]


def _segment_speed(samples, pts, route_len, last_progress_m):
    """Средняя скорость на текущем сегменте (вокруг последней позиции)."""
    seg_frac = last_progress_m / route_len if route_len else 0.0
    lo, hi = max(seg_frac - 0.1, 0.0), min(seg_frac + 0.1, 1.0)
    speeds = []
    for s in samples:
        proj_m = _route_metrics(float(s["lat"]), float(s["lon"]), pts)[0]
        frac = proj_m / route_len if route_len else 0.0
        if lo <= frac <= hi and s.get("speed") is not None:
            speeds.append(float(s["speed"]))
    return round(sum(speeds) / len(speeds), 2) if speeds else None


def fleet_features(telemetry_rows, schedule_stops_rows):
    """Считает признаки по каждому ТС: скорость, простой, позиция и отклонение."""
    routes = build_routes(schedule_stops_rows)
    by_tr = defaultdict(list)
    for t in telemetry_rows:
        by_tr[t["tr_id"]].append(t)

    out = []
    for tr_id, samples in by_tr.items():
        speeds = [float(x["speed"]) for x in samples if x.get("speed") is not None]
        if not speeds:
            continue

        avg_speed = sum(speeds) / len(speeds)
        idle_share = sum(1 for s in speeds if s < 3) / len(speeds)

        last = max(samples, key=lambda x: str(x.get("event_time", "")))
        lat, lon = float(last["lat"]), float(last["lon"])

        item = {
            "tr_id": tr_id,
            "n_points": len(samples),
            "avg_speed_kmh": round(avg_speed, 2),
            "idle_points": sum(1 for s in speeds if s < 3),
            "idle_share": round(idle_share, 3),
        }

        pts = routes.get(tr_id)
        if pts:
            progress_m, route_len, next_idx, plan_next = _route_metrics(lat, lon, pts)
            nlat, nlon, _nplan = pts[next_idx]
            dist_next_m = _hav(lat, lon, nlat, nlon)
            item["route_len_m"] = round(route_len, 1)
            item["route_progress_m"] = round(progress_m, 1)
            item["route_progress_frac"] = round(progress_m / route_len, 3) if route_len else None
            item["next_stop_index"] = next_idx
            item["seg_speed_kmh"] = _segment_speed(samples, pts, route_len, progress_m)
            item["deviation_s"] = _deviation_s(dist_next_m, plan_next, avg_speed, last)
        out.append(item)
    return out


def _deviation_s(dist_next_m, plan_next, avg_speed_kmh, last):
    """Ожидаемое время до остановки минус плановый остаток (сек, +/- = позже/раньше)."""
    t_last = _parse_dt(last.get("event_time"))
    t_plan = _parse_dt(plan_next)
    if t_last is None or t_plan is None or not avg_speed_kmh or avg_speed_kmh <= 0:
        return None

    plan_remaining_s = (t_plan - t_last).total_seconds()
    exp_remaining_s = dist_next_m / (avg_speed_kmh / 3.6)
    return round(exp_remaining_s - plan_remaining_s, 1)


def _parse_dt(value):
    """Парсит 'YYYY-MM-DD HH:MM:SS[.f]' в datetime; None при ошибке."""
    from datetime import datetime
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def whatif_add_vehicle(schedule_stops_rows, telemetry_rows, tr_id, add_vehicles):
    """Симулирует выпуск доп. ТС: интервалы и задержка «до/после»."""
    pts = build_routes(schedule_stops_rows).get(tr_id)
    if not pts:
        return None

    route_len = sum(
        _hav(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
        for i in range(len(pts) - 1)
    )
    active = max(sum(1 for t in telemetry_rows if t["tr_id"] == tr_id and t.get("speed") is not None), 1)

    speeds = [float(t["speed"]) for t in telemetry_rows if t["tr_id"] == tr_id and t.get("speed")]
    avg_speed = max(sum(speeds) / len(speeds) if speeds else 25.0, 1.0)

    cycle_s = route_len / (avg_speed / 3.6)
    interval_before = cycle_s / active
    total_after = active + max(add_vehicles, 0)
    interval_after = cycle_s / total_after if total_after else interval_before

    return {
        "tr_id": tr_id,
        "route_len_m": round(route_len, 1),
        "active_vehicles": active,
        "add_vehicles": add_vehicles,
        "avg_speed_kmh": round(avg_speed, 1),
        "interval_before_s": round(interval_before, 1),
        "interval_after_s": round(interval_after, 1),
        "delay_before_s": round(interval_before * 0.4, 1),
        "delay_after_s": round(interval_after * 0.4, 1),
    }