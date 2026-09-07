from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def main() -> int:
    path = DATA / "fii_ranking.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    funds = payload.get("funds") or []

    # O fallback oficial B3↔CVM pode encontrar mais de uma linha CVM para o
    # mesmo ISIN/ticker (classes/reapresentações). Para o ranking, ticker é a
    # chave de negociação: mantenha uma única observação, priorizando maior
    # data de relatório e depois maior score.
    chosen: dict[str, dict] = {}
    for fund in funds:
        ticker = str(fund.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        current = chosen.get(ticker)
        if current is None:
            chosen[ticker] = fund
            continue
        old_key = (str(current.get("report_date") or ""), float(current.get("score") or 0))
        new_key = (str(fund.get("report_date") or ""), float(fund.get("score") or 0))
        if new_key > old_key:
            chosen[ticker] = fund

    funds = sorted(chosen.values(), key=lambda x: float(x.get("score") or 0), reverse=True)
    coverage = dict((payload.get("meta") or {}).get("coverage") or {})
    coverage.update({
        "universe": len(funds),
        "with_price": sum(f.get("price") is not None for f in funds),
        "with_nav": sum(f.get("nav") is not None for f in funds),
        "with_dy_12m": sum(f.get("dy") is not None for f in funds),
        "with_liquidity": sum(f.get("liquidity") is not None for f in funds),
        "with_vacancy": sum(f.get("vacancy") is not None for f in funds),
    })
    payload["funds"] = funds
    payload.setdefault("meta", {})["coverage"] = coverage

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA / "fii_data.js").write_text(
        "window.FII_DATA = " + json.dumps(payload, ensure_ascii=False, indent=2) + ";\n",
        encoding="utf-8",
    )

    flat = []
    for fund in funds:
        row = {k: v for k, v in fund.items() if k not in ("breakdown", "flags")}
        row["flags"] = ",".join(fund.get("flags") or [])
        for key, value in (fund.get("breakdown") or {}).items():
            row[f"score_{key}"] = value
        flat.append(row)
    pd.DataFrame(flat).to_csv(DATA / "fii_ranking.csv", index=False, encoding="utf-8-sig")
    (DATA / "coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Deduplicação OK: {len(funds)} tickers únicos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
