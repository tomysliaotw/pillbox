"""TFDA catalogue importer that works both online and fully offline."""
import io
import json
from pathlib import Path
import zipfile

from pillbox_config import TFDA_ARCHIVE_PATH
from pillbox_database import replace_drug_catalogue

TFDA_JSON_URL = "https://data.fda.gov.tw/data/opendata/export/36/json"


def _load_records(archive: Path) -> list[dict]:
    with zipfile.ZipFile(archive) as package:
        json_files = [name for name in package.namelist() if name.lower().endswith(".json")]
        if not json_files:
            raise ValueError("The TFDA archive contains no JSON file.")
        with package.open(json_files[0]) as source:
            return json.loads(source.read().decode("utf-8-sig"))


def _clean_records(records: list[dict]) -> list[tuple[str, str, str, str]]:
    cleaned = []
    for item in records:
        if str(item.get("註銷狀態", "")).strip() not in ("", "None"):
            continue
        chinese, english = str(item.get("中文品名") or "").strip(), str(item.get("英文品名") or "").strip()
        if not chinese and not english:
            continue
        name = f"{chinese} ({english})" if chinese and english else chinese or english
        indication = str(item.get("適應症") or "").strip() or "一般用藥"
        form, ingredient = str(item.get("劑型") or "").strip(), str(item.get("主成分略述") or "").strip()
        warning = f"[{form}] {ingredient}" if ingredient else f"[{form}]"
        cleaned.append((name, indication, warning, str(item.get("許可證字號") or "").strip()))
    return cleaned


def import_tfda_archive(archive_path: Path | str = TFDA_ARCHIVE_PATH) -> dict:
    """Load a pre-downloaded TFDA ZIP. No network connection is required."""
    archive = Path(archive_path)
    if not archive.is_file():
        raise FileNotFoundError(f"TFDA archive not found: {archive}")
    records = _load_records(archive)
    count = replace_drug_catalogue(_clean_records(records))
    return {"source": str(archive), "raw_records": len(records), "stored_records": count}


def download_and_import(destination: Path | str = TFDA_ARCHIVE_PATH) -> dict:
    """Optional preparation-time helper; never call this on an offline deployment."""
    import requests
    destination = Path(destination)
    response = requests.get(TFDA_JSON_URL, timeout=120)
    response.raise_for_status()
    destination.write_bytes(response.content)
    return import_tfda_archive(destination)
