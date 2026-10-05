"""Stałe integracji Energa Mój Licznik."""
from datetime import timedelta

DOMAIN = "energa_moj_licznik"

CONF_METERS = "meters"  # lista wybranych identyfikatorów PPE
CONF_METERS_INFO = "meters_info"  # {id: {"name": ..., "ppe": ...}} - nazwy z portalu
CONF_NAMES = "names"  # {id: nazwa własna}
CONF_STORAGE_RATIO = "storage_ratio"  # stara, wspólna wartość dla wszystkich liczników (opcje) - tylko odczyt
CONF_STORAGE_RATIOS = "storage_ratios"  # {id licznika: % oddanej energii zwracanej z magazynu operatora} (opcje)
# Net metering: zwrot 80 % (instalacje do 10 kW) albo 70 % (powyżej 10 kW).
STORAGE_RATIO_CHOICES = (70.0, 80.0)
DEFAULT_STORAGE_RATIO = 80.0  # domyślnie dla nowych liczników
LEGACY_STORAGE_RATIO = 70.0  # wartość z czasów jednej, wspólnej opcji - dla wpisów sprzed per-licznikowych progów

BASE_URL = "https://mojlicznik.energa-operator.pl"
DEFAULT_SCAN_INTERVAL = timedelta(hours=1)  # portal publikuje dane godzinowe z ok. 1 h opóźnieniem
BACKFILL_DAYS = 14  # ile dni wstecz importować przy pierwszym uruchomieniu
MAX_CATCHUP_DAYS = 31  # maksymalny zakres dogrywania po dłuższej przerwie

# Klucze odczytów: kierunek (A+ pobór, A- oddanie) + strefa
ZONES = ("A+1", "A+2", "A-1", "A-2")

# Serie statystyk godzinowych: (kierunek portalu 'mo', strefa lub None = suma, klucz, opis)
SERIES = (
    ("A+", None, "pobor", "pobór (godzinowo)"),
    ("A+", 1, "pobor_strefa_1", "pobór strefa 1 (godzinowo)"),
    ("A+", 2, "pobor_strefa_2", "pobór strefa 2 (godzinowo)"),
    ("A-", None, "oddanie", "oddanie (godzinowo)"),
    ("A-", 1, "oddanie_strefa_1", "oddanie strefa 1 (godzinowo)"),
    ("A-", 2, "oddanie_strefa_2", "oddanie strefa 2 (godzinowo)"),
)
