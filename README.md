# Energa Mój Licznik – integracja Home Assistant

Nieoficjalna integracja pobierająca dane z portalu [Mój Licznik](https://mojlicznik.energa-operator.pl/)
(Energa-Operator) i udostępniająca je w Home Assistant. Obsługuje wiele PPE oraz prosumentów
(A+ pobór / A− oddanie, strefy 1 i 2, taryfy jednostrefowe i wielostrefowe, np. G12W).

**Status: wersja robocza.** Użycie na własne ryzyko; projekt nie jest związany z Energą.
Portal nie ma oficjalnego API – zmiana po stronie operatora może zatrzymać integrację.

## Instalacja
**HACS:** HACS → Integracje → ⋮ → Własne repozytoria → adres tego repozytorium, kategoria „Integracja”;
zainstaluj „Energa Mój Licznik” i zrestartuj Home Assistanta.
**Ręcznie:** skopiuj `custom_components/energa_moj_licznik` do `/config/custom_components/` i zrestartuj Home Assistanta.

Następnie: Ustawienia → Urządzenia i usługi → Dodaj integrację → „Energa Mój Licznik”.

## Konfiguracja
1. Login i hasło do portalu Mój Licznik.
2. Wybór punktów poboru (PPE) z konta.
3. Nazwy liczników – domyślnie numer licznika (z portalu), można wpisać własną nazwę (używana jako nazwa urządzenia i encji).
4. Tylko dla prosumentów: próg zwrotu z magazynu energii (net metering) – **80 %** (instalacje do 10 kW, wartość domyślna)
   albo **70 %** (powyżej 10 kW). Wybór jest osobny dla każdego licznika.

Opcje integracji (⚙ przy wpisie) pozwalają później zmienić nazwy i progi zwrotu; integracja przeładowuje się po zapisaniu,
stan magazynu energii zostaje zachowany. Przy wygaśnięciu hasła HA poprosi o ponowne uwierzytelnienie.

## Encje
Wszystkie encje należą do urządzenia danego licznika. Dane z portalu odświeżane są co godzinę (portal publikuje dane godzinowe
z ok. 1 h opóźnieniem; godziny niekompletne lub szacowane są pomijane do czasu potwierdzenia).
Wszystkie wartości w kWh.

### Stany licznika
| Encja | Dla kogo | Skąd |
|---|---|---|
| Pobór strefa 1, Pobór strefa 2 | wszyscy | ostatni odczyt licznika (A+), `total_increasing` |
| Oddanie strefa 1, Oddanie strefa 2 | prosumenci | ostatni odczyt licznika (A−), `total_increasing` |
| Bilans strefa 1, Bilans strefa 2, Bilans łącznie | prosumenci | **wyliczane:** pobór − oddanie ze stanów liczników (bez `state_class`, może maleć) |

Odczyty zmieniają się raz na dobę (portal podaje stan z północy). Atrybuty: `reading_time` (czas odczytu, UTC),
`last_refresh` (ostatnie pobranie z portalu, UTC).

### Zużycie dzienne
| Encja | Dla kogo | Skąd |
|---|---|---|
| Pobór dziś, Pobór wczoraj | wszyscy | **wyliczane:** suma kompletnych godzin z wykresu dobowego portalu (A+) |
| Oddanie dziś, Oddanie wczoraj | prosumenci | j.w. (A−) |
| Bilans dziś, Bilans wczoraj | prosumenci | **wyliczane:** pobór − oddanie |

Bez `state_class` (do wyświetlania i automatyzacji, nie do panelu Energia). Atrybuty: `date`, `complete_hours`,
`zone_1`, `zone_2` (rozbicie na strefy).

### Magazyn energii u operatora (prosumenci)
| Encja | Rodzaj | Opis |
|---|---|---|
| Magazyn energii | sensor | **wyliczany model** stanu magazynu (opusty), patrz niżej |
| Ustaw magazyn energii | number | ręczne ustawienie stanu (kWh) |

Usługi: `energa_moj_licznik.set_storage` (`value` w kWh) i `energa_moj_licznik.reset_storage` (zeruje stan).

Model liczy stan doba po dobie (strefa czasowa Europe/Warsaw): bilans każdej godziny (pobór − oddanie), potem suma bilansów w dobie.
Nadwyżka oddana w dobie trafia do magazynu pomniejszona o próg zwrotu (80 % lub 70 %); niedobór jest pobierany z magazynu
(nie poniżej 0). Doba jest rozliczana po zakończeniu – stan jest na koniec ostatniej rozliczonej doby (atrybut `calculated_until`).
Pozostałe atrybuty: `ratio_percent`, `credited_since_set`, `drawn_since_set`, `last_set`.
Liczenie startuje od momentu dodania integracji (bez danych historycznych), więc **przy pierwszym użyciu ustaw stan początkowy**
(encja „Ustaw magazyn energii” albo usługa `set_storage`). To model, a nie odczyt z portalu – po rozliczeniu z operatorem
warto go skorygować. Model zakłada jedną pulę dla wszystkich stref taryfy.

### Statystyki godzinowe (panel Energia)
Integracja importuje godzinowe statystyki zewnętrzne do rejestratora (`recorder`):

| Identyfikator statystyki | Opis |
|---|---|
| `energa_moj_licznik:<PPE>_pobor` | pobór, suma stref |
| `…_pobor_strefa_1`, `…_pobor_strefa_2` | pobór w strefie |
| `…_oddanie`, `…_oddanie_strefa_1`, `…_oddanie_strefa_2` | oddanie (prosumenci) |

Przy pierwszym uruchomieniu importowane jest 14 dni wstecz, później dogrywane są nowe godziny (do 31 dni po dłuższej przerwie).

**Panel Energia:** Ustawienia → Panele → Energia → Sieć elektroenergetyczna → „Dodaj źródło” – wybierz statystyki
„… pobór strefa 1 (godzinowo)” i „… pobór strefa 2 (godzinowo)”; analogicznie „zwrot do sieci” dla oddania.
Używaj statystyk godzinowych, a nie sensorów stanów (te zmieniają się raz na dobę – grozi podwójne liczenie).

## Karta Lovelace
`cards/energa-meter-card.js` – karta imitująca „Ostatnie odczyty licznika” z portalu (mechaniczny licznik z animacją).
Skopiuj plik do `/config/www/energa-meter/` i dodaj zasób Lovelace `/local/energa-meter/energa-meter-card.js` (typ: moduł JavaScript).
Przy aktualizacji pliku dopisz do adresu zasobu nową wartość `?v=…`, bo przeglądarka mocno go buforuje.

## Diagnostyka
Skrypty `scripts/probe.py` i `scripts/probe_chart.py` odpytują portal i zapisują zanonimizowane odpowiedzi do `probe_output/`:
```
pip install aiohttp
$env:ENERGA_USER="login"; $env:ENERGA_PASS="haslo"; py scripts/probe.py
```
Przed udostępnieniem wyników sprawdź je – maskowanie nie obejmuje wszystkiego.

## Struktura repozytorium
- `custom_components/energa_moj_licznik/` – integracja (`api.py` klient portalu, `hourly.py` logika godzinowa, `stats.py` import statystyk,
  `energy_store.py` model magazynu, `coordinator.py`, `sensor.py`, `number.py`, `config_flow.py`)
- `cards/` – karta Lovelace
- `scripts/` – diagnostyka portalu
- `tests/` – testy czystej logiki (`py -m pytest -q tests`)
