"""Testy danych godzinowych: parsowanie wykresu z portalu i sumowanie."""
import importlib
import pathlib
import sys
import types
from datetime import date, datetime, timezone

pkg = sys.modules.get("energa_pkg")
if pkg is None:
    pkg = types.ModuleType("energa_pkg")
    pkg.__path__ = [str(pathlib.Path(__file__).parents[1] / "custom_components/energa_moj_licznik")]
    sys.modules["energa_pkg"] = pkg
api = importlib.import_module("energa_pkg.api")
hourly = importlib.import_module("energa_pkg.hourly")
PORTAL_TZ = api.PORTAL_TZ

# Struktura odpowiedzi jak w prawdziwym HAR (doba 2026-10-01, taryfa G12W); ostatnia godzina niekompletna.
CHART = {
    "success": True,
    "response": {
        "mainChart": [
            {"tm": "1790805600000", "zones": [None, 2.578, None], "est": False, "cplt": True},  # 00:00
            {"tm": "1790809200000", "zones": [None, 1.036, None], "est": False, "cplt": True},  # 01:00
            {"tm": "1790827200000", "zones": [2.727, None, None], "est": False, "cplt": True},  # 06:00
            {"tm": "1790830800000", "zones": [0.777, None, None], "est": False, "cplt": True},  # 07:00
            {"tm": "1790856000000", "zones": [0.0, None, None], "est": True, "cplt": False},  # niekompletna
        ]
    },
}


def points():
    return api.EnergaClient._parse_chart(CHART)


def test_parse_chart():
    p = points()
    assert len(p) == 5
    assert p[0].start == datetime(2026, 9, 30, 22, 0, tzinfo=timezone.utc)  # 00:00 czasu lokalnego
    assert p[0].zones == [None, 2.578, None]
    assert [x.complete for x in p] == [True, True, True, True, False]


def test_parse_chart_error_response():
    import pytest

    with pytest.raises(api.EnergaError):
        api.EnergaClient._parse_chart({"success": False, "error": "x"})


def test_accumulate_total_and_zones():
    p = points()
    total = hourly.accumulate(p, None, 0.0, None)
    assert [r[1] for r in total] == [2.578, 1.036, 2.727, 0.777]  # niekompletna godzina pominięta
    assert total[-1][2] == round(2.578 + 1.036 + 2.727 + 0.777, 5)
    z2 = hourly.accumulate(p, None, 0.0, 2)
    assert z2[-1][2] == round(2.578 + 1.036, 5)  # strefa 2 tylko w godzinach nocnych


def test_accumulate_continues_from_last_statistic():
    p = points()
    after = datetime(2026, 9, 30, 23, 0, tzinfo=timezone.utc)  # 01:00 lokalnie już zaimportowane
    rows = hourly.accumulate(p, after, 100.0, None)
    assert [r[0] for r in rows] == [p[2].start, p[3].start]
    assert rows[0][2] == round(100.0 + 2.727, 5)


def test_accumulate_stops_at_first_incomplete_hour():
    p = points()
    p.insert(2, hourly.ChartPoint(start=datetime(2026, 10, 1, 1, 0, tzinfo=timezone.utc), zones=[1.0], complete=False))
    rows = hourly.accumulate(p, None, 0.0, None)
    assert len(rows) == 2  # brak luk w sumie narastającej


def test_daily_usage():
    u = hourly.daily_usage(points(), date(2026, 10, 1), PORTAL_TZ)
    assert u.hours == 4
    assert u.zones[1] == round(2.727 + 0.777, 5)
    assert u.zones[2] == round(2.578 + 1.036, 5)
    assert u.total == round(2.578 + 1.036 + 2.727 + 0.777, 5)
    other = hourly.daily_usage(points(), date(2026, 9, 30), PORTAL_TZ)
    assert other.hours == 0 and other.total == 0


def test_day_chart_request_matches_browser_request():
    """Parametry zapytania identyczne jak w HAR: mainChartDate=północ Warszawy, type=DAY, meterPoint, mo."""
    import asyncio

    import aiohttp
    from aiohttp import web

    seen = {}

    async def run():
        async def chart(request):
            seen.update(request.query)
            return web.json_response(CHART)

        app = web.Application()
        app.router.add_get("/dp/resources/chart", chart)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        api.BASE_URL = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        async with aiohttp.ClientSession() as s:
            client = api.EnergaClient(s, "u", "p")
            meter = api.Meter(id="100001", ppe="PPE", name="Dom")
            pts = await client.async_get_day_chart(meter, "A+", date(2026, 10, 1))
        await runner.cleanup()
        return pts

    pts = asyncio.run(run())
    assert seen == {"mainChartDate": "1790805600000", "type": "DAY", "meterPoint": "100001", "mo": "A+"}
    assert len(pts) == 5


def test_chart_id_discovery_picks_working_field_and_caches():
    """Regresja z logu: id licznika (100002) daje 404, działa inne pole (dev=100001)."""
    import asyncio

    import aiohttp
    from aiohttp import web

    mp = {"id": 100002, "dev": 100001, "name": "adres z spacjami", "mult": 1.0, "alarmsEnabled": False}
    calls = []

    async def run():
        async def chart(request):
            calls.append(request.query["meterPoint"])
            if request.query["meterPoint"] != "100001":
                return web.Response(status=404, text="brak Punktu Pomiaru")
            return web.json_response(CHART)

        app = web.Application()
        app.router.add_get("/dp/resources/chart", chart)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        api.BASE_URL = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        meters = api.EnergaClient._parse_meters({"response": {"meterPoints": [dict(mp, agreementPoints=[{"code": "P"}])]}})
        meter = meters[0]
        assert list(meter.chart_ids) == ["dev", "id"]  # tylko pola, które mogą być identyfikatorem
        async with aiohttp.ClientSession() as s:
            client = api.EnergaClient(s, "u", "p")
            first = await client.async_get_day_chart(meter, "A+", date(2026, 10, 1))
            n_after_first = len(calls)
            await client.async_get_day_chart(meter, "A+", date(2026, 10, 2))
        await runner.cleanup()
        return first, n_after_first

    first, n_first = asyncio.run(run())
    assert len(first) == 5
    assert calls[:1] == ["100001"]  # dev sprawdzany jako pierwszy
    assert calls.count("100002") == 0 and len(calls) == n_first + 1  # zapamiętane, bez ponownego zgadywania


def test_chart_id_discovery_fails_with_clear_message():
    import asyncio

    import aiohttp
    from aiohttp import web

    async def run():
        async def chart(request):
            return web.Response(status=404, text="brak Punktu Pomiaru")

        app = web.Application()
        app.router.add_get("/dp/resources/chart", chart)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        api.BASE_URL = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        meter = api.Meter(id="1", ppe="P", name="Dom", chart_ids={"dev": "2", "id": "1"})
        async with aiohttp.ClientSession() as s:
            try:
                await api.EnergaClient(s, "u", "p").async_get_day_chart(meter, "A+", date(2026, 10, 1))
            except api.EnergaError as err:
                return str(err)
        await runner.cleanup()

    msg = asyncio.run(run())
    assert "dev, id" in msg


ACCOUNT_HTML = """
<table><tr><td>590000000000000001</td><td>00-000 Adres</td>
<td><a href="EnergyIndex.do?mpc=100001&amp;ppe=590000000000000001">12345678</a></td></tr>
<tr><td><a href="EnergyIndex.do?mpc=222222&ppe=590000000000000002">99999999</a></td></tr></table>
"""


def test_parse_mpc_from_account_page():
    assert api.EnergaClient._parse_mpc(ACCOUNT_HTML) == {
        "590000000000000001": "100001",
        "590000000000000002": "222222",
    }
    assert api.EnergaClient._parse_mpc('<a href="x?mpc=5">1</a>') == {"": "5"}
    assert api.EnergaClient._parse_mpc("brak") == {}


def test_get_meters_uses_mpc_from_account_page_first():
    """Z strony 'Moje konto' bierzemy mpc=100001 (id licznika 100002 jest nieprawidłowe dla wykresów)."""
    import asyncio

    import aiohttp
    from aiohttp import web

    chart_calls = []
    user = {"success": True, "response": {
        "agreementPoints": [{"code": "590000000000000001", "type": "Wytwórca"}],
        "meterPoints": [{"id": 100002, "dev": 77, "name": "x", "tariff": "G12W",
                         "agreementPoints": [{"code": "590000000000000001"}], "lastMeasurements": []}]}}

    async def run():
        async def user_data(_):
            return web.json_response(user)

        async def account(_):
            return web.Response(text=ACCOUNT_HTML, content_type="text/html")

        async def chart(request):
            chart_calls.append(request.query["meterPoint"])
            if request.query["meterPoint"] != "100001":
                return web.Response(status=404, text="brak Punktu Pomiaru")
            return web.json_response(CHART)

        app = web.Application()
        app.router.add_get("/dp/resources/user/data", user_data)
        app.router.add_get("/dp/UserAccount.do", account)
        app.router.add_get("/dp/resources/chart", chart)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        api.BASE_URL = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        async with aiohttp.ClientSession() as s:
            client = api.EnergaClient(s, "u", "p")
            (meter,) = await client.async_get_meters()
            pts = await client.async_get_day_chart(meter, "A+", date(2026, 10, 1))
        await runner.cleanup()
        return meter, pts

    meter, pts = asyncio.run(run())
    assert list(meter.chart_ids)[:1] == ["mpc"] and meter.chart_ids["mpc"] == "100001"
    assert chart_calls == ["100001"]  # od razu właściwy identyfikator, bez zgadywania
    assert len(pts) == 5


def _login_scenario(password, mode="normal"):
    """Atrapa portalu: formularz z _antixsrf, ciasteczko sesji po poprawnym haśle."""
    import asyncio

    import aiohttp
    from aiohttp import web

    form = '<html><title>Logowanie - Portal Klienta</title><form><input name="_antixsrf" value="tok"></form>{extra}</html>'

    async def run():
        async def login_page(request):
            return web.Response(text=form.format(extra=""), content_type="text/html")

        async def login_post(request):
            data = await request.post()
            if mode == "captcha":
                return web.Response(text=form.format(extra='<div class="g-recaptcha"></div>'), content_type="text/html")
            if data["j_password"] == "ok":
                resp = web.Response(text="<html>UserData</html>", content_type="text/html")
                resp.set_cookie("SESSION", "1")
                return resp
            return web.Response(
                text=form.format(extra='<div class="errorMsg">Nieprawidłowy login lub hasło</div>'),
                content_type="text/html",
            )

        async def user_data(request):
            if request.cookies.get("SESSION"):
                return web.json_response({"success": True, "response": {}})
            return web.Response(text=form.format(extra=""), content_type="text/html")

        app = web.Application()
        app.router.add_get("/dp/UserLogin.do", login_page)
        app.router.add_post("/dp/UserLogin.do", login_post)
        app.router.add_get("/dp/resources/user/data", user_data)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        api.BASE_URL = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as s:
                await api.EnergaClient(s, "u", password).async_login()
        finally:
            await runner.cleanup()

    try:
        asyncio.run(run())
    except Exception as err:  # noqa: BLE001
        return err
    return None


def test_login_ok():
    assert _login_scenario("ok") is None


def test_login_wrong_password_is_auth_error_with_portal_message():
    err = _login_scenario("zle")
    assert isinstance(err, api.EnergaAuthError)
    assert "Nieprawidłowy login lub hasło" in str(err) and "Logowanie - Portal Klienta" in str(err)


def test_login_captcha_is_transient_not_auth_error():
    err = _login_scenario("ok", mode="captcha")
    assert isinstance(err, api.EnergaError) and not isinstance(err, api.EnergaAuthError)


def _day_points(day, values, complete=True):
    """24 godziny doby UTC: values = {godzina: (pobór, oddanie)}, reszta 0."""
    from datetime import datetime, timedelta, timezone

    h0 = datetime(2026, 1, day, tzinfo=timezone.utc)
    plus, minus = [], []
    for i in range(24):
        a, b = values.get(i, (0.0, 0.0))
        plus.append(hourly.ChartPoint(start=h0 + timedelta(hours=i), zones=[a, 0.0, None], complete=complete))
        minus.append(hourly.ChartPoint(start=h0 + timedelta(hours=i), zones=[b, 0.0, None], complete=complete))
    return plus, minus


def test_storage_daily_sum_then_ratio():
    from datetime import datetime, timedelta, timezone

    # doba 1: godziny netto: -5 (nadwyżka), +3 (niedobór), -2 -> suma doby -4 -> do magazynu 0.7*4
    p1, m1 = _day_points(1, {10: (0.0, 5.0), 11: (3.0, 0.0), 12: (0.0, 2.0)})
    # doba 2: netto +1.0 -> z magazynu
    p2, m2 = _day_points(2, {8: (1.0, 0.0)})
    # doba 3 niekompletna -> nie rozliczana
    p3, m3 = _day_points(3, {8: (9.0, 0.0)})
    p3, m3 = p3[:10], m3[:10]
    value, last, credited, drawn = hourly.storage_steps(p1 + p2 + p3, m1 + m2 + m3, None, 0.0, 0.7)
    assert credited == 2.8 and drawn == 1.0 and value == 1.8
    assert last == datetime(2026, 1, 2, 23, tzinfo=timezone.utc)
    # ponowne wywołanie od 'last' nic nie zmienia
    assert hourly.storage_steps(p1 + p2 + p3, m1 + m2 + m3, last, value, 0.7)[0] == 1.8


def test_storage_daily_floor_and_incomplete_hour():
    p, m = _day_points(1, {5: (7.0, 0.0)})
    assert hourly.storage_steps(p, m, None, 2.0, 0.7)[0] == 0.0  # niedobór > stanu
    p, m = _day_points(1, {5: (0.0, 4.0)}, complete=True)
    p[23] = hourly.ChartPoint(start=p[23].start, zones=[0.0, 0.0, None], complete=False)
    assert hourly.storage_steps(p, m, None, 0.0, 0.7)[1] is None  # doba z niekompletną godziną
    assert hourly.latest_common_hour(*_day_points(1, {})) is not None
