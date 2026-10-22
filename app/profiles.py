"""Профили групп компаний и карта чувствительности к макрофакторам (без LLM).

Данные кейса -> чистка шума -> профиль группы (отрасль, масштаб, продукты банка, ВЭД,
контрагенты по графу платежей) -> оценки чувствительности к 7 факторам с объяснениями.
Запуск: python -m app.cli build-profiles
"""
import json
from pathlib import Path

import pandas as pd

from . import config, db

BIG = 15e9  # порог крупного бизнеса по кейсу, руб.

# Укрупненные отрасли по первым двум цифрам ОКВЭД
SECTOR = {
    **{f"{i:02d}": "Сельское хозяйство" for i in (1, 2, 3)},
    **{f"{i:02d}": "Добыча" for i in range(5, 10)},
    **{f"{i:02d}": "Пищевая промышленность" for i in (10, 11, 12)},
    **{f"{i:02d}": "Легкая и деревообработка" for i in (13, 14, 15, 16, 17, 18)},
    "19": "Нефтепереработка", "20": "Химия", "21": "Фармацевтика", "22": "Химия",
    "23": "Стройматериалы", "24": "Металлургия", "25": "Металлообработка",
    **{f"{i:02d}": "Машиностроение и электроника" for i in range(26, 34)},
    "35": "Энергетика", **{f"{i:02d}": "ЖКХ и экология" for i in (36, 37, 38, 39)},
    "41": "Строительство", "42": "Инфраструктурное строительство", "43": "Строительство",
    "45": "Автоторговля", "46": "Оптовая торговля", "47": "Розничная торговля",
    **{f"{i:02d}": "Транспорт и логистика" for i in (49, 50, 51, 52, 53)},
    "55": "HoReCa", "56": "HoReCa", **{f"{i:02d}": "ИТ и связь" for i in (58, 59, 60, 61, 62, 63)},
    **{f"{i:02d}": "Финансы" for i in (64, 65, 66)}, "68": "Недвижимость",
    **{f"{i:02d}": "Профуслуги" for i in (69, 70, 71, 72, 73, 74, 75)},
    **{f"{i:02d}": "Аренда и услуги бизнесу" for i in (77, 78, 79, 80, 81, 82)},
    "85": "Образование", "86": "Здравоохранение", "87": "Здравоохранение", "88": "Здравоохранение",
}
# Тонкие подотрасли оптовой торговли, важные для сырьевых новостей
COMMODITY = {"46.71": "топливо", "46.72": "металлы", "46.21": "зерно и корма", "46.3": "продукты питания",
             "46.73": "стройматериалы и лес", "46.75": "химия", "46.4": "потребтовары", "46.69": "оборудование"}
CAPEX_HEAVY = {"Строительство", "Инфраструктурное строительство", "Недвижимость", "Энергетика", "Металлургия",
               "Добыча", "Транспорт и логистика", "Нефтепереработка"}
CONSUMER = {"Розничная торговля", "HoReCa", "Автоторговля", "Пищевая промышленность", "Здравоохранение"}


def okved_norm(code):
    s = str(code).strip()
    parts = s.split(".")
    if len(parts) > 1 and len(parts[1]) == 1:  # 41.2 -> 41.20 (разный формат в данных)
        parts[1] += "0"
    return ".".join(parts)


def commodity_of(code):
    for pref, name in COMMODITY.items():
        if code.startswith(pref):
            return name
    if code[:2] in ("24",):
        return "металлы"
    if code[:2] in ("19", "06", "09"):
        return "топливо"
    if code[:2] == "01":
        return "зерно и корма"
    return None


def load(raw: Path):
    u = pd.read_csv(raw / "df_Uid_data_hack.csv", sep=";", low_memory=False)
    f = pd.read_csv(raw / "df_Fid_Uid_link_hack.csv", sep=None, engine="python")
    p = pd.read_csv(raw / "df_Uid_payment_hack.csv", sep=None, engine="python")
    return u, f, p


def clean_companies(u):
    q = {}
    dup = u.SYNTHETIC_U.duplicated(keep=False)
    q["duplicate_id_rows"] = int(dup.sum())
    u = u.assign(DQ_DUP_ID=dup)
    u = u.sort_values("REVENUE_COMPANY", ascending=False).drop_duplicates("SYNTHETIC_U")  # оставляем строку с max выручкой
    u["OKVED"] = u.MAINOKVED_CODE.map(okved_norm)
    u["OKVED2"] = u.OKVED.str[:2]
    u["SECTOR"] = u.OKVED2.map(SECTOR).fillna("Прочее")
    u["COMMODITY"] = u.OKVED.map(commodity_of)
    q["revenue_below_100k"] = int((u.REVENUE_COMPANY < 1e5).sum())
    q["fts_country_missing_share"] = round(float(u.FTS_COUNTRY_NAME.isna().mean()), 3)
    return u, q


def clean_payments(p, comp, q):
    q["self_payments"] = int((p.PAYER == p.BENEFICIARY).sum())
    p = p[p.PAYER != p.BENEFICIARY].copy()
    cap = p.PAY_AMT.quantile(0.999)
    q["payments_winsorized_at"] = float(cap)
    q["payments_winsorized"] = int((p.PAY_AMT > cap).sum())
    p["AMT"] = p.PAY_AMT.clip(upper=cap)
    grp = comp.set_index("SYNTHETIC_U").GROUP_ID
    p["G_PAY"], p["G_BEN"] = p.PAYER.map(grp), p.BENEFICIARY.map(grp)
    intra = p.G_PAY.notna() & (p.G_PAY == p.G_BEN)
    q["intra_group_payments"] = int(intra.sum())
    return p[~intra]


def shares(series, top=5):
    s = series.groupby(level=0).sum().sort_values(ascending=False)
    tot = s.sum()
    return {k: round(float(v / tot), 3) for k, v in s.head(top).items()} if tot > 0 else {}


def build(raw: Path | None = None, threshold: float = BIG, out_json: Path | None = None):
    u, f, p = load(Path(raw or config.RAW_DIR))
    comp, q = clean_companies(u)
    pay = clean_payments(p, comp, q)
    sec = comp.set_index("SYNTHETIC_U").SECTOR

    g = comp.groupby("GROUP_ID")
    big = g.REVENUE_COMPANY.sum()
    big_ids = big[big > threshold].index
    persons = f.groupby("SYNTHETIC_U").SYNTHETIC_F.nunique()

    profiles = []
    for gid in big_ids:
        m = comp[comp.GROUP_ID == gid]
        ids = set(m.SYNTHETIC_U)
        rev = m.REVENUE_COMPANY.sum()
        sect = m.groupby("SECTOR").REVENUE_COMPANY.sum().sort_values(ascending=False)
        lead = m.iloc[0]
        out = pay[pay.PAYER.isin(ids)]  # платим поставщикам
        inc = pay[pay.BENEFICIARY.isin(ids)]  # получаем от покупателей
        sup = out.set_index(out.BENEFICIARY.map(sec).fillna("Вне справочника")).AMT
        cus = inc.set_index(inc.PAYER.map(sec).fillna("Вне справочника")).AMT
        top_sup = out.groupby("BENEFICIARY").AMT.sum()
        top_cus = inc.groupby("PAYER").AMT.sum()
        w = m.REVENUE_COMPANY / rev if rev > 0 else m.REVENUE_COMPANY * 0  # вес компании в группе по выручке
        cw = {}
        for c_str, wi in zip(m.FTS_COUNTRY_NAME, w):
            if isinstance(c_str, str):
                for c in c_str.split(","):
                    cw[c.strip()] = cw.get(c.strip(), 0) + wi
        countries = [c for c, _ in sorted(cw.items(), key=lambda x: -x[1])]
        com = m.groupby("COMMODITY").REVENUE_COMPANY.sum() / rev  # своя выручка в сырьевых ОКВЭД
        sup_com = out.assign(C=out.BENEFICIARY.map(comp.set_index("SYNTHETIC_U").COMMODITY)).groupby("C").AMT.sum()
        sup_com = sup_com / out.AMT.sum() if len(out) else sup_com
        commodities = {k: round(float(com.get(k, 0) + 0.5 * sup_com.get(k, 0)), 3) for k in set(com.index) | set(sup_com.index)}
        commodities = dict(sorted(((k, v) for k, v in commodities.items() if v >= 0.02), key=lambda x: -x[1]))
        flags = {k: round(float((w * (m[c] == 1)).sum()), 2) for k, c in [("кредиты", "CL_PRODUCT_LOA_MARK"), ("гарантии", "CL_PRODUCT_GAR_MARK"),
                 ("лизинг", "CL_PRODUCT_LEA_MARK"), ("депозиты", "CL_PRODUCT_DEP_MARK"), ("зарплатный_проект", "CL_PRODUCT_ZPP_MARK"),
                 ("РКО", "CL_PRODUCT_RKO_MARK"), ("факторинг", "FACTORING_MARK"), ("ВЭД_ФТС", "FTS_FLAG")]}
        sector_share = {k: round(float(v / rev), 3) for k, v in sect.head(4).items()} if rev > 0 else {}
        main_sector = sect.index[0]
        prof = {
            "group_id": gid, "companies": int(len(m)), "revenue_bn_rub": round(rev / 1e9, 1),
            "lead_company": lead.SYNTHETIC_U, "lead_okved": f"{lead.OKVED} {lead.MAINOKVED_NAME}",
            "main_sector": main_sector, "sector_mix": sector_share,
            "regions": m.REGION.value_counts().head(3).to_dict(),
            "client_type": m.groupby("CL_TYPE").REVENUE_COMPANY.sum().idxmax(), "products": flags,
            "foreign_ownership": round(float((w * m.OKFS_NAME.str.contains("ИНОСТРАН")).sum()), 2),
            "trade_countries": countries[:8], "trade_countries_weight": {c: round(float(cw[c]), 2) for c in countries[:8]}, "commodities": commodities,
            "stage": m.ECO_MARK_LABEL.value_counts().index[0] if m.ECO_MARK_LABEL.notna().any() else None,
            "suppliers_by_sector": shares(sup), "customers_by_sector": shares(cus),
            "top_supplier_share": round(float(top_sup.max() / top_sup.sum()), 3) if len(top_sup) else None,
            "top_customer_share": round(float(top_cus.max() / top_cus.sum()), 3) if len(top_cus) else None,
            "linked_persons": int(persons.reindex(list(ids)).fillna(0).sum()),
            "top_companies": [{"id": r.SYNTHETIC_U, "okved": f"{r.OKVED} {r.MAINOKVED_NAME}", "region": r.REGION,
                               "revenue_share": round(float(r.REVENUE_COMPANY / rev), 3) if rev else 0,
                               "credit": bool(r.CL_PRODUCT_LOA_MARK == 1), "leasing": bool(r.CL_PRODUCT_LEA_MARK == 1), "fts": bool(r.FTS_FLAG == 1)}
                              for r in m.head(5).itertuples()],
            "top_suppliers": [{"id": k, "sector": sec.get(k, "Вне справочника"), "share": round(float(v / top_sup.sum()), 3)}
                              for k, v in top_sup.sort_values(ascending=False).head(5).items()] if len(top_sup) else [],
            "top_customers": [{"id": k, "sector": sec.get(k, "Вне справочника"), "share": round(float(v / top_cus.sum()), 3)}
                              for k, v in top_cus.sort_values(ascending=False).head(5).items()] if len(top_cus) else [],
            "data_quality": {"dup_ids": int(m.DQ_DUP_ID.sum()), "payments_out": int(len(out)), "payments_in": int(len(inc)),
                             "counterparties_outside_dataset": round(float((sup.index == "Вне справочника").mean()), 2) if len(sup) else None},
        }
        prof["exposure"] = exposures(prof)
        profiles.append(prof)

    profiles.sort(key=lambda x: -x["revenue_bn_rub"])
    out_json = Path(out_json or config.PROFILES_JSON)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(profiles, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_json.parent / "data_quality.json").write_text(json.dumps(q, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"groups > {threshold / 1e9:g} bn: {len(profiles)}; data quality: {q}")
    return profiles


def load_to_db(path: Path | None = None):
    """Загрузка готовых профилей (JSON) в БД сервиса."""
    profiles = json.loads(Path(path or config.PROFILES_JSON).read_text(encoding="utf-8"))
    c = db.conn()
    c.execute("DELETE FROM profiles")
    c.executemany("INSERT INTO profiles VALUES(?,?,?,?)",
                  [(p["group_id"], p["main_sector"], p["revenue_bn_rub"], db.jdump(p)) for p in profiles])
    c.commit()
    return len(profiles)


def get(group_id: str) -> dict | None:
    r = db.conn().execute("SELECT data FROM profiles WHERE group_id=?", (group_id,)).fetchone()
    if not r:
        return None
    prof = json.loads(r["data"])
    for row in db.conn().execute("SELECT key, value FROM profile_overrides WHERE group_id=?", (group_id,)):
        prof.setdefault("client_corrections", {})[row["key"]] = row["value"]  # уточнения от клиента
    corr = {k.lower(): v.lower() for k, v in prof.get("client_corrections", {}).items()}
    rules = [("плавающая ставка", "нет", "ставка", "по данным клиента ставки зафиксированы"),
             ("хеджирование валюты", "есть", "курс", "по данным клиента валютный риск захеджирован")]
    for key, val, factor, note in rules:  # уточнение клиента меняет чувствительность и объяснение
        if corr.get(key) == val and factor in prof["exposure"]:
            prof["exposure"][factor]["score"] = round(prof["exposure"][factor]["score"] * 0.4, 2)
            prof["exposure"][factor]["because"] = [note] + prof["exposure"][factor]["because"]
    return prof


def _countries(pr, n=4):
    cw = pr.get("trade_countries_weight", {})
    return ", ".join(f"{c.title()} {cw.get(c, 0):.0%}" for c in pr["trade_countries"][:n])


def exposures(pr):
    """Чувствительность группы к макрофакторам, 0..1. Каждая оценка объяснима: список причин."""
    e, why = {}, {}
    sect_mix, prod = pr["sector_mix"], pr["products"]  # продукты: доля выручки группы у компаний с продуктом
    capex = sum(v for k, v in sect_mix.items() if k in CAPEX_HEAVY)
    e["ставка"] = 0.35 * prod["кредиты"] + 0.2 * prod["лизинг"] + 0.15 * prod["гарантии"] + 0.3 * capex
    why["ставка"] = [f"кредиты у {prod['кредиты']:.0%} выручки группы" if prod["кредиты"] > 0.05 else "",
                     f"лизинг у {prod['лизинг']:.0%}" if prod["лизинг"] > 0.05 else "",
                     f"гарантии у {prod['гарантии']:.0%}" if prod["гарантии"] > 0.05 else "",
                     f"капиталоемкие отрасли {capex:.0%} выручки" if capex > 0.1 else ""]
    trade = prod["ВЭД_ФТС"]
    e["курс"] = 0.7 * trade + 0.3 * pr["foreign_ownership"] + (0.15 if pr["main_sector"] in ("Оптовая торговля", "Автоторговля") and trade > 0.1 else 0)
    why["курс"] = [f"ВЭД (ФТС) у {trade:.0%} выручки" if trade > 0.05 else "",
                   ("основные страны: " + _countries(pr)) if pr["trade_countries"] else "",
                   f"иностранный капитал {pr['foreign_ownership']:.0%}" if pr["foreign_ownership"] > 0.05 else ""]
    cons = sum(v for k, v in sect_mix.items() if k in CONSUMER) + 0.5 * pr["customers_by_sector"].get("Розничная торговля", 0)
    e["потребспрос"] = cons + 0.1 * prod["зарплатный_проект"]
    why["потребспрос"] = [f"потребительские отрасли {cons:.0%}" if cons > 0.05 else ""]
    e["сырье"] = sum(pr["commodities"].values())
    why["сырье"] = [f"{k} {v:.0%}" for k, v in list(pr["commodities"].items())[:4]]
    build = sum(v for k, v in sect_mix.items() if k in ("Строительство", "Недвижимость", "Стройматериалы", "Инфраструктурное строительство"))
    e["стройка_ипотека"] = build + 0.5 * pr["suppliers_by_sector"].get("Строительство", 0)
    why["стройка_ипотека"] = [f"стройка и недвижимость {build:.0%} выручки" if build > 0.05 else ""]
    e["санкции_ВЭД"] = trade * (1 + 0.1 * min(len(pr["trade_countries"]), 5))
    why["санкции_ВЭД"] = [f"ВЭД (ФТС) у {trade:.0%} выручки", "страны: " + _countries(pr)] if trade > 0.05 else []
    e["налоги_регулирование"] = 0.35 + 0.15 * min(1, pr["revenue_bn_rub"] / 100)  # налог на прибыль касается всех, крупнее - сильнее
    why["налоги_регулирование"] = [f"выручка группы {pr['revenue_bn_rub']} млрд руб.", f"{pr['companies']} компаний в группе"]
    e["концентрация_контрагентов"] = max(pr["top_supplier_share"] or 0, pr["top_customer_share"] or 0)
    why["концентрация_контрагентов"] = [f"крупнейший поставщик {pr['top_supplier_share']:.0%}" if pr["top_supplier_share"] else "",
                                        f"крупнейший покупатель {pr['top_customer_share']:.0%}" if pr["top_customer_share"] else ""]
    return {k: {"score": round(float(min(1, v)), 2), "because": [w for w in why[k] if w]} for k, v in e.items()}


if __name__ == "__main__":
    build()
    load_to_db()
