"""Map Matching: привязка GPS-позиций к нити графика маршрута (с учётом курса)."""

import math

_HEADING_WEIGHT = 40.0  # штраф (метры) за каждый градус несовпадения курса


def _bearing(p1, p2):
    """Азимут (0..360) направления от p1 к p2."""
    lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
    lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def _bearing_diff(a, b):
    d = abs(a - b) % 360.0
    return d if d <= 180.0 else 360.0 - d


class MapMatcher:
    """Привязывает GPS-точки к полилинии маршрута, построенной из остановок."""

    def __init__(self, stops, use_heading=True):
        """stops: итерируемый объект пар (lat, lon) в порядке проезда."""
        self.pts = [(float(a), float(b)) for a, b in stops]
        self.n = len(self.pts)
        self.use_heading = use_heading
        self.seg_len = [self._hav(self.pts[i], self.pts[i + 1]) for i in range(self.n - 1)]
        self.seg_bearing = [
            _bearing(self.pts[i], self.pts[i + 1]) if i < self.n - 1 else 0.0
            for i in range(self.n - 1)
        ]
        self.route_len = sum(self.seg_len)
        self._pref = [sum(self.seg_len[:i]) for i in range(self.n)]

    @staticmethod
    def _hav(p1, p2):
        """Гаверсинусное расстояние в метрах."""
        R = 6371000.0
        lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
        lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])
        dlat, dlon = lat2 - lat1, lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        return 2 * R * math.asin(math.sqrt(a))

    def _project(self, p, a, b):
        """Проецирует точку p на отрезок ab; возвращает (t, dist, proj)."""
        ax, ay = a
        bx, by = b
        dx, dy = bx - ax, by - ay
        if dx == 0 and dy == 0:
            return 0.0, self._hav(p, a), a
        t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / (dx * dx + dy * dy)
        t = max(0.0, min(1.0, t))
        proj = (ax + t * dx, ay + t * dy)
        return t, self._hav(p, proj), proj

    def match(self, lat, lon, heading=None, speed=None):
        """Привязывает точку к маршруту (с учётом курса) и возвращает метрики."""
        p = (float(lat), float(lon))
        if self.n < 2:
            return {
                "route_progress_m": 0.0, "route_progress_frac": 0.0,
                "dist_to_route_m": (self._hav(p, self.pts[0]) if self.pts else 0.0),
                "matched_lat": (self.pts[0][0] if self.pts else lat),
                "matched_lon": (self.pts[0][1] if self.pts else lon),
                "seg_index": 0, "seg_start_m": 0.0,
            }

        best_seg, best_t, best_proj = 0, 0.0, p
        best_dist = float("inf")
        best_score = float("inf")
        for i in range(self.n - 1):
            t, d, proj = self._project(p, self.pts[i], self.pts[i + 1])
            score = d
            if self.use_heading and heading is not None:
                ang = _bearing_diff(heading, self.seg_bearing[i])
                penalty = _HEADING_WEIGHT * (ang / 180.0)
                if speed is not None:
                    penalty /= max(speed, 1.0)  # медленный ТС — курс менее достоверен
                score += penalty
            if score < best_score:
                best_score, best_seg, best_t, best_proj, best_dist = score, i, t, proj, d

        offset = self._pref[best_seg] + self.seg_len[best_seg] * best_t
        progress_frac = offset / self.route_len if self.route_len else 0.0
        return {
            "route_progress_m": offset,
            "route_progress_frac": progress_frac,
            "dist_to_route_m": best_dist,
            "matched_lat": best_proj[0],
            "matched_lon": best_proj[1],
            "seg_index": best_seg,
            "seg_start_m": self._pref[best_seg],
        }

    def dist_to_stop(self, lat, lon, target_stop, along_route=True):
        """Дистанция до целевой остановки: по нити маршрута или по прямой."""
        match = self.match(lat, lon)
        if along_route and self.route_len:
            progress = match["route_progress_m"]
            best = 0
            best_d = float("inf")
            for idx, stop in enumerate(self.pts):
                d = self._hav(stop, target_stop)
                if d < best_d:
                    best_d, best = d, idx
            stop_at = self._pref[best]
            remaining = max(stop_at - progress + self.seg_len[min(best, self.n - 2)] * 0, 0.0)
            # остаток до позиции остановки >= текущая позиция
            return max(stop_at - progress, 0.0)
        return self._hav((lat, lon), target_stop)


def decode_geom(geom: str):
    """Парсит 'POINT (lon lat)' в пару (lat, lon)."""
    inner = geom.replace("POINT", "").strip().strip("()")
    parts = inner.split()
    return float(parts[1]), float(parts[0])