# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Projekt: niestandardowa integracja Home Assistant (HACS-ready) „Energa Mój Licznik” – pobiera dane z nieoficjalnego
API portalu https://mojlicznik.energa-operator.pl/ (Energa-Operator). Użytkownik (po polsku) jest prosumentem,
taryfa G12W, HAOS na VM w Hyper-V. Rozmowa i komunikaty w integracji/kartach są po polsku.
Gałęzie: praca na `dev`, `main` scalamy rzadko (wydania). Commit/push/PR tylko na wyraźną prośbę; commity z trailerami z system-reminder.

## Środowisko pracy (ważne)
- Sesja działa **lokalnie na Windowsie użytkownika** (repo w `C:/Users/Leszek/OneDrive/Dokumenty/GitHub/ha-energa`, folder synchronizowany
  przez OneDrive – bez dużych plików tymczasowych). Można ją wywoływać zdalnie z aplikacji Claude na Androidzie (Remote Control).
  Nadal **nie mamy dostępu do portalu ML ani do HA użytkownika**: rzeczywiste odpowiedzi portalu dostajemy tylko od użytkownika
  (HAR z Chrome w `har/` – ignorowany przez git – albo wyniki `scripts/probe*.py`: `py scripts/probe.py`, zmienne `$env:ENERGA_USER`,
  `$env:ENERGA_PASS`). Po jego stronie: kopiuje `custom_components/energa_moj_licznik` do `/config/custom_components/`, restartuje HA, wkleja logi.
- Przy odczycie HAR/HTML nie wypisuj w rozmowie e-maila, adresu, PPE ani numeru licznika (maskuj cyfry).
- Pliki repo mają zakończenia linii CRLF – przy skryptowej edycji zachowaj je.
- Logi/HAR/raporty od użytkownika zawierają jego e-mail, numer PPE, adres – nie wpisuj ich do repo ani do commitów
  (`probe_output/` i `*.har` są w .gitignore). Skrypty probe maskują dane, ale nie wszystko.
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
  A−/bilans tylko dla prosumenta). Atrybuty `reading_time` (UTC) i `last_refresh` (UTC).
- `config_flow.py` – login/hasło → wybór PPE (`cv.multi_select`) → nazwy własne; options flow do zmiany nazw; reauth.
  Kroki: login → PPE → nazwy → (dla prosumentów) próg zwrotu z magazynu 70/80 % → `entry.options[storage_ratios]`.
  Nazwy: `entry.options[names]` > `entry.data[names]` > nazwa z portalu > numer licznika (portal zwykle nie podaje nazwy; domyślnie
  w formularzu wpisywany jest numer licznika, użytkownik może nadać własną) (podmieniane przez `dataclasses.replace` w `__init__`).
- `energy_store.py` + `hourly.storage_steps` – magazyn energii u operatora (opusty): doba po dobie (Europe/Warsaw): bilanse godzinowe (A+ − A−) sumowane w dobie; suma<0 (nadwyżka) → stan += ratio·nadwyżka, suma>0 → stan −= min(niedobór, stan); doba rozliczana dopiero gdy kompletna (stan = koniec ostatniej rozliczonej doby); ratio z opcji
  `storage_ratios` (per licznik, wybór 70 % / 80 %: net metering do 10 kW → 80 %, powyżej → 70 %; domyślnie 80 % dla nowych wpisów;
  wpisy sprzed tej opcji zachowują starą wspólną `storage_ratio`, domyślnie 70 %, więc istniejąca instalacja użytkownika się nie zmienia); stan trwale w `helpers.storage.Store`, start od ostatniej kompletnej godziny (bez backfillu), ustawianie przez usługi
  `set_storage`/`reset_storage` (sensor „Magazyn energii”) i encję `number` „Ustaw magazyn energii” (`number.py`). Jedna pula (bez podziału na strefy) – założenie niezweryfikowane z rozliczeniem Energi.
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
  DAY = godziny (dane do ok. 1 h wstecz, ostatnia godzina bywa niekompletna: `cplt:false`/`est:true`), MONTH = jedna pozycja z sumami stref.
- **`meterPoint` to `mpc`, NIE `meterPoints[].id`**: bierzemy z HTML `/dp/UserAccount.do` (odnośniki `EnergyIndex.do?mpc=<mpc>&ppe=<PPE>`), mapowanie po PPE.
  Zapas: `Meter.chart_ids` (kolejno `mpc`, `dev`, `id`, inne pola) – klient próbuje i zapamiętuje działające (400/404 = zły identyfikator).

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
- Panel Energia: nie mieszaj sensorów stanów licznika (zmieniają się raz na dobę o północy) ze statystykami godzinowymi – podwójne liczenie; sensory dzienne celowo bez `state_class`.
- Sensory szablonowe użytkownika z `float` bez wartości domyślnej sypały błędami przy `unavailable` – zaleca się `availability` (`has_value`) zamiast `float(0)` (fałszywy „reset” dla total_increasing).

## Stan i pomysły na dalej
- Wg użytkownika wszystko działa (logowanie, sensory, import godzinowy, karta). Niezweryfikowane w prawdziwym HA: dokładne pola metadanych statystyk w nowych wersjach HA.
- W toku (gałąź `dev`): sensory miesięczne – **tylko bilans** (`type=MONTH`, `mo=BP`; potrzebny HAR strony „Wykresy” z widokiem miesiąca),
  weryfikacja modelu magazynu energii z danymi z portalu (potrzebny HAR strony, na której portal pokazuje stan magazynu/opusty), README po polsku
  (lista sensorów), wydanie przez HACS (workflow już są). Później: README po angielsku.
- Zrobione na `dev`: próg magazynu per licznik (70/80 %), domyślna nazwa urządzenia = numer licznika (`meterSN`).
