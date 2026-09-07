from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests

B3_URL = "https://bvmf.bmfbovespa.com.br/InstDados/SerHist/COTAHIST_A{year}.ZIP"


def _slice(line: str, a: int, b: int) -> str:
    return line[a:b].strip()


def parse_cotahist_text(text: str) -> pd.DataFrame:
    """Parse the official fixed-width B3 COTAHIST file.

    Only regular cash-market FII rows are retained (BDI 12, market type 010).
    Prices/financial volume are stored by B3 with two implied decimal places.
    """
    rows: list[dict] = []
    for line in text.splitlines():
        if len(line) < 245 or _slice(line, 0, 2) != "01":
            continue
        bdi = _slice(line, 10, 12)
        market = _slice(line, 24, 27)
        if bdi != "12" or market != "010":
            continue
        try:
            rows.append({"date": pd.to_datetime(_slice(line, 2, 10), format="%Y%m%d"),"ticker": _slice(line, 12, 24),"name_b3": _slice(line, 27, 39),"isin": _slice(line, 230, 242),"open": int(_slice(line, 56, 69) or 0) / 100.0,"high": int(_slice(line, 69, 82) or 0) / 100.0,"low": int(_slice(line, 82, 95) or 0) / 100.0,"avg": int(_slice(line, 95, 108) or 0) / 100.0,"close": int(_slice(line, 108, 121) or 0) / 100.0,"trades": int(_slice(line, 147, 152) or 0),"quantity": int(_slice(line, 152, 170) or 0),"financial_volume": int(_slice(line, 170, 188) or 0) / 100.0})
        except (TypeError, ValueError):
            continue
    return pd.DataFrame(rows)


def _download_zip(year: int, cache_dir: Path, timeout: int = 90) -> bytes:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"COTAHIST_A{year}.ZIP"
    if cached.exists() and year < date.today().year:
        return cached.read_bytes()
    url = B3_URL.format(year=year)
    headers = {"User-Agent": "Mozilla/5.0 FII-Radar/1.0"}
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    cached.write_bytes(response.content)
    return response.content


def load_year(year: int, cache_dir: str | Path = ".cache/b3") -> pd.DataFrame:
    payload = _download_zip(year, Path(cache_dir))
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        members = [m for m in zf.namelist() if m.lower().endswith(".txt")]
        if not members:
            raise RuntimeError(f"Nenhum TXT encontrado no COTAHIST de {year}.")
        raw = zf.read(members[0])
    text = raw.decode("latin-1", errors="replace")
    return parse_cotahist_text(text)


def load_history(years: Iterable[int], cache_dir: str | Path = ".cache/b3") -> pd.DataFrame:
    frames = []
    for year in years:
        frame = load_year(int(year), cache_dir)
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(subset=["date", "ticker", "isin"], keep="last")
    return out.sort_values(["ticker", "date"]).reset_index(drop=True)
