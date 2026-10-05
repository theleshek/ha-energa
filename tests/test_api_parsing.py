"""Testy parsowania odpowiedzi portalu (zanonimizowane dane przykładowe)."""
import importlib
import pathlib
import sys
import types
from decimal import Decimal

# Pakiet ładowany bez __init__.py (ten wymaga Home Assistanta); moduły api/hourly są czyste.
pkg = types.ModuleType("energa_pkg")
pkg.__path__ = [str(pathlib.Path(__file__).parents[1] / "custom_components/energa_moj_licznik")]
sys.modules["energa_pkg"] = pkg
api = importlib.import_module("energa_pkg.api")
hourly = importlib.import_module("energa_pkg.hourly")

DATA = {"success": True, "response": {
    "agreementPoints": [{"code": "PPE1", "type": "Wytwórca"}],
    "meterPoints": [{"id": "10", "name": "Dom", "tariff": "G12W",
        "agreementPoints": [{"code": "PPE1"}],
        "lastMeasurements": [
            {"date": "1790809200000", "zone": "A+ strefa 1", "value": 74941.001},
            {"date": "1790809200000", "zone": "A+ strefa 2", "value": 123058.095},
            {"date": "1790809200000", "zone": "A- strefa 1", "value": 31836.846},
            {"date": "1790809200000", "zone": "A- strefa 2", "value": 31859.901}]}]}}


def test_meters_prosumer():
    (m,) = api.EnergaClient._parse_meters(DATA)
    assert (m.id, m.ppe, m.tariff, m.prosumer) == ("10", "PPE1", "G12W", True)


def test_readings():
    r = api.EnergaClient._parse_readings(DATA)["10"]
    assert r.values["A+1"] == Decimal("74941.001")
    assert set(r.values) == {"A+1", "A+2", "A-1", "A-2"}
    assert r.timestamp is not None


def test_non_prosumer():
    d = {"response": {"meterPoints": [{"id": 1, "agreementPoints": [{"code": "X"}],
        "lastMeasurements": [{"zone": "A+ strefa 1", "value": 1}]}]}}
    assert api.EnergaClient._parse_meters(d)[0].prosumer is False


def test_html_instead_of_json_is_energa_error():
    import asyncio
    import aiohttp
    from aiohttp import web

    async def run():
        async def html(_):
            return web.Response(text="<html>blokada</html>", content_type="text/html")
        app = web.Application()
        app.router.add_get("/x", html)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        api.BASE_URL = f"http://127.0.0.1:{port}"
        async with aiohttp.ClientSession() as s:
            client = api.EnergaClient(s, "u", "p")
            try:
                await client._request("GET", "/x")
            except api.EnergaError:
                return True
            return False
        await runner.cleanup()

    assert asyncio.run(run())


def test_client_works_with_readonly_session_headers():
    """Regresja: sesja HA ma nagłówki tylko do odczytu (mappingproxy)."""
    class S:
        headers = __import__("types").MappingProxyType({})
    api.EnergaClient(S(), "u", "p")  # nie może rzucać


def test_second_login_with_active_session_does_not_need_form():
    """Regresja: przy aktywnej sesji /UserLogin.do nie ma formularza (brak _antixsrf)."""
    import asyncio
    import aiohttp
    from aiohttp import web

    async def run():
        state = {"logged": False, "posts": 0}

        async def login_page(request):
            if state["logged"]:  # portal przekierowuje zalogowanych na UserData.do
                return web.Response(text="<html>UserData bez formularza</html>", content_type="text/html")
            return web.Response(text='<input name="_antixsrf" type="hidden" value="tok1">', content_type="text/html")

        async def login_post(request):
            form = await request.post()
            assert form["_antixsrf"] == "tok1"
            state["logged"] = True
            state["posts"] += 1
            return web.Response(text="ok")

        async def user_data(request):
            if state["logged"]:
                return web.json_response({"success": True, "response": {"meterPoints": []}})
            return web.Response(text="<html>login</html>", content_type="text/html")

        app = web.Application()
        app.router.add_get("/dp/UserLogin.do", login_page)
        app.router.add_post("/dp/UserLogin.do", login_post)
        app.router.add_get("/dp/resources/user/data", user_data)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        api.BASE_URL = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        async with aiohttp.ClientSession() as s:
            client = api.EnergaClient(s, "u", "p")
            await client.async_login()
            await client.async_login()  # drugi raz: sesja ważna, bez formularza
        await runner.cleanup()
        return state["posts"]

    assert asyncio.run(run()) == 1


ACCOUNT_HTML = """
<a href="EnergyIndex.do?mpc=111111&ppe=481000000000000001" title="12345678"> 12345678 </a>
<img src="images/ppeEdit.png" onclick="showDialogMeterName(this)"
     ppe="481000000000000001"
     meterSN="12345678"
     meterName=""
     displayMeterName="false" title="Edytuj" />
<img src="images/ppeEdit.png" meterSN="87654321" ppe="481000000000000002" />
<img src="images/ppeEdit.png" ppe="481000000000000003" meterSN="" />
"""


def test_parse_serials_by_ppe():
    found = api.EnergaClient._parse_serials(ACCOUNT_HTML)
    assert found == {"481000000000000001": "12345678", "481000000000000002": "87654321"}


def test_parse_serials_without_ppe_is_single_fallback():
    assert api.EnergaClient._parse_serials('<img meterSN="42">') == {"": "42"}
    assert api.EnergaClient._parse_serials("<html>brak</html>") == {}
