#!/usr/bin/env python3
"""Miesięczne pobranie (A+) i oddanie (A-) w podziale na strefy - dane do weryfikacji modelu magazynu energii.

    $env:ENERGA_USER="login"; $env:ENERGA_PASS="haslo"
    py scripts/probe_year.py [rok_początkowy]      # domyślnie 2023

Wynik: probe_output/year_report.json - tylko miesiące i wartości kWh (bez PPE, adresu, numeru licznika).
Używa tego samego klienta co integracja (custom_components/energa_moj_licznik/api.py).
"""
import asyncio
import importlib
import json
import os
import pathlib
import sys
import types
from datetime import datetime

import aiohttp

ROOT = pathlib.Path(__file__).resolve().parents[1]
pkg = types.ModuleType("energa_pkg")
pkg.__path__ = [str(ROOT / "custom_components" / "energa_moj_licznik")]
sys.modules["energa_pkg"] = pkg
api = importlib.import_module("energa_pkg.api")


async def main() -> None:
    first_year = int(sys.argv[1]) if len(sys.argv) > 1 else 2023
    last_year = datetime.now(api.PORTAL_TZ).year
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar()) as session:
        client = api.EnergaClient(session, os.environ["ENERGA_USER"], os.environ["ENERGA_PASS"])
        await client.async_login()
        report = {}
        for number, meter in enumerate(await client.async_get_meters(), start=1):
            months = {}
            for mo in ("A+", "A-"):
                for year in range(first_year, last_year + 1):
                    for p in await client.async_get_year_chart(meter, mo, year):
                        key = p.start.astimezone(api.PORTAL_TZ).strftime("%Y-%m")
                        months.setdefault(key, {})[mo] = {"zones": p.zones, "complete": p.complete}
                    await asyncio.sleep(0.3)
            report[f"meter_{number}"] = {"tariff": meter.tariff, "prosumer": meter.prosumer, "months": dict(sorted(months.items()))}
    out = ROOT / "probe_output"
    out.mkdir(exist_ok=True)
    (out / "year_report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print("Zapisano probe_output/year_report.json")


asyncio.run(main())
