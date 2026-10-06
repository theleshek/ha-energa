#!/usr/bin/env python3
"""Godzinowe A+ i A- per strefa za wybrany zakres dat - do porównania z fakturą (sumy godzinowych sald).

    $env:ENERGA_USER="login"; $env:ENERGA_PASS="haslo"
    py scripts/probe_hours.py 2026-04-01 2026-08-31      # domyślnie: 2026-04-01 .. 2026-08-31

Wynik: probe_output/hours_report.json - godziny (UTC) i wartości kWh per strefa, bez PPE, adresu i numeru licznika.
Pobiera po jednym wykresie doby na dzień i kierunek (ok. 300 zapytań dla 5 miesięcy, kilka minut).
"""
import asyncio
import importlib
import json
import os
import pathlib
import sys
import types
from datetime import date, timedelta

import aiohttp

ROOT = pathlib.Path(__file__).resolve().parents[1]
pkg = types.ModuleType("energa_pkg")
pkg.__path__ = [str(ROOT / "custom_components" / "energa_moj_licznik")]
sys.modules["energa_pkg"] = pkg
api = importlib.import_module("energa_pkg.api")


async def main() -> None:
    start = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2026, 4, 1)
    end = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date(2026, 8, 31)
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar()) as session:
        client = api.EnergaClient(session, os.environ["ENERGA_USER"], os.environ["ENERGA_PASS"])
        await client.async_login()
        report = {}
        for number, meter in enumerate(await client.async_get_meters(), start=1):
            if not meter.prosumer:
                continue
            hours = {}
            day = start
            while day <= end:
                for mo in ("A+", "A-"):
                    for p in await client.async_get_day_chart(meter, mo, day):
                        row = hours.setdefault(p.start.isoformat(), {"complete": True})
                        row[mo] = p.zones
                        row["complete"] = row["complete"] and p.complete
                    await asyncio.sleep(0.25)
                day += timedelta(days=1)
            report[f"meter_{number}"] = {"from": start.isoformat(), "to": end.isoformat(), "hours": dict(sorted(hours.items()))}
            print(f"Licznik {number}: {len(hours)} godzin")
    out = ROOT / "probe_output"
    out.mkdir(exist_ok=True)
    (out / "hours_report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    print("Zapisano probe_output/hours_report.json")


asyncio.run(main())
