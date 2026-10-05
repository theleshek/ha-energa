#!/usr/bin/env python3
"""Diagnostyka portalu Energa Mój Licznik v3 (uruchom u siebie).

    $env:ENERGA_USER="login"; $env:ENERGA_PASS="haslo"
    py scripts/probe.py

Wynik: probe_output/report.txt - struktura danych konta z ZAMASKOWANYMI
wartościami tekstowymi (PPE, adres, e-mail, nazwy) oraz lista endpointów
/dp/resources/... znalezionych w stronach i skryptach portalu.
Liczby (odczyty) i krótkie wartości techniczne (<=10 znaków) zostają.
"""
import asyncio
import json
import os
import pathlib
import re
import sys

import aiohttp

BASE = "https://mojlicznik.energa-operator.pl"
OUT = pathlib.Path("probe_output")
LOG = []


def say(*a):
    line = " ".join(str(x) for x in a)
    print(line)
    LOG.append(line)


def mask(o):
    if isinstance(o, dict):
        return {k: mask(v) for k, v in o.items()}
    if isinstance(o, list):
        return [mask(v) for v in o[:3]] + (["...(+%d)" % (len(o) - 3)] if len(o) > 3 else [])
    if isinstance(o, str) and (len(o) > 10 or re.search(r"@|\d{6,}", o)):
        return "***"
    return o


async def text(s, path, **kw):
    async with s.get(BASE + path, **kw) as r:
        return r.status, await r.text(errors="replace")


async def main():
    user, pwd = os.environ.get("ENERGA_USER"), os.environ.get("ENERGA_PASS")
    if not user or not pwd:
        sys.exit("Ustaw ENERGA_USER i ENERGA_PASS")
    OUT.mkdir(exist_ok=True)
    async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"}) as s:
        _, page = await text(s, "/dp/UserLogin.do")
        tok = re.search(r'name="_antixsrf"[^>]*value="([^"]+)"', page).group(1)
        async with s.post(BASE + "/dp/UserLogin.do", data={
            "selectedForm": "1", "save": "save", "_antixsrf": tok, "clientOS": "web",
            "j_username": user, "j_password": pwd}) as r:
            html = await r.text(errors="replace")
        st, body = await text(s, "/dp/resources/user/data")
        say("user/data:", st)
        try:
            say(json.dumps(mask(json.loads(body)), indent=1, ensure_ascii=False))
        except ValueError:
            say("nie JSON:", body[:200])

        # Szukamy endpointów w HTML po zalogowaniu i w skryptach JS
        found = set(re.findall(r"resources/[A-Za-z0-9_/\-]+", html))
        for src in set(re.findall(r'<script[^>]+src="([^"]+)"', html)):
            url = src if src.startswith("/") else "/dp/" + src
            if src.startswith("http"):
                continue
            try:
                _, js = await text(s, url)
                found |= set(re.findall(r"resources/[A-Za-z0-9_/\-]+", js))
            except Exception as e:  # noqa: BLE001
                say("skrypt pominięty:", src, e)
        say("\nZnalezione endpointy:")
        for f in sorted(found):
            say("  /dp/" + f)
    (OUT / "report.txt").write_text("\n".join(LOG), encoding="utf-8")
    print("\nGotowe: probe_output/report.txt (przejrzyj przed wysłaniem)")


asyncio.run(main())
