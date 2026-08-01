"""
Downloads the three DOSM datasets this project needs, straight from DOSM's
open data storage, and saves them locally as Parquet files.

Why download and store locally instead of reading straight from the URL every
time? Two reasons:
1. You don't want to hit DOSM's servers on every single script run while you're
   iterating on schema design and load logic.
2. It mirrors how a real pipeline works: a "raw" landing zone for data exactly
   as received, before any transformation touches it.
"""

import requests
from pathlib import Path

# Confirmed dataset URLs from DOSM's open data catalogue (open.dosm.gov.my).
# Each entry: (friendly name, source URL, local filename)
DATASETS = [
    (
        "crime_district",
        "https://storage.data.gov.my/publicsafety/crime_district.parquet",
        "crime_district.parquet",
    ),
    (
        "hh_income_district",
        "https://storage.dosm.gov.my/hies/hh_income_district.parquet",
        "hh_income_district.parquet",
    ),
    (
        "population_district",
        "https://storage.dosm.gov.my/population/population_district.parquet",
        "population_district.parquet",
    ),
]

RAW_DATA_DIR = Path(__file__).parent.parent / "data" / "raw"


def download_file(name: str, url: str, filename: str) -> None:
    dest = RAW_DATA_DIR / filename
    print(f"Downloading {name} from {url} ...")

    response = requests.get(url, timeout=60)
    response.raise_for_status()  # fail loudly if the request didn't succeed

    dest.write_bytes(response.content)
    size_mb = dest.stat().st_size / (1024 * 1024)
    print(f"  Saved to {dest} ({size_mb:.2f} MB)")


def main() -> None:
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)

    for name, url, filename in DATASETS:
        try:
            download_file(name, url, filename)
        except requests.RequestException as e:
            # If DOSM changes a URL or renames a dataset, this tells you exactly
            # which one failed instead of silently skipping it.
            print(f"  FAILED to download {name}: {e}")
            print(f"  Check the current URL at https://open.dosm.gov.my/data-catalogue/{name}")

    print("\nDone. Check data/raw/ for the downloaded files.")


if __name__ == "__main__":
    main()
