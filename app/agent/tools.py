"""Инструменты агента. Только чтение; каждый вызов журналируется (кто, что, сколько времени)."""
import json
import time
from datetime import date

from .. import config, db, profiles, ranking
from ..research import index as research

FORECAST_FOR = {"ставка": "ключевая ставка", "курс": "курс рубля", "потребспрос": "оборот розничной торговли",
                "сырье": "инфляция", "налоги_регулирование": "ВВП", "стройка_ипотека": "ключевая ставка", "санкции_ВЭД": "курс рубля"}


class Toolbox:
    def __init__(self, trace_id: str, user_id: str, allowed_groups: list[str]):
        self.trace_id, self.user_id, self.allowed = trace_id, user_id, set(allowed_groups)
        self.calls = []

    def _run(self, name, args, fn):
        t0 = time.time()
        ok, res = True, None
        try:
            res = fn()
        except Exception as e:  # ошибка инструмента - часть трассы, агент решает, что делать дальше
            ok, res = False, {"error": str(e)}
        ms = round((time.time() - t0) * 1000, 1)
        db.log_tool(self.trace_id, self.user_id, name, args, ms, ok)
        self.calls.append({"tool": name, "args": args, "ok": ok, "ms": ms, "result_preview": json.dumps(res, ensure_ascii=False)[:300]})
        return res

    def _check_access(self, group_id):
        if group_id not in self.allowed:
            raise PermissionError(f"нет доступа к {group_id}")

    def get_client_profile(self, group_id):
        def f():
            self._check_access(group_id)
            return profiles.get(group_id)
        return self._run("get_client_profile", {"group_id": group_id}, f)

    def get_event(self, event_id):
        def f():
            r = db.conn().execute("SELECT data FROM events WHERE id=?", (event_id,)).fetchone()
            if not r:
                raise KeyError(event_id)
            ev = json.loads(r["data"])
            dup = db.conn().execute("SELECT source, title FROM news WHERE cluster_id=? AND id<>?", (ev["news_id"], ev["news_id"])).fetchall()
            ev["also_reported_by"] = [d["source"] for d in dup]
            return ev
        return self._run("get_event", {"event_id": event_id}, f)

    def get_counterparty_exposure(self, group_id, sectors=None):
        def f():
            self._check_access(group_id)
            p = profiles.get(group_id)
            sectors_ = sectors or []
            return {"suppliers_by_sector": {k: v for k, v in p["suppliers_by_sector"].items() if not sectors_ or k in sectors_},
                    "customers_by_sector": {k: v for k, v in p["customers_by_sector"].items() if not sectors_ or k in sectors_},
                    "top_supplier_share": p["top_supplier_share"], "top_customer_share": p["top_customer_share"],
                    "top_customers": p.get("top_customers", [])[:3], "top_suppliers": p.get("top_suppliers", [])[:3],
                    "outside_dataset": p["data_quality"]["counterparties_outside_dataset"]}
        return self._run("get_counterparty_exposure", {"group_id": group_id, "sectors": sectors}, f)

    def search_bank_research(self, query, factor=None, limit=4):
        return self._run("search_bank_research", {"query": query[:200], "factor": factor},
                         lambda: research.search(query, factor=factor, limit=limit))

    def get_bank_forecast(self, factor):
        def f():
            ind = FORECAST_FOR.get(factor)
            rows = research.forecasts(ind) if ind else []
            return rows
        return self._run("get_bank_forecast", {"factor": factor}, f)

    def find_related_events(self, event, group_profile, days=30):
        def f():
            rows = db.conn().execute("SELECT data FROM events WHERE id<>? AND published<=? AND published>=date(?, ?)",
                                     (event["id"], config.TODAY.isoformat(), event["date"], f"-{days} day")).fetchall()
            rel = []
            for r in rows:
                ev = json.loads(r["data"])
                common = set(ev.get("factors", {})) & set(event.get("factors", {}))
                if common:
                    sc = ranking.score(ev, group_profile)["score"]
                    rel.append({"id": ev["id"], "title": ev["title"], "date": ev["date"], "source": ev["source"], "common_factors": sorted(common),
                                "signs": {f: ev["factors"][f] for f in common},
                                "relevance": sc, "synthetic": ev.get("synthetic", False)})
            rel.sort(key=lambda x: -x["relevance"])
            return rel[:3]
        return self._run("find_related_events", {"event_id": event["id"], "days": days}, f)


def today_age(d: str) -> int:
    return (config.TODAY - date.fromisoformat(d)).days
