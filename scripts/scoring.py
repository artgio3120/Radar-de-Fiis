from __future__ import annotations

import math
from typing import Any


def clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def linear(value: float | None, lo: float, hi: float) -> float:
    if value is None or not math.isfinite(float(value)):
        return 50.0
    if hi == lo:
        return 50.0
    return clamp((float(value) - lo) / (hi - lo) * 100.0)


def log_score(value: float | None, lo: float, hi: float) -> float:
    if value is None or not math.isfinite(float(value)) or float(value) <= 0:
        return 0.0
    x = math.log10(float(value))
    return linear(x, math.log10(lo), math.log10(hi))


def classify_type(segment: str | None) -> str:
    s = (segment or "").lower()
    if any(k in s for k in ("título", "titulo", "valores mobili", "receb", "cri", "papel")):
        return "Papel"
    if any(k in s for k in ("multicategoria", "híbr", "hibr", "misto")):
        return "Híbrido"
    if any(k in s for k in ("logíst", "logist", "shopping", "laje", "escrit", "renda urbana", "varejo", "hospital", "hotel", "educacional", "residencial", "agência", "agencia")):
        return "Tijolo"
    return "Outros"


def valuation_score(pvp: float | None) -> float:
    if pvp is None or not math.isfinite(float(pvp)) or pvp <= 0:
        return 0.0
    p = float(pvp)
    if p < 0.55:
        return linear(p, 0.20, 0.55) * 0.55
    if p <= 0.85:
        return 100.0
    if p <= 1.05:
        return clamp(100 - (p - 0.85) / 0.20 * 30)
    return clamp(70 - (p - 1.05) / 0.35 * 70)


def income_score(dy: float | None) -> float:
    if dy is None or not math.isfinite(float(dy)):
        return 35.0
    d = float(dy)
    base = linear(d, 5.0, 14.0)
    if d > 22:
        base *= 0.60
    elif d > 18:
        base *= 0.82
    return clamp(base)


def operational_score(vacancy: float | None, fund_type: str) -> float:
    if fund_type == "Papel":
        return 70.0
    if vacancy is None or not math.isfinite(float(vacancy)):
        return 55.0
    return clamp(100 - float(vacancy) / 35.0 * 100)


WEIGHTS = {
    "Tijolo": {"valuation": .25, "income": .20, "liquidity": .15, "size": .15, "operational": .15, "growth": .10},
    "Papel": {"valuation": .20, "income": .30, "liquidity": .15, "size": .15, "operational": .05, "growth": .15},
    "Híbrido": {"valuation": .25, "income": .25, "liquidity": .15, "size": .15, "operational": .10, "growth": .10},
    "Outros": {"valuation": .25, "income": .25, "liquidity": .15, "size": .15, "operational": .10, "growth": .10},
}


def score_fund(fund: dict[str, Any]) -> tuple[float, dict[str, float]]:
    t = fund.get("type") or classify_type(fund.get("segment"))
    breakdown = {
        "valuation": valuation_score(fund.get("pvp")),
        "income": income_score(fund.get("dy")),
        "liquidity": log_score(fund.get("liquidity"), 200_000, 10_000_000),
        "size": log_score(fund.get("pl"), 200_000_000, 10_000_000_000),
        "operational": operational_score(fund.get("vacancy"), t),
        "growth": linear(fund.get("growth_12m"), -10.0, 20.0),
    }
    weights = WEIGHTS.get(t, WEIGHTS["Outros"])
    score = sum(breakdown[k] * weights[k] for k in weights)
    return round(clamp(score), 1), {k: round(v, 1) for k, v in breakdown.items()}


def classify_bucket(fund: dict[str, Any], score: float) -> str:
    flags = set(fund.get("flags") or [])
    pvp = fund.get("pvp")
    dy = fund.get("dy")
    if flags.intersection({"pvp_fora_da_faixa", "vp_cota_diverge_pl_cotas", "vacancia_extrema", "sem_preco"}) or (pvp is not None and pvp < .55) or (dy is not None and dy > 25):
        return "Risco"
    if score >= 88 and (fund.get("liquidity") or 0) >= 2_000_000 and (fund.get("pl") or 0) >= 1_000_000_000:
        return "Ancoragem"
    if score >= 75 and (fund.get("growth_12m") or 0) >= 10:
        return "Crescimento"
    return "Oportunidade"
