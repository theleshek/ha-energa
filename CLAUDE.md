# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Projekt: niestandardowa integracja Home Assistant (HACS-ready) „Energa Mój Licznik” – pobiera dane z nieoficjalnego
API portalu https://mojlicznik.energa-operator.pl/ (Energa-Operator). Użytkownik (po polsku) jest prosumentem,
taryfa G12W, HAOS (Core 2026.9.x) na VM w Hyper-V. Rozmowa, komunikaty integracji i karty są po polsku, commity też.
Repozytorium jest **publiczne** (`theleshek/ha-energa`, gałąź `main`); PR i commity tylko na wyraźną prośbę użytkownika.

## Środowisko pracy (ważne)
- Praca lokalna na Windows, PowerShell 5.1 (brak `&&`; polecenia w osobnych liniach), repo w
  `C:\Users\Leszek\OneDrive\Dokumenty\GitHub\ha-energa`. Folder jest w OneDrive – przy błędach typu `index.lock` / „unable to unlink”
  przenieś repo poza OneDrive.
- Claude **nie ma dostępu do portalu ML ani do HA użytkownika**. Rzeczywiste odpowiedzi portalu dostajemy tylko od użytkownika:
  uruchamia `scripts/probe*.py` u siebie (`$env:ENERGA_USER="..."; $env:ENERGA_PASS="..."; py scripts/probe.py`) i wkleja wyniki
  albo HAR z Chrome. Po jego stronie: kopia lub instalacja przez HACS, restart HA, wklejanie logów.
- **Repo jest publiczne: nie wpisuj tu danych z logów/HAR/raportów** (e-mail, hasło, numer PPE, adres, numer fabryczny licznika,
  wewnętrzne identyfikatory portalu `id`/`mpc`/`dev`, kod pocztowy). W testach i dokumentacji używaj wartości fikcyjnych
  (np. PPE `590000000000000001`, `mpc=100001`, `id=100002`). `probe_output/` i `*.har` są w `.gitignore`.
- Nie wymyślaj struktur odpowiedzi portalu – najpierw dane od użytkownika, parsowanie pisz tolerancyjnie.

## Komendy (PowerShell)
```
py -m pip install pytest aiohttp pillow playwright          # środowisko testowe (HA NIE jest zainstalowany)
py -m pytest -q tests                                       # wszystkie testy
py -m pytest -q tests -k discovery                          # pojedynczy test
Get-ChildItem custom_components\energa_moj_licznik\*.py | ForEach-Object { py -m py_compile $_.FullName }
node --check card/energa-meter-card.js
```
- Moduły zależne od HA (`__init__`, `config_flow`, `coordinator`, `sensor`, `number`, `energy_store`, `stats`) da się tylko skompilować
  (`py_compile`), nie uruchomić. Testowalna logika jest celowo w czystych modułach: `api.py`, `hourly.py`, `const.py`.
- Testy ładują pakiet bez `__init__.py`: `types.ModuleType("energa_pkg")` z `__path__`, potem `importlib.import_module("energa_pkg.api")`.
  Testy HTTP stawiają atrapę portalu na `aiohttp.web` i nadpisują `api.BASE_URL`; cookie jar w testach: `CookieJar(unsafe=True)` (IP).
- Test karty (Chromium, playwright): launch z `executable_path` do zainstalowanej Chromium; atrapa `hass` z `states`, `config.time_zone`.
- CI (`.github/workflows/`): `tests.yml` (pytest, `py_compile`, `node --check` karty – **ścieżka karty musi zgadzać się z `card/`**),
  `validate.yml` (hassfest + `hacs/action`; HACS wymaga tematów i opisu repo na GitHubie).

## Struktura repo
- `custom_components/energa_moj_licznik/` – integracja (HACS instaluje tylko ten katalog). `manifest.json`: klucze po `domain`, `name`
  posortowane alfabetycznie (wymóg hassfest), wersja podbijana przy wydaniach (obecnie 0.3.0).
- `card/energa-meter-card.js` – karta Lovelace. HACS jej **nie** instaluje (leży poza `custom_components/`); użytkownik kopiuje ją ręcznie do
  `/config/www/energa-meter/` i dodaje zasób `/local/energa-meter/energa-meter-card.js?v=…` (typ: module).
- `hacs.json` – tylko `name` i `render_readme` (pole `homeassistant` z min. wersją powodowało błąd walidacji HACS – nie dodawać bez sprawdzenia).
- `brand/` w integracji – ikony. `scripts/probe.py`, `scripts/probe_chart.py` – diagnostyka portalu. `tests/` – pytest.

## Architektura
- `api.py` – `EnergaClient` (aiohttp): logowanie, `async_get_meters`, `async_get_readings`, `async_get_day_chart`; modele
  `Meter`, `Readings`; wyjątki `EnergaError` → `EnergaHttpError`(status) / `EnergaAuthError`.
- `hourly.py` – czysta logika: `ChartPoint`, `accumulate` (sumy narastające, przerywa na pierwszej niekompletnej godzinie),
  `daily_usage`, `storage_steps`, `latest_common_hour`.
- `stats.py` – import godzinowych statystyk zewnętrznych do recordera (`energa_moj_licznik:<PPE>_pobor[_strefa_N]`, `_oddanie…`);
  suma narastająca startuje od ostatniej zapisanej statystyki (`get_last_statistics`), backfill `BACKFILL_DAYS`=14, dogrywanie
  do `MAX_CATCHUP_DAYS`=31; metadane budowane adaptacyjnie (pola `mean_type`/`unit_class` zależą od wersji HA).
  `async_sync_hourly` zwraca `(zużycie dziś/wczoraj, surowe punkty godzinowe per kierunek)`.
- `coordinator.py` – `DataUpdateCoordinator` co 1 h: login (jeśli sesja nieważna) → odczyty → dla każdego licznika
  `async_sync_hourly` → aktualizacja magazynu energii; błąd danych godzinowych nie wyłącza sensorów stanów. `MeterData(readings, daily)`,
  `coordinator.storages` (id licznika → `EnergyStore`, tylko prosumenci).
- `sensor.py` – sensory:
  - stany (A+1, A+2, A−1, A−2; `total_increasing`; A− tylko prosument),
  - bilans stanów (Bilans strefa 1/2/łącznie = A+ − A−, prosument, bez state_class),
  - dzienne (Pobór/Oddanie/Bilans dziś/wczoraj z godzin kompletnych, bez state_class),
  - „Magazyn energii” (`EnergaStorageSensor`) + usługi encji `set_storage` (value kWh) i `reset_storage` (`services.yaml`).
  Atrybuty `reading_time` (UTC) i `last_refresh` (UTC).
- `number.py` – encja „Ustaw magazyn energii” (ustawia stan magazynu z UI).
- `energy_store.py` – magazyn energii u operatora (opusty), stan trwały w `helpers.storage.Store` (`energa_moj_licznik.storage_<entry>_<PPE>`).
  Model **dobowy** (Europe/Warsaw), `hourly.storage_steps`: bilans godzinowy `A+(h) − A−(h)` → suma bilansów w dobie `N`;
  `N<0` (nadwyżka): stan += ratio·(−N); `N>0` (niedobór): stan −= min(N, stan). Doba rozliczana dopiero gdy kompletna (wszystkie godziny
  kompletne w obu kierunkach; 23/25 h w dniach zmiany czasu); stan = koniec ostatniej rozliczonej doby (atrybut `calculated_until`).
  Start bez backfillu (od ostatniej kompletnej doby), stan początkowy ustawia użytkownik. `ratio` z opcji `storage_ratio` (domyślnie 70 %).
  Jedna pula bez podziału na strefy – założenie niezweryfikowane z rozliczeniem Energi.
- `config_flow.py` – login/hasło → wybór PPE (`cv.multi_select`) → nazwy własne; options flow (nazwy + `storage_ratio`); reauth.
  Nazwy: `entry.options[names]` > `entry.data[names]` > nazwa z portalu (podmieniane przez `dataclasses.replace` w `__init__`).
- `__init__.py` – sesja `async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar())`, platformy `sensor` i `number`.
- Karta (`card/energa-meter-card.js`, v0.3.0) – licznik mechaniczny jak na portalu, animacja przewijania, opcje `show_last_change`,
  `show_last_refresh`, `animate_on_load`, `digits_before`, `decimals`. Zasób użytkownika: `/local/energa-meter/energa-meter-card.js?v=…`.

## Zweryfikowane fakty o portalu (z raportów/HAR użytkownika)
- **Logowanie**: GET `/dp/UserLogin.do` → z formularza pole ukryte `_antixsrf`; POST (form) `selectedForm=1, save=save, _antixsrf, clientOS=web,
  j_username, j_password`; przekierowanie na `UserData.do`. Poprawne logowanie weryfikuj GET `/dp/resources/user/data` (JSON `success:true`).
  Przy aktywnej sesji `UserLogin.do` **nie ma formularza** → najpierw sprawdź `_session_valid()`. Portal przy błędach zwraca **HTTP 200 + HTML**
  (np. tytuł „Błąd - Portal Klienta”), więc odpowiedź nie-JSON to normalny scenariusz (łap `ValueError`). Potrzebny User-Agent przeglądarki.
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
- Zapytania wykresów z parametrami `mainChartMeterPoint/mainChartType/mainChartMeterObject`, `from`, `meterObject`, `mo=<kod OBIS>`; oraz `meterPoint=<id z user/data>` (zwraca 404 „brak Punktu Pomiaru”).
  Dane godzinowe istnieją tylko dla sum (OBIS 1.8.0/2.8.0 = `mo=A+`/`A-`); strefy osobno mają tylko WEEK/MONTH/YEAR – strefy bierzemy z tablicy `zones` w DAY.
- HA: `session.headers.update(...)` na sesji z `async_create_clientsession` → `AttributeError` (mappingproxy); nagłówek `User-Agent` przeglądarki podawaj **w każdym żądaniu**.
  Sesja z własnym `cookie_jar=aiohttp.CookieJar()`. Schemat `vol.All(vol.Coerce(list), [vol.In(..)])` w config flow → „Unknown error”; używaj `cv.multi_select`.
- Drugi login zaraz po pierwszym (koordynator po setupie) → „Nie znaleziono tokenu _antixsrf” – patrz wyżej (sprawdzaj sesję).
- `ConfigEntryAuthFailed` bez implementacji reauth = ślepy zaułek; AuthError rzucamy tylko gdy portal wyraźnie odrzuca dane (CAPTCHA/limity/5xx → `EnergaError`, ponowienie).
- Skrypty probe maskowały liczby ≥6 cyfr (ukryło `id`/`mpc`) i pierwsze wersje strzelały w złe parametry – kosztowało kilka rund; przy nowych próbach
  najpierw HAR z przeglądarki (kod JS portalu: `js/app/chart.js` buduje parametry), potem skrypt.
- Karta: nie renderuj `innerHTML` przy każdym `set hass` (niszczy animację) – DOM budowany raz, cyfry to paski 0–9 przesuwane `translateY`.
  HA mocno cache'uje JS: po zmianie pliku podbij `?v=` w zasobach (wersja karty jest w konsoli: `ENERGA-METER-CARD vX`). `reading_time` jest w UTC – karta formatuje do strefy HA.
- Panel Energia: nie mieszaj sensorów stanów licznika (zmieniają się raz na dobę o północy) ze statystykami godzinowymi – podwójne liczenie; sensory dzienne,
  bilanse i „Magazyn energii” celowo bez `state_class` (nie dodawać do panelu Energia).
- Sensory szablonowe użytkownika z `float` bez wartości domyślnej sypały błędami przy `unavailable` – zaleca się `availability` (`has_value`) zamiast `float(0)` (fałszywy „reset” dla total_increasing).
- Magazyn energii: najpierw liczyłem go godzina po godzinie (zasilanie `ratio·A−`, pobór osobno), potem z godzinowym netto – obie wersje odrzucone przez użytkownika.
  Obowiązuje model dobowy opisany wyżej (suma bilansów godzinowych w dobie, współczynnik na nadwyżce dobowej).
- Walidacja HACS: pole `"homeassistant"` w `hacs.json` i niesortowane klucze `manifest.json` powodowały błędy (`hacsjson`, `integration_manifest`);
  `hacs/action` wymaga też tematów i opisu repo na GitHubie. `git filter-branch` bywa blokowany przez uprawnienia – przepisywanie historii robi użytkownik sam
  (`git filter-repo`) albo zakłada repo od nowa z jednym commitem.

## Stan i pomysły na dalej
- Wg użytkownika wszystko działa (logowanie, sensory, import godzinowy, karta). Niezweryfikowane w prawdziwym HA: dokładne pola metadanych statystyk
  w nowych wersjach HA oraz model magazynu energii względem rzeczywistego rozliczenia Energi (jedna pula, doba po dobie).
- Możliwe kolejne kroki: sensory miesięczne (`type=MONTH`), Release `v0.3.0` i instalacja przez HACS, README po angielsku z ostrzeżeniem o nieoficjalnym API,
  rejestracja karty przez samą integrację (żeby nie kopiować jej ręcznie), sensory „pobrane/oddane” po saldowaniu godzinowym, podział magazynu na strefy G12W,
  domyślna nazwa urządzenia = numer licznika (z `UserAccount.do`).