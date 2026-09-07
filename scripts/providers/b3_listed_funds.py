from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://sistemaswebb3-listados.b3.com.br/fundsProxy/fundsCall/GetListFundDownload/{payload}"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")


def _find_col(df: pd.DataFrame, exact: tuple[str, ...], contains: tuple[str, ...] = ()) -> str | None:
    cols = {_norm(c): c for c in df.columns}
    for name in exact:
        if _norm(name) in cols:
            return cols[_norm(name)]
    pieces = tuple(_norm(x) for x in contains)
    for n, original in cols.items():
        if pieces and all(p in n for p in pieces):
            return original
    return None


def _decode_csv(raw: bytes) -> pd.DataFrame:
    last_error: Exception | None = None
    for enc in ("utf-8-sig", "latin-1"):
        for sep in (";", ","):
            try:
                df = pd.read_csv(io.BytesIO(raw), sep=sep, encoding=enc, dtype=str, low_memory=False)
                if len(df.columns) >= 2:
                    return df
            except Exception as exc:
                last_error = exc
    raise RuntimeError(f"Não foi possível interpretar o CSV de FIIs listados da B3: {last_error}")


def _standardize(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["ticker", "name_b3_list", "cnpj_b3_list"])
    ticker_col = _find_col(df,("Codigo", "Código", "Ticker", "Codigo_Negociacao", "Código de Negociação", "Cod. Negociacao"),("codigo",))
    name_col = _find_col(df,("Nome do Fundo", "Nome_Fundo", "Razao Social", "Razão Social", "Fundo", "Denominacao Social"),("fundo",))
    cnpj_col = _find_col(df, ("CNPJ", "CNPJ Fundo", "CNPJ_Fundo"), ("cnpj",))
    rows: list[dict[str, str]] = []
    for _, row in df.iterrows():
        raw_ticker = str(row.get(ticker_col, "") if ticker_col else "").upper().strip()
        match = re.search(r"\b([A-Z]{4,7}\d{1,2})\b", raw_ticker)
        ticker = match.group(1) if match else ""
        if not ticker:
            continue
        name = str(row.get(name_col, "") if name_col else "").strip()
        cnpj = re.sub(r"\D", "", str(row.get(cnpj_col, "") if cnpj_col else ""))
        rows.append({"ticker": ticker, "name_b3_list": name, "cnpj_b3_list": cnpj})
    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=["ticker", "name_b3_list", "cnpj_b3_list"])
    return out.drop_duplicates("ticker", keep="last").sort_values("ticker").reset_index(drop=True)


def load_listed_funds(cache_path: str | Path = ".cache/b3/listed_funds.csv", timeout: int = 90) -> pd.DataFrame:
    """Download B3's complete FII list (typeFund=7)."""
    cache_path = Path(cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    request_obj = {"typeFund": 7, "pageNumber": 1, "pageSize": 20}
    payload = base64.b64encode(json.dumps(request_obj, separators=(",", ":")).encode()).decode()
    url = BASE_URL.format(payload=payload)
    headers = {"User-Agent": "Mozilla/5.0 FII-Radar/2.0","Accept": "text/csv,application/octet-stream,*/*","Referer": "https://sistemaswebb3-listados.b3.com.br/fundsPage/7"}
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        if len(response.content) < 100:
            raise RuntimeError("Resposta do exportador de FIIs da B3 veio vazia.")
        cache_path.write_bytes(response.content)
        return _standardize(_decode_csv(response.content))
    except Exception:
        if cache_path.exists():
            return _standardize(_decode_csv(cache_path.read_bytes()))
        raise
