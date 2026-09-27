"""
Пайплайн признаков для предиктора задержек городского транспорта.
Работает одинаково для train (labels_train.csv) и validate (points.csv):
используются только данные, доступные на момент T (анти-утечка).

Использование:
    feats = build_features(points_df, traffic_df, schedule_df)
    # points_df: sample_id, tr_id, T, target_stop_id, target_time_begin, cur_dev_s
    # schedule_df: для validate — schedule_plan.csv (time_fact_begin может отсутствовать)
"""
import re
import numpy as np
import pandas as pd

FEATURES = [
    'horizon_min', 'hour', 'minute', 'dow', 't_since_last_s', 'last_speed',
    'last_lon', 'last_lat', 'speed_mean_5m', 'speed_max_5m', 'speed_std_5m',
    'stop_share_5m', 'speed_mean_10m', 'speed_max_10m', 'speed_std_10m',
    'stop_share_10m', 'dist_10m_m', 'plan_seg_s', 'dist_to_target_m',
    'req_speed_kmh', 'eta_delay_5m', 'eta_delay_10m', 'n_stops_between',
    'cur_dev_s',
]


def parse_geom(s):
    if pd.isna(s):
        return (np.nan, np.nan)
    m = re.match(r'POINT \(([\d.]+) ([\d.]+)\)', str(s))
    return (float(m.group(1)), float(m.group(2))) if m else (np.nan, np.nan)


def hav(lat1, lon1, lat2, lon2):
    """Гаверсинус, метры (принимает numpy-массивы)."""
    R = 6371000.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = np.radians(lat2 - lat1), np.radians(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


def build_features(points, traffic, schedule):
    points = points.copy()
    traffic = traffic.copy()
    schedule = schedule.copy()
    points['T'] = pd.to_datetime(points['T'])
    points['target_time_begin'] = pd.to_datetime(points['target_time_begin'])
    traffic['event_time'] = pd.to_datetime(traffic['event_time'])
    schedule['time_begin'] = pd.to_datetime(schedule['time_begin'])

    schedule[['stop_lon', 'stop_lat']] = pd.DataFrame(
        schedule['geom'].apply(parse_geom).tolist(), index=schedule.index)
    sched_sorted = schedule.sort_values(['tr_id', 'time_begin']).reset_index(drop=True)
    sched_sorted['prev_time_begin'] = sched_sorted.groupby('tr_id')['time_begin'].shift(1)
    sched_sorted['plan_seg_s'] = (
        sched_sorted['time_begin'] - sched_sorted['prev_time_begin']).dt.total_seconds()
    sched_by_stop = sched_sorted.set_index('tt_action_item_id')

    tele = traffic[traffic['location_valid']].sort_values(['tr_id', 'event_time'])
    tele_grp = dict(tuple(tele.groupby('tr_id')))

    rows = []
    for _, r in points.iterrows():
        tr, T = r['tr_id'], r['T']
        f = {'sample_id': r['sample_id'], 'cur_dev_s': r['cur_dev_s'],
             'horizon_min': (r['target_time_begin'] - T).total_seconds() / 60,
             'hour': T.hour, 'minute': T.minute, 'dow': T.dayofweek}

        g = tele_grp.get(tr)
        if g is not None:
            hist = g[g['event_time'].values <= np.datetime64(T)]
            if len(hist):
                last = hist.iloc[-1]
                f['t_since_last_s'] = (T - last['event_time']).total_seconds()
                f['last_speed'] = last['speed']
                f['last_lon'], f['last_lat'] = last['lon'], last['lat']
                for w in (5, 10):
                    sp = hist[hist['event_time'] > T - pd.Timedelta(minutes=w)]['speed'].values
                    if len(sp):
                        f[f'speed_mean_{w}m'] = sp.mean()
                        f[f'speed_max_{w}m'] = sp.max()
                        f[f'speed_std_{w}m'] = sp.std() if len(sp) > 1 else 0.0
                        f[f'stop_share_{w}m'] = (sp < 3).mean()
                h10 = hist[hist['event_time'] > T - pd.Timedelta(minutes=10)]
                f['dist_10m_m'] = float(hav(h10['lat'].values[:-1], h10['lon'].values[:-1],
                                            h10['lat'].values[1:], h10['lon'].values[1:]).sum()) if len(h10) > 1 else 0.0

        s = sched_by_stop.loc[r['target_stop_id']]
        f['plan_seg_s'] = s['plan_seg_s']
        if 'last_lon' in f and not pd.isna(f.get('last_lon')):
            f['dist_to_target_m'] = float(hav(f['last_lat'], f['last_lon'], s['stop_lat'], s['stop_lon']))
            time_left_s = max((r['target_time_begin'] - T).total_seconds(), 1)
            f['req_speed_kmh'] = f['dist_to_target_m'] / time_left_s * 3.6
            for w in (5, 10):
                m = f.get(f'speed_mean_{w}m')
                f[f'eta_delay_{w}m'] = (f['dist_to_target_m'] / (m / 3.6) - time_left_s) if m and m > 0.5 else np.nan
        sch_tr = sched_sorted[sched_sorted['tr_id'] == tr]
        f['n_stops_between'] = int(((sch_tr['time_begin'] > T) &
                                    (sch_tr['time_begin'] < r['target_time_begin'])).sum())
        rows.append(f)

    return pd.DataFrame(rows).set_index('sample_id')


if __name__ == '__main__':
    import lightgbm as lgb
    # пример инференса на validate
    points = pd.read_csv('../validate/points.csv')
    traffic = pd.read_csv('../validate/traffic.csv')
    schedule = pd.read_csv('../validate/schedule_plan.csv')
    feats = build_features(points, traffic, schedule)
    model = lgb.Booster(model_file='model.txt')
    preds = model.predict(feats[FEATURES])
    sub = pd.DataFrame({'sample_id': feats.index, 'prediction': preds.round(1)})
    sub.to_csv('submission.csv', sep=';', index=False)
    print(f'submission.csv: {len(sub)} строк')
