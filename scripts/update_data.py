from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.providers.b3_cotahist import load_history
from scripts.providers.b3_listed_funds import load_listed_funds
from scripts.providers.cvm_fii import load_monthly, load_quarterly_properties
from scripts.scoring import classify_bucket, classify_type, score_fund


def safe_float(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def pct_change(current, old):
    c, o = safe_float(current), safe_float(old)
    if c is None or o is None or o == 0:
        return None
    return (c / o - 1) * 100


def normalize_monthly_dy(value):
    x = safe_float(value)
    if x is None:
        return None
    if abs(x) <= 0.20:
        return x * 100.0
    return x


def _dedupe_monthly(monthly: pd.DataFrame) -> pd.DataFrame:
    if monthly.empty:
        return monthly
    m = monthly.copy()
    if "delivery_date" not in m:
        m["delivery_date"] = m.get("reference_date")
    m["delivery_date"] = m["delivery_date"].fillna(m["reference_date"])
    if "version" not in m:
        m["version"] = 0
    m["version_num"] = pd.to_numeric(m["version"], errors="coerce").fillna(0)
    key = "cnpj" if "cnpj" in m and m["cnpj"].astype(str).str.len().ge(8).any() else "isin"
    m = m.sort_values([key, "reference_date", "delivery_date", "version_num"])
    return m.groupby([key, "reference_date"], as_index=False).tail(1).reset_index(drop=True)


def latest_fundamentals(monthly: pd.DataFrame) -> pd.DataFrame:
    m = _dedupe_monthly(monthly)
    if m.empty:
        return m
    key = "cnpj" if "cnpj" in m and m["cnpj"].astype(str).str.len().ge(8).any() else "isin"
    return m.sort_values([key, "reference_date", "delivery_date"]).groupby(key, as_index=False).tail(1).reset_index(drop=True)


def nearest_prior_value(df: pd.DataFrame, target_date: pd.Timestamp, col: str):
    if col not in df:
        return None
    subset = df[df["reference_date"] <= target_date].sort_values("reference_date")
    if subset.empty:
        return None
    vals = subset[col].dropna()
    return vals.iloc[-1] if not vals.empty else None


def history_for_fund(monthly_clean: pd.DataFrame, row: pd.Series) -> pd.DataFrame:
    cnpj = str(row.get("cnpj") or "")
    isin = str(row.get("isin") or "")
    if cnpj and "cnpj" in monthly_clean:
        mh = monthly_clean[monthly_clean["cnpj"].astype(str) == cnpj]
        if not mh.empty:
            return mh.sort_values("reference_date")
    if isin and "isin" in monthly_clean:
        return monthly_clean[monthly_clean["isin"].astype(str) == isin].sort_values("reference_date")
    return pd.DataFrame()


def dy_12m_from_cvm(mh: pd.DataFrame) -> tuple[float | None, int, float | None]:
    if mh.empty or "dy_month" not in mh:
        return None, 0, None
    tmp = mh.dropna(subset=["reference_date"]).sort_values("reference_date").tail(12).copy()
    vals = [normalize_monthly_dy(x) for x in tmp["dy_month"].tolist()]
    vals = [x for x in vals if x is not None]
    if not vals:
        return None, 0, None
    return sum(vals), len(vals), (sum(vals[-6:]) / len(vals[-6:]) if vals else None)


def _safe_date_iso(value):
    try:
        if value is None or pd.isna(value):
            return None
        return pd.Timestamp(value).date().isoformat()
    except Exception:
        return None


def _universe_from_official_sources(b3: pd.DataFrame, latest: pd.DataFrame, listed: pd.DataFrame) -> pd.DataFrame:
    b3_latest = b3.sort_values("date").groupby("ticker", as_index=False).tail(1).copy()
    if listed is not None and not listed.empty:
        universe = listed.copy()
        universe = universe.merge(b3_latest, on="ticker", how="left")
        if "cnpj_b3_list" in universe and "cnpj" in latest:
            by_cnpj = latest.copy()
            by_cnpj["cnpj"] = by_cnpj["cnpj"].astype(str)
            universe = universe.merge(by_cnpj, left_on="cnpj_b3_list", right_on="cnpj", how="left", suffixes=("_b3", ""))
        if "isin" in latest and "isin_b3" in universe:
            missing = universe.get("cnpj", pd.Series(index=universe.index, dtype=object)).isna()
            if missing.any():
                by_isin = latest.add_suffix("_isinmatch")
                recovered = universe.loc[missing, ["ticker", "isin_b3"]].merge(by_isin, left_on="isin_b3", right_on="isin_isinmatch", how="left").set_index("ticker")
                for col in latest.columns:
                    src = f"{col}_isinmatch"
                    if src in recovered:
                        universe.loc[missing, col] = universe.loc[missing, "ticker"].map(recovered[src])
        return universe
    if "isin" not in latest:
        return pd.DataFrame()
    latest2 = latest[latest["isin"].astype(str).str.len() > 4].copy()
    return b3_latest.merge(latest2, on="isin", how="inner", suffixes=("_b3", ""))


def build_funds(b3: pd.DataFrame, monthly: pd.DataFrame, props: pd.DataFrame, listed: pd.DataFrame) -> tuple[list[dict], dict]:
    latest = latest_fundamentals(monthly)
    monthly_clean = _dedupe_monthly(monthly)
    if b3.empty or latest.empty:
        raise RuntimeError("B3 ou CVM sem dados suficientes para montar o universo.")
    universe = _universe_from_official_sources(b3, latest, listed)
    if universe.empty:
        raise RuntimeError("Nenhum FII foi identificado entre as fontes oficiais B3/CVM.")
    prop_by_cnpj = props.set_index("cnpj").to_dict("index") if not props.empty and "cnpj" in props else {}
    funds: list[dict] = []
    latest_market_date = pd.Timestamp(b3["date"].max())
    for _, row in universe.iterrows():
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        hist = b3[b3["ticker"] == ticker].sort_values("date").copy()
        close = safe_float(hist.iloc[-1]["close"]) if not hist.empty else None
        price_date = pd.Timestamp(hist.iloc[-1]["date"]) if not hist.empty else None
        nav = safe_float(row.get("nav"))
        pvp = close / nav if close is not None and nav is not None and nav > 0 else None
        liq = safe_float(hist.tail(30)["financial_volume"].mean()) if not hist.empty and "financial_volume" in hist else None
        high252 = safe_float(hist.tail(252)["high"].max()) if not hist.empty and "high" in hist else None
        low252 = safe_float(hist.tail(252)["low"].min()) if not hist.empty and "low" in hist else None
        change30 = None
        if len(hist) > 1:
            old = hist.iloc[max(0, len(hist) - 22)]["close"]
            change30 = pct_change(close, old)
        mh = history_for_fund(monthly_clean, row)
        dy, dy_months, dy_avg6 = dy_12m_from_cvm(mh)
        growth_pl = growth_holders = None
        if not mh.empty and pd.notna(row.get("reference_date")):
            target = pd.Timestamp(row["reference_date"]) - pd.DateOffset(months=12)
            old_pl = nearest_prior_value(mh, target + pd.DateOffset(days=20), "pl")
            old_holders = nearest_prior_value(mh, target + pd.DateOffset(days=20), "shareholders")
            growth_pl = pct_change(row.get("pl"), old_pl)
            growth_holders = pct_change(row.get("shareholders"), old_holders)
        growth_candidates = [x for x in (growth_pl, growth_holders) if x is not None]
        growth12 = sum(growth_candidates) / len(growth_candidates) if growth_candidates else None
        cnpj = str(row.get("cnpj") or row.get("cnpj_b3_list") or "")
        prop = prop_by_cnpj.get(cnpj, {})
        vacancy = safe_float(prop.get("vacancy"))
        if vacancy is not None and vacancy <= 1.0:
            vacancy *= 100.0
        properties = prop.get("properties")
        segment = str(row.get("segment") or "Não definido").strip()
        fund_type = classify_type(segment)
        pl = safe_float(row.get("pl"))
        shares = safe_float(row.get("shares"))
        shareholders = safe_float(row.get("shareholders"))
        name = str(row.get("name") or row.get("name_b3_list") or row.get("name_b3") or ticker).strip()
        isin = str(row.get("isin") or row.get("isin_b3") or "").strip()
        flags: list[str] = []
        if close is None: flags.append("sem_preco")
        if price_date is not None:
            stale_days = max(0, (latest_market_date.normalize() - price_date.normalize()).days)
            if stale_days > 7: flags.append("preco_defasado")
        else: stale_days = None
        if nav is None or nav <= 0: flags.append("vp_cota_nao_positivo")
        if pvp is not None and (pvp < .2 or pvp > 5): flags.append("pvp_fora_da_faixa")
        if dy is not None and dy > 30: flags.append("dy_extremo")
        if 0 < dy_months < 10: flags.append(f"dy_parcial_{dy_months}_meses")
        if vacancy is not None and vacancy >= 70: flags.append("vacancia_extrema")
        elif vacancy is not None and vacancy >= 45: flags.append("vacancia_suspeita")
        if pl and shares and nav:
            implied = pl / shares if shares else None
            if implied and abs(implied / nav - 1) > .01: flags.append("vp_cota_diverge_pl_cotas")
        fund = {"ticker": ticker,"name": name,"isin": isin,"cnpj": cnpj,"segment": segment,"type": fund_type,"price": close,"nav": nav,"dy": dy,"dy_months": dy_months,"dy_avg_month_6m": dy_avg6,"pvp": pvp,"liquidity": liq,"pl": pl,"vacancy": vacancy,"properties": properties,"shareholders": shareholders,"shares": shares,"admin_fee": safe_float(row.get("admin_fee")),"growth_12m": growth12,"growth_pl_12m": growth_pl,"growth_shareholders_12m": growth_holders,"change_30d": change30,"high_252d": high252,"low_252d": low252,"flags": flags,"report_date": _safe_date_iso(row.get("reference_date")),"price_date": _safe_date_iso(price_date),"price_stale_days": stale_days,"dividend_source": "CVM Informe Mensal — Percentual_Dividend_Yield_Mes"}
        score, breakdown = score_fund(fund)
        fund["score"] = score
        fund["breakdown"] = breakdown
        fund["classification"] = classify_bucket(fund, score)
        funds.append(fund)
    funds.sort(key=lambda x: (x.get("score") or 0), reverse=True)
    coverage = {"listed_b3": int(len(listed)) if listed is not None and not listed.empty else None,"universe": len(funds),"with_price": sum(f.get("price") is not None for f in funds),"with_nav": sum(f.get("nav") is not None for f in funds),"with_dy_12m": sum(f.get("dy") is not None for f in funds),"with_liquidity": sum(f.get("liquidity") is not None for f in funds),"with_vacancy": sum(f.get("vacancy") is not None for f in funds),"latest_market_date": _safe_date_iso(latest_market_date)}
    return funds, coverage


def write_outputs(funds: list[dict], coverage: dict, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="minutes")
    payload = {"meta": {"updated_at": now,"sources": {"b3": "B3 COTAHIST + lista oficial de FIIs","cvm": "CVM Informes Mensal/Trimestral","dividends": "CVM Informe Mensal"},"demo": False,"state": "live","method": "2em1 Quant v2.0","coverage": coverage,"notes": "Universo: lista oficial B3 (com fallback ISIN B3↔CVM). Preço/liquidez: B3. VP/PL/DY: CVM. Sem lista manual de tickers."},"funds": funds}
    (out_dir / "fii_ranking.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "fii_data.js").write_text("window.FII_DATA = " + json.dumps(payload, ensure_ascii=False, indent=2) + ";\n", encoding="utf-8")
    flat = []
    for f in funds:
        row = {k: v for k, v in f.items() if k not in ("breakdown", "flags")}
        row["flags"] = ",".join(f.get("flags") or [])
        for k, v in (f.get("breakdown") or {}).items(): row[f"score_{k}"] = v
        flat.append(row)
    pd.DataFrame(flat).to_csv(out_dir / "fii_ranking.csv", index=False, encoding="utf-8-sig")
    (out_dir / "coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Atualiza TODOS os FIIs do Radar Quantitativo.")
    parser.add_argument("--keep-existing-on-error", action="store_true")
    args = parser.parse_args()
    now = pd.Timestamp.today()
    years = sorted({int(now.year), int(now.year - 1)})
    try:
        print("[1/5] Baixando universo completo de FIIs listados na B3...")
        try:
            listed = load_listed_funds(ROOT / ".cache" / "b3" / "listed_funds.csv")
            print(f"      {len(listed):,} códigos no exportador oficial B3")
        except Exception as exc:
            listed = pd.DataFrame()
            print(f"      aviso: lista B3 indisponível ({exc}); usando fallback oficial por ISIN")
        print("[2/5] Baixando histórico oficial B3 (ano atual + anterior)...")
        b3 = load_history(years, ROOT / ".cache" / "b3")
        print(f"      {len(b3):,} registros de mercado FII")
        print("[3/5] Baixando informes mensais CVM (fundamentos + DY)...")
        monthly = load_monthly(years, ROOT / ".cache" / "cvm")
        print(f"      {len(monthly):,} registros CVM")
        print("[4/5] Buscando imóveis/vacância trimestral CVM...")
        props = load_quarterly_properties(years, ROOT / ".cache" / "cvm")
        print(f"      {len(props):,} fundos com bloco imobiliário")
        print("[5/5] Cruzando universo, calculando métricas e Score 2em1...")
        funds, coverage = build_funds(b3, monthly, props, listed)
        write_outputs(funds, coverage, ROOT / "data")
        print(f"OK: {len(funds)} FIIs publicados.")
        print("Cobertura:", json.dumps(coverage, ensure_ascii=False))
    except Exception as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        if args.keep_existing_on_error:
            print("Mantendo o último snapshot publicado; a próxima execução tentará novamente.")
            return 0
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
