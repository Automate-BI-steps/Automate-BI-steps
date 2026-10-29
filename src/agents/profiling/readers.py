"""Lecture des CSV : détection d'encodage, de séparateur et d'en-tête.

Les fichiers sont toujours lus en texte brut (dtype=str, sans conversion des
valeurs manquantes) : c'est le profilage qui décide des types et des nulls.
"""

import csv
from pathlib import Path

import pandas as pd
from charset_normalizer import from_bytes

# Valeurs considérées comme manquantes (comparaison insensible à la casse).
NULL_TOKENS = {"", "na", "n/a", "nan", "null", "none", "nil", "-", "?"}

# Noms normalisés pour les encodages les plus courants.
_ENCODING_ALIASES = {
    "latin_1": "ISO-8859-1",
    "iso8859_1": "ISO-8859-1",
    "cp1252": "Windows-1252",
    "utf_8": "UTF-8",
    "ascii": "ASCII",
}


def detect_encoding(raw: bytes) -> str:
    if raw.isascii():
        return "ASCII"
    try:
        raw.decode("utf-8")
        return "UTF-8"
    except UnicodeDecodeError:
        pass
    # Cas le plus fréquent pour des données occidentales (export Excel / Windows).
    # La détection statistique se trompe souvent sur de petits fichiers (ex. cp1250).
    try:
        raw.decode("cp1252")
        has_c1 = any(0x80 <= b <= 0x9F for b in raw)
        return "Windows-1252" if has_c1 else "ISO-8859-1"
    except UnicodeDecodeError:
        pass
    best = from_bytes(raw).best()
    name = best.encoding if best else "latin_1"
    return _ENCODING_ALIASES.get(name, name)


def is_utf8_compatible(encoding: str) -> bool:
    return encoding.upper() in {"UTF-8", "ASCII"}


SEPARATORS = ",;\t|"


def detect_dialect(sample: str) -> tuple[str, bool]:
    """Retourne (séparateur, présence d'un en-tête).

    Le sniffer de la bibliothèque standard peut lever une ValueError (et non csv.Error)
    sur certains textes ; on retombe alors sur la virgule et l'en-tête présent.
    """
    sniffer = csv.Sniffer()
    try:
        delimiter = sniffer.sniff(sample, delimiters=SEPARATORS).delimiter
    except Exception:  # noqa: BLE001 - csv.Error ou ValueError selon le contenu
        delimiter = ","
    if delimiter not in SEPARATORS:
        delimiter = ","
    try:
        has_header = sniffer.has_header(sample)
    except Exception:  # noqa: BLE001
        has_header = True
    return delimiter, has_header


def inspect_file(path: Path) -> dict:
    """Métadonnées d'un fichier (section metadata.files de la spec)."""
    meta = {
        "file_name": path.name,
        "table_name": path.stem,
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "encoding": None,
        "delimiter": None,
        "has_header": None,
        "read_status": "ok",
        "read_message": None,
    }
    try:
        raw = path.read_bytes()
        encoding = detect_encoding(raw)
        sample = raw[:20000].decode(encoding, errors="replace")
        delimiter, has_header = detect_dialect(sample)
        meta.update(encoding=encoding, delimiter=delimiter, has_header=has_header)
        if not is_utf8_compatible(encoding):
            meta["read_status"] = "warning"
            meta["read_message"] = f"Fichier encodé en {encoding} (pas UTF-8)."
        read_table(meta)  # vérifie que le fichier est lisible
    except Exception as exc:  # noqa: BLE001 - tout échec de lecture est reporté
        meta["read_status"] = "error"
        meta["read_message"] = f"{type(exc).__name__}: {exc}"
    return meta


def read_table(meta: dict, encoding: str | None = None) -> pd.DataFrame:
    """Lit un CSV en texte brut avec l'encodage détecté (ou celui imposé)."""
    has_header = meta["has_header"] is not False
    df = pd.read_csv(
        meta["path"],
        sep=meta["delimiter"] or ",",
        encoding=encoding or meta["encoding"],
        encoding_errors="replace",
        header=0 if has_header else None,
        dtype=str,
        keep_default_na=False,
        na_filter=False,
    )
    if not has_header:
        df.columns = [f"col_{i}" for i in range(df.shape[1])]
    return df


def null_mask(series: pd.Series) -> pd.Series:
    return series.str.strip().str.lower().isin(NULL_TOKENS)
