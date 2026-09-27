"""
Вариант пайплайна признаков БЕЗ геометрии (без гаверсинуса и координат).

Отличия от features.py:
- не используются lon/lat телеметрии и geom расписания;
- убраны: last_lon/last_lat, dist_10m_m (одометр по GPS),
  dist_to_target_m, req_speed_kmh, eta_delay_5m/10m;
- остаются: cur_dev_s, горизонт, время суток, скоростные агрегаты
  за 5/10 мин, доля стоянки, свежесть данных, плановая длительность
  сегмента, число промежуточных остановок.

CV MAE этого варианта: ~77.4 с (против ~72.8 с у полного набора) —
плата за отказ от гео ~5 секунд.

Совместим по интерфейсу с features.py: та же сигнатура
build_features(points, traffic, schedule), тот же формат вывода.
Чтобы обучить no-geo модель, в train_model.py замените импорт на:
    from features_nogeo import build_features, FEATURES
"""
import numpy as np
import pandas as pd

FEATURES = [
    'horizon_min', 'hour', 'minute', 'dow',
    't_since_last_s', 'last_speed',
    'speed_mean_5m', 'speed_max_5m', 'speed_std_5m', 'stop_share_5m',
    'speed_mean_10m', 'speed_max_10m', 'speed_std_10m', 'stop_share_10m',
    'plan_seg_s', 'n_stops_between', 'cur_dev_s',
]


def build_features(points, traffic, schedule):
    """
    Признаки для прогнозных точек без координат.
    Анти-утечка: только телеметрия с event_time <= T.
    Возвращает DataFrame с индексом sample_id и колонками FEATURES.
    """
    points = points.copy()
    traffic = traffic.copy()
    schedule = schedule.copy()
    points['T'] = pd.to_datetime(points['T'])
    points['target_time_begin'] = pd.to_datetime(points['target_time_begin'])
    traffic['event_time'] = pd.to_datetime(traffic['event_time'])
    schedule['time_begin'] = pd.to_datetime(schedule['time_begin'])

    # плановый порядок остановок и длительность сегментов (геом не нужен)
    sched_sorted = schedule.sort_values(['tr_id', 'time_begin']).reset_index(drop=True)
    sched_sorted['prev_time_begin'] = sched_sorted.groupby('tr_id')['time_begin'].shift(1)
    sched_sorted['plan_seg_s'] = (
        sched_sorted['time_begin'] - sched_sorted['prev_time_begin']).dt.total_seconds()
    sched_by_stop = sched_sorted.set_index('tt_action_item_id')

    # телеметрия: скорость не требует валидных координат,
    # но оставляем фильтр для единообразия с основным вариантом
    tele = traffic[traffic['location_valid']].sort_values(['tr_id', 'event_time'])
    tele_grp = dict(tuple(tele.groupby('tr_id')))

    rows = []
    for _, r in points.iterrows():
        tr, T = r['tr_id'], r['T']
        f = {'sample_id': r['sample_id'], 'cur_dev_s': r['cur_dev_s'],
             'horizon_min': (r['target_time_begin'] - T).total_seconds() / 60,
             'hour': T.hour, 'minute': T.minute, 'dow': T.dayofweek}

        # --- динамика ТС на момент T (только скорость, без координат) ---
        g = tele_grp.get(tr)
        if g is not None:
            hist = g[g['event_time'].values <= np.datetime64(T)]
            if len(hist):
                last = hist.iloc[-1]
                f['t_since_last_s'] = (T - last['event_time']).total_seconds()
                f['last_speed'] = last['speed']
                for w in (5, 10):
                    sp = hist[hist['event_time'] > T - pd.Timedelta(minutes=w)]['speed'].values
                    if len(sp):
                        f[f'speed_mean_{w}m'] = sp.mean()
                        f[f'speed_max_{w}m'] = sp.max()
                        f[f'speed_std_{w}m'] = sp.std() if len(sp) > 1 else 0.0
                        f[f'stop_share_{w}m'] = (sp < 3).mean()

        # --- план до целевой остановки ---
        f['plan_seg_s'] = sched_by_stop.loc[r['target_stop_id'], 'plan_seg_s']
        sch_tr = sched_sorted[sched_sorted['tr_id'] == tr]
        f['n_stops_between'] = int(((sch_tr['time_begin'] > T) &
                                    (sch_tr['time_begin'] < r['target_time_begin'])).sum())
        rows.append(f)

    return pd.DataFrame(rows).set_index('sample_id')


if __name__ == '__main__':
    import lightgbm as lgb
    # пример инференса на validate
    points = pd.read_csv('validate/points.csv')
    traffic = pd.read_csv('validate/traffic.csv')
    schedule = pd.read_csv('validate/schedule_plan.csv')
    feats = build_features(points, traffic, schedule)
    model = lgb.Booster(model_file='model.txt')
    preds = model.predict(feats[FEATURES])
    sub = pd.DataFrame({'sample_id': feats.index, 'prediction': preds.round(1)})
    sub.to_csv('submission.csv', sep=';', index=False)
    print(f'submission.csv: {len(sub)} строк')
