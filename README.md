# Energa Mój Licznik – integracja Home Assistant

Nieoficjalna integracja pobierająca dane z portalu [Mój Licznik](https://mojlicznik.energa-operator.pl/)
(Energa-Operator) i udostępniająca je w Home Assistant. Obsługuje wiele PPE oraz prosumentów
(A+ pobór / A− oddanie, strefy 1 i 2, taryfy jednostrefowe i wielostrefowe, np. G12W).

**Status: wersja robocza (0.4.4).** Użycie na własne ryzyko; projekt nie jest związany z Energą.
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
| Saldo liczników strefa 1, strefa 2, łącznie | prosumenci | **wyliczane:** pobór − oddanie ze stanów liczników (bez `state_class`, może maleć) |

Odczyty zmieniają się raz na dobę (portal podaje stan z północy). Atrybuty: `reading_time` (czas odczytu, UTC),
`last_refresh` (ostatnie pobranie z portalu, UTC).

Wszystkie „bilanse” i „salda liczników” to **fizyczna różnica pobór − oddanie** (bez współczynnika 0,7/0,8, bez podziału na strefy w rozliczeniu i bez okresu
rozliczeniowego). Rozliczenie według zasad net metering robią sensory „Magazyn energii” i „Saldo okresu rozliczeniowego” (niżej).

### Zużycie dzienne
| Encja | Dla kogo | Skąd |
|---|---|---|
| Pobór dziś, Pobór wczoraj | wszyscy | **wyliczane:** suma kompletnych godzin z wykresu dobowego portalu (A+) |
| Oddanie dziś, Oddanie wczoraj | prosumenci | j.w. (A−) |
| Bilans dziś, Bilans wczoraj | prosumenci | **wyliczane:** pobór − oddanie |

Bez `state_class` (do wyświetlania i automatyzacji, nie do panelu Energia). Atrybuty: `date`, `complete_hours`,
`zone_1`, `zone_2` (rozbicie na strefy).

### Bilans miesięczny
| Encja | Dla kogo | Skąd |
|---|---|---|
| Bilans ten miesiąc, Bilans poprzedni miesiąc | prosumenci | wykres roczny portalu (`mo=BP`, pobór − oddanie); ujemny = więcej oddano niż pobrano |

Bez `state_class`. Atrybuty: `month` (RRRR-MM), `complete`. Bieżący miesiąc zmienia się z dnia na dzień.

### Magazyn energii u operatora (prosumenci)
| Encja | Rodzaj | Opis |
|---|---|---|
| Magazyn energii strefa 1, Magazyn energii strefa 2 | sensor | **wyliczany model** stanu magazynu (opusty) w strefie |
| Magazyn energii razem | sensor | suma obu stref |
| Ustaw magazyn energii strefa 1, strefa 2 | number | ręczne ustawienie stanu strefy (kWh) |
| Saldo okresu rozliczeniowego | sensor | zmiana magazynu w bieżącym okresie: współczynnik × suma godzin z nadwyżką − suma godzin z niedoborem (atrybuty: strefa 1 i 2, sumy sald, okres) |

Model odtwarza rozliczenie Energi z faktury (sprawdzony na fakturach za 2026 r., zgodność co do pojedynczych kWh):
1. Bilans liczony jest **w każdej godzinie osobno dla każdej strefy** (pobór − oddanie, z danych godzinowych portalu).
2. W okresie rozliczeniowym (domyślnie 2 miesiące: sty–lut, mar–kwi, …; wybór w opcjach) sumowane są **osobno** godziny z nadwyżką
   i godziny z niedoborem – bez salda dobowego. Nadwyżka dopisuje się do magazynu pomnożona przez współczynnik
   (80 % do 10 kW, 70 % powyżej) jako partia z datą końca okresu; niedobór pomniejsza magazyn w całości.
3. Pobór jest pokrywany najpierw z tej samej strefy (od najstarszej partii, FIFO), a potem z nadwyżek drugiej strefy;
   partie wygasają po 12 miesiącach. Czego zabraknie, trzeba kupić (atrybut `to_pay_kwh`).

Wartość sensora to stan, jaki wyszedłby z rozliczenia bieżącego okresu „teraz” (podgląd w trakcie okresu). Atrybuty:
`lots` (partie: strefa, data wprowadzenia, kWh), `settled_kwh` (po ostatnim zamkniętym okresie), `to_pay_kwh`,
`positive_balance_kwh` / `negative_balance_kwh` (sumy godzinowych sald bieżącego okresu, przed współczynnikiem),
`period_start`, `period_end`, `period_months`, `ratio_percent`, `calculated_until`, `last_set`, a przy braku danych `data_gap_from`.
Przy pierwszym starcie integracja dociąga dane godzinowe od początku bieżącego okresu rozliczeniowego (do kilkudziesięciu zapytań).

**Stan początkowy ustaw z faktury** („Magazyn energii po rozliczeniu”, partie z datami wprowadzenia), osobno dla każdej strefy:
usługa `energa_moj_licznik.set_storage` na encji strefy (`value` w kWh, opcjonalnie `date` = data wprowadzenia z faktury
i `append: true`, by dodać kolejną partię zamiast zastąpić stan) albo encja „Ustaw magazyn energii strefa N” (jedna partia
z końca ostatniego okresu). `energa_moj_licznik.reset_storage` zeruje strefę (na encji „razem” – obie). Przykład z faktury:
strefa 1: partia 1000 kWh z datą 30.06.2026, potem druga partia 500 kWh z datą 31.08.2026 z `append: true`.

To model, nie odczyt z portalu – po każdej fakturze warto porównać wynik i skorygować. Sensory nie mają `state_class`,
nie dodawaj ich do panelu Energia. Gdy portal nie ma danych godzinowych za część okresu, liczenie wstrzymuje się
(atrybut `data_gap_from`) – ustaw wtedy stan z faktury.

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
Skrypty `scripts/probe.py` i `scripts/probe_chart.py` odpytują portal i zapisują zanonimizowane odpowiedzi do `probe_output/`; `scripts/probe_year.py` (miesięczne A+/A− per strefa) i `scripts/probe_hours.py` (godzinowe A+/A−, do porównania z fakturą) zapisują tylko liczby:
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
