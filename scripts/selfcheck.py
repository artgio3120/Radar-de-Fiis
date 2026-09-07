from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="data/fii_ranking.json")
    p.add_argument("--min-universe", type=int, default=100)
    args = p.parse_args()

    path = Path(args.data)
    if not path.exists():
        raise SystemExit(f"Arquivo não gerado: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    funds = payload.get("funds") or []
    if len(funds) < args.min_universe:
        raise SystemExit(f"Universo anormalmente pequeno: {len(funds)} FIIs")

    tickers = [str(f.get("ticker") or "") for f in funds]
    if len(tickers) != len(set(tickers)):
        raise SystemExit("Tickers duplicados no universo publicado")

    priced = [f for f in funds if f.get("price") is not None]
    navd = [f for f in funds if f.get("nav") is not None]
    if len(priced) < 50 or len(navd) < 50:
        raise SystemExit(f"Cobertura insuficiente: preço={len(priced)}, VP/cota={len(navd)}")

    for f in funds:
        for key in ("price", "nav", "dy", "pvp", "liquidity", "pl", "score"):
            v = f.get(key)
            if v is not None and (not isinstance(v, (int, float)) or not math.isfinite(v)):
                raise SystemExit(f"Valor inválido em {f.get('ticker')}:{key}={v!r}")
        if f.get("price") is not None and f["price"] <= 0:
            raise SystemExit(f"Preço não positivo em {f.get('ticker')}")
        if f.get("pvp") is not None and f.get("price") is not None and f.get("nav"):
            recomputed = f["price"] / f["nav"]
            if abs(recomputed - f["pvp"]) > 1e-9:
                raise SystemExit(f"P/VP não fecha em {f.get('ticker')}")

    print(f"Self-check OK: {len(funds)} FIIs; {len(priced)} com preço; {len(navd)} com VP/cota.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
