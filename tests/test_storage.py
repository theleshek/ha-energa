"""Testy magazynu energii (net metering): przypadki wzorcowe z faktur Energi 2026 (okresy dwumiesięczne)."""
import importlib
import pathlib
import sys
import types
from datetime import date, datetime, timedelta, timezone

import pytest

pkg = sys.modules.get("energa_pkg")
if pkg is None:
    pkg = types.ModuleType("energa_pkg")
    pkg.__path__ = [str(pathlib.Path(__file__).parents[1] / "custom_components/energa_moj_licznik")]
    sys.modules["energa_pkg"] = pkg
api = importlib.import_module("energa_pkg.api")
hourly = importlib.import_module("energa_pkg.hourly")
storage = importlib.import_module("energa_pkg.storage")
TZ = api.PORTAL_TZ
Lot = storage.Lot


def totals(lots):
    return {z: round(sum(lot.amount for lot in lots[z])) for z in (1, 2)}


def test_period_bounds():
    assert storage.period_bounds(date(2026, 4, 15), 2) == (date(2026, 3, 1), date(2026, 4, 30))
    assert storage.period_bounds(date(2026, 8, 31), 2) == (date(2026, 7, 1), date(2026, 8, 31))
    assert storage.period_bounds(date(2026, 2, 10), 1) == (date(2026, 2, 1), date(2026, 2, 28))
    assert storage.period_bounds(date(2026, 12, 31), 3) == (date(2026, 10, 1), date(2026, 12, 31))


def test_invoice_march_april_surplus_covers_other_zone_and_rest_is_billed():
    """Faktura 00031: strefa 1 miała nadwyżkę 656, która pokryła niedobór 956 strefy 2; do zapłaty 300 kWh."""
    lots, billed = storage.settle(
        {1: [], 2: []}, {1: 909, 2: 2531}, {1: 2236, 2: 2250}, 0.7, date(2026, 4, 30)
    )
    assert totals(lots) == {1: 0, 2: 0}
    assert billed[1] == 0 and billed[2] == pytest.approx(300, abs=1)


def test_invoice_may_june_new_lots():
    """Faktura 00032: magazyn 1443 / 1062 po okresie."""
    lots, billed = storage.settle({1: [], 2: []}, {1: 239, 2: 1062}, {1: 2403, 2: 3034}, 0.7, date(2026, 6, 30))
    assert totals(lots) == {1: 1443, 2: 1062}
    assert billed == {1: 0.0, 2: 0.0}


def test_invoice_july_august_fifo_and_lot_dates():
    """Faktura 00033: pobór z najstarszej partii tej samej strefy, nowa partia z datą końca okresu."""
    old = {1: [Lot(date(2026, 6, 30), 1443)], 2: [Lot(date(2026, 6, 30), 1062)]}
    lots, billed = storage.settle(old, {1: 307, 2: 1030}, {1: 2275, 2: 2431}, 0.7, date(2026, 8, 31))
    assert [lot.day for lot in lots[1]] == [date(2026, 6, 30), date(2026, 8, 31)]
    assert [lot.amount for lot in lots[1]] == pytest.approx([1136, 1593], abs=1)  # 1592,5 - faktura zaokrągla w górę
    assert [lot.day for lot in lots[2]] == [date(2026, 6, 30), date(2026, 8, 31)]
    assert [lot.amount for lot in lots[2]] == pytest.approx([32, 1702], abs=1)
    assert sum(lot.amount for lot in lots[1]) == pytest.approx(2729, abs=1)
    assert sum(lot.amount for lot in lots[2]) == pytest.approx(1734, abs=1)
    assert billed == {1: 0.0, 2: 0.0}


def test_lots_expire_after_12_months():
    old = {1: [Lot(date(2025, 6, 30), 500), Lot(date(2025, 12, 31), 100)], 2: []}
    lots, billed = storage.settle(old, {1: 50, 2: 0}, {1: 0, 2: 0}, 0.7, date(2026, 8, 31))
    assert [(lot.day, lot.amount) for lot in lots[1]] == [(date(2025, 12, 31), 50.0)]  # partia z 06.2025 wygasła
    assert billed[1] == 0.0
    # w dniu końca ważności (12 miesięcy od daty wprowadzenia) partia jeszcze działa
    lots, _ = storage.settle({1: [Lot(date(2025, 6, 30), 500)], 2: []}, {1: 0, 2: 0}, {1: 0, 2: 0}, 0.7, date(2026, 6, 30))
    assert totals(lots)[1] == 500


def test_own_zone_first_then_other_zone():
    old = {1: [Lot(date(2026, 6, 30), 100)], 2: [Lot(date(2026, 6, 30), 100)]}
    lots, billed = storage.settle(old, {1: 150, 2: 20}, {1: 0, 2: 0}, 0.8, date(2026, 8, 31))
    assert totals(lots) == {1: 0, 2: 30}  # strefa 1: własne 100 + 50 ze strefy 2; strefa 2: 100 - 50 - 20
    assert billed == {1: 0.0, 2: 0.0}


def _hours(day, values, zones_idx=0):
    """24 godziny doby lokalnej ``day``: values = {godzina lokalna: (pobór, oddanie)}; strefa 1 = indeks 0."""
    start = datetime(day.year, day.month, day.day, tzinfo=TZ).astimezone(timezone.utc)
    plus, minus = [], []
    for i in range(24):
        a, b = values.get(i, (0.0, 0.0))
        t = start + timedelta(hours=i)
        z_a = [None, None, None]
        z_b = [None, None, None]
        z_a[zones_idx], z_b[zones_idx] = a, b
        plus.append(hourly.ChartPoint(start=t, zones=z_a, complete=True))
        minus.append(hourly.ChartPoint(start=t, zones=z_b, complete=True))
    return plus, minus


def _days(first, count, values_by_day):
    plus, minus = [], []
    for n in range(count):
        day = first + timedelta(days=n)
        p, m = _hours(day, values_by_day.get(n, {}))
        plus += p
        minus += m
    return plus, minus


def test_advance_sums_hourly_balances_without_daily_netting():
    """Godziny z nadwyżką i z niedoborem sumujemy osobno (a nie saldem doby); w jednej godzinie pobór i oddanie się znoszą."""
    state = storage.StorageState()
    plus, minus = _days(date(2026, 1, 1), 1, {0: {10: (0.0, 5.0), 11: (3.0, 0.0), 12: (2.0, 2.0)}})
    changed, gap = storage.advance(state, plus, minus, 0.7, 2, date(2026, 1, 1), TZ)
    assert changed and gap is None
    assert state.period_start == date(2026, 1, 1)
    assert (state.pos[1], state.neg[1]) == (3.0, 5.0)  # godzina 12: 2 - 2 = 0
    assert state.last_hour == plus[-1].start
    # ponowne wywołanie tych samych godzin nic nie zmienia
    assert storage.advance(state, plus, minus, 0.7, 2, date(2026, 1, 1), TZ) == (False, None)
    lots, billed = storage.project(state, 0.7, 2)
    assert totals(lots) == {1: 0, 2: 0}  # 0,7 * 5 = 3,5 - 3 = 0,5 -> zaokrąglone
    assert round(lots[1][0].amount, 3) == 0.5


def test_advance_settles_period_on_rollover_and_detects_gap():
    state = storage.StorageState()
    # luty (ostatni dzień okresu sty-lut) i pierwszy dzień marca: dane od 1 stycznia -> 59 dni
    plus, minus = _days(date(2026, 1, 1), 59, {0: {10: (0.0, 100.0)}, 58: {10: (4.0, 0.0)}})
    changed, gap = storage.advance(state, plus, minus, 0.8, 2, date(2026, 2, 28), TZ)  # okres sty-lut jeszcze otwarty
    assert changed and gap is None and state.period_start == date(2026, 1, 1)
    p2, m2 = _days(date(2026, 3, 1), 1, {0: {12: (10.0, 0.0)}})
    changed, gap = storage.advance(state, p2, m2, 0.8, 2, date(2026, 3, 1), TZ)
    assert changed and gap is None
    # okres sty-lut rozliczony: 0,8 * 100 - 4 = 76 (partia z 28.02.2026); w marcu trwa nowy okres
    assert [(lot.day, round(lot.amount, 3)) for lot in state.lots[1]] == [(date(2026, 2, 28), 76.0)]
    assert state.period_start == date(2026, 3, 1) and state.pos[1] == 10.0
    # brak godzin (luka) zatrzymuje liczenie i wskazuje pierwszą brakującą godzinę
    p3, m3 = _days(date(2026, 3, 3), 1, {})
    changed, gap = storage.advance(state, p3, m3, 0.8, 2, date(2026, 3, 3), TZ)
    assert not changed and gap == state.last_hour + timedelta(hours=1)


def test_advance_starts_only_from_period_start_and_ignores_incomplete_hours():
    state = storage.StorageState()
    plus, minus = _days(date(2026, 3, 5), 1, {})  # dane nie od początku okresu -> luka od 1 marca
    changed, gap = storage.advance(state, plus, minus, 0.7, 2, date(2026, 3, 5), TZ)
    assert not changed and gap == datetime(2026, 3, 1, tzinfo=TZ).astimezone(timezone.utc)
    assert storage.needed_from(state, date(2026, 3, 5), 2, TZ) == date(2026, 3, 1)
    plus, minus = _days(date(2026, 3, 1), 1, {})
    plus[5] = hourly.ChartPoint(start=plus[5].start, zones=[0.0, None, None], complete=False)
    plus, minus = plus[:6], minus[:6]  # najnowsza godzina niekompletna (jak na końcu danych portalu)
    changed, gap = storage.advance(state, plus, minus, 0.7, 2, date(2026, 3, 1), TZ)
    assert changed and gap is None and state.last_hour == plus[4].start  # liczymy tylko kompletne godziny
    assert storage.advance(state, plus, minus, 0.7, 2, date(2026, 3, 1), TZ)[0] is False


def test_state_roundtrip():
    state = storage.StorageState()
    state.lots[1] = [Lot(date(2026, 6, 30), 12.5)]
    state.period_start = date(2026, 7, 1)
    state.pos[2], state.neg[1] = 3.0, 4.0
    state.last_hour = datetime(2026, 7, 1, 5, tzinfo=timezone.utc)
    again = storage.StorageState.from_dict(state.to_dict())
    assert again.to_dict() == state.to_dict()
    assert again.lots[1][0].day == date(2026, 6, 30) and again.last_hour == state.last_hour


def test_period_change_is_ratio_times_negative_minus_positive():
    state = storage.StorageState()
    state.pos = {1: 100.0, 2: 40.0}
    state.neg = {1: 300.0, 2: 10.0}
    change = storage.period_change(state, 0.7)
    assert change == {1: pytest.approx(110.0), 2: pytest.approx(-33.0)}
    assert sum(change.values()) == pytest.approx(77.0)
