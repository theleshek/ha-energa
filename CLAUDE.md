# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Projekt: niestandardowa integracja Home Assistant (HACS-ready) „Energa Mój Licznik” – pobiera dane z nieoficjalnego
API portalu https://mojlicznik.energa-operator.pl/ (Energa-Operator). Użytkownik (po polsku) jest prosumentem,
taryfa G12W, HAOS na VM w Hyper-V. Rozmowa i komunikaty w integracji/kartach są po polsku.
HAOS (Core 2026.9.x). Repozytorium jest **publiczne** (`theleshek/ha-energa`).
Gałęzie: praca na `dev`, `main` scalamy rzadko (wydania, przez PR). Commit/push/PR tylko na wyraźną prośbę; commity z trailerami z system-reminder.

## Środowisko pracy (ważne)
- Sesja działa **lokalnie na Windowsie użytkownika** (repo w `C:/Users/Leszek/GitHub/ha-energa`, **poza OneDrive** – synchronizacja OneDrive
  blokowała `.git/objects`; nie przenoś repo z powrotem). Można ją wywoływać zdalnie z aplikacji Claude na Androidzie (Remote Control).
  Nadal **nie mamy dostępu do portalu ML ani do HA użytkownika**: rzeczywiste odpowiedzi portalu dostajemy tylko od użytkownika
  (HAR z Chrome w `har/` – ignorowany przez git – albo wyniki `scripts/probe*.py`: `py scripts/probe.py`, zmienne `$env:ENERGA_USER`,
  `$env:ENERGA_PASS`). Po jego stronie: kopiuje `custom_components/energa_moj_licznik` do `/config/custom_components/`, restartuje HA, wkleja logi.
- Przy odczycie HAR/HTML nie wypisuj w rozmowie e-maila, adresu, PPE ani numeru licznika (maskuj cyfry).
- Pliki repo mają zakończenia linii CRLF – przy skryptowej edycji zachowaj je.
- **Repo jest publiczne: nie wpisuj tu danych z logów/HAR/faktur/raportów** (e-mail, hasło, numer PPE, adres, numer fabryczny licznika,
  wewnętrzne identyfikatory portalu `id`/`mpc`/`dev`, kod pocztowy, numery faktur/klienta). W testach i dokumentacji używaj wartości
  fikcyjnych (np. PPE `590000000000000001`, `mpc=100001`, `id=100002`). `probe_output/`, `har/` i `*.har` są w `.gitignore`;
  skrypty probe maskują dane, ale nie wszystko.
- Użytkownik pracuje w PowerShell 5.1 (brak `&&`; polecenia w osobnych liniach). `gh` (GitHub CLI) jest w `C:\Program Files\GitHub CLI\gh.exe`
  (może nie być w PATH sesji). Pliki z `.gitignore` (`har/`, `probe_output/`) są tylko lokalne – nie ma ich w chmurze.
- Nie wymyślaj struktur odpowiedzi portalu – najpierw dane od użytkownika, parsowanie pisz tolerancyjnie.

## Komendy
```
pip install pytest aiohttp tzdata                # środowisko testowe (HA NIE jest zainstalowany; tzdata potrzebne na Windowsie)
python -m pytest -q tests                        # wszystkie testy (na Windowsie też: py -m pytest)
python -m pytest -q tests -k serials             # pojedynczy test
for f in custom_components/energa_moj_licznik/*.py; do python -m py_compile "$f"; done   # w Git Bash
node --check cards/energa-meter-card.js          # wymaga Node.js (CI to robi; lokalnie może go nie być)
```
- Test karty (Chromium) był robiony w poprzednim, chmurowym środowisku; lokalnie wymaga `pip install pillow playwright` i przeglądarki.
- Moduły zależne od HA (`__init__`, `config_flow`, `coordinator`, `sensor`, `stats`) da się tylko skompilować (`py_compile`),
  nie uruchomić. Testowalna logika jest celowo w czystych modułach: `api.py`, `hourly.py`, `const.py`.
- Testy ładują pakiet bez `__init__.py`: `types.ModuleType("energa_pkg")` z `__path__`, potem `importlib.import_module("energa_pkg.api")`.
  Testy HTTP stawiają atrapę portalu na `aiohttp.web` i nadpisują `api.BASE_URL`; cookie jar w testach: `CookieJar(unsafe=True)` (IP).
- Test karty (Chromium): atrapa `hass` z `states`, `config.time_zone`. Karta jest w `cards/energa-meter-card.js`
  (katalog to `cards/`, nie `card/`; CI w `.github/workflows` – `tests.yml`, `validate.yml` z hassfest i HACS – już istnieje).

## Struktura repo
- `custom_components/energa_moj_licznik/` – integracja (HACS instaluje tylko ten katalog). `manifest.json`: klucze po `domain`, `name`
  posortowane alfabetycznie (wymóg hassfest), wersja podbijana przy wydaniach.
- `cards/energa-meter-card.js` – karta Lovelace. HACS jej **nie** instaluje (leży poza `custom_components/`); użytkownik kopiuje ją ręcznie do
  `/config/www/energa-meter/` i dodaje zasób `/local/energa-meter/energa-meter-card.js?v=…` (typ: module). Ścieżka w CI musi zgadzać się z `cards/`.
- `hacs.json` – tylko `name` i `render_readme` (pole `homeassistant` z min. wersją powodowało błąd walidacji HACS – nie dodawać bez sprawdzenia).
- `brand/` w integracji – ikony. `scripts/` – diagnostyka portalu. `tests/` – pytest. `.github/workflows/` – `tests.yml`, `validate.yml`.

## Architektura
- `api.py` – `EnergaClient` (aiohttp): logowanie, `async_get_meters`, `async_get_readings`, `async_get_day_chart`; modele
  `Meter` (m.in. `serial` – numer licznika z `UserAccount.do`, atrybut `meterSN`), `Readings`; wyjątki `EnergaError` → `EnergaHttpError`(status) / `EnergaAuthError`.
- `hourly.py` – czysta logika: `ChartPoint`, `accumulate` (sumy narastające, przerywa na pierwszej niekompletnej godzinie),
  `daily_usage`.
- `stats.py` – import godzinowych statystyk zewnętrznych do recordera (`energa_moj_licznik:<PPE>_pobor[_strefa_N]`, `_oddanie…`);
  suma narastająca startuje od ostatniej zapisanej statystyki (`get_last_statistics`), backfill `BACKFILL_DAYS`=14, dogrywanie
  do `MAX_CATCHUP_DAYS`=31; metadane budowane adaptacyjnie (pola `mean_type`/`unit_class` zależą od wersji HA).
- `coordinator.py` – `DataUpdateCoordinator` co 1 h: login (jeśli sesja nieważna) → odczyty → dla każdego licznika
  `async_sync_hourly`; błąd danych godzinowych nie wyłącza sensorów stanów. `MeterData(readings, daily)`.
- `sensor.py` – sensory stanów (A+1, A+2, A−1, A−2; `total_increasing`) i dzienne (Pobór/Oddanie/Bilans dziś/wczoraj, bez state_class;
  A−/bilans tylko dla prosumenta). Atrybuty `reading_time` (UTC) i `last_refresh` (UTC). Dla prosumenta także „Bilans ten/poprzedni miesiąc”
  (`EnergaMonthlySensor`, wykres YEAR `mo=BP`, `MeterData.monthly`, `hourly.monthly_balance`; w styczniu drugie zapytanie o poprzedni rok).
- `config_flow.py` – login/hasło → wybór PPE (`cv.multi_select`) → nazwy własne; options flow do zmiany nazw; reauth.
  Kroki: login → PPE → nazwy → (dla prosumentów) próg zwrotu z magazynu 70/80 % → `entry.options[storage_ratios]`.
  Nazwy: `entry.options[names]` > `entry.data[names]` > nazwa z portalu > numer licznika (portal zwykle nie podaje nazwy; domyślnie
  w formularzu wpisywany jest numer licznika, użytkownik może nadać własną) (podmieniane przez `dataclasses.replace` w `__init__`).
- `storage.py` (czysta logika) + `energy_store.py` (trwały stan w `helpers.storage.Store`, klucz `storage2_…`) – magazyn energii u operatora (net metering),
  model **zweryfikowany na trzech kolejnych fakturach Energi co do kWh** (patrz „Zweryfikowane fakty”): bilanse godzinowe per strefa, sumy dodatnich/ujemnych
  godzin osobno w okresie rozliczeniowym (`storage.period_bounds`, okresy od stycznia, domyślnie 2 mies., opcja `storage_period`), nadwyżka × współczynnik →
  partia (data = koniec okresu, ważna 12 mies., FIFO), pobór z tej samej strefy, potem z drugiej, reszta = `to_pay`. `advance()` wlicza kompletne godziny
  po kolei (luka → stop i `gap_from`), `settle()` zamyka okres, `project()` daje stan „gdyby okres skończył się teraz”. Przy starcie koordynator dociąga
  dane od początku okresu (`needed_from`). Sensory „Magazyn energii strefa 1/2/razem”, number „Ustaw magazyn energii strefa N”, usługi
  `set_storage` (value, date, append) / `reset_storage`. Współczynnik z opcji `storage_ratios` (per licznik, 70 % / 80 %: do 10 kW → 80 %, powyżej → 70 %;
  domyślnie 80 % dla nowych wpisów; wpisy sprzed tej opcji zachowują starą wspólną `storage_ratio`, domyślnie 70 %).
- `cards/energa-meter-card.js` – karta Lovelace (licznik mechaniczny jak na portalu, animacja przewijania, opcje `show_last_change`,
  `show_last_refresh`, `animate_on_load`). Zasób użytkownika: `/local/energa-meter/energa-meter-card.js?v=…`.
- `brand/` – ikony (nowsze HA czytają ikony integracji custom stąd). `scripts/probe.py`, `scripts/probe_chart.py` – diagnostyka portalu.

## Zweryfikowane fakty o portalu (z raportów/HAR użytkownika)
- **Logowanie**: GET `/dp/UserLogin.do` → z formularza pole ukryte `_antixsrf`; POST (form) `selectedForm=1, save=save, _antixsrf, clientOS=web,
  j_username, j_password`; przekierowanie na `UserData.do`. Poprawne logowanie weryfikuj GET `/dp/resources/user/data` (JSON `success:true`).
  Przy aktywnej sesji `UserLogin.do` **nie ma formularza** → najpierw sprawdź `_session_valid()`. Portal przy błędach zwraca **HTTP 200 + HTML**
  (np. tytuł „Błąd - Portal Klienta”), więc odpowiedź nie-JSON to normalny scenariusz (łap `ValueError`).
- **`/dp/resources/user/data`**: `agreementPoints[{code=PPE, type="Wytwórca"…}]`, `meterPoints[{id, name, dev, tariff, lastMeasurements[{date,zone,value,precision:4,digitsBeforeSep:8}],
  meterObjects[{obis,label,availableCharts}]…}]`. Format pól `date`/`zone` w `lastMeasurements` był w raportach zamaskowany – parsery są tolerancyjne
  (`_zone_key` regexem na „A+ strefa 1”, `_parse_date` ms-epoch lub ISO); **jeśli sensory będą puste, podejrzewaj to miejsce**.
- **Wykresy**: GET `/dp/resources/chart?mainChartDate=<ms północy Europe/Warsaw>&type=DAY|WEEK|MONTH|YEAR&meterPoint=<mpc>&mo=A+|A-|BP`
  (`A+` pobór, `A-` oddanie, `BP` bilans = A+ − A−). Odpowiedź: `response.mainChart[{tm(ms,str), zones[3] (null = strefa nieaktywna), est, cplt}]`, jednostka kWh;
  DAY = godziny (dane do ok. 1 h wstecz, ostatnia godzina bywa niekompletna: `cplt:false`/`est:true`), MONTH = jedna pozycja na **dobę**,
  YEAR = jedna pozycja na **miesiąc** (`tm` = północ 1. dnia w Europe/Warsaw; bieżący miesiąc też jest, częściowy, z `cplt:true`);
  `mainChartDate` dla YEAR = 1 stycznia, dla MONTH = 1. dzień miesiąca. Dla `mo=BP` `zones` ma **jeden** element (suma, etykieta „Wartości”),
  ujemny = nadwyżka oddana. Panel „Energia całkowita we wskazanym okresie” na stronie Wykresy to suma pozycji w zaznaczonym zakresie
  (sprawdzone: −9853,349 kWh = suma miesięcy kwiecień–październik z `mo=BP`).
- **`meterPoint` to `mpc`, NIE `meterPoints[].id`**: bierzemy z HTML `/dp/UserAccount.do` (odnośniki `EnergyIndex.do?mpc=<mpc>&ppe=<PPE>`), mapowanie po PPE.
  Zapas: `Meter.chart_ids` (kolejno `mpc`, `dev`, `id`, inne pola) – klient próbuje i zapamiętuje działające (400/404 = zły identyfikator).

- **Rozliczenie net metering u Energi (faktury użytkownika dla G12W, okresy 2-miesięczne, instalacja powyżej 10 kW → współczynnik 0,7)**: godzinowe salda per strefa
  (L1 = strefa 1, L2 = strefa 2); „suma godzinowych sald dodatnich/ujemnych” za cały okres (nie za dobę); magazyn = partie z datą wprowadzenia (koniec okresu);
  nowa partia = 0,7 × suma ujemnych; pobór z magazynu tej samej strefy (najstarsza partia), a niedobór strefy bez zapasu jest pokrywany nadwyżką drugiej strefy
  (faktura 00031: nadwyżka L1 656 pokryła niedobór L2 956, do zapłaty 300 kWh). Sumy godzinowych sald z faktur odtwarza się z godzinowych A+/A− portalu
  (`scripts/probe_hours.py`), odczyty brutto na fakturze = portal. Ustawa o OZE art. 4: godzinowe bilansowanie ust. 2b, Wi ust. 3, ważność 12 mies. i FIFO ust. 5–5a,
  kolejność stref ust. 5b–5e. Nie znaleziono żadnej warstwy dobowej.

## Co NIE działało (nie powtarzaj bez powodu)
- Stare/zgadnięte API: POST `UserLogin.do` bez `_antixsrf`, pola `j_username/j_password` + `clientOS=ios` → strona błędu. `UserData.do?clientOS=ios` zwraca HTML, nie JSON.
- Zapytania wykresów z parametrami `mainChartMeterPoint/mainChartType/mainChartMeterObject`, `from`, `meterObject`, `mo=<kod OBIS>`; oraz `meterPoint=<id z user/data>` (id=100002 → 404 „brak Punktu Pomiaru”).
  Dane godzinowe istnieją tylko dla sum (OBIS 1.8.0/2.8.0 = `mo=A+`/`A-`); strefy osobno mają tylko WEEK/MONTH/YEAR – strefy bierzemy z tablicy `zones` w DAY.
- HA: `session.headers.update(...)` na sesji z `async_create_clientsession` → `AttributeError` (mappingproxy); nagłówek `User-Agent` przeglądarki podawaj **w każdym żądaniu**.
  Sesja z własnym `cookie_jar=aiohttp.CookieJar()`. Schemat `vol.All(vol.Coerce(list), [vol.In(..)])` w config flow → „Unknown error”; używaj `cv.multi_select`.
- Drugi login zaraz po pierwszym (koordynator po setupie) → „Nie znaleziono tokenu _antixsrf” – patrz wyżej (sprawdzaj sesję).
- `ConfigEntryAuthFailed` bez implementacji reauth = ślepy zaułek; AuthError rzucamy tylko gdy portal wyraźnie odrzuca dane (CAPTCHA/limity/5xx → `EnergaError`, ponowienie).
- Skrypty probe maskowały liczby ≥6 cyfr (ukryło `id`/`mpc`) i pierwsze wersje strzelały w złe parametry – kosztowało kilka rund; przy nowych próbach
  najpierw HAR z przeglądarki (kod JS portalu: `js/app/chart.js` buduje parametry), potem skrypt.
- Karta: nie renderuj `innerHTML` przy każdym `set hass` (niszczy animację) – DOM budowany raz, cyfry to paski 0–9 przesuwane `translateY`.
  HA mocno cache'uje JS: po zmianie pliku podbij `?v=` w zasobach (wersja karty jest w konsoli: `ENERGA-METER-CARD vX`). `reading_time` jest w UTC – karta formatuje do strefy HA.
- Walidacja HACS: pole `"homeassistant"` w `hacs.json` i niesortowane klucze `manifest.json` powodowały błędy (`hacsjson`, `integration_manifest`);
  `hacs/action` wymaga też tematów i opisu repo na GitHubie. `git filter-branch` bywa blokowany przez uprawnienia – przepisywanie historii robi
  użytkownik sam (`git filter-repo`) albo zakłada repo od nowa z jednym commitem.
- Panel Energia: nie mieszaj sensorów stanów licznika (zmieniają się raz na dobę o północy) ze statystykami godzinowymi – podwójne liczenie; sensory dzienne celowo bez `state_class`.
- Sensory szablonowe użytkownika z `float` bez wartości domyślnej sypały błędami przy `unavailable` – zaleca się `availability` (`has_value`) zamiast `float(0)` (fałszywy „reset” dla total_increasing).

## Stan i pomysły na dalej
- Wg użytkownika wszystko działa (logowanie, sensory, import godzinowy, karta). Niezweryfikowane w prawdziwym HA: dokładne pola metadanych statystyk w nowych wersjach HA.
- W toku (gałąź `dev`): wydanie przez HACS (workflow już są), po wdrożeniu u użytkownika porównanie sensorów magazynu z kolejną fakturą.
  Później: README po angielsku, rejestracja karty przez samą integrację (żeby nie kopiować jej ręcznie). Nieobsłużone: zmiana współczynnika w trakcie okresu, inne długości okresu niż wyrównane do stycznia, ust. 11 art. 4.
- Zrobione na `dev`: model magazynu per strefa zgodny z fakturą, próg magazynu per licznik (70/80 %), domyślna nazwa urządzenia = numer licznika (`meterSN`), README po polsku z listą sensorów,
  sensory „Bilans ten/poprzedni miesiąc”.
