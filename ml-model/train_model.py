"""
Обучение и оценка модели предиктора задержек.

Схема данных хакатона:
    train/traffic.csv, train/schedule.csv   обучающий период (телеметрия + расписание с фактом)
    labels/labels_train.csv                 разметка по train
    test/traffic.csv,  test/schedule.csv    тестовый период
    labels/labels_test.csv                  разметка по test (локальная проверка!)
    validate/                               настоящий сабмит, разметки нет и не будет

Что делает скрипт:
1. Строит признаки из train и обучает модель ТОЛЬКО на train.
2. Строит признаки из test (тем же кодом) и честно оценивает MAE
   на labels_test — данные, которые модель никогда не видела.
3. Сравнивает с baseline «прогноз = cur_dev_s».
4. Сохраняет model.txt
"""
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error

#from features_gav import build_features, FEATURES
from features_base import build_features, FEATURES

TRAIN_TRAFFIC  = 'train/traffic.csv'
TRAIN_SCHEDULE = 'train/schedule.csv'
TRAIN_LABELS   = 'labels/labels_train.csv'

TEST_TRAFFIC   = 'test/traffic.csv'
TEST_SCHEDULE  = 'test/schedule.csv'
TEST_LABELS    = 'labels/labels_test.csv'

MODEL_OUT = 'model.txt'
TARGET    = 'target_delay_s'

LGBM_PARAMS = dict(
    objective='l1', # оптимизация MAE
    learning_rate=0.05,
    num_leaves=63,
    min_data_in_leaf=20,
    feature_fraction=0.9,
    bagging_fraction=0.9,
    bagging_freq=1,
    verbose=-1,
    seed=42,
)

NUM_BOOST_ROUND = 200 # зафиксировано по временной CV на train
EARLY_STOP      = 200 # используется, если TRAIN_FRACTION < 1
TRAIN_FRACTION  = 0.8 # доля train для early stopping; 1.0 = без него


# ---------------------------------------------------------------------------
def load_dataset(traffic_path, schedule_path, labels_path):
    """Загружает один период и возвращает (df с признаками и таргетом)."""
    labels   = pd.read_csv(labels_path)
    traffic  = pd.read_csv(traffic_path, low_memory=False)
    schedule = pd.read_csv(schedule_path)

    feats = build_features(labels, traffic, schedule)
    df = (labels.set_index('sample_id')
                .join(feats.drop(columns=['cur_dev_s']))
                .sort_values('T').reset_index())
    df['T'] = pd.to_datetime(df['T'])
    return df


def evaluate(model, df):
    """MAE модели и baseline на готовом df."""
    pred = model.predict(df[FEATURES])
    mae_model = mean_absolute_error(df[TARGET], pred)
    mae_base  = mean_absolute_error(df[TARGET], df['cur_dev_s'])
    return mae_model, mae_base

# ---------------------------------------------------------------------------
def main():
    print('1/4 train: загрузка и признаки...')
    df_train = load_dataset(TRAIN_TRAFFIC, TRAIN_SCHEDULE, TRAIN_LABELS)
    print(f'    {len(df_train)} точек, {len(FEATURES)} признаков')

    print('2/4 обучение на train...')
    if TRAIN_FRACTION < 1.0:
        # шаг 1: часть train оставляем для early stopping (по времени, не вперёд)
        cut = df_train['T'].quantile(TRAIN_FRACTION)
        m_tr = df_train['T'] <= cut
        dtr = lgb.Dataset(df_train.loc[m_tr, FEATURES], df_train.loc[m_tr, TARGET])
        dva = lgb.Dataset(df_train.loc[~m_tr, FEATURES], df_train.loc[~m_tr, TARGET])
        probe = lgb.train(LGBM_PARAMS, dtr, num_boost_round=3000, valid_sets=[dva],
                          callbacks=[lgb.early_stopping(EARLY_STOP, verbose=False)])
        # шаг 2: дообучаем на ВСЁМ train с найденным числом деревьев (+20% запаса)
        n_trees = max(int(probe.best_iteration * 1.2), 50)
        model = lgb.train(LGBM_PARAMS,
                          lgb.Dataset(df_train[FEATURES], df_train[TARGET]),
                          num_boost_round=n_trees)
        print(f'    early stopping нашёл {probe.best_iteration} деревьев, '
              f'дообучено на всём train: {n_trees} деревьев')
    else:
        model = lgb.train(LGBM_PARAMS,
                          lgb.Dataset(df_train[FEATURES], df_train[TARGET]),
                          num_boost_round=NUM_BOOST_ROUND)
        n_trees = NUM_BOOST_ROUND
        print(f'    обучено на всех данных: {n_trees} деревьев')

    mae_tr, mae_tr_base = evaluate(model, df_train)
    print(f'    train MAE (in-sample): {mae_tr:.2f} с | baseline {mae_tr_base:.2f} с')

    print('3/4 test: честная оценка на невиданных данных...')
    df_test = load_dataset(TEST_TRAFFIC, TEST_SCHEDULE, TEST_LABELS)
    mae_te, mae_te_base = evaluate(model, df_test)
    improvement = (1 - mae_te / mae_te_base) * 100
    print(f'    {len(df_test)} точек')
    print(f'    TEST MAE: {mae_te:.2f} с | baseline cur_dev_s: {mae_te_base:.2f} с')
    print(f'    улучшение к baseline: {improvement:.1f}%')

    print('4/4 сохранение...')
    model.save_model(MODEL_OUT)
    print(f'готово: {MODEL_OUT}')


if __name__ == '__main__':
    main()
