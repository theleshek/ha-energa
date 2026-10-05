"""Klient nieoficjalnego API portalu Energa Mój Licznik.

UWAGA: endpointy i format odpowiedzi poniżej pochodzą z wiedzy o istniejących
nieoficjalnych klientach i NIE są jeszcze zweryfikowane na prawdziwym koncie.
Najpierw uruchom scripts/probe.py, a potem dopasuj parsowanie w `_parse_*`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from decimal import Decimal
import logging
import re
from typing import Any

from zoneinfo import ZoneInfo

import aiohttp

from .const import BASE_URL
from .hourly import ChartPoint

_LOGGER = logging.getLogger(__name__)


# Portal odrzuca klientów niewyglądających na przeglądarkę. Nagłówki podajemy w każdym
# żądaniu, bo sesja Home Assistanta ma nagłówki tylko do odczytu.
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
}


PORTAL_TZ = ZoneInfo("Europe/Warsaw")  # strefa czasowa, w której portal liczy doby


class EnergaError(Exception):
    """Ogólny błąd klienta."""


class EnergaHttpError(EnergaError):
    """Odpowiedź HTTP z błędem (np. 404 dla nieznanego punktu pomiaru)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class EnergaAuthError(EnergaError):
    """Nieprawidłowy login lub hasło."""


@dataclass
class Meter:
    """Punkt poboru energii (PPE)."""

    id: str
    ppe: str
    name: str
    tariff: str | None = None
    prosumer: bool = False
    # Kandydaci na identyfikator "meterPoint" dla wykresów (pole konta -> wartość).
    # Portal używa innej liczby niż `id` licznika w danych konta, więc klient sprawdza
    # kolejne pola i zapamiętuje to, które działa.
    chart_ids: dict[str, str] = field(default_factory=dict)


@dataclass
class Readings:
    """Ostatnie odczyty licznika: klucz strefy (np. 'A+1') -> kWh."""

    timestamp: datetime | None = None
    values: dict[str, Decimal] = field(default_factory=dict)


class EnergaClient:
    """Asynchroniczny klient portalu ML."""

    def __init__(self, session: aiohttp.ClientSession, username: str, password: str) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._chart_ids: dict[str, str] = {}  # id licznika -> działający identyfikator dla wykresów

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            async with self._session.request(
                method, f"{BASE_URL}{path}", headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=30), **kwargs
            ) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except aiohttp.ClientResponseError as err:
            raise EnergaHttpError(err.status, f"{type(err).__name__}: {err.status} {err.message}") from err
        except (aiohttp.ClientError, ValueError) as err:
            # ValueError: odpowiedź nie jest JSON-em (np. strona HTML zamiast danych)
            raise EnergaError(f"{type(err).__name__}: {err}") from err

    async def _session_valid(self) -> bool:
        """Czy bieżąca sesja jest zalogowana (endpoint danych konta zwraca JSON success)."""
        try:
            data = await self._request("GET", "/dp/resources/user/data")
        except EnergaError:
            return False
        return isinstance(data, dict) and bool(data.get("success"))

    async def async_login(self) -> None:
        """Zaloguj, jeśli sesja nie jest już ważna.

        Przy aktywnej sesji portal nie pokazuje formularza logowania (brak tokenu
        _antixsrf), więc najpierw sprawdzamy, czy nie jesteśmy zalogowani.

        Wyjątki: EnergaAuthError tylko gdy portal wyraźnie odrzucił dane logowania;
        przy blokadzie, CAPTCHA lub błędach serwera rzucamy EnergaError (ponowna próba później).
        """
        if await self._session_valid():
            _LOGGER.debug("Sesja portalu nadal ważna, pomijam logowanie")
            return
        _LOGGER.debug("Logowanie do portalu Mój Licznik")
        try:
            async with self._session.get(
                f"{BASE_URL}/dp/UserLogin.do", headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                page = await resp.text(errors="replace")
        except aiohttp.ClientError as err:
            raise EnergaError(f"{type(err).__name__}: {err}") from err
        match = re.search(r'name="_antixsrf"[^>]*value="([^"]+)"', page) or re.search(
            r'value="([^"]+)"[^>]*name="_antixsrf"', page
        )
        if not match:
            title, hint = _page_summary(page)
            raise EnergaError(f"Nie znaleziono tokenu _antixsrf na stronie logowania (strona: '{title}'; {hint})")
        try:
            async with self._session.post(
                f"{BASE_URL}/dp/UserLogin.do",
                data={
                    "selectedForm": "1",
                    "save": "save",
                    "_antixsrf": match.group(1),
                    "clientOS": "web",
                    "j_username": self._username,
                    "j_password": self._password,
                },
                headers=_HEADERS,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                status, final_path, answer = resp.status, resp.url.path, await resp.text(errors="replace")
        except aiohttp.ClientError as err:
            raise EnergaError(f"{type(err).__name__}: {err}") from err
        # Poprawne logowanie = endpoint JSON odpowiada success:true
        if await self._session_valid():
            _LOGGER.debug("Zalogowano do portalu Mój Licznik")
            return
        title, hint = _page_summary(answer)
        details = f"HTTP {status}, adres końcowy {final_path}, strona '{title}', {hint}"
        _LOGGER.warning("Logowanie do portalu nie powiodło się: %s", details)
        transient = status >= 500 or status == 429 or re.search(r"captcha", answer, re.I) or re.search(
            r"zbyt wiele|zablokowan|tymczasow|limit|spróbuj ponownie", hint, re.I
        )
        if transient:
            raise EnergaError(f"Portal chwilowo odmawia logowania ({details})")
        raise EnergaAuthError(f"Logowanie nieudane ({details})")

    async def async_get_meters(self) -> list[Meter]:
        """Pobierz listę liczników (PPE) konta wraz z identyfikatorami dla wykresów."""
        meters = self._parse_meters(await self._request("GET", "/dp/resources/user/data"))
        try:
            mpc_by_ppe = self._parse_mpc(await self._get_text("/dp/UserAccount.do"))
        except EnergaError as err:
            _LOGGER.warning("Nie udało się pobrać strony konta z identyfikatorami liczników: %s", err)
            mpc_by_ppe = {}
        for meter in meters:
            mpc = mpc_by_ppe.get(meter.ppe) or (mpc_by_ppe.get("") if len(meters) == 1 else None)
            if mpc:
                # identyfikator z odnośnika EnergyIndex.do?mpc=...&ppe=... ma pierwszeństwo
                meter.chart_ids = {"mpc": mpc, **meter.chart_ids}
        _LOGGER.debug("Identyfikatory mpc znalezione dla %d z %d liczników", sum("mpc" in m.chart_ids for m in meters), len(meters))
        return meters

    async def _get_text(self, path: str) -> str:
        try:
            async with self._session.get(
                f"{BASE_URL}{path}", headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                resp.raise_for_status()
                return await resp.text(errors="replace")
        except aiohttp.ClientError as err:
            raise EnergaError(f"{type(err).__name__}: {err}") from err

    @staticmethod
    def _parse_mpc(html: str) -> dict[str, str]:
        """Z odnośników EnergyIndex.do?mpc=<id>&ppe=<PPE> zrób słownik PPE -> mpc.

        Gdy odnośniki nie zawierają PPE, a jest jeden identyfikator, zapisz go pod kluczem ''.
        """
        found: dict[str, str] = {}
        for m in re.finditer(r"mpc=(\d+)(?:&amp;|&)ppe=(\d+)", html):
            found.setdefault(m.group(2), m.group(1))
        for m in re.finditer(r"ppe=(\d+)(?:&amp;|&)mpc=(\d+)", html):
            found.setdefault(m.group(1), m.group(2))
        if not found:
            ids = set(re.findall(r"mpc=(\d+)", html))
            if len(ids) == 1:
                found[""] = ids.pop()
        return found

    async def async_get_readings(self) -> dict[str, Readings]:
        """Pobierz ostatnie odczyty wszystkich liczników; zwraca słownik: id licznika -> Readings."""
        data = await self._request("GET", "/dp/resources/user/data")
        if not isinstance(data, dict) or not data.get("success"):
            raise EnergaAuthError("Sesja wygasła")
        readings = self._parse_readings(data)
        _LOGGER.debug(
            "Pobrano odczyty: %s",
            {mid: {k: str(v) for k, v in r.values.items()} for mid, r in readings.items()},
        )
        return readings

    async def async_get_day_chart(self, meter: Meter, mo: str, day: date) -> list[ChartPoint]:
        """Pobierz godzinowy wykres doby. mo: 'A+' (pobór) lub 'A-' (oddanie)."""
        cached = self._chart_ids.get(meter.id)
        if cached:
            return await self._day_chart(cached, meter, mo, day)
        tried: list[str] = []
        for field_name, value in (meter.chart_ids or {"id": meter.id}).items():
            try:
                points = await self._day_chart(value, meter, mo, day)
            except EnergaHttpError as err:
                if err.status in (400, 404):  # zły identyfikator: próbujemy następne pole
                    tried.append(field_name)
                    continue
                raise
            self._chart_ids[meter.id] = value
            _LOGGER.info("Wykresy dla %s: identyfikator punktu pomiaru z pola '%s'", meter.name, field_name)
            return points
        raise EnergaError(
            "Nie znaleziono identyfikatora punktu pomiaru dla wykresów "
            f"(próbowano pól: {', '.join(tried) or 'brak'})"
        )

    async def _day_chart(self, meter_point: str, meter: Meter, mo: str, day: date) -> list[ChartPoint]:
        midnight = datetime.combine(day, time.min, tzinfo=PORTAL_TZ)
        data = await self._request(
            "GET",
            "/dp/resources/chart",
            params={
                "mainChartDate": int(midnight.timestamp() * 1000),
                "type": "DAY",
                "meterPoint": meter_point,
                "mo": mo,
            },
        )
        points = self._parse_chart(data)
        _LOGGER.debug("Wykres %s %s dla %s: %d godzin", mo, day, meter.name, len(points))
        return points

    @staticmethod
    def _parse_chart(data: Any) -> list[ChartPoint]:
        if not isinstance(data, dict) or not data.get("success"):
            raise EnergaError(f"Portal zwrócił błąd wykresu: {(data or {}).get('error') if isinstance(data, dict) else data}")
        points = []
        for item in (data.get("response") or {}).get("mainChart") or []:
            try:
                start = datetime.fromtimestamp(int(item["tm"]) / 1000, tz=timezone.utc)
            except (KeyError, ValueError, TypeError):
                continue
            points.append(
                ChartPoint(
                    start=start,
                    zones=[None if v is None else float(v) for v in item.get("zones") or []],
                    complete=bool(item.get("cplt")) and not item.get("est"),
                )
            )
        return points

    @staticmethod
    def _response(data: Any) -> dict:
        return (data or {}).get("response") or {}

    @classmethod
    def _parse_meters(cls, data: Any) -> list[Meter]:
        resp = cls._response(data)
        types = {a.get("code"): a.get("type") for a in resp.get("agreementPoints") or []}
        meters = []
        for mp in resp.get("meterPoints") or []:
            agreements = mp.get("agreementPoints") or []
            ppe = str(agreements[0]["code"]) if agreements else str(mp["id"])
            zones = [_zone_key(m.get("zone")) for m in mp.get("lastMeasurements") or []]
            meters.append(
                Meter(
                    chart_ids=_chart_id_candidates(mp),
                    id=str(mp["id"]),
                    ppe=ppe,
                    name=mp.get("name") or ppe,
                    tariff=mp.get("tariff"),
                    # Prosument: typ umowy "Wytwórca" lub strefy oddania (A-) w odczytach
                    prosumer=types.get(ppe) == "Wytwórca" or any(z and z.startswith("A-") for z in zones),
                )
            )
        return meters

    @classmethod
    def _parse_readings(cls, data: Any) -> dict[str, Readings]:
        result: dict[str, Readings] = {}
        for mp in cls._response(data).get("meterPoints") or []:
            readings = Readings()
            for m in mp.get("lastMeasurements") or []:
                key, value = _zone_key(m.get("zone")), m.get("value")
                if key is None or value is None:
                    continue
                readings.values[key] = Decimal(str(value))
                readings.timestamp = _parse_date(m.get("date")) or readings.timestamp
            result[str(mp["id"])] = readings
        return result


def _page_summary(html: str) -> tuple[str, str]:
    """Tytuł strony i tekst komunikatu błędu (jeśli jest) - do diagnostyki logowania."""
    title = re.search(r"(?is)<title>(.*?)</title>", html)
    title_text = re.sub(r"\s+", " ", title.group(1)).strip() if title else "?"
    msg = re.search(
        r'(?is)<[^>]+(?:class|id)="[^"]*(?:err|alert|warn|msg|message|fail)[^"]*"[^>]*>(.*?)</', html
    )
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", msg.group(1))).strip() if msg else ""
    return title_text[:80], (f"komunikat: '{text[:200]}'" if text else "bez komunikatu błędu na stronie")


def _chart_id_candidates(mp: dict) -> dict[str, str]:
    """Pola licznika, które mogą być identyfikatorem 'meterPoint' dla wykresów (najpierw dev, id)."""
    found: dict[str, str] = {}
    priority = {"dev": 0, "id": 1}
    keys = sorted(mp, key=lambda k: (priority.get(k, 2), list(mp).index(k)))
    for key in keys:
        value = mp[key]
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            continue
        if not (key in ("dev", "id") or re.search(r"id|dev|point|meter", key, re.I)):
            continue
        if re.fullmatch(r"[A-Za-z0-9]{3,20}", str(value)):
            found[key] = str(value)
    return found


def _zone_key(zone: Any) -> str | None:
    """'A+ strefa 1' -> 'A+1', 'A- strefa 2' -> 'A-2'."""
    match = re.search(r"A\s*([+-]).*?(\d)", str(zone or ""))
    return f"A{match.group(1)}{match.group(2)}" if match else None


def _parse_date(value: Any) -> datetime | None:
    """Data jako milisekundy epoch (tekst/liczba) lub 'RRRR-MM-DD GG:MM'."""
    try:
        if str(value).isdigit():
            return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
        return datetime.fromisoformat(str(value))
    except (ValueError, OverflowError):
        return None
