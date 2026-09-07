from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests

MONTHLY_URL = "https://dados.cvm.gov.br/dados/FII/DOC/INF_MENSAL/DADOS/inf_mensal_fii_{year}.zip"
QUARTERLY_URL = "https://dados.cvm.gov.br/dados/FII/DOC/INF_TRIMESTRAL/DADOS/inf_trimestral_fii_{year}.zip"


def norm(value: str) -> str:
    s = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def find_col(df: pd.DataFrame, candidates: Iterable[str], contains: Iterable[str] = ()) -> str | None:
    normalized = {norm(c): c for c in df.columns}
    for cand in candidates:
        if norm(cand) in normalized:
            return normalized[norm(cand)]
    contains_n = [norm(x) for x in contains]
    for nc, original in normalized.items():
        if all(piece in nc for piece in contains_n):
            return original
    return None


def parse_br_number(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.strip().replace({"": None, "nan": None, "None": None, "-": None})
    comma_mask = s.str.contains(",", na=False)
    s.loc[comma_mask] = s.loc[comma_mask].str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce")


def _download(url: str, path: Path, immutable: bool, timeout: int = 90) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    if immutable and path.exists():
        return path.read_bytes()
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0 FII-Radar/1.0"}, timeout=timeout)
    response.raise_for_status()
    path.write_bytes(response.content)
    return response.content


def _read_members(payload: bytes) -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        for member in zf.namelist():
            if not member.lower().endswith(".csv"):
                continue
            raw = zf.read(member)
            df = None
            for enc in ("utf-8-sig", "latin-1"):
                try:
                    df = pd.read_csv(io.BytesIO(raw), sep=";", dtype=str, encoding=enc, low_memory=False)
                    break
                except UnicodeDecodeError:
                    continue
            if df is not None:
                result[member.lower()] = df
    return result


def _choose_table(tables: dict[str, pd.DataFrame], keywords: list[str], exclude: list[str] | None = None) -> pd.DataFrame:
    exclude = exclude or []
    for name, df in tables.items():
        n = norm(name)
        if all(k in n for k in keywords) and not any(x in n for x in exclude):
            return df.copy()
    return pd.DataFrame()


def _standardize_monthly_table(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    mapping: dict[str, str] = {}
    specs = {
        "cnpj": (["CNPJ_Fundo_Classe", "CNPJ_Fundo"], ["cnpj", "fundo"]),
        "name": (["Nome_Fundo_Classe", "Nome_Fundo", "Denominacao_Social"], ["nome", "fundo"]),
        "reference_date": (["Data_Referencia"], ["data", "referencia"]),
        "delivery_date": (["Data_Entrega"], ["data", "entrega"]),
        "version": (["Versao"], ["versao"]),
        "isin": (["Codigo_ISIN", "Cod_ISIN", "ISIN"], ["isin"]),
        "segment": (["Segmento_Atuacao", "Segmento"], ["segmento"]),
        "pl": (["Patrimonio_Liquido"], ["patrimonio", "liquido"]),
        "nav": (["Valor_Patrimonial_Cotas", "Valor_Patrimonial_Cota"], ["valor", "patrimonial", "cota"]),
        "shares": (["Num_Cotas", "Numero_Cotas", "Quantidade_Cotas", "Quantidade_de_Cotas"], ["cotas"]),
        "shareholders": (["Num_Cotistas", "Numero_Cotistas", "Quantidade_Cotistas", "Total_Numero_Cotistas"], ["cotistas"]),
        "dy_month": (["Percentual_Dividend_Yield_Mes", "Dividend_Yield_Mes"], ["dividend", "yield"]),
        "admin_fee": (["Percentual_Despesas_Taxa_Administracao"], ["taxa", "administracao"]),
    }
    for target, (cands, contains) in specs.items():
        col = find_col(df, cands, contains)
        if col:
            mapping[col] = target
    out = df.rename(columns=mapping)
    keep = [c for c in specs if c in out.columns]
    out = out[keep].copy()
    for c in ("reference_date", "delivery_date"):
        if c in out:
            out[c] = pd.to_datetime(out[c], errors="coerce", dayfirst=False)
    for c in ("pl", "nav", "shares", "shareholders", "dy_month", "admin_fee"):
        if c in out:
            out[c] = parse_br_number(out[c])
    if "isin" in out:
        out["isin"] = out["isin"].astype(str).str.strip().replace("nan", "")
    if "cnpj" in out:
        out["cnpj"] = out["cnpj"].astype(str).str.replace(r"\D", "", regex=True)
    return out


def _merge_monthly_tables(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    general = _choose_table(tables, ["inf_mensal_fii_geral"])
    if general.empty:
        general = _choose_table(tables, ["inf_mensal_fii"], exclude=["ativo", "passivo", "distrib", "complement", "tipo"])
    complement = _choose_table(tables, ["complement"])
    general = _standardize_monthly_table(general)
    complement = _standardize_monthly_table(complement)
    if general.empty and complement.empty:
        candidates = []
        for df in tables.values():
            std = _standardize_monthly_table(df)
            if not std.empty and ("pl" in std.columns or "nav" in std.columns or "isin" in std.columns):
                candidates.append(std)
        return pd.concat(candidates, ignore_index=True, sort=False) if candidates else pd.DataFrame()
    if general.empty:
        return complement
    if complement.empty:
        return general
    keys = [k for k in ("cnpj", "reference_date", "version") if k in general.columns and k in complement.columns]
    if len(keys) < 2:
        keys = [k for k in ("cnpj", "reference_date") if k in general.columns and k in complement.columns]
    if not keys:
        return pd.concat([general, complement], ignore_index=True, sort=False)
    return general.merge(complement, on=keys, how="outer", suffixes=("", "_comp"))


def load_monthly(years: Iterable[int], cache_dir: str | Path = ".cache/cvm") -> pd.DataFrame:
    cache_dir = Path(cache_dir)
    frames = []
    current_year = pd.Timestamp.today().year
    for year in years:
        url = MONTHLY_URL.format(year=int(year))
        payload = _download(url, cache_dir / f"inf_mensal_fii_{year}.zip", int(year) < current_year)
        tables = _read_members(payload)
        merged = _merge_monthly_tables(tables)
        if not merged.empty:
            merged["source_year"] = int(year)
            frames.append(merged)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True, sort=False)
    for base in ("isin", "name", "segment", "pl", "nav", "shares", "shareholders", "dy_month", "admin_fee", "delivery_date"):
        comp = f"{base}_comp"
        if comp in out.columns:
            if base not in out.columns:
                out[base] = out[comp]
            else:
                out[base] = out[base].where(out[base].notna() & (out[base].astype(str) != ""), out[comp])
    if "reference_date" in out:
        out = out[out["reference_date"].notna()]
    return out.reset_index(drop=True)


def _standardize_property_table(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    specs = {
        "cnpj": (["CNPJ_Fundo_Classe", "CNPJ_Fundo"], ["cnpj", "fundo"]),
        "reference_date": (["Data_Referencia"], ["data", "referencia"]),
        "delivery_date": (["Data_Entrega"], ["data", "entrega"]),
        "property": (["Nome_Imovel", "Nome_do_Imovel", "Empreendimento"], ["imovel"]),
        "area": (["Area", "Area_m2", "Area_M2", "Area_Total"], ["area"]),
        "vacancy": (["Percentual_Vacancia", "Vacancia"], ["vacancia"]),
    }
    mapping = {}
    for target, (cands, contains) in specs.items():
        col = find_col(df, cands, contains)
        if col:
            mapping[col] = target
    out = df.rename(columns=mapping)
    keep = [x for x in specs if x in out.columns]
    out = out[keep].copy()
    if "cnpj" in out:
        out["cnpj"] = out["cnpj"].astype(str).str.replace(r"\D", "", regex=True)
    for c in ("reference_date", "delivery_date"):
        if c in out:
            out[c] = pd.to_datetime(out[c], errors="coerce")
    for c in ("area", "vacancy"):
        if c in out:
            out[c] = parse_br_number(out[c])
    return out


def load_quarterly_properties(years: Iterable[int], cache_dir: str | Path = ".cache/cvm") -> pd.DataFrame:
    cache_dir = Path(cache_dir)
    frames = []
    current_year = pd.Timestamp.today().year
    for year in years:
        try:
            payload = _download(QUARTERLY_URL.format(year=int(year)), cache_dir / f"inf_trimestral_fii_{year}.zip", int(year) < current_year)
        except requests.RequestException:
            continue
        tables = _read_members(payload)
        prop = _choose_table(tables, ["imovel"])
        prop = _standardize_property_table(prop)
        if not prop.empty:
            frames.append(prop)
    if not frames:
        return pd.DataFrame(columns=["cnpj", "vacancy", "properties", "vacancy_date"])
    df = pd.concat(frames, ignore_index=True, sort=False)
    if "reference_date" not in df or "cnpj" not in df:
        return pd.DataFrame(columns=["cnpj", "vacancy", "properties", "vacancy_date"])
    df = df[df["reference_date"].notna() & df["cnpj"].notna()].copy()
    latest_date = df.groupby("cnpj")["reference_date"].transform("max")
    latest = df[df["reference_date"] == latest_date].copy()
    def agg(group: pd.DataFrame) -> pd.Series:
        vacancy = None
        if "vacancy" in group:
            valid = group["vacancy"].notna()
            if valid.any():
                if "area" in group and (group.loc[valid, "area"].fillna(0) > 0).any():
                    w = group.loc[valid, "area"].fillna(0)
                    vacancy = (group.loc[valid, "vacancy"] * w).sum() / w.sum() if w.sum() else group.loc[valid, "vacancy"].mean()
                else:
                    vacancy = group.loc[valid, "vacancy"].mean()
        properties = None
        if "property" in group:
            properties = int(group["property"].dropna().astype(str).str.strip().replace("", pd.NA).dropna().nunique())
        return pd.Series({"vacancy": vacancy, "properties": properties, "vacancy_date": group["reference_date"].max()})
    return latest.groupby("cnpj", as_index=False).apply(agg, include_groups=False).reset_index(drop=True)
