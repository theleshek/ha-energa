# Energa Mój Licznik – integracja Home Assistant

Nieoficjalna integracja pobierająca dane z portalu [Mój Licznik](https://mojlicznik.energa-operator.pl/)
(Energa-Operator) i udostępniająca je jako sensory w Home Assistant. Obsługuje wiele PPE
oraz prosumentów (A+ pobór / A− oddanie, strefy 1 i 2).

**Status: wersja robocza (0.3.0).** Użycie na własne ryzyko; projekt nie jest związany z Energą.

## Struktura
- `custom_components/energa_moj_licznik/` – integracja (config flow, koordynator, sensory)
- `cards/energa-meter-card.js` – karta imitująca „Ostatnie odczyty licznika”
- `scripts/probe.py` – diagnostyka portalu (anonimizuje wyniki)

## Diagnostyka
```
pip install aiohttp
ENERGA_USER=login ENERGA_PASS=haslo python scripts/probe.py
```

## Dane i sensory
- **Stany licznika** (A+/A− strefa 1 i 2, kWh, `total_increasing`) – aktualizowane z portalu co godzinę.
- **Zużycie dzienne**: „Pobór dziś/wczoraj”, a dla prosumenta także „Oddanie dziś/wczoraj” i „Bilans dziś/wczoraj”
  (pobór − oddanie). Liczone z kompletnych godzin; atrybuty: `date`, `complete_hours`, `zone_1`, `zone_2`.
- **Statystyki godzinowe** (do panelu Energia i wykresów) importowane do rejestratora jako statystyki zewnętrzne:
  `energa_moj_licznik:<PPE>_pobor`, `_pobor_strefa_1`, `_pobor_strefa_2` oraz `_oddanie…` (prosument).
  Przy pierwszym uruchomieniu importowanych jest 14 dni wstecz; później dogrywane są nowe godziny.

### Panel Energia
Ustawienia → Panele → Energia → Sieć elektroenergetyczna → „Dodaj źródło”: wybierz statystyki
`… pobór strefa 1 (godzinowo)` i `… pobór strefa 2 (godzinowo)`; analogicznie „zwrot do sieci” dla oddania.
(Wybierz statystyki godzinowe, a nie sensory stanów – te zmieniają się raz na dobę.)

Portal publikuje dane godzinowe z ok. 1 h opóźnieniem; niekompletne (szacowane) godziny są pomijane do czasu
potwierdzenia.


## Magazyn energii u operatora (prosumenci)
Sensor **Magazyn energii** (kWh) liczy stan doba po dobie: najpierw bilans każdej godziny (pobór − oddanie),
potem suma bilansów w dobie. Gdy w dobie powstała nadwyżka oddana, trafia do magazynu pomniejszona o współczynnik
(opcje integracji → „Zwrot z magazynu energii”, domyślnie 70 %); gdy niedobór, jest pobierany z magazynu (nie poniżej 0).
Doba jest rozliczana po zakończeniu (stan jest na koniec ostatniej rozliczonej doby, atrybut `calculated_until`).
Liczenie startuje od momentu dodania integracji, więc **przy pierwszym użyciu ustaw stan początkowy**:
encją „Ustaw magazyn energii” albo usługą `energa_moj_licznik.set_storage` (`value` w kWh);
`energa_moj_licznik.reset_storage` zeruje stan. Wartość to model, nie odczyt z portalu – po rozliczeniu
z operatorem warto ją skorygować. Sensor nie ma `state_class`, nie dodawaj go do panelu Energia.
