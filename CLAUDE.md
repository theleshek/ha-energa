# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Projekt: niestandardowa integracja Home Assistant (HACS-ready) „Energa Mój Licznik” – pobiera dane z nieoficjalnego
API portalu https://mojlicznik.energa-operator.pl/ (Energa-Operator). Użytkownik (po polsku) jest prosumentem,
taryfa G12W, HAOS na VM w Hyper-V. Rozmowa i komunikaty w integracji/kartach są po polsku. Gałąź robocza:
`claude/vibrant-franklin-xaeuuj`; PR tylko na wyraźną prośbę; commity z trailerami z system-reminder.

## Środowisko pracy (ważne)
- Sesja Claude działa w chmurowym kontenerze **bez dostępu do portalu ML i do HA użytkownika**. Rzeczywiste odpowiedzi portalu
  dostajemy tylko od użytkownika: uruchamia on `scripts/probe*.py` u siebie (Windows/PowerShell: `py scripts/probe.py`,
  zmienne `$env:ENERGA_USER`, `$env:ENERGA_PASS`) i wkleja/załącza wyniki albo HAR z Chrome. Po jego stronie: kopiuje
  `custom_components/energa_moj_licznik` do `/config/custom_components/`, restartuje HA, wkleja logi.
- Logi/HAR/raporty od użytkownika zawierają jego e-mail, numer PPE, adres – nie wpisuj ich do repo ani do commitów
  (`probe_output/` i `*.har` są w .gitignore). Skrypty probe maskują dane, ale nie wszystko.
- Nie wymyślaj struktur odpowiedzi portalu – najpierw dane od użytkownika, parsowanie pisz tolerancyjnie.

## Komendy
```
pip install pytest aiohttp pillow playwright     # środowisko testowe (HA NIE jest zainstalowany)
python3 -m pytest -q tests                       # wszystkie testy
python3 -m pytest -q tests -k discovery          # pojedynczy test
for f in custom_components/energa_moj_licznik/*.py; do python3 -m py_compile $f; done
node --check custom_components/energa_moj_licznik/www/energa-meter-card.js
```
- Moduły zależne od HA (`__init__`, `config_flow`, `coordinator`, `sensor`, `stats`) da się tylko skompilować (`py_compile`),
  nie uruchomić. Testowalna logika jest celowo w czystych modułach: `api.py`, `hourly.py`, `const.py`.
- Testy ładują pakiet bez `__init__.py`: `types.ModuleType("energa_pkg")` z `__path__`, potem `importlib.import_module("energa_pkg.api")`.
  Testy HTTP stawiają atrapę portalu na `aiohttp.web` i nadpisują `api.BASE_URL`; cookie jar w testach: `CookieJar(unsafe=True)` (IP).
- Test karty (Chromium): `pip install playwright`, launch z
  `executable_path='/opt/pw-browsers/chromium-1194/chrome-linux/chrome', args=['--no-sandbox']` (nie uruchamiaj `playwright install`);
  atrapa `hass` z `states`, `config.time_zone`. Karta jest w `www/energa-meter-card.js`.

## Architektura
- `api.py` – `EnergaClient` (aiohttp): logowanie, `async_get_meters`, `async_get_readings`, `async_get_day_chart`; modele
  `Meter`, `Readings`; wyjątki `EnergaError` → `EnergaHttpError`(status) / `EnergaAuthError`.
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
  Nazwy: `entry.options[names]` > `entry.data[names]` > nazwa z portalu (podmieniane przez `dataclasses.replace` w `__init__`).
- `energy_store.py` + `hourly.storage_steps` – magazyn energii u operatora (opusty): doba po dobie (Europe/Warsaw): bilanse godzinowe (A+ − A−) sumowane w dobie; suma<0 (nadwyżka) → stan += ratio·nadwyżka, suma>0 → stan −= min(niedobór, stan); doba rozliczana dopiero gdy kompletna (stan = koniec ostatniej rozliczonej doby); ratio z opcji
  `storage_ratio` (domyślnie 70 %); stan trwale w `helpers.storage.Store`, start od ostatniej kompletnej godziny (bez backfillu), ustawianie przez usługi
  `set_storage`/`reset_storage` (sensor „Magazyn energii”) i encję `number` „Ustaw magazyn energii” (`number.py`). Jedna pula (bez podziału na strefy) – założenie niezweryfikowane z rozliczeniem Energi.
- `www/energa-meter-card.js` – karta Lovelace (licznik mechaniczny jak na portalu, animacja przewijania, opcje `show_last_change`,
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
- Możliwe kolejne kroki: sensory miesięczne (`type=MONTH`), wydanie/PR i instalacja przez HACS, README po angielsku, domyślna nazwa urządzenia = numer licznika
  (z `UserAccount.do`), ewentualne `docs/` (katalog pusty).
