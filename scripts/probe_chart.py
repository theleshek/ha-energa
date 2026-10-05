#!/usr/bin/env python3
"""Diagnostyka wykresów (dane godzinowe/dzienne) portalu Energa Mój Licznik.

    $env:ENERGA_USER="login"; $env:ENERGA_PASS="haslo"
    py scripts/probe_chart.py

Wynik: probe_output/chart_report.txt. Zawiera:
 1) fragmenty kodu JS portalu opisujące wywołania /resources/chart (kod publiczny),
 2) odpowiedzi kilku wariantów zapytań o wykres z zamaskowanymi danymi osobowymi.
Wartości liczbowe (zużycie) zostają.
Ograniczenie: do wartości `meterPoint` podstawiane są kolejno: id, dev, kod PPE, nazwa (nie trafiają do raportu). Przejrzyj plik przed wysłaniem.
"""
import asyncio
import json
import os
import pathlib
import re
import sys
import time

import aiohttp

BASE = "https://mojlicznik.energa-operator.pl"
OUT = pathlib.Path("probe_output")
UA = {"User-Agent": "Mozilla/5.0"}
KEEP = {"obis", "label", "zone", "tp", "type", "unit", "tariff", "availableCharts", "precision"}
LOG = []


def say(*a):
    line = " ".join(str(x) for x in a)
    print(line)
    LOG.append(line)


def mask(o, key=None):
    if isinstance(o, dict):
        return {k: mask(v, k) for k, v in o.items()}
    if isinstance(o, list):
        head = [mask(v, key) for v in o[:30]]
        return head + (["...(+%d)" % (len(o) - 30)] if len(o) > 30 else [])
    if isinstance(o, str) and key not in KEEP and (len(o) > 14 or "@" in o):
        return "***"
    return o


async def get(s, path, **kw):
    async with s.get(BASE + path, headers=UA, **kw) as r:
        return r.status, await r.text(errors="replace")


async def main():
    user, pwd = os.environ.get("ENERGA_USER"), os.environ.get("ENERGA_PASS")
    if not user or not pwd:
        sys.exit("Ustaw ENERGA_USER i ENERGA_PASS")
    OUT.mkdir(exist_ok=True)
    async with aiohttp.ClientSession() as s:
        _, page = await get(s, "/dp/UserLogin.do")
        tok = re.search(r'name="_antixsrf"[^>]*value="([^"]+)"', page).group(1)
        async with s.post(BASE + "/dp/UserLogin.do", headers=UA, data={
            "selectedForm": "1", "save": "save", "_antixsrf": tok, "clientOS": "web",
            "j_username": user, "j_password": pwd}) as r:
            html = await r.text(errors="replace")

        # 1) kod JS portalu: jak budowane są parametry zapytania o wykres
        say("=== FRAGMENTY JS ===")
        srcs = sorted(x for x in set(re.findall(r'<script[^>]+src="([^"]+)"', html)) if not x.startswith("http"))
        seen = set()
        for src in srcs:
            url = src if src.startswith("/") else "/dp/" + src
            _, js = await get(s, url)
            if "resources/chart" not in js:
                continue
            for pat in (r"meterPoint\s*[:=]", r"\bmo\b\s*[:=]", r"\.params\.(from|type|mo)\b", r"MDM\.chart\.url|this\.url", r"data:\s*(this|MDM\.chart)\.params"):
                for m in list(re.finditer(pat, js))[:3]:
                    key = (src, m.start() // 400)
                    if key in seen:
                        continue
                    seen.add(key)
                    say(f"--- {src} @ {m.start()} [{pat}] ---")
                    say(re.sub(r"\s+", " ", js[max(0, m.start() - 350):m.end() + 450]))
            break  # wystarczy pierwszy plik zawierający kod wykresu

        # 2) próby zapytań
        _, body = await get(s, "/dp/resources/user/data")
        resp = json.loads(body)["response"]
        mp = resp["meterPoints"][0]
        objs = mp.get("meterObjects") or []
        say("\n=== klucze meterPoint ===", sorted(mp.keys()))
        cands = {"id": mp.get("id"), "dev": mp.get("dev"),
                 "ppe": (mp.get("agreementPoints") or [{}])[0].get("code"), "name": mp.get("name")}
        obis_list = [o["obis"] for o in objs if o.get("obis")][:1] + [o["obis"] for o in objs if o.get("obis", "").startswith("1-0:2.8.0")][:1]
        day0 = int(time.mktime(time.localtime(time.time() - 2 * 86400)[:3] + (0, 0, 0, 0, 0, -1)) * 1000)
        say("\n=== PRÓBY /dp/resources/chart (meterPoint z różnych pól) ===")
        for field, val in cands.items():
            if not val:
                continue
            for obis in obis_list[:1]:
                params = {"meterPoint": val, "type": "DAY", "from": day0, "mo": obis}
                st, txt = await get(s, "/dp/resources/chart", params=params)
                say(f"\nmeterPoint={{{field}}} type=DAY from={day0} mo={obis} -> {st}")
                try:
                    say(json.dumps(mask(json.loads(txt)), ensure_ascii=False)[:2500])
                except ValueError:
                    say("nie JSON:", re.sub(r"\s+", " ", txt[:200]))
                if st == 200:
                    # pierwszy działający wariant: sprawdź też pozostałe typy i oddanie
                    for typ, mo in (("MONTH", obis), ("DAY", obis_list[-1])):
                        st2, txt2 = await get(s, "/dp/resources/chart", params={**params, "type": typ, "mo": mo})
                        say(f"\n  >> type={typ} mo={mo} -> {st2}")
                        try:
                            say(json.dumps(mask(json.loads(txt2)), ensure_ascii=False)[:1800])
                        except ValueError:
                            say("nie JSON:", re.sub(r"\s+", " ", txt2[:200]))
                    break
    (OUT / "chart_report.txt").write_text("\n".join(LOG), encoding="utf-8")
    print("\nGotowe: probe_output/chart_report.txt (przejrzyj przed wysłaniem)")


asyncio.run(main())
