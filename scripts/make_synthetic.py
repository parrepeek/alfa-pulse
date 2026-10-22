"""Синтетические данные в формате кейса (без реальных клиентов): data/synthetic/*.csv

Используются для сквозной проверки построения профилей без исходных CSV:
python scripts/make_synthetic.py && python -c "from app import profiles; profiles.build('data/synthetic', 1e9, 'data/synthetic/profiles.json')"
"""
import csv
import random
from pathlib import Path

random.seed(42)
OUT = Path(__file__).resolve().parents[1] / "data" / "synthetic"
OUT.mkdir(parents=True, exist_ok=True)
OKVED = [("46.71", "Торговля оптовая твердым, жидким и газообразным топливом"), ("41.20", "Строительство жилых и нежилых зданий"),
         ("45.11", "Торговля легковыми автомобилями"), ("56.10", "Деятельность ресторанов"), ("46.21", "Торговля оптовая зерном"),
         ("62.01", "Разработка компьютерного программного обеспечения"), ("49.41", "Деятельность автомобильного грузового транспорта"),
         ("68.20", "Аренда и управление недвижимым имуществом"), ("24.10", "Производство чугуна, ферросплавов, стали")]
REGIONS = [("г. Москва", "Москва"), ("г. Иркутск", "Иркутская область"), ("г. Новосибирск", "Новосибирская область"), ("г. Казань", "Республика Татарстан")]
COUNTRIES = ["КИТАЙ", "ТУРЦИЯ", "ОАЭ", "КАЗАХСТАН", "ИНДИЯ"]
HEAD = ["SYNTHETIC_U", "ORG_TYPE", "OKFS_NAME", "COMPANY_AGE_YEARS_GRADATION", "ECO_MARK_LABEL", "MAINOKVED_CODE", "MAINOKVED_NAME", "NEGATIVEMARK",
        "AMOUNT_WORKERS_FNS_GRADATION", "AMOUNT_WORKERS_SPARK_GRADATION", "CL_TYPE", "CL_PRODUCT_RKO_MARK", "CL_PRODUCT_GAR_MARK", "CL_PRODUCT_LOA_MARK",
        "CL_PRODUCT_LEA_MARK", "CL_PRODUCT_DEP_MARK", "CL_PRODUCT_ZPP_MARK", "FTS_FLAG", "FTS_COUNTRY_NAME", "FACTORING_MARK", "REVENUE_COMPANY",
        "GROUP_ID", "CITY", "REGION"]
rows, ids = [], []
for g in range(40):
    main = random.choice(OKVED)
    for k in range(random.randint(1, 12)):
        uid = f"S_{g}_{k}"
        ids.append(uid)
        ok = main if k == 0 or random.random() < 0.6 else random.choice(OKVED)
        city, region = random.choice(REGIONS)
        fts = random.random() < 0.3
        rows.append([uid, "КОММ_ОРГ", "ЧАСТНАЯ СОБСТВЕННОСТЬ", random.choice(["0-3", "4-7", "8-15", "16-25"]), random.choice(["Оптимизация", "Рождение", ""]),
                     ok[0], ok[1], 0, random.choice(["Менее 15", "16-100", "101-250"]), "", random.choice(["CLIENT", "PROSPECT"]),
                     int(random.random() < 0.6), int(random.random() < 0.2), int(random.random() < 0.3), int(random.random() < 0.3),
                     int(random.random() < 0.2), int(random.random() < 0.4), int(fts), ", ".join(random.sample(COUNTRIES, 2)) if fts else "",
                     0, round(random.lognormvariate(20, 1.6)), f"group_syn_{g}", city, region])
with open(OUT / "df_Uid_data_hack.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(HEAD)
    w.writerows(rows)
with open(OUT / "df_Uid_payment_hack.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(["BENEFICIARY", "PAYER", "PAY_AMT", "PAY_CNT"])
    for _ in range(3000):
        a, b = random.sample(ids + [f"X_{i}" for i in range(100)], 2)
        w.writerow([a, b, round(random.lognormvariate(13, 2)), random.randint(1, 50)])
with open(OUT / "df_Fid_Uid_link_hack.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(["SYNTHETIC_F", "CITY_NAME", "RC_NAME", "SYNTHETIC_U"])
    for i in range(400):
        w.writerow([f"F_{i}", "Москва", "МОСКОВСКИЙ", random.choice(ids)])
print("synthetic:", len(rows), "companies ->", OUT)
