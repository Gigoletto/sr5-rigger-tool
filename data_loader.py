"""CSV-Kataloge einlesen, mit Pydantic validieren und als Dictionaries ablegen.

Keine UI und keine Berechnungslogik. Erwartetes CSV-Format: Semikolon-getrennt,
bevorzugt UTF-8 mit BOM (`utf-8-sig`). Abweichende Kodierung (z. B. cp1252)
wird als Fallback akzeptiert.
"""

from __future__ import annotations

import csv
import io
import re
import types
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar, Union, get_args, get_origin

from pydantic import BaseModel, ValidationError

from models import (
    AutosoftTemplate,
    CommlinkTemplate,
    RiggerConsoleTemplate,
    VehicleTemplate,
    WeaponTemplate,
)

T = TypeVar("T", bound=BaseModel)

DEFAULT_DATA_DIR = Path(__file__).resolve().parent

VEHICLES_FILENAME = "Fahrzeuge_und_Drohnen.csv"
WEAPONS_FILENAME = "Waffen.csv"
AUTOSOFTS_FILENAME = "Autosoft.csv"
COMMLINKS_FILENAME = "Kommlinks.csv"
RIGGER_CONSOLES_FILENAME = "Riggerkonsolen.csv"

_NUMERIC_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_NUMERIC_TYPES = (int, float)


class CatalogLoadError(Exception):
    """Mindestens eine CSV-Zeile konnte nicht validiert werden."""


def _unwrap_optional(annotation: object) -> object:
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def _numeric_columns(model_cls: type[BaseModel]) -> set[str]:
    """CSV-Spalten, die im Modell als int/float erwartet werden."""
    columns: set[str] = set()
    for name, field in model_cls.model_fields.items():
        if _unwrap_optional(field.annotation) in _NUMERIC_TYPES:
            columns.add(name)
            if field.alias:
                columns.add(field.alias)
    return columns


def _read_csv_text(path: Path) -> str:
    """Lies den Dateitext; zuerst utf-8-sig, bei Bedarf cp1252."""
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        warnings.warn(
            f"{path.name}: keine gültige UTF-8-Datei, Fallback auf cp1252.",
            stacklevel=3,
        )
        return raw.decode("cp1252")


def _coerce_cell(value: str) -> object | None:
    """Leere Zellen -> None; reine Zahlen (inkl. 2.0) -> int/float."""
    text = value.strip()
    if text == "":
        return None
    if _NUMERIC_RE.fullmatch(text):
        number = float(text)
        if number.is_integer():
            return int(number)
        return number
    return text


_RK_INT_RE = re.compile(r"-?\d+")


def _parse_rk(value: object) -> int:
    """CSV-RK: '-', leer und unlesbare Werte werden zu 0."""
    if value is None:
        return 0
    text = str(value).strip()
    if text in {"", "-", ".", "–", "—"}:
        return 0
    match = _RK_INT_RE.search(text)
    return int(match.group(0)) if match else 0


def _normalize_row(row: dict[str | None, str | None], model_cls: type[BaseModel]) -> dict[str, object]:
    """Bereinigt eine DictReader-Zeile für die Pydantic-Validierung."""
    numeric_cols = _numeric_columns(model_cls)
    cleaned: dict[str, object] = {}
    for key, value in row.items():
        if key is None:
            continue
        column = key.strip()
        if not column or value is None:
            continue
        text = str(value).strip()
        if column in {"RK", "rueckstosskompensation"}:
            cleaned[column] = _parse_rk(text)
            continue
        if text == "":
            continue
        if column in numeric_cols:
            cell = _coerce_cell(text)
            if cell is None:
                continue
            cleaned[column] = cell
        else:
            cleaned[column] = text

    for name, field in model_cls.model_fields.items():
        if not field.is_required() or field.annotation is not str:
            continue
        alias = field.alias or name
        if alias not in cleaned and name not in cleaned:
            cleaned[alias] = ""
    return cleaned


def _unique_key(name: str, catalog: dict[str, T], item: T) -> str:
    """Name als Schlüssel; bei Kollision Quelle/Seite oder laufende Nummer anhängen."""
    if name not in catalog:
        return name
    quelle = getattr(item, "quelle", None)
    if quelle:
        candidate = f"{name} [{quelle}]"
        if candidate not in catalog:
            return candidate
        seite = getattr(item, "seite", None)
        if seite is not None:
            candidate = f"{name} [{quelle} S.{seite}]"
            if candidate not in catalog:
                return candidate
    suffix = 2
    while True:
        candidate = f"{name} #{suffix}"
        if candidate not in catalog:
            return candidate
        suffix += 1


def _is_blank_row(row: dict[str | None, str | None]) -> bool:
    return not any((value or "").strip() for value in row.values() if value is not None)


def _load_catalog(
    path: str | Path, model_cls: type[T], *, strict: bool = True
) -> dict[str, T]:
    csv_path = Path(path)
    if not csv_path.is_file():
        raise FileNotFoundError(f"Katalogdatei nicht gefunden: {csv_path}")

    reader = csv.DictReader(io.StringIO(_read_csv_text(csv_path)), delimiter=";")
    catalog: dict[str, T] = {}
    errors: list[str] = []

    for line_no, row in enumerate(reader, start=2):
        if _is_blank_row(row):
            continue
        cleaned = _normalize_row(row, model_cls)
        raw_name = cleaned.get("Name")
        if not raw_name:
            errors.append(f"{csv_path.name}:{line_no}: Zeile ohne Name.")
            continue
        try:
            item = model_cls.model_validate(cleaned)
        except ValidationError as exc:
            errors.append(f"{csv_path.name}:{line_no} ({raw_name}): {exc}")
            continue
        key = _unique_key(str(item.name), catalog, item)
        if key != item.name:
            warnings.warn(
                f"{csv_path.name}:{line_no}: doppelter Name {item.name!r}, "
                f"abgelegt als {key!r}.",
                stacklevel=3,
            )
        catalog[key] = item

    if errors:
        details = "\n".join(errors)
        message = (
            f"{len(errors)} ungültige Zeile(n) in {csv_path.name}:\n{details}"
        )
        if strict:
            raise CatalogLoadError(message)
        warnings.warn(message, stacklevel=3)
    return catalog


def load_vehicles(path: str | Path) -> dict[str, VehicleTemplate]:
    return _load_catalog(path, VehicleTemplate)


def load_weapons(path: str | Path) -> dict[str, WeaponTemplate]:
    return _load_catalog(path, WeaponTemplate, strict=False)


def load_autosofts(path: str | Path) -> dict[str, AutosoftTemplate]:
    return _load_catalog(path, AutosoftTemplate)


def load_commlinks(path: str | Path) -> dict[str, CommlinkTemplate]:
    return _load_catalog(path, CommlinkTemplate)


def load_rigger_consoles(path: str | Path) -> dict[str, RiggerConsoleTemplate]:
    return _load_catalog(path, RiggerConsoleTemplate)


@dataclass
class Catalog:
    """Alle Kataloge, einmalig beim Start der Anwendung ladbar."""

    vehicles: dict[str, VehicleTemplate]
    weapons: dict[str, WeaponTemplate]
    autosofts: dict[str, AutosoftTemplate]
    commlinks: dict[str, CommlinkTemplate]
    riggerkonsolen: dict[str, RiggerConsoleTemplate]

    @classmethod
    def load(cls, data_dir: str | Path | None = None) -> Catalog:
        base = Path(data_dir) if data_dir is not None else DEFAULT_DATA_DIR
        return cls(
            vehicles=load_vehicles(base / VEHICLES_FILENAME),
            weapons=load_weapons(base / WEAPONS_FILENAME),
            autosofts=load_autosofts(base / AUTOSOFTS_FILENAME),
            commlinks=load_commlinks(base / COMMLINKS_FILENAME),
            riggerkonsolen=load_rigger_consoles(base / RIGGER_CONSOLES_FILENAME),
        )
