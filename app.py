"""Shadowrun 5 Rigger-Baukasten – Streamlit-Oberfläche.

Keine eigene Regelberechnung: alle Einsatzwerte kommen aus
``calculate_active_vehicle``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from data_loader import (
    DEFAULT_DATA_DIR,
    WEAPONS_FILENAME,
    Catalog,
    CatalogLoadError,
)
from models import (
    MAX_MOUNTED_WEAPONS,
    ActiveVehicle,
    Attribute,
    Character,
    CommlinkTemplate,
    Fahrzeugfertigkeit,
    InterfaceMode,
    MountedWeapon,
    Quality,
    RiggerConsole,
    RiggerConsoleTemplate,
    RiggerState,
    SkillSpecializations,
    Skills,
    VehicleTemplate,
    WeaponTemplate,
    Zustandsmonitor,
)
from rules_engine import (
    CYBERPROGRAM_EFFECTS,
    CYBERPROGRAM_OPTIONS,
    CalculatedConsoleStats,
    CalculatedVehicleStats,
    apply_vehicle_mod,
    calculate_active_vehicle,
    calculate_console_stats,
    calculate_weapon_accuracy,
    calculate_weapon_limit,
    classify_gunnery_weapon,
    condition_monitor_max,
    condition_monitor_modifier,
    gunnery_spec_bonus,
    has_smartsoft,
    make_mounted_weapon,
    program_slot_limit,
    vehicle_skill_name,
    weapon_mount_costs,
)

APP_TITLE = "Shadowrun 5 - Der Profirigger"

INTERFACE_LABELS: dict[InterfaceMode, str] = {
    InterfaceMode.AR: "AR",
    InterfaceMode.VR_COLD: "VR Cold",
    InterfaceMode.VR_HOT: "VR Hot",
    InterfaceMode.DIREKTVERBINDUNG: "Direktverbindung",
    InterfaceMode.AUTOPILOT: "Autopilot",
}
GLOBAL_INTERFACE_MODES: tuple[InterfaceMode, ...] = (
    InterfaceMode.AR,
    InterfaceMode.VR_COLD,
    InterfaceMode.VR_HOT,
    InterfaceMode.DIREKTVERBINDUNG,
)

QUALITY_OPTIONS = [
    "Fahrzeugempathie",
    "Rennpilot",
    "Meisterfahrer",
    "Raser",
    "Unauffälligkeit",
    "Geborener Schrauber",
    "Technisches Improvisationstalent",
    "Übertakter",
]
QUALITY_EFFECTS: dict[str, str] = {
    "Fahrzeugempathie": (
        "+1 auf Steuern-Pool und Handling-Limit bei AR."
    ),
    "Rennpilot": (
        "Boni auf schwierige Manöver/Stunts, kurzzeitige Erhöhung von "
        "Geschwindigkeit/Handling möglich."
    ),
    "Meisterfahrer": "Geländemodifikatoren werden um 1 gesenkt.",
    "Raser": (
        "+1 auf Fahrzeugproben bei hohen Geschwindigkeiten "
        "(Geschwindigkeit 3+, bei Flugzeugen 4+)."
    ),
    "Unauffälligkeit": (
        "Beobachter erhalten -2 auf Wahrnehmungsproben "
        "(gilt auch für selbstgesteuerte Drohnen)."
    ),
    "Geborener Schrauber": (
        "+1 Würfelpool auf alle Proben der Mechanik-Fertigkeitsgruppe."
    ),
    "Technisches Improvisationstalent": (
        "Erleichtert Reparaturen unter erschwerten Bedingungen."
    ),
    "Übertakter": (
        "Ermöglicht Leistungssteigerung von Matrix-Hardware/Konsolen "
        "über das Standardlimit hinaus."
    ),
}
QUALITY_HELP = QUALITY_EFFECTS

SKILL_LABELS: dict[str, str] = {
    "bodenfahrzeuge": "Bodenfahrzeuge",
    "flugzeuge": "Flugzeuge",
    "laeufer": "Läufer",
    "wasserfahrzeuge": "Schiffe",
}
FERTIGKEIT_OPTIONS = ["Bodenfahrzeuge", "Flugzeuge", "Läufer", "Schiffe"]
FERTIGKEIT_BY_LABEL: dict[str, Fahrzeugfertigkeit] = {
    "Bodenfahrzeuge": Fahrzeugfertigkeit.BODENFAHRZEUGE,
    "Flugzeuge": Fahrzeugfertigkeit.FLUGZEUGE,
    "Läufer": Fahrzeugfertigkeit.LAEUFER,
    "Schiffe": Fahrzeugfertigkeit.WASSERFAHRZEUGE,
}

SPECIALIZATION_OPTIONS: dict[str, list[str]] = {
    "bodenfahrzeuge": [
        "Autos",
        "Fernsteuerung",
        "Hovercrafts",
        "Kettenfahrzeuge",
        "Motorräder",
    ],
    "flugzeuge": [
        "Fernsteuerung",
        "Kippflügler",
        "Luftschiffe",
        "Rotormaschinen",
        "Starrflügler",
        "Vektorschubmaschinen",
    ],
    "wasserfahrzeuge": [
        "Fernsteuerung",
        "Großschiffe",
        "Motorboote",
        "Segelboote",
        "U-Boote",
    ],
    "laeufer": [
        "Fernsteuerung",
        "Vielbeiner",
        "Vierbeiner",
        "Zweibeiner",
    ],
    "geschuetze": [
        "Artillerie",
        "Energiewaffen",
        "Feuerwaffen",
        "Lenkraketen",
        "Raketen",
    ],
}

Steuergeraet = CommlinkTemplate | RiggerConsoleTemplate
WEAPON_NONE = "Keine"
MAX_ACTIVE_VEHICLES = 30
VEHICLE_SLOTS_KEY = "active_vehicles"
SELECTED_VEHICLE_KEY = "selected_vehicle_idx"
PROFILE_FORMAT = "riggertool-profile"
PROFILE_VERSION = 1
VEHICLE_WIDGET_PREFIXES = (
    "veh_kategorie_",
    "veh_model_",
    "veh_fertigkeit_",
    "veh_mod_",
    "weapon_slot_",
    "spezialisierung_geschuetze_",
    "spezialisierung_steuern_",
    "autopilot_",
)
VEHICLE_MOD_FIELDS: tuple[tuple[str, str], ...] = (
    ("handling", "Handling"),
    ("beschleunigung", "Beschleunigung"),
    ("geschwindigkeit", "Geschwindigkeit"),
    ("pilot", "Pilot"),
    ("rumpf", "Rumpf"),
    ("panzerung", "Panzerung"),
    ("sensor", "Sensor"),
)


def _skill_specializations(character: Character, skill_key: str) -> list[str]:
    """Gewählte Spezialisierungen einer Fertigkeit."""
    specs = getattr(character.spezialisierungen, skill_key, None)
    return [item for item in (specs or []) if item]


def _specialization_help(specs: list[str]) -> str:
    listed = ", ".join(specs)
    return f"Aktiviert +2 Bonus. Deine Spezialisierungen: {listed}"


def _vehicle_mod_key(field: str, vehicle_id: int) -> str:
    return f"veh_{vehicle_id}_mod_{field}"


def _weapon_slot_key(vehicle_id: int, index: int) -> str:
    return f"veh_{vehicle_id}_weapon_{index + 1}"


def _gun_spec_key(vehicle_id: int, index: int) -> str:
    return f"veh_{vehicle_id}_gun_spec_{index + 1}"


def _drive_spec_key(vehicle_id: int) -> str:
    return f"veh_{vehicle_id}_drive_spec"


def _autopilot_key(vehicle_id: int) -> str:
    return f"veh_{vehicle_id}_autopilot"


def _damage_key(vehicle_id: int) -> str:
    return f"veh_{vehicle_id}_damage"


def _category_key(vehicle_id: int) -> str:
    return f"veh_kategorie_{vehicle_id}"


def _model_key(vehicle_id: int) -> str:
    return f"veh_model_{vehicle_id}"


def _fertigkeit_key(vehicle_id: int) -> str:
    return f"veh_fertigkeit_{vehicle_id}"


def _weapon_dict_key(slot: int) -> str:
    return f"weapon_{slot + 1}"


def _weapon_fixed_dict_key(slot: int) -> str:
    return f"weapon_{slot + 1}_fixed"


def _weapon_fixed_key(vehicle_id: int, index: int) -> str:
    return f"veh_{vehicle_id}_weapon_{index + 1}_fixed"


def _default_mods() -> dict[str, int]:
    return {field: 0 for field, _label in VEHICLE_MOD_FIELDS}


def _default_weapons() -> list[dict]:
    return [
        {"name": WEAPON_NONE, "gun_spec": False, "fixed": False}
        for _ in range(MAX_MOUNTED_WEAPONS)
    ]


def _bind_widget(key: str, value) -> None:
    """Setzt den Widget-Wert aus dem Fahrzeug-Dict, bevor das Widget entsteht."""
    current = st.session_state.get(key)
    if current == value:
        return
    try:
        if isinstance(value, int) and current is not None and int(current) == value:
            return
    except (TypeError, ValueError):
        pass
    st.session_state[key] = value


def _vehicle_widget_keys(index: int) -> list[str]:
    """Alle persistierten Widget-Keys eines Fahrzeug-Index (alt und neu)."""
    keys = [
        _category_key(index),
        _model_key(index),
        _fertigkeit_key(index),
        _autopilot_key(index),
        _drive_spec_key(index),
        _damage_key(index),
        f"autopilot_{index}",
        f"spezialisierung_steuern_{index}",
    ]
    for field, _label in VEHICLE_MOD_FIELDS:
        keys.append(_vehicle_mod_key(field, index))
        keys.append(f"veh_mod_{index}_{field}")
    for slot in range(MAX_MOUNTED_WEAPONS):
        keys.append(_weapon_slot_key(index, slot))
        keys.append(_gun_spec_key(index, slot))
        keys.append(_weapon_fixed_key(index, slot))
        keys.append(f"weapon_slot_{index}_{slot}")
        keys.append(f"spezialisierung_geschuetze_{index}_{slot}")
    return list(dict.fromkeys(keys))


def _default_vehicle_entry(index: int) -> dict:
    """Neues Fahrzeug-Dict mit allen persistenten Keys."""
    weapons = _default_weapons()
    entry: dict = {
        "name": f"Fahrzeug {index + 1}",
        "category": "Alle",
        "model": "",
        "autopilot_active": False,
        "fertigkeit": "Bodenfahrzeuge",
        "drive_spec": False,
        "current_damage": 0,
        "mods": _default_mods(),
        "weapons": weapons,
    }
    for slot in range(MAX_MOUNTED_WEAPONS):
        entry[_weapon_dict_key(slot)] = WEAPON_NONE
        entry[_weapon_fixed_dict_key(slot)] = False
    return entry


def _as_bool(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "ja"}
    return bool(value)


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _weapon_name_from_entry(entry: dict, slot: int) -> str:
    named = entry.get(_weapon_dict_key(slot))
    if named:
        return str(named)
    weapons = entry.get("weapons") or []
    if slot < len(weapons):
        raw = weapons[slot]
        if isinstance(raw, str) and raw:
            return raw
        if isinstance(raw, dict):
            name = str(raw.get("name") or "").strip()
            if name:
                return name
    return WEAPON_NONE


def _ensure_vehicle_shape(item: dict, index: int) -> dict:
    """Füllt fehlende Keys, ohne vorhandene Waffen/Mods zu löschen."""
    base = _default_vehicle_entry(index)
    if not str(item.get("name") or "").strip():
        item["name"] = str(item.get("model") or "").strip() or base["name"]
    item.setdefault("category", "Alle")
    if not item["category"]:
        item["category"] = "Alle"
    item.setdefault("model", "")
    if "autopilot_active" not in item:
        item["autopilot_active"] = _as_bool(item.get("autopilot", False))
    item.setdefault("fertigkeit", "Bodenfahrzeuge")
    item.setdefault("drive_spec", False)
    item["current_damage"] = max(0, _as_int(item.get("current_damage"), 0))
    mods = item.get("mods")
    if not isinstance(mods, dict):
        item["mods"] = _default_mods()
    else:
        filled = _default_mods()
        for field in filled:
            filled[field] = _as_int(mods.get(field, 0), 0)
        item["mods"] = filled
    weapons = _default_weapons()
    raw_weapons = item.get("weapons")
    if isinstance(raw_weapons, list):
        for slot, raw in enumerate(raw_weapons[:MAX_MOUNTED_WEAPONS]):
            if isinstance(raw, str):
                weapons[slot]["name"] = raw or WEAPON_NONE
            elif isinstance(raw, dict):
                weapons[slot]["name"] = str(raw.get("name") or WEAPON_NONE) or WEAPON_NONE
                weapons[slot]["gun_spec"] = _as_bool(raw.get("gun_spec"))
                weapons[slot]["fixed"] = _as_bool(
                    raw.get("fixed", raw.get("feste_halterung"))
                )
    for slot in range(MAX_MOUNTED_WEAPONS):
        named = item.get(_weapon_dict_key(slot))
        if named:
            weapons[slot]["name"] = str(named)
        if _weapon_fixed_dict_key(slot) in item:
            weapons[slot]["fixed"] = _as_bool(item.get(_weapon_fixed_dict_key(slot)))
        item[_weapon_dict_key(slot)] = weapons[slot]["name"]
        item[_weapon_fixed_dict_key(slot)] = bool(weapons[slot]["fixed"])
    item["weapons"] = weapons
    return item


def _hydrate_from_widgets(item: dict, index: int) -> None:
    """Füllt leere Dict-Felder aus noch vorhandenen Widget-Keys (Migration)."""
    if not str(item.get("model") or "").strip():
        model_key = _model_key(index)
        if model_key in st.session_state:
            model = str(st.session_state[model_key] or "").strip()
            if model:
                item["model"] = model
                item["name"] = model
    fert_key = _fertigkeit_key(index)
    if fert_key in st.session_state and item.get("fertigkeit") in (None, "", "Bodenfahrzeuge"):
        item["fertigkeit"] = str(st.session_state[fert_key] or item.get("fertigkeit"))
    auto_key = _autopilot_key(index)
    old_auto = f"autopilot_{index}"
    if not item.get("autopilot_active"):
        if auto_key in st.session_state:
            item["autopilot_active"] = bool(st.session_state[auto_key])
        elif old_auto in st.session_state:
            item["autopilot_active"] = bool(st.session_state[old_auto])
    drive_key = _drive_spec_key(index)
    old_drive = f"spezialisierung_steuern_{index}"
    if not item.get("drive_spec"):
        if drive_key in st.session_state:
            item["drive_spec"] = bool(st.session_state[drive_key])
        elif old_drive in st.session_state:
            item["drive_spec"] = bool(st.session_state[old_drive])
    if not item.get("current_damage"):
        dmg_key = _damage_key(index)
        if dmg_key in st.session_state:
            item["current_damage"] = max(0, _as_int(st.session_state.get(dmg_key), 0))
    for field, _label in VEHICLE_MOD_FIELDS:
        if _as_int(item.get("mods", {}).get(field), 0) != 0:
            continue
        key = _vehicle_mod_key(field, index)
        old_key = f"veh_mod_{index}_{field}"
        if key in st.session_state:
            item["mods"][field] = _as_int(st.session_state[key], 0)
        elif old_key in st.session_state:
            item["mods"][field] = _as_int(st.session_state[old_key], 0)
    for slot in range(MAX_MOUNTED_WEAPONS):
        if _weapon_name_from_entry(item, slot) != WEAPON_NONE:
            continue
        wkey = _weapon_slot_key(index, slot)
        old_wkey = f"weapon_slot_{index}_{slot}"
        name = None
        if wkey in st.session_state:
            name = str(st.session_state[wkey] or WEAPON_NONE)
        elif old_wkey in st.session_state:
            name = str(st.session_state[old_wkey] or WEAPON_NONE)
        if name and name != WEAPON_NONE:
            item["weapons"][slot]["name"] = name
            item[_weapon_dict_key(slot)] = name
        gkey = _gun_spec_key(index, slot)
        old_gkey = f"spezialisierung_geschuetze_{index}_{slot}"
        if not item["weapons"][slot]["gun_spec"]:
            if gkey in st.session_state:
                item["weapons"][slot]["gun_spec"] = bool(st.session_state[gkey])
            elif old_gkey in st.session_state:
                item["weapons"][slot]["gun_spec"] = bool(st.session_state[old_gkey])
        if not item["weapons"][slot].get("fixed"):
            fkey = _weapon_fixed_key(index, slot)
            if fkey in st.session_state:
                item["weapons"][slot]["fixed"] = bool(st.session_state[fkey])
                item[_weapon_fixed_dict_key(slot)] = item["weapons"][slot]["fixed"]


def _normalize_vehicle_entry(item: object, index: int) -> dict:
    """Macht aus Alt-IDs oder unvollständigen Einträgen ein vollständiges Fahrzeug-Dict."""
    if isinstance(item, dict):
        entry = _ensure_vehicle_shape(dict(item), index)
        _hydrate_from_widgets(entry, index)
        return entry
    old_id = int(item) if isinstance(item, int) else index + 1
    entry = _default_vehicle_entry(index)
    model = str(st.session_state.get(f"veh_{old_id}_model") or st.session_state.get(_model_key(index)) or "").strip()
    category = str(
        st.session_state.get(f"veh_{old_id}_kategorie")
        or st.session_state.get(_category_key(index))
        or "Alle"
    )
    entry["model"] = model
    entry["category"] = category or "Alle"
    entry["name"] = model or entry["name"]
    _hydrate_from_widgets(entry, index)
    return entry


def ensure_vehicle_slots() -> list[dict]:
    """Mindestens ein vollständiges Fahrzeug-Dict in der Session."""
    raw = st.session_state.get(VEHICLE_SLOTS_KEY)
    if not raw:
        st.session_state[VEHICLE_SLOTS_KEY] = [_default_vehicle_entry(0)]
        return st.session_state[VEHICLE_SLOTS_KEY]
    migrated = [
        _normalize_vehicle_entry(item, index) for index, item in enumerate(list(raw))
    ]
    st.session_state[VEHICLE_SLOTS_KEY] = migrated
    return migrated


def add_vehicle_slot() -> None:
    """Hängt einen neuen Fahrzeug-Eintrag an die Session-Liste."""
    slots = ensure_vehicle_slots()
    if len(slots) >= MAX_ACTIVE_VEHICLES:
        return
    slots.append(_default_vehicle_entry(len(slots)))
    st.session_state[VEHICLE_SLOTS_KEY] = slots
    st.session_state[SELECTED_VEHICLE_KEY] = len(slots) - 1


def _sync_vehicle_category(index: int) -> None:
    """Schreibt die Kategorie nur in den Eintrag am Index."""
    vehicles = st.session_state.get(VEHICLE_SLOTS_KEY) or []
    if not (0 <= index < len(vehicles)):
        return
    _ensure_vehicle_shape(vehicles[index], index)
    category = str(st.session_state.get(_category_key(index)) or "Alle")
    vehicles[index]["category"] = category


def _sync_vehicle_model(index: int) -> None:
    """Aktualisiert Name und Modell nur am Index des geänderten Dropdowns."""
    vehicles = st.session_state.get(VEHICLE_SLOTS_KEY) or []
    if not (0 <= index < len(vehicles)):
        return
    _ensure_vehicle_shape(vehicles[index], index)
    model = str(st.session_state.get(_model_key(index)) or "").strip()
    vehicles[index]["model"] = model
    vehicles[index]["name"] = model or f"Fahrzeug {index + 1}"


def _sync_vehicle_fertigkeit(index: int) -> None:
    vehicles = st.session_state.get(VEHICLE_SLOTS_KEY) or []
    if not (0 <= index < len(vehicles)):
        return
    _ensure_vehicle_shape(vehicles[index], index)
    label = str(st.session_state.get(_fertigkeit_key(index)) or "Bodenfahrzeuge")
    vehicles[index]["fertigkeit"] = label


def _sync_vehicle_autopilot(index: int) -> None:
    """Schreibt den Autopilot-Toggle nur in den Eintrag am Index."""
    vehicles = st.session_state.get(VEHICLE_SLOTS_KEY) or []
    if not (0 <= index < len(vehicles)):
        return
    _ensure_vehicle_shape(vehicles[index], index)
    active = bool(st.session_state.get(_autopilot_key(index)))
    vehicles[index]["autopilot_active"] = active
    vehicles[index]["autopilot"] = active


def _sync_vehicle_mod(index: int, field: str) -> None:
    vehicles = st.session_state.get(VEHICLE_SLOTS_KEY) or []
    if not (0 <= index < len(vehicles)):
        return
    _ensure_vehicle_shape(vehicles[index], index)
    vehicles[index]["mods"][field] = _as_int(
        st.session_state.get(_vehicle_mod_key(field, index), 0), 0
    )


def _sync_vehicle_weapon(index: int, slot: int) -> None:
    """Speichert die gewählte Waffe sofort im Fahrzeug-Dict."""
    vehicles = st.session_state.get(VEHICLE_SLOTS_KEY) or []
    if not (0 <= index < len(vehicles)):
        return
    _ensure_vehicle_shape(vehicles[index], index)
    name = str(st.session_state.get(_weapon_slot_key(index, slot)) or WEAPON_NONE)
    vehicles[index]["weapons"][slot]["name"] = name
    vehicles[index][_weapon_dict_key(slot)] = name


def _sync_vehicle_weapon_fixed(index: int, slot: int) -> None:
    vehicles = st.session_state.get(VEHICLE_SLOTS_KEY) or []
    if not (0 <= index < len(vehicles)):
        return
    _ensure_vehicle_shape(vehicles[index], index)
    fixed = bool(st.session_state.get(_weapon_fixed_key(index, slot)))
    vehicles[index]["weapons"][slot]["fixed"] = fixed
    vehicles[index][_weapon_fixed_dict_key(slot)] = fixed


def _sync_vehicle_gun_spec(index: int, slot: int) -> None:
    vehicles = st.session_state.get(VEHICLE_SLOTS_KEY) or []
    if not (0 <= index < len(vehicles)):
        return
    _ensure_vehicle_shape(vehicles[index], index)
    vehicles[index]["weapons"][slot]["gun_spec"] = bool(
        st.session_state.get(_gun_spec_key(index, slot))
    )


def _sync_vehicle_drive_spec(index: int) -> None:
    vehicles = st.session_state.get(VEHICLE_SLOTS_KEY) or []
    if not (0 <= index < len(vehicles)):
        return
    _ensure_vehicle_shape(vehicles[index], index)
    vehicles[index]["drive_spec"] = bool(st.session_state.get(_drive_spec_key(index)))


def _sync_vehicle_damage(index: int) -> None:
    vehicles = st.session_state.get(VEHICLE_SLOTS_KEY) or []
    if not (0 <= index < len(vehicles)):
        return
    _ensure_vehicle_shape(vehicles[index], index)
    vehicles[index]["current_damage"] = max(
        0, _as_int(st.session_state.get(_damage_key(index)), 0)
    )


def _move_vehicle_widget_state(src: int, dst: int) -> None:
    """Kopiert persistierte Fahrzeug-Widgets von einem Index auf einen anderen."""
    if src == dst:
        return
    for src_key, dst_key in zip(_vehicle_widget_keys(src), _vehicle_widget_keys(dst)):
        if src_key in st.session_state:
            st.session_state[dst_key] = st.session_state[src_key]
        elif dst_key in st.session_state:
            del st.session_state[dst_key]


def _clear_vehicle_index_keys(index: int) -> None:
    for key in _vehicle_widget_keys(index):
        if key in st.session_state:
            del st.session_state[key]


def _on_delete_vehicle(index: int) -> None:
    """Callback: löscht das Fahrzeug, bevor die Widgets neu aufgebaut werden."""
    if len(ensure_vehicle_slots()) <= 1:
        st.session_state["_vehicle_delete_blocked"] = True
        return
    delete_vehicle_slot(index)
    st.rerun()


def delete_vehicle_slot(index: int) -> bool:
    """Entfernt ein Fahrzeug. Widget-Keys werden geleert und neu aus den Dicts gebunden."""
    slots = ensure_vehicle_slots()
    if len(slots) <= 1 or not (0 <= index < len(slots)):
        return False
    slots.pop(index)
    st.session_state[VEHICLE_SLOTS_KEY] = slots
    _clear_vehicle_widget_keys()
    selected = _ss_int(SELECTED_VEHICLE_KEY, 0)
    if selected > index:
        selected -= 1
    st.session_state[SELECTED_VEHICLE_KEY] = max(0, min(selected, len(slots) - 1))
    return True


def effective_vehicle_mode(global_mode: InterfaceMode, index: int) -> InterfaceMode:
    """Autopilot-Toggle des Fahrzeugs überschreibt den globalen Interfacemodus."""
    resolved_global = (
        InterfaceMode.AR if global_mode is InterfaceMode.AUTOPILOT else global_mode
    )
    key = _autopilot_key(index)
    if key in st.session_state:
        return (
            InterfaceMode.AUTOPILOT if st.session_state[key] else resolved_global
        )
    slots = st.session_state.get(VEHICLE_SLOTS_KEY) or []
    if 0 <= index < len(slots) and _as_bool(
        slots[index].get("autopilot_active", slots[index].get("autopilot"))
    ):
        return InterfaceMode.AUTOPILOT
    return resolved_global


def _ss_int(key: str, default: int) -> int:
    """Liest einen Integer aus der Session, sonst den Default."""
    if key not in st.session_state or st.session_state[key] is None:
        return default
    try:
        return int(st.session_state[key])
    except (TypeError, ValueError):
        return default


def _ss_bool(key: str, default: bool = False) -> bool:
    return bool(st.session_state.get(key, default))


def _ss_str_list(key: str) -> list[str]:
    raw = st.session_state.get(key) or []
    if isinstance(raw, str):
        return [raw] if raw else []
    return [str(item) for item in raw if item]


def _profile_filename(rigger_name: str) -> str:
    raw = (rigger_name or "Rigger").strip() or "Rigger"
    safe = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in raw)
    safe = "_".join(safe.split()) or "Rigger"
    return f"rigger_profile_{safe}.json"


def _parse_interface_mode(raw: object) -> InterfaceMode:
    if isinstance(raw, InterfaceMode):
        if raw is InterfaceMode.AUTOPILOT:
            return InterfaceMode.AR
        return raw
    text = str(raw or "AR").strip()
    if text.upper() == "AUTOPILOT" or text == "Autopilot":
        return InterfaceMode.AR
    try:
        parsed = InterfaceMode(text)
    except ValueError:
        parsed = None
    if parsed is InterfaceMode.AUTOPILOT:
        return InterfaceMode.AR
    if parsed is not None:
        return parsed
    try:
        parsed = InterfaceMode[text]
    except KeyError:
        parsed = None
    if parsed is InterfaceMode.AUTOPILOT:
        return InterfaceMode.AR
    if parsed is not None:
        return parsed
    for mode, label in INTERFACE_LABELS.items():
        if text == label and mode is not InterfaceMode.AUTOPILOT:
            return mode
    return InterfaceMode.AR


def _clear_vehicle_widget_keys() -> None:
    """Entfernt alte Fahrzeug-Widget-Keys, damit Import keine Reste hinterlässt."""
    stale: list = []
    for key in list(st.session_state.keys()):
        text = str(key)
        if any(text.startswith(prefix) for prefix in VEHICLE_WIDGET_PREFIXES):
            stale.append(key)
            continue
        parts = text.split("_", 2)
        if len(parts) >= 3 and parts[0] == "veh" and parts[1].isdigit():
            stale.append(key)
    for index in range(MAX_ACTIVE_VEHICLES):
        stale.extend(_vehicle_widget_keys(index))
    for key in dict.fromkeys(stale):
        if key in st.session_state:
            del st.session_state[key]


def _collect_vehicle_profile(index: int, entry: dict) -> dict:
    """Exportiert ein Fahrzeug inkl. Mods, Waffen und Einstellungen."""
    _ensure_vehicle_shape(entry, index)
    return {
        "name": entry.get("name") or f"Fahrzeug {index + 1}",
        "category": entry.get("category") or "Alle",
        "model": entry.get("model") or "",
        "fertigkeit": entry.get("fertigkeit") or "Bodenfahrzeuge",
        "mods": dict(entry.get("mods") or _default_mods()),
        "drive_spec": _as_bool(entry.get("drive_spec")),
        "autopilot_active": _as_bool(entry.get("autopilot_active")),
        "autopilot": _as_bool(entry.get("autopilot_active")),
        "current_damage": max(0, _as_int(entry.get("current_damage"), 0)),
        "weapons": list(entry.get("weapons") or _default_weapons()),
        **{
            _weapon_dict_key(slot): _weapon_name_from_entry(entry, slot)
            for slot in range(MAX_MOUNTED_WEAPONS)
        },
        **{
            _weapon_fixed_dict_key(slot): _as_bool(
                (entry.get("weapons") or [{}])[slot].get("fixed")
                if slot < len(entry.get("weapons") or [])
                else entry.get(_weapon_fixed_dict_key(slot))
            )
            for slot in range(MAX_MOUNTED_WEAPONS)
        },
    }


def build_profile_payload() -> dict:
    """Sammelt alle relevanten Session-Daten für den JSON-Export."""
    mode = _parse_interface_mode(st.session_state.get("interface_mode", InterfaceMode.AR))
    slots = ensure_vehicle_slots()
    return {
        "format": PROFILE_FORMAT,
        "version": PROFILE_VERSION,
        "rigger_name": st.session_state.get("rigger_name") or "Rigger",
        "attribute": {
            "rea": _ss_int("attr_rea", 4),
            "log": _ss_int("attr_log", 4),
            "intuition": _ss_int("attr_int", 4),
        },
        "fertigkeiten": {
            "bodenfahrzeuge": _ss_int("skill_bodenfahrzeuge", 4),
            "flugzeuge": _ss_int("skill_flugzeuge", 0),
            "laeufer": _ss_int("skill_laeufer", 0),
            "wasserfahrzeuge": _ss_int("skill_wasserfahrzeuge", 0),
            "geschuetze": _ss_int("skill_geschuetze", 3),
            "wahrnehmung": _ss_int("skill_wahrnehmung", 3),
            "elektronische_kriegsfuehrung": _ss_int(
                "skill_elektronische_kriegsfuehrung", 0
            ),
            "schleichen": _ss_int("skill_schleichen", 0),
        },
        "spezialisierungen": {
            "bodenfahrzeuge": _ss_str_list("spec_bodenfahrzeuge"),
            "flugzeuge": _ss_str_list("spec_flugzeuge"),
            "laeufer": _ss_str_list("spec_laeufer"),
            "wasserfahrzeuge": _ss_str_list("spec_wasserfahrzeuge"),
            "geschuetze": _ss_str_list("spec_geschuetze"),
        },
        "cyberware": {
            "riggerkontrolle": _ss_int("rigger_control", 1),
            "riggerkontrollbooster": _ss_int("riggerkontrollbooster", 0),
            "smartlink_active": _ss_bool("smartlink_active"),
            "vorteile": _ss_str_list("qualities"),
        },
        "matrix": {
            "steuergeraet": st.session_state.get("steuergeraet"),
            "interface_mode": mode.value if isinstance(mode, InterfaceMode) else str(mode),
            "autosofts": _ss_str_list("autosofts"),
            "cyberprogramme": _ss_str_list("console_programme"),
            "rauschen": _ss_int("umgebung_rauschen", 0),
        },
        "selected_vehicle_idx": _ss_int(SELECTED_VEHICLE_KEY, 0),
        "active_vehicles": [
            _collect_vehicle_profile(index, entry) for index, entry in enumerate(slots)
        ],
    }


def _apply_vehicle_profile(
    index: int,
    data: dict,
    weapon_catalog: dict[str, WeaponTemplate] | None,
    *,
    default_autopilot: bool = False,
) -> dict:
    """Schreibt ein importiertes Fahrzeug ins persistente Dict."""
    entry = _default_vehicle_entry(index)
    category = str(data.get("category") or "Alle").strip() or "Alle"
    model = str(data.get("model") or "").strip()
    name = str(data.get("name") or "").strip() or model or entry["name"]
    fertigkeit = str(data.get("fertigkeit") or "").strip()
    if "autopilot_active" in data:
        autopilot = _as_bool(data.get("autopilot_active"))
    elif "autopilot" in data:
        autopilot = _as_bool(data.get("autopilot"))
    else:
        autopilot = default_autopilot
    entry["name"] = name
    entry["category"] = category
    entry["model"] = model
    entry["fertigkeit"] = fertigkeit if fertigkeit in FERTIGKEIT_OPTIONS else "Bodenfahrzeuge"
    entry["autopilot_active"] = autopilot
    entry["autopilot"] = autopilot
    entry["drive_spec"] = bool(data.get("drive_spec"))
    current_damage = _as_int(data.get("current_damage"), 0)
    monitor = data.get("zustandsmonitor")
    if isinstance(monitor, dict):
        current_damage = _as_int(monitor.get("aktuell"), current_damage)
    entry["current_damage"] = max(0, current_damage)
    mods = data.get("mods") or {}
    if not isinstance(mods, dict):
        mods = {}
    for field, _label in VEHICLE_MOD_FIELDS:
        entry["mods"][field] = _as_int(mods.get(field, 0), 0)
    weapons = data.get("weapons") or []
    if not isinstance(weapons, list):
        weapons = []
    known_weapons = set(weapon_catalog) if weapon_catalog else None
    for slot in range(MAX_MOUNTED_WEAPONS):
        raw = weapons[slot] if slot < len(weapons) else {}
        named = data.get(_weapon_dict_key(slot))
        fixed = False
        if isinstance(raw, str):
            weapon_name = raw
            gun_spec = False
        elif isinstance(raw, dict):
            weapon_name = str(raw.get("name") or WEAPON_NONE)
            gun_spec = _as_bool(raw.get("gun_spec"))
            fixed = _as_bool(raw.get("fixed", raw.get("feste_halterung")))
        else:
            weapon_name = WEAPON_NONE
            gun_spec = False
        if named:
            weapon_name = str(named)
        if _weapon_fixed_dict_key(slot) in data:
            fixed = _as_bool(data.get(_weapon_fixed_dict_key(slot)))
        if not weapon_name:
            weapon_name = WEAPON_NONE
        if (
            known_weapons is not None
            and weapon_name != WEAPON_NONE
            and weapon_name not in known_weapons
        ):
            weapon_name = WEAPON_NONE
            gun_spec = False
        entry["weapons"][slot] = {
            "name": weapon_name,
            "gun_spec": gun_spec,
            "fixed": fixed,
        }
        entry[_weapon_dict_key(slot)] = weapon_name
        entry[_weapon_fixed_dict_key(slot)] = fixed
    return entry


def apply_profile_payload(
    payload: dict,
    catalog: Catalog,
    weapon_catalog: dict[str, WeaponTemplate] | None = None,
) -> None:
    """Überschreibt den Session-State mit einem geladenen Rigger-Profil."""
    attributes = payload.get("attribute") or payload.get("attributes") or {}
    skills = payload.get("fertigkeiten") or payload.get("skills") or {}
    specs = payload.get("spezialisierungen") or payload.get("specializations") or {}
    cyber = payload.get("cyberware") or {}
    matrix = payload.get("matrix") or {}
    if not isinstance(attributes, dict):
        attributes = {}
    if not isinstance(skills, dict):
        skills = {}
    if not isinstance(specs, dict):
        specs = {}
    if not isinstance(cyber, dict):
        cyber = {}
    if not isinstance(matrix, dict):
        matrix = {}

    st.session_state["rigger_name"] = str(
        payload.get("rigger_name") or "Rigger"
    ).strip() or "Rigger"
    st.session_state["attr_rea"] = int(attributes.get("rea") or 4)
    st.session_state["attr_log"] = int(attributes.get("log") or 4)
    st.session_state["attr_int"] = int(attributes.get("intuition") or 4)
    st.session_state["skill_bodenfahrzeuge"] = int(skills.get("bodenfahrzeuge") or 0)
    st.session_state["skill_flugzeuge"] = int(skills.get("flugzeuge") or 0)
    st.session_state["skill_laeufer"] = int(skills.get("laeufer") or 0)
    st.session_state["skill_wasserfahrzeuge"] = int(skills.get("wasserfahrzeuge") or 0)
    st.session_state["skill_geschuetze"] = int(skills.get("geschuetze") or 0)
    st.session_state["skill_wahrnehmung"] = int(skills.get("wahrnehmung") or 0)
    st.session_state["skill_elektronische_kriegsfuehrung"] = int(
        skills.get("elektronische_kriegsfuehrung") or 0
    )
    st.session_state["skill_schleichen"] = int(skills.get("schleichen") or 0)

    def _filter_specs(skill_key: str, stored) -> list[str]:
        allowed = set(SPECIALIZATION_OPTIONS.get(skill_key, []))
        if not isinstance(stored, list):
            return []
        return [str(item) for item in stored if item in allowed]

    st.session_state["spec_bodenfahrzeuge"] = _filter_specs(
        "bodenfahrzeuge", specs.get("bodenfahrzeuge")
    )
    st.session_state["spec_flugzeuge"] = _filter_specs("flugzeuge", specs.get("flugzeuge"))
    st.session_state["spec_laeufer"] = _filter_specs("laeufer", specs.get("laeufer"))
    st.session_state["spec_wasserfahrzeuge"] = _filter_specs(
        "wasserfahrzeuge", specs.get("wasserfahrzeuge")
    )
    st.session_state["spec_geschuetze"] = _filter_specs(
        "geschuetze", specs.get("geschuetze")
    )

    st.session_state["rigger_control"] = int(cyber.get("riggerkontrolle") or 0)
    st.session_state["riggerkontrollbooster"] = int(
        cyber.get("riggerkontrollbooster") or 0
    )
    st.session_state["smartlink_active"] = bool(cyber.get("smartlink_active"))
    vorteile = cyber.get("vorteile") or cyber.get("qualities") or []
    if not isinstance(vorteile, list):
        vorteile = []
    st.session_state["qualities"] = [
        str(name) for name in vorteile if name in QUALITY_OPTIONS
    ]

    device_options = _device_options(catalog)
    device_label = matrix.get("steuergeraet")
    if device_label in device_options:
        st.session_state["steuergeraet"] = device_label
    raw_mode = matrix.get("interface_mode")
    global_was_autopilot = str(raw_mode).upper() in {"AUTOPILOT"}
    st.session_state["interface_mode"] = _parse_interface_mode(raw_mode)
    autosoft_names = matrix.get("autosofts") or []
    if not isinstance(autosoft_names, list):
        autosoft_names = []
    st.session_state["autosofts"] = [
        str(name) for name in autosoft_names if name in catalog.autosofts
    ]
    programmes = matrix.get("cyberprogramme") or matrix.get("console_programme") or []
    if not isinstance(programmes, list):
        programmes = []
    st.session_state["console_programme"] = [
        str(name) for name in programmes if name in CYBERPROGRAM_OPTIONS
    ]
    st.session_state["umgebung_rauschen"] = int(matrix.get("rauschen") or 0)

    _clear_vehicle_widget_keys()
    vehicles = payload.get("active_vehicles") or []
    if not isinstance(vehicles, list):
        vehicles = []
    new_slots: list[dict] = []
    for index, item in enumerate(vehicles[:MAX_ACTIVE_VEHICLES]):
        if not isinstance(item, dict):
            item = _normalize_vehicle_entry(item, index)
        new_slots.append(
            _apply_vehicle_profile(
                index,
                item,
                weapon_catalog,
                default_autopilot=global_was_autopilot,
            )
        )
    if not new_slots:
        new_slots = [_default_vehicle_entry(0)]
    st.session_state[VEHICLE_SLOTS_KEY] = new_slots
    selected = payload.get("selected_vehicle_idx", 0)
    try:
        selected_idx = int(selected)
    except (TypeError, ValueError):
        selected_idx = 0
    st.session_state[SELECTED_VEHICLE_KEY] = max(
        0, min(selected_idx, len(new_slots) - 1)
    )


def render_profile_io(
    catalog: Catalog,
    weapon_catalog: dict[str, WeaponTemplate] | None = None,
) -> None:
    """Export-Download und JSON-Import ganz oben in der Sidebar."""
    if st.session_state.pop("_profile_import_ok", False):
        st.sidebar.success("Profil geladen.")
    if "_profile_upload_nonce" not in st.session_state:
        st.session_state["_profile_upload_nonce"] = 0

    with st.sidebar.expander("[ PROFIL & DATEN ]", expanded=True):
        profile = build_profile_payload()
        rigger_name = str(st.session_state.get("rigger_name") or "Rigger")
        st.download_button(
            "Profil exportieren (JSON)",
            data=json.dumps(profile, ensure_ascii=False, indent=2),
            file_name=_profile_filename(rigger_name),
            mime="application/json",
            key="profile_download",
            help="Speichert Rigger, Matrix, Fahrzeuge, Mods und Waffen.",
        )
        uploaded = st.file_uploader(
            "Profil laden",
            type=["json"],
            key=f"profile_upload_{st.session_state['_profile_upload_nonce']}",
            help="JSON-Profil aus einem früheren Export.",
        )
        if uploaded is None:
            return
        try:
            payload = json.loads(uploaded.getvalue().decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            st.error(f"Die Datei ist kein gültiges JSON: {error}")
            return
        if not isinstance(payload, dict):
            st.error("Die JSON-Datei enthält kein Objekt.")
            return
        fmt = payload.get("format")
        if fmt and fmt != PROFILE_FORMAT:
            st.error("Diese JSON-Datei ist kein Rigger-Profil.")
            return
        try:
            apply_profile_payload(payload, catalog, weapon_catalog)
        except Exception as error:
            st.error(f"Profil konnte nicht geladen werden: {error}")
            return
        st.session_state["_profile_upload_nonce"] += 1
        st.session_state["_profile_import_ok"] = True
        st.rerun()


def ensure_selected_vehicle_idx(count: int) -> int:
    """Hält den aktiven Fahrzeug-Index im gültigen Bereich."""
    if count <= 0:
        if st.session_state.get(SELECTED_VEHICLE_KEY) != 0:
            st.session_state[SELECTED_VEHICLE_KEY] = 0
        return 0
    idx = _ss_int(SELECTED_VEHICLE_KEY, 0)
    idx = max(0, min(idx, count - 1))
    if st.session_state.get(SELECTED_VEHICLE_KEY) != idx:
        st.session_state[SELECTED_VEHICLE_KEY] = idx
    return idx


def render_vehicle_nav(slots: list[dict[str, str]]) -> int:
    """Stabile Fahrzeugwahl ohne Tab-Rebuild bei Namensänderungen."""
    idx = ensure_selected_vehicle_idx(len(slots))
    labels = _unique_tab_labels(slots)
    options = list(range(len(slots)))

    def _label(option: int) -> str:
        if 0 <= option < len(labels):
            return labels[option]
        return f"Fahrzeug {option + 1}"

    widget_kwargs = {
        "options": options,
        "format_func": _label,
        "key": SELECTED_VEHICLE_KEY,
        "label_visibility": "collapsed",
    }
    segmented = getattr(st, "segmented_control", None)
    if callable(segmented):
        chosen = segmented("Aktives Fahrzeug", required=True, **widget_kwargs)
    else:
        chosen = st.radio(
            "Aktives Fahrzeug",
            horizontal=True,
            **widget_kwargs,
        )
    if chosen is None:
        return idx
    try:
        return int(chosen)
    except (TypeError, ValueError):
        return idx


def vehicle_mods_from_session(vehicle_id: int) -> dict[str, int]:
    """Manuelle Fahrzeug-Mods aus dem persistenten Fahrzeug-Dict."""
    slots = st.session_state.get(VEHICLE_SLOTS_KEY) or []
    if 0 <= vehicle_id < len(slots):
        _ensure_vehicle_shape(slots[vehicle_id], vehicle_id)
        return dict(slots[vehicle_id]["mods"])
    return _default_mods()


def _split_mod_pair(result, fallback_left: int, fallback_right: int) -> tuple[int, int]:
    """Zerlegt das Ergebnis von ``apply_vehicle_mod`` wieder in zwei Integer."""
    if isinstance(result, str) and "/" in result:
        left_raw, right_raw = result.split("/", 1)
        try:
            return int(left_raw.strip()), int(right_raw.strip())
        except ValueError:
            return fallback_left, fallback_right
    try:
        number = int(result)
        return number, number
    except (TypeError, ValueError):
        return fallback_left, fallback_right


def apply_mods_to_vehicle_template(
    template: VehicleTemplate, mods: dict[str, int]
) -> VehicleTemplate:
    """Gibt eine Template-Kopie mit angewandten manuellen Modifikatoren zurück."""
    if not any(int(value or 0) for value in mods.values()):
        return template

    handling = apply_vehicle_mod(
        f"{template.handling_strasse}/{template.handling_gelaende}",
        mods.get("handling", 0),
    )
    accel = apply_vehicle_mod(
        f"{template.beschleunigung_normal}/{template.beschleunigung_sprint}",
        mods.get("beschleunigung", 0),
    )
    speed = apply_vehicle_mod(
        f"{template.geschwindigkeit_normal}/{template.geschwindigkeit_sprint}",
        mods.get("geschwindigkeit", 0),
    )
    h1, h2 = _split_mod_pair(
        handling, template.handling_strasse, template.handling_gelaende
    )
    a1, a2 = _split_mod_pair(
        accel, template.beschleunigung_normal, template.beschleunigung_sprint
    )
    s1, s2 = _split_mod_pair(
        speed, template.geschwindigkeit_normal, template.geschwindigkeit_sprint
    )
    pilot = apply_vehicle_mod(template.pilot, mods.get("pilot", 0))
    rumpf = apply_vehicle_mod(template.rumpf, mods.get("rumpf", 0))
    panzerung = apply_vehicle_mod(template.panzerung, mods.get("panzerung", 0))
    sensor = apply_vehicle_mod(template.sensor, mods.get("sensor", 0))
    return template.model_copy(
        update={
            "handling_strasse": int(h1),
            "handling_gelaende": int(h2),
            "beschleunigung_normal": int(a1),
            "beschleunigung_sprint": int(a2),
            "geschwindigkeit_normal": int(s1),
            "geschwindigkeit_sprint": int(s2),
            "pilot": max(0, int(pilot)),
            "rumpf": max(0, int(rumpf)),
            "panzerung": max(0, int(panzerung)),
            "sensor": max(0, int(sensor)),
        }
    )


def render_vehicle_mod_inputs(vehicle_id: int) -> dict[str, int]:
    """Eingaberaster für manuelle Fahrzeug-Modifikatoren."""
    slots = st.session_state.get(VEHICLE_SLOTS_KEY) or []
    entry = slots[vehicle_id] if 0 <= vehicle_id < len(slots) else _default_vehicle_entry(vehicle_id)
    _ensure_vehicle_shape(entry, vehicle_id)
    with st.expander("[ MODIFIKATIONEN ]", expanded=False):
        st.caption(
            "Diese Werte werden auf Katalog-Basiswerte addiert (beide Seiten bei Werten mit /)."
        )
        cols = st.columns(len(VEHICLE_MOD_FIELDS))
        for column, (field, label) in zip(cols, VEHICLE_MOD_FIELDS):
            with column:
                _bind_widget(
                    _vehicle_mod_key(field, vehicle_id),
                    _as_int(entry["mods"].get(field), 0),
                )
                st.number_input(
                    label,
                    step=1,
                    format="%d",
                    min_value=-20,
                    max_value=20,
                    key=_vehicle_mod_key(field, vehicle_id),
                    on_change=_sync_vehicle_mod,
                    args=(vehicle_id, field),
                )
    return vehicle_mods_from_session(vehicle_id)


st.set_page_config(
    page_title=APP_TITLE,
    layout="wide",
)
st.markdown(
    """
    <style>
    /* --- AGGRESSIVER STREAMLIT LAYOUT RESET --- */

    /* 1. Versteckt die unsichtbare Streamlit-Kopfzeile komplett */
    header[data-testid="stHeader"] {
        display: none !important;
        height: 0px !important;
        padding: 0px !important;
    }

    /* 2. Zerstört das gigantische Standard-Padding in allen Streamlit-Hauptcontainern */
    .block-container,
    div.block-container,
    div[data-testid="stAppViewBlockContainer"] {
        padding-top: 0rem !important;
        padding-bottom: 1rem !important;
        margin-top: 0rem !important;
    }

    /* 3. Setzt das allgemeine App-Padding zurück */
    .stApp {
        margin-top: 0px !important;
    }

    /* 4. Verhindert, dass das Image-Element selbst einen oberen Rand erzwingt */
    [data-testid="stImage"] {
        margin-top: -1rem !important;
    }

    @import url("https://fonts.googleapis.com/css2?family=Rajdhani:wght@500;600;700&display=swap");
    /* --- Streamlit Layout Reset --- */
    .block-container,
    [data-testid="stMainBlockContainer"],
    div[data-testid="stAppViewBlockContainer"] {
        padding-top: 0rem !important;
        padding-bottom: 1rem !important;
        margin-top: 0 !important;
    }
    [data-testid="stHeader"] {
        display: none !important;
        height: 0px !important;
        padding: 0px !important;
    }
    [data-testid="stToolbar"],
    [data-testid="stDecoration"] {
        display: none !important;
    }
    [data-testid="stImage"] {
        margin-top: -1rem !important;
        margin-bottom: 0 !important;
        padding: 0 !important;
    }
    [data-testid="stImage"] img {
        display: block;
        margin: 0 !important;
    }
    .element-container:has(style),
    div[data-testid="stMarkdown"]:has(style) {
        display: none !important;
        height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
    }

    html, body, [class*="css"] {
        font-family: "Rajdhani", sans-serif !important;
    }

    .stApp {
        background-color: #070a0f !important;
        background-image:
            radial-gradient(ellipse at top, rgba(0, 243, 255, 0.05) 0%, transparent 70%),
            linear-gradient(rgba(0, 243, 255, 0.02) 1px, transparent 1px),
            linear-gradient(90deg, rgba(0, 243, 255, 0.02) 1px, transparent 1px) !important;
        background-size: 100% 100%, 25px 25px, 25px 25px !important;
        color: #d1dbe5 !important;
    }

    [data-testid="stSidebar"] {
        background-color: #070a0f !important;
        border-right: 1px solid rgba(0, 243, 255, 0.2) !important;
    }

    [data-testid="stMetricValue"] {
        font-family: "Rajdhani", sans-serif !important;
        font-size: 1.35rem !important;
        font-weight: 700 !important;
        color: #00f3ff !important;
        text-shadow: 0 0 8px rgba(0, 243, 255, 0.5);
        letter-spacing: 0.5px;
    }

    [data-testid="stMetricLabel"] {
        font-family: "Rajdhani", sans-serif !important;
        font-size: 0.85rem !important;
        font-weight: 600 !important;
        text-transform: uppercase;
        letter-spacing: 1.5px;
        color: #7a8b9e !important;
    }

    div[data-testid="stMetric"],
    .stExpander,
    div[data-testid="stForm"],
    div[data-testid="stVerticalBlock"] > div[style*="border"] {
        background: rgba(13, 19, 29, 0.75) !important;
        backdrop-filter: blur(8px) !important;
        border: 1px solid rgba(0, 243, 255, 0.2) !important;
        border-top: 2px solid #00f3ff !important;
        border-radius: 4px !important;
        box-shadow: 0 8px 20px rgba(0, 0, 0, 0.6), inset 0 0 15px rgba(0, 243, 255, 0.03) !important;
    }

    button[data-baseweb="tab"] {
        background: rgba(10, 14, 22, 0.8) !important;
        border: 1px solid rgba(0, 243, 255, 0.15) !important;
        border-radius: 3px 3px 0 0 !important;
        color: #8fa0b5 !important;
        font-family: "Rajdhani", sans-serif !important;
        font-weight: 600 !important;
        font-size: 0.95rem !important;
        text-transform: uppercase;
        letter-spacing: 1.2px;
        padding: 8px 16px !important;
    }

    button[data-baseweb="tab"][aria-selected="true"] {
        background: linear-gradient(180deg, rgba(255, 0, 85, 0.25) 0%, rgba(15, 20, 30, 0.9) 100%) !important;
        border: 1px solid #ff0055 !important;
        border-bottom: 3px solid #ff0055 !important;
        color: #ffffff !important;
        text-shadow: 0 0 6px rgba(255, 0, 85, 0.6);
    }

    .stButton > button {
        background: linear-gradient(135deg, rgba(0, 243, 255, 0.15) 0%, rgba(0, 100, 150, 0.3) 100%) !important;
        border: 1px solid #00f3ff !important;
        color: #ffffff !important;
        font-family: "Rajdhani", sans-serif !important;
        font-weight: 700 !important;
        text-transform: uppercase;
        letter-spacing: 1px;
        border-radius: 2px !important;
        transition: all 0.2s ease-in-out;
    }

    .stButton > button:hover {
        background: #00f3ff !important;
        color: #000000 !important;
        box-shadow: 0 0 12px rgba(0, 243, 255, 0.8);
    }

    div[data-testid="stNotification"],
    div[data-testid="stAlert"] {
        background: rgba(255, 0, 85, 0.12) !important;
        border: 1px solid #ff0055 !important;
        border-left: 5px solid #ff0055 !important;
        color: #ff4477 !important;
        font-weight: 600;
    }

    div[data-testid="stSegmentedControl"] {
        flex-wrap: wrap;
    }
    div[data-testid="stRadio"] > div {
        flex-wrap: wrap;
        gap: 0.2rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
banner_paths = [
    "rigger_banner.png",
    "rigger_banner_png",
    "rigger_banner.jpg",
    "rigger_banner_png.png",
]
banner_loaded = False
_app_dir = Path(__file__).resolve().parent
for name in banner_paths:
    for path in (name, str(_app_dir / name)):
        if os.path.exists(path):
            st.image(path, use_container_width=True)
            banner_loaded = True
            break
    if banner_loaded:
        break
if not banner_loaded:
    st.markdown(
        "<div style='background:#070a0f; padding:20px; border-left:4px solid #00f3ff; "
        "color:#00f3ff; font-family:monospace; margin-bottom:20px;'>"
        "[ SYS_ERROR: BANNER_IMAGE_NOT_FOUND ]</div>",
        unsafe_allow_html=True,
    )


@st.cache_resource
def load_catalog() -> Catalog:
    """CSV-Kataloge einmalig laden und im Prozess halten."""
    return Catalog.load()


@st.cache_resource
def load_weapons_pandas() -> tuple[dict[str, WeaponTemplate], tuple[str, ...]]:
    """Lädt Waffen.csv per Pandas; unvollständige Zeilen werden aufgefüllt oder übersprungen."""
    path = Path(DEFAULT_DATA_DIR) / WEAPONS_FILENAME
    if not path.is_file():
        raise FileNotFoundError(f"Katalogdatei nicht gefunden: {path}")
    try:
        weapons_df = pd.read_csv(path, sep=";", encoding="utf-8-sig")
    except UnicodeDecodeError:
        weapons_df = pd.read_csv(path, sep=";", encoding="cp1252")

    text_defaults = {
        "Typ": "-",
        "Präzision": "-",
        "Schaden": "-",
        "DK": "-",
        "Reichw./Modus": "-",
    }
    for column, default in text_defaults.items():
        if column not in weapons_df.columns:
            weapons_df[column] = default
        else:
            weapons_df[column] = weapons_df[column].fillna(default)

    if "RK" not in weapons_df.columns:
        weapons_df["RK"] = 0
    else:
        rk_extracted = weapons_df["RK"].astype(str).str.extract(r"(-?\d+)", expand=False)
        weapons_df["RK"] = pd.to_numeric(rk_extracted, errors="coerce").fillna(0).astype(int)

    catalog: dict[str, WeaponTemplate] = {}
    skipped: list[str] = []
    for line_no, row in enumerate(weapons_df.to_dict(orient="records"), start=2):
        payload: dict[str, object] = {}
        for key, value in row.items():
            if pd.isna(value):
                continue
            payload[key] = value
        payload.setdefault("DK", "-")
        payload.setdefault("Reichw./Modus", "-")
        payload.setdefault("Typ", "-")
        payload.setdefault("Präzision", "-")
        payload.setdefault("Schaden", "-")
        payload["RK"] = int(row["RK"]) if not pd.isna(row["RK"]) else 0
        raw_name = str(payload.get("Name") or "").strip()
        if not raw_name or raw_name.lower() in {"nan", "none"}:
            skipped.append(f"Zeile {line_no} (ohne Name)")
            continue
        try:
            item = WeaponTemplate.model_validate(payload)
        except Exception:
            skipped.append(f"{raw_name} (Zeile {line_no})")
            continue
        catalog[item.name] = item
    return catalog, tuple(skipped)


def mounted_weapons_from_session(
    weapon_catalog: dict[str, WeaponTemplate],
    vehicle_id: int,
    *,
    smartlink_active: bool = False,
) -> list[MountedWeapon]:
    """Liest die vier Waffenslots aus dem persistenten Fahrzeug-Dict."""
    slots = st.session_state.get(VEHICLE_SLOTS_KEY) or []
    entry = slots[vehicle_id] if 0 <= vehicle_id < len(slots) else _default_vehicle_entry(vehicle_id)
    _ensure_vehicle_shape(entry, vehicle_id)
    mounted: list[MountedWeapon] = []
    for index in range(MAX_MOUNTED_WEAPONS):
        choice = _weapon_name_from_entry(entry, index)
        if not choice or choice == WEAPON_NONE:
            continue
        template = weapon_catalog.get(choice)
        if template is None:
            continue
        checked = _as_bool(entry["weapons"][index].get("gun_spec"))
        fixed = _as_bool(
            entry["weapons"][index].get("fixed", entry.get(_weapon_fixed_dict_key(index)))
        )
        mounted.append(
            make_mounted_weapon(
                template,
                smartlink=smartlink_active,
                spezialisierung_aktiv=checked,
                feste_halterung=fixed,
            )
        )
    return mounted


def build_character(
    *,
    name: str,
    rea: int,
    log: int,
    intuition: int,
    bodenfahrzeuge: int,
    flugzeuge: int,
    laeufer: int,
    wasserfahrzeuge: int,
    geschuetze: int,
    wahrnehmung: int,
    elektronische_kriegsfuehrung: int,
    schleichen: int,
    rigger_control: int,
    riggerkontrollbooster: int,
    quality_names: list[str],
    smartlink_active: bool = False,
    spezialisierungen: SkillSpecializations | None = None,
    riggerkonsole: RiggerConsole | None = None,
    programme: list[str] | None = None,
    rauschen: int = 0,
) -> Character:
    """Baut das Pydantic-Character-Objekt aus den Sidebar-Eingaben."""
    programs = list(programme or [])
    console = riggerkonsole or RiggerConsole()
    console.programme = programs
    return Character(
        name=name.strip() or "Rigger",
        attribute=Attribute(REA=rea, LOG=log, INT=intuition),
        skills=Skills(
            bodenfahrzeuge=bodenfahrzeuge,
            flugzeuge=flugzeuge,
            laeufer=laeufer,
            wasserfahrzeuge=wasserfahrzeuge,
            geschuetze=geschuetze,
            wahrnehmung=wahrnehmung,
            elektronische_kriegsfuehrung=elektronische_kriegsfuehrung,
            schleichen=schleichen,
        ),
        spezialisierungen=spezialisierungen or SkillSpecializations(),
        riggerkonsole=console,
        programme=programs,
        rauschen=max(int(rauschen), 0),
        rigger_control_level=rigger_control,
        riggerkontrollbooster=riggerkontrollbooster,
        vorteile=list(quality_names),
        qualities=[
            Quality(name=name, beschreibung=QUALITY_EFFECTS.get(name, ""))
            for name in quality_names
        ],
        smartlink_active=smartlink_active,
    )


def render_sidebar(
    catalog: Catalog,
    weapon_catalog: dict[str, WeaponTemplate] | None = None,
) -> tuple[Character, Steuergeraet, InterfaceMode, list]:
    stored_mode = st.session_state.get("interface_mode")
    if stored_mode is InterfaceMode.AUTOPILOT or stored_mode == "AUTOPILOT":
        st.session_state["interface_mode"] = InterfaceMode.AR
        for index, item in enumerate(st.session_state.get(VEHICLE_SLOTS_KEY) or []):
            if isinstance(item, dict):
                item["autopilot_active"] = True
                item["autopilot"] = True
            st.session_state[_autopilot_key(index)] = True
    render_profile_io(catalog, weapon_catalog)
    rigger_name = st.sidebar.text_input("Riggername", value="Rigger", key="rigger_name")

    device_options = _device_options(catalog)
    if not device_options:
        st.sidebar.error("Keine Riggerkonsolen oder Kommlinks im Katalog.")
        st.stop()

    with st.sidebar.expander("Globale Matrix- & Riggereinstellungen", expanded=True):
        device_label = st.selectbox(
            "Steuergerät",
            options=list(device_options),
            key="steuergeraet",
            help="Riggerkonsole oder Kommlink aus dem Katalog.",
        )
        steuergeraet = device_options[device_label]
        st.caption(_device_caption(steuergeraet))
        mode = st.radio(
            "Interfacemodus",
            options=list(GLOBAL_INTERFACE_MODES),
            format_func=lambda m: INTERFACE_LABELS[m],
            key="interface_mode",
        )
        autosoft_names = st.multiselect(
            "Autosofts",
            options=list(catalog.autosofts),
            key="autosofts",
            help="Teilen sich die Programmslots der Konsole mit den Cyberprogrammen.",
        )
        autosofts = [catalog.autosofts[name] for name in autosoft_names]
        programme = st.multiselect(
            "Cyberprogramme",
            options=list(CYBERPROGRAM_OPTIONS),
            key="console_programme",
            help="Laufende Programme. Limit = Gerätestufe, +2 mit Virtueller Maschine.",
        )
        max_programme = program_slot_limit(int(steuergeraet.geraetestufe), list(programme))
        slots_used = len(programme) + len(autosofts)
        st.caption(
            f"Programmslots: {slots_used} / {max_programme} "
            f"({len(programme)} Cyberprogramme + {len(autosofts)} Autosofts)"
        )
        if slots_used > max_programme:
            st.warning(
                f"Zu viele Programme ({slots_used}/{max_programme}). "
                "Cyberprogramme und Autosofts teilen sich die Slots der Konsole."
            )

    st.sidebar.subheader("Umgebung")
    rauschen = st.sidebar.number_input(
        "Rauschen",
        min_value=0,
        max_value=20,
        value=0,
        step=1,
        key="umgebung_rauschen",
        help="Umgebungsrauschen der Funkstrecke. Direktverbindung ignoriert es.",
    )

    with st.sidebar.expander("Cyberware & Vorteile", expanded=True):
        rig_disabled = mode is InterfaceMode.AR
        rigger_control = st.slider(
            "Riggerkontrolle",
            min_value=0,
            max_value=3,
            value=1,
            disabled=rig_disabled,
            key="rigger_control",
            help="Wirkt in VR und Direktverbindung auf Limits und Pools.",
        )
        riggerkontrollbooster = st.slider(
            "Riggerkontrollbooster",
            min_value=0,
            max_value=3,
            value=0,
            disabled=rig_disabled,
            key="riggerkontrollbooster",
            help="Wirkt in VR und Direktverbindung nur auf Würfelpools, nicht auf Limits.",
        )
        if rig_disabled:
            st.caption("In AR ohne Wirkung – Slider gesperrt.")
        smartlink_active = st.checkbox("Smartlink aktiv", key="smartlink_active")
        quality_names = st.multiselect(
            "Vorteile",
            options=QUALITY_OPTIONS,
            key="qualities",
            help="Rigger-Vorteile. Harte Boni gelten nur, wenn der Modus sie zulässt.",
        )

    st.sidebar.subheader("Attribute und Fertigkeiten")
    st.sidebar.caption("Basis-Werte")
    st.sidebar.markdown("**Attribute**")
    rea = st.sidebar.number_input(
        "Reaktion (REA)", min_value=1, max_value=12, value=4, step=1, key="attr_rea"
    )
    log = st.sidebar.number_input(
        "Logik (LOG)", min_value=1, max_value=12, value=4, step=1, key="attr_log"
    )
    intuition = st.sidebar.number_input(
        "Intuition (INT)", min_value=1, max_value=12, value=4, step=1, key="attr_int"
    )
    st.sidebar.markdown("**Fertigkeiten**")
    bodenfahrzeuge = st.sidebar.number_input(
        "Bodenfahrzeuge", min_value=0, max_value=25, value=4, step=1, key="skill_bodenfahrzeuge"
    )
    spec_boden = st.sidebar.multiselect(
        "Spezialisierung Bodenfahrzeuge",
        options=SPECIALIZATION_OPTIONS["bodenfahrzeuge"],
        key="spec_bodenfahrzeuge",
    )
    flugzeuge = st.sidebar.number_input(
        "Flugzeuge", min_value=0, max_value=25, value=0, step=1, key="skill_flugzeuge"
    )
    spec_flug = st.sidebar.multiselect(
        "Spezialisierung Flugzeuge",
        options=SPECIALIZATION_OPTIONS["flugzeuge"],
        key="spec_flugzeuge",
    )
    laeufer = st.sidebar.number_input(
        "Läufer", min_value=0, max_value=25, value=0, step=1, help="Walker", key="skill_laeufer"
    )
    spec_laeufer = st.sidebar.multiselect(
        "Spezialisierung Läufer",
        options=SPECIALIZATION_OPTIONS["laeufer"],
        key="spec_laeufer",
    )
    wasserfahrzeuge = st.sidebar.number_input(
        "Schiffe",
        min_value=0,
        max_value=25,
        value=0,
        step=1,
        help="Watercraft",
        key="skill_wasserfahrzeuge",
    )
    spec_schiffe = st.sidebar.multiselect(
        "Spezialisierung Schiffe",
        options=SPECIALIZATION_OPTIONS["wasserfahrzeuge"],
        key="spec_wasserfahrzeuge",
    )
    geschuetze = st.sidebar.number_input(
        "Geschütze", min_value=0, max_value=25, value=3, step=1, key="skill_geschuetze"
    )
    spec_guns = st.sidebar.multiselect(
        "Spezialisierung Geschütze",
        options=SPECIALIZATION_OPTIONS["geschuetze"],
        key="spec_geschuetze",
    )
    wahrnehmung = st.sidebar.number_input(
        "Wahrnehmung", min_value=0, max_value=25, value=3, step=1, key="skill_wahrnehmung"
    )
    elektronische_kriegsfuehrung = st.sidebar.number_input(
        "Elektronische Kriegsführung",
        min_value=0,
        max_value=25,
        value=0,
        step=1,
        help="Electronic Warfare",
        key="skill_elektronische_kriegsfuehrung",
    )
    schleichen = st.sidebar.number_input(
        "Schleichen",
        min_value=0,
        max_value=25,
        value=0,
        step=1,
        help="Stealth",
        key="skill_schleichen",
    )

    character = build_character(
        name=rigger_name,
        rea=rea,
        log=log,
        intuition=intuition,
        bodenfahrzeuge=bodenfahrzeuge,
        flugzeuge=flugzeuge,
        laeufer=laeufer,
        wasserfahrzeuge=wasserfahrzeuge,
        geschuetze=geschuetze,
        wahrnehmung=wahrnehmung,
        elektronische_kriegsfuehrung=elektronische_kriegsfuehrung,
        schleichen=schleichen,
        rigger_control=rigger_control,
        riggerkontrollbooster=riggerkontrollbooster,
        quality_names=quality_names,
        smartlink_active=smartlink_active,
        spezialisierungen=SkillSpecializations(
            bodenfahrzeuge=spec_boden,
            flugzeuge=spec_flug,
            laeufer=spec_laeufer,
            wasserfahrzeuge=spec_schiffe,
            geschuetze=spec_guns,
        ),
        riggerkonsole=RiggerConsole(
            geraetestufe=int(steuergeraet.geraetestufe),
            basis_datenverarbeitung=int(steuergeraet.datenverarbeitung),
            basis_firewall=int(steuergeraet.firewall),
            programme=list(programme),
        ),
        programme=list(programme),
        rauschen=int(rauschen),
    )
    return character, steuergeraet, mode, autosofts


def _device_options(catalog: Catalog) -> dict[str, Steuergeraet]:
    options: dict[str, Steuergeraet] = {}
    for key, item in catalog.riggerkonsolen.items():
        options[f"Konsole: {key}"] = item
    for key, item in catalog.commlinks.items():
        options[f"Kommlink: {key}"] = item
    return options


def _device_caption(device: Steuergeraet) -> str:
    parts = [
        f"Gerätestufe {device.geraetestufe}",
        f"DV {device.datenverarbeitung}",
        f"Firewall {device.firewall}",
    ]
    if isinstance(device, RiggerConsoleTemplate):
        parts.append(f"Programme {device.programme}")
    return " · ".join(parts)


def render_vehicle_selectors(
    catalog: Catalog, index: int
) -> tuple[VehicleTemplate, Fahrzeugfertigkeit]:
    """Kategorie, Modell und Fertigkeit eines Fahrzeug-Tabs."""
    if not catalog.vehicles:
        st.error("Keine Fahrzeuge im Katalog.")
        st.stop()

    slots = st.session_state.get(VEHICLE_SLOTS_KEY) or []
    entry = slots[index] if 0 <= index < len(slots) else _default_vehicle_entry(index)
    _ensure_vehicle_shape(entry, index)

    action_col, delete_col = st.columns([4, 1])
    with delete_col:
        st.button(
            "[ LÖSCHEN ] Fahrzeug",
            key=f"delete_veh_{index}",
            on_click=_on_delete_vehicle,
            args=(index,),
        )
        if st.session_state.pop("_vehicle_delete_blocked", False):
            st.warning("Mindestens ein Fahrzeug muss aktiv bleiben")
    with action_col:
        auto_key = _autopilot_key(index)
        _bind_widget(auto_key, _as_bool(entry.get("autopilot_active", entry.get("autopilot"))))
        st.toggle(
            "[ AUTOPILOT ] für dieses Fahrzeug aktivieren",
            key=auto_key,
            on_change=_sync_vehicle_autopilot,
            args=(index,),
        )

    categories = sorted({item.kategorie for item in catalog.vehicles.values()})
    category_options = ["Alle"] + categories
    cat_key = _category_key(index)
    saved_category = str(entry.get("category") or "Alle")
    if saved_category not in category_options:
        saved_category = "Alle"
    _bind_widget(cat_key, saved_category)
    kategorie = st.selectbox(
        "Kategorie",
        options=category_options,
        key=cat_key,
        on_change=_sync_vehicle_category,
        args=(index,),
    )
    vehicle_keys = [
        key
        for key, item in catalog.vehicles.items()
        if kategorie == "Alle" or item.kategorie == kategorie
    ]
    if not vehicle_keys:
        st.warning("Keine Fahrzeuge in dieser Kategorie.")
        st.stop()

    model_key = _model_key(index)
    saved_model = str(entry.get("model") or "").strip()
    if saved_model in vehicle_keys:
        _bind_widget(model_key, saved_model)
    elif model_key not in st.session_state or st.session_state[model_key] not in vehicle_keys:
        _bind_widget(model_key, vehicle_keys[0])
        if 0 <= index < len(slots):
            slots[index]["model"] = vehicle_keys[0]
            slots[index]["name"] = vehicle_keys[0]

    vehicle_key = st.selectbox(
        "Fahrzeug / Drohne",
        options=vehicle_keys,
        key=model_key,
        on_change=_sync_vehicle_model,
        args=(index,),
    )
    vehicle_template = catalog.vehicles[vehicle_key]
    st.caption(
        f"{vehicle_template.kategorie} · Pilot {vehicle_template.pilot} · "
        f"Handling {vehicle_template.handling_strasse}/"
        f"{vehicle_template.handling_gelaende} · "
        f"Sensor {vehicle_template.sensor}"
    )
    guessed = vehicle_skill_name(vehicle_template.kategorie)
    default_label = SKILL_LABELS.get(guessed, "Bodenfahrzeuge")
    fert_key = _fertigkeit_key(index)
    saved_fert = str(entry.get("fertigkeit") or "").strip()
    if saved_fert not in FERTIGKEIT_OPTIONS:
        saved_fert = default_label
    _bind_widget(fert_key, saved_fert)
    fertigkeit_label = st.selectbox(
        "Erforderliche Fahrzeugfertigkeit",
        options=FERTIGKEIT_OPTIONS,
        key=fert_key,
        on_change=_sync_vehicle_fertigkeit,
        args=(index,),
        help="Drohnen können fahren, fliegen oder laufen – hier festlegen.",
    )
    fahrzeugfertigkeit = FERTIGKEIT_BY_LABEL[fertigkeit_label]
    return vehicle_template, fahrzeugfertigkeit


def _unique_tab_labels(vehicles: list[dict[str, str]]) -> list[str]:
    """Tab-Namen aus der Fahrzeugliste; Duplikate bekommen einen Zähler."""
    labels: list[str] = []
    seen: dict[str, int] = {}
    for item in vehicles:
        name = str(item.get("name") or "Fahrzeug")
        count = seen.get(name, 0) + 1
        seen[name] = count
        labels.append(name if count == 1 else f"{name} ({count})")
    return labels


def render_matrix_header(
    konsole: CalculatedConsoleStats, character: Character, mode: InterfaceMode
) -> None:
    """Globale Matrixwerte über den Fahrzeug-Tabs."""
    if mode in (InterfaceMode.VR_COLD, InterfaceMode.VR_HOT, InterfaceMode.DIREKTVERBINDUNG):
        rk = character.rigger_control_level
        booster = character.riggerkontrollbooster
        if booster > 0:
            st.success(
                f"**Riggerkontrolle Stufe {rk} + Booster Stufe {booster} aktiv** – "
                "Limits und Fahrzeugpools sind entsprechend erhöht."
            )
        else:
            st.success(
                f"**Riggerkontrolle Stufe {rk} aktiv** – "
                "Limits und Fahrzeugpools sind um diese Stufe erhöht."
            )
    else:
        st.info(
            "**Riggerkontrolle inaktiv** – keine Limit-Boni durch die "
            "Riggersteuerung (AR oder Autopilot)."
        )

    with st.container(border=True):
        st.markdown("**Matrix / Riggerkonsole**")
        _metric_grid(
            [
                (
                    "Datenverarbeitung",
                    konsole.datenverarbeitung,
                    konsole.datenverarbeitung_formel,
                ),
                (
                    "Firewall",
                    konsole.firewall,
                    konsole.firewall_formel,
                ),
                (
                    "Rauschunterdrückung",
                    konsole.rauschunterdrueckung,
                    konsole.rauschunterdrueckung_formel,
                ),
                (
                    "Programme",
                    f"{konsole.programm_slots_genutzt} / {konsole.max_programme}",
                    "Cyberprogramme + Autosofts. Limit = Gerätestufe"
                    + (
                        " + 2 Virtuelle Maschine"
                        if konsole.virtuelle_maschine_aktiv
                        else ""
                    ),
                ),
                (
                    "Rauschen (Effektiv)",
                    f"{konsole.rauschen} ({konsole.effektives_rauschen})",
                    konsole.rauschen_formel,
                ),
            ],
            columns=5,
        )
        if konsole.programme_ueber_limit:
            st.warning(
                f"Mehr Programme als das Limit erlaubt "
                f"({konsole.programm_slots_genutzt}/{konsole.max_programme}, "
                f"{len(konsole.programme)} Cyberprogramme + "
                f"{konsole.autosoft_anzahl} Autosofts)."
            )

    with st.expander("Aktive Programme", expanded=False):
        if konsole.programme:
            for name in konsole.programme:
                effect = CYBERPROGRAM_EFFECTS.get(
                    name, "Kein hinterlegter Effekttext."
                )
                st.write(f"- **{name}:** {effect}")
        else:
            st.caption("Keine Cyberprogramme aktiv.")



def _metric_grid(entries: list[tuple], columns: int = 3) -> None:
    """Setzt Metriken zeilenweise in ein Raster.

    Eintrag: ``(label, value)``, ``(label, value, help)`` oder
    ``(label, value, help, delta)``.
    """
    for row_start in range(0, len(entries), columns):
        cols = st.columns(columns)
        for offset, col in enumerate(cols):
            index = row_start + offset
            if index >= len(entries):
                break
            item = entries[index]
            label, value = item[0], item[1]
            help_text = item[2] if len(item) > 2 else None
            delta = item[3] if len(item) > 3 else None
            kwargs: dict = {}
            if help_text:
                kwargs["help"] = help_text
            if delta not in (0, None):
                kwargs["delta"] = delta
            col.metric(label, value, **kwargs)


def _visible_pool_metrics(
    stats: CalculatedVehicleStats, vehicle: ActiveVehicle, character: Character
) -> list[tuple[str, object]]:
    """Alle relevanten Würfelpools inkl. Defaulting und reinem Pilot-Wert."""
    handling = stats.finales_handling
    handling_off = stats.finales_handling_gelaende
    sensor = stats.finales_limit_sensor
    drive_value = (
        f"{stats.finaler_wuerfelpool_steuern} [{handling}/{handling_off}]"
    )
    dodge_value = (
        f"{stats.finaler_wuerfelpool_ausweichen} "
        f"[{stats.ausweichen_limit}/{stats.ausweichen_limit_gelaende}]"
    )
    soak = (
        "Schadenswiderstand (Rumpf + Panzerung)",
        stats.finaler_wuerfelpool_schadenswiderstand,
        "Rumpf + Panzerung, ohne Limit.",
    )

    if stats.interface_mode is InterfaceMode.AUTOPILOT:
        return [
            ("Steuern (Autopilot)", drive_value),
            (
                "Geschütze (Autopilot)",
                f"{stats.finaler_wuerfelpool_geschuetze} [Waffe]",
            ),
            (
                "Wahrnehmung (Autopilot)",
                f"{stats.finaler_wuerfelpool_wahrnehmung} [{sensor}]",
            ),
            (
                "Schleichen (Autopilot)",
                f"{stats.finaler_wuerfelpool_schleichen} [{handling}]",
            ),
            ("Ausweichen (Autopilot)", dodge_value),
            soak,
        ]

    skill_key = vehicle.fahrzeugfertigkeit.value
    label = SKILL_LABELS.get(skill_key, skill_key)
    if character.skills.elektronische_kriegsfuehrung <= 0:
        ew_value = "0 [nicht improvisierbar]"
    else:
        ew_value = (
            f"{stats.finaler_wuerfelpool_elektronische_kriegsfuehrung} [{sensor}]"
        )
    return [
        (label, drive_value),
        ("Geschütze", f"{stats.finaler_wuerfelpool_geschuetze} [Waffe]"),
        ("Wahrnehmung", f"{stats.finaler_wuerfelpool_wahrnehmung} [{sensor}]"),
        ("Elektronische Kriegsführung", ew_value),
        ("Schleichen", f"{stats.finaler_wuerfelpool_schleichen} [{handling}]"),
        ("Ausweichen", dodge_value),
        soak,
    ]


def _weapon_slot_title(
    index: int,
    choice: str,
    weapon_catalog: dict[str, WeaponTemplate],
    *,
    ist_drohne: bool,
) -> str:
    """Expander-Titel mit Namen und UP- bzw. Halterungskosten."""
    slot = f"Waffe {index + 1}"
    if not choice or choice == WEAPON_NONE:
        return f"{slot}: Keine"
    template = weapon_catalog.get(choice)
    if template is None:
        return f"{slot}: {choice}"
    mounted = make_mounted_weapon(template)
    if ist_drohne:
        cost = f"{mounted.up_kosten} UP"
    else:
        unit = "Halterung" if mounted.slot_kosten == 1 else "Halterungen"
        cost = f"{mounted.slot_kosten} {unit}"
    return f"{slot}: {mounted.name} ({cost})"


def render_armament(
    stats: CalculatedVehicleStats,
    weapon_catalog: dict[str, WeaponTemplate],
    character: Character,
    vehicle: ActiveVehicle,
    vehicle_id: int,
) -> None:
    """Waffenauswahl, Kapazität und RK unter Proben & Pools."""
    with st.expander("Bewaffnung & Halterungen", expanded=True):
        used = stats.kapazitaet_genutzt
        maximum = stats.kapazitaet_maximum
        if stats.ist_drohne:
            capacity_label = f"UP genutzt: {used} / {maximum} UP"
        else:
            capacity_label = f"Halterungen: {used} / {maximum} belegt"
        if stats.kapazitaet_ueberladen:
            capacity_label += " [ UEBERLADEN ]"
        st.markdown(f"**{capacity_label}**")
        if maximum <= 0:
            st.progress(1.0 if used > 0 else 0.0)
        else:
            st.progress(min(used / maximum, 1.0))
        if stats.kapazitaet_ueberladen:
            st.warning("Die Montage überschreitet die verfügbare Kapazität.")

        options = [WEAPON_NONE, *weapon_catalog]

        def format_weapon_option(weapon_name: str | None) -> str:
            """Zeigt UP- bzw. Halterungskosten direkt in der Waffenliste."""
            if not weapon_name or weapon_name == WEAPON_NONE:
                return WEAPON_NONE
            template = weapon_catalog.get(weapon_name)
            if template is None:
                return str(weapon_name)
            up_kosten, slot_kosten = weapon_mount_costs(template.waffentyp)
            if stats.ist_drohne:
                return f"{weapon_name} ({up_kosten} UP)"
            suffix = "en" if slot_kosten > 1 else ""
            return f"{weapon_name} ({slot_kosten} Halterung{suffix})"

        remaining_weapon_stats = list(stats.waffen)
        slots = st.session_state.get(VEHICLE_SLOTS_KEY) or []
        entry = (
            slots[vehicle_id]
            if 0 <= vehicle_id < len(slots)
            else _default_vehicle_entry(vehicle_id)
        )
        _ensure_vehicle_shape(entry, vehicle_id)
        for index in range(MAX_MOUNTED_WEAPONS):
            saved_weapon = _weapon_name_from_entry(entry, index)
            if saved_weapon not in options:
                saved_weapon = WEAPON_NONE
            _bind_widget(_weapon_slot_key(vehicle_id, index), saved_weapon)
            current = saved_weapon
            title = _weapon_slot_title(
                index, current, weapon_catalog, ist_drohne=stats.ist_drohne
            )
            with st.expander(title, expanded=False):
                choice = st.selectbox(
                    "Waffe",
                    options=options,
                    format_func=format_weapon_option,
                    key=_weapon_slot_key(vehicle_id, index),
                    on_change=_sync_vehicle_weapon,
                    args=(vehicle_id, index),
                )
                if choice == WEAPON_NONE:
                    st.caption("Kein Waffensystem montiert.")
                    continue
                fixed_key = _weapon_fixed_key(vehicle_id, index)
                saved_fixed = _as_bool(
                    entry["weapons"][index].get(
                        "fixed", entry.get(_weapon_fixed_dict_key(index))
                    )
                )
                _bind_widget(fixed_key, saved_fixed)
                st.checkbox(
                    "Feste Halterung (unbeweglich)",
                    key=fixed_key,
                    on_change=_sync_vehicle_weapon_fixed,
                    args=(vehicle_id, index),
                    help=(
                        "Unbeweglich: Würfelpool über Fahrzeugfertigkeit (Rigger) "
                        "bzw. Manövrieren-Autosoft (Autopilot)."
                    ),
                )
                current_fixed = bool(st.session_state.get(fixed_key, saved_fixed))
                entry["weapons"][index]["fixed"] = current_fixed
                entry[_weapon_fixed_dict_key(index)] = current_fixed
                gun_specs = _skill_specializations(character, "geschuetze")
                if gun_specs:
                    _bind_widget(
                        _gun_spec_key(vehicle_id, index),
                        _as_bool(entry["weapons"][index].get("gun_spec")),
                    )
                    st.checkbox(
                        "Spezialisierung anwenden (+2)",
                        key=_gun_spec_key(vehicle_id, index),
                        on_change=_sync_vehicle_gun_spec,
                        args=(vehicle_id, index),
                        help=_specialization_help(gun_specs),
                    )
                template = weapon_catalog[choice]
                mounted = make_mounted_weapon(template)
                wstat = (
                    remaining_weapon_stats.pop(0) if remaining_weapon_stats else None
                )
                if wstat is not None:
                    category = wstat.geschuetz_kategorie
                    spec_hint = wstat.spezialisierung_hinweis
                    angriff_pool = wstat.angriff_pool
                    weapon_limit = wstat.angriff_limit
                    praezision_text = wstat.praezision
                    total_rk = wstat.rk_gesamt
                else:
                    category, _matching = classify_gunnery_weapon(
                        mounted.typ, mounted.name
                    )
                    checkbox_on = _as_bool(entry["weapons"][index].get("gun_spec"))
                    spec_dice, _cat, spec_hint = gunnery_spec_bonus(
                        checkbox=checkbox_on,
                        typ=mounted.typ,
                        name=mounted.name,
                        selected_specs=gun_specs,
                        mode=stats.interface_mode,
                    )
                    total_rk = mounted.rk + stats.rueckstoss_fahrzeugbonus
                    smartlink_active = (
                        character.smartlink_active or has_smartsoft(vehicle)
                    )
                    praezision_text, parsed_accuracy = calculate_weapon_accuracy(
                        mounted.praezision, smartlink_active
                    )
                    weapon_limit = (
                        None
                        if parsed_accuracy is None
                        else calculate_weapon_limit(
                            parsed_accuracy,
                            stats.interface_mode,
                            character.rigger_control_level,
                        )
                    )
                    angriff_pool = stats.finaler_wuerfelpool_geschuetze + spec_dice
                st.caption(f"[Kategorie: {category}]")
                if wstat is not None and wstat.feste_halterung:
                    st.caption("Feste Halterung (unbeweglich).")
                elif wstat is not None:
                    st.caption("Bewegliche Halterung.")
                if spec_hint:
                    st.caption(spec_hint)
                if wstat is not None and wstat.angriff_formel:
                    st.caption(wstat.angriff_formel)
                angriff_value = (
                    f"{angriff_pool} [{weapon_limit}]"
                    if weapon_limit is not None
                    else f"{angriff_pool} [–]"
                )
                angriff, schaden, dk, praezision, modus, rk_col, munition = st.columns(7)
                angriff.metric("Angriff", angriff_value)
                schaden.metric("Schaden", mounted.schaden or "–")
                dk.metric("DK", mounted.dk or "–")
                praezision.metric("Präzision", praezision_text)
                modus.metric("Modus", mounted.modus or "–")
                rk_col.metric("Gesamte RK", total_rk)
                munition.metric("Munition", mounted.munition or "–")
                st.caption(
                    f"{mounted.typ} · Waffen-RK {mounted.rk} + Chassis "
                    f"{stats.rueckstoss_fahrzeugbonus}"
                    f" · UP {mounted.up_kosten} · Halterung {mounted.slot_kosten}"
                )


def render_vehicle_panel(
    *,
    catalog: Catalog,
    weapon_catalog: dict[str, WeaponTemplate],
    character: Character,
    steuergeraet: Steuergeraet,
    mode: InterfaceMode,
    autosofts: list,
    vehicle_id: int,
) -> None:
    """Fahrzeugauswahl, Mods, Pools und Bewaffnung eines Tabs."""
    vehicle_template, fahrzeugfertigkeit = render_vehicle_selectors(catalog, vehicle_id)
    mode = effective_vehicle_mode(mode, vehicle_id)
    modus_label = INTERFACE_LABELS.get(mode, mode.value)
    sprung = (
        " · eingesprungen"
        if mode
        in (
            InterfaceMode.VR_COLD,
            InterfaceMode.VR_HOT,
            InterfaceMode.DIREKTVERBINDUNG,
        )
        else ""
    )
    st.caption(f"{vehicle_template.kategorie} · Modus {modus_label}{sprung}")

    mods = render_vehicle_mod_inputs(vehicle_id)
    mounted_weapons = mounted_weapons_from_session(
        weapon_catalog, vehicle_id, smartlink_active=character.smartlink_active
    )
    drive_specs = _skill_specializations(character, fahrzeugfertigkeit.value)
    slots = st.session_state.get(VEHICLE_SLOTS_KEY) or []
    entry = slots[vehicle_id] if 0 <= vehicle_id < len(slots) else _default_vehicle_entry(vehicle_id)
    spec_drive = _as_bool(entry.get("drive_spec")) and bool(drive_specs)
    modified_template = apply_mods_to_vehicle_template(vehicle_template, mods)
    monitor_max = condition_monitor_max(
        modified_template.rumpf, modified_template.kategorie
    )
    current_damage = max(
        0, min(_as_int(entry.get("current_damage"), 0), monitor_max)
    )
    entry["current_damage"] = current_damage
    state = RiggerState(
        character=character,
        steuergeraet=steuergeraet,
        fahrzeuge=[
            ActiveVehicle(
                template=modified_template,
                waffen=mounted_weapons,
                autosofts=autosofts,
                fahrzeugfertigkeit=fahrzeugfertigkeit,
                interface_mode=mode,
                spezialisierung_steuern=spec_drive,
                zustandsmonitor=Zustandsmonitor(
                    aktuell=current_damage,
                    maximum=monitor_max,
                ),
            )
        ],
    )
    stats = calculate_active_vehicle(state, 0)
    vehicle = state.fahrzeuge[0]
    chassis = vehicle.template
    handling_mod = mods.get("handling", 0)
    accel_mod = mods.get("beschleunigung", 0)
    speed_mod = mods.get("geschwindigkeit", 0)
    pilot_mod = mods.get("pilot", 0)
    rumpf_mod = mods.get("rumpf", 0)
    panzer_mod = mods.get("panzerung", 0)
    sensor_mod = mods.get("sensor", 0)
    konsole = stats.konsole

    block_vehicle, block_pools = st.columns(2)
    with block_vehicle:
        with st.container(border=True):
            st.markdown("**Fahrzeugeigenschaften**")
            _metric_grid(
                [
                    (
                        "Handling (Str/Gel)",
                        f"{chassis.handling_strasse} / {chassis.handling_gelaende}",
                        None,
                        handling_mod if handling_mod != 0 else None,
                    ),
                    (
                        "Beschleunigung",
                        f"{chassis.beschleunigung_normal} / {chassis.beschleunigung_sprint}",
                        None,
                        accel_mod if accel_mod != 0 else None,
                    ),
                    (
                        "Geschwindigkeit",
                        f"{chassis.geschwindigkeit_normal} / {chassis.geschwindigkeit_sprint}",
                        None,
                        speed_mod if speed_mod != 0 else None,
                    ),
                    (
                        "Pilot",
                        chassis.pilot,
                        None,
                        pilot_mod if pilot_mod != 0 else None,
                    ),
                    (
                        "Rumpf",
                        chassis.rumpf,
                        None,
                        rumpf_mod if rumpf_mod != 0 else None,
                    ),
                    (
                        "Panzerung",
                        chassis.panzerung,
                        None,
                        panzer_mod if panzer_mod != 0 else None,
                    ),
                    (
                        "Sensor",
                        chassis.sensor,
                        None,
                        sensor_mod if sensor_mod != 0 else None,
                    ),
                    ("Sitze", chassis.sitze),
                ]
            )
            if drive_specs:
                _bind_widget(_drive_spec_key(vehicle_id), _as_bool(entry.get("drive_spec")))
                st.checkbox(
                    "Spezialisierung anwenden (+2)",
                    key=_drive_spec_key(vehicle_id),
                    on_change=_sync_vehicle_drive_spec,
                    args=(vehicle_id,),
                    help=_specialization_help(drive_specs),
                )
            st.markdown("**Zustandsmonitor**")
            damage_key = _damage_key(vehicle_id)
            _bind_widget(damage_key, current_damage)
            st.number_input(
                "Erlittener Schaden",
                min_value=0,
                max_value=int(monitor_max),
                step=1,
                key=damage_key,
                on_change=_sync_vehicle_damage,
                args=(vehicle_id,),
                help=(
                    f"{monitor_max} Kästchen "
                    f"({'Drohne 6' if stats.ist_drohne else 'Fahrzeug 12'} "
                    f"+ ceil(Rumpf {chassis.rumpf} / 2))."
                ),
            )
            shown_damage = max(
                0,
                min(_as_int(st.session_state.get(damage_key), current_damage), monitor_max),
            )
            entry["current_damage"] = shown_damage
            damage_mod = condition_monitor_modifier(shown_damage)
            st.caption(f"{shown_damage} / {monitor_max} Kästchen")
            if damage_mod < 0:
                st.warning(f"Schadensmodifikator: {damage_mod}")
            else:
                st.info("Schadensmodifikator: 0")

    with block_pools:
        with st.container(border=True):
            st.markdown("**Proben & Pools**")
            init_col, threshold_col = st.columns(2)
            init_col.metric(
                "Initiative",
                f"{stats.initiative_wert} + {stats.initiative_wuerfel}W6",
            )
            threshold_col.metric("Schwellenwert-Mod", stats.schwellenwert_mod)
            pool_entries = _visible_pool_metrics(stats, vehicle, character)
            if pool_entries:
                _metric_grid(pool_entries)
            else:
                st.caption(
                    "Keine Würfelpools für die gewählten Fertigkeiten bzw. Autosofts."
                )

    render_armament(stats, weapon_catalog, character, vehicle, vehicle_id)

    with st.expander("Berechnungsdetails & Modifikatoren", expanded=False):
        st.markdown("**Formeln**")
        st.write(
            f"**Steuern** ({stats.fertigkeit_steuern}): "
            f"`{stats.wuerfelpool_steuern_formel}`"
        )
        st.write(f"**Handling:** `{stats.handling_formel}`")
        st.write(f"**Geschütze:** `{stats.wuerfelpool_geschuetze_formel}`")
        for waffe in stats.waffen:
            st.write(f"**Angriff ({waffe.name}):** `{waffe.angriff_formel}`")
            if waffe.spezialisierung_hinweis and not waffe.spezialisierung_angewandt:
                st.caption(f"{waffe.name}: {waffe.spezialisierung_hinweis}")
        if (
            character.smartlink_active
            and stats.interface_mode is not InterfaceMode.AUTOPILOT
        ):
            st.caption(
                "Smartlink: +2 auf den Geschütze-Pool "
                "(LOG + Geschütze + 2 Smartlink, nicht im Autopilot)."
            )
        st.write(f"**Wahrnehmung:** `{stats.wuerfelpool_wahrnehmung_formel}`")
        st.write(
            f"**Elektronische Kriegsführung:** `{stats.wuerfelpool_ew_formel}`"
        )
        st.write(f"**Schleichen:** `{stats.wuerfelpool_schleichen_formel}`")
        st.write(f"**Ausweichen:** `{stats.wuerfelpool_ausweichen_formel}`")
        st.write(
            f"**Schadenswiderstand:** `{stats.wuerfelpool_schadenswiderstand_formel}`"
        )
        st.write(f"**Initiative:** `{stats.initiative_formel}`")
        if stats.schadensmodifikator:
            st.caption(
                f"Zustandsmonitor {stats.zustandsmonitor_aktuell}/"
                f"{stats.zustandsmonitor_maximum}: "
                f"{stats.schadensmodifikator} auf Fahrzeug-Würfelpools "
                "(nicht auf Limits, Initiative oder Schadenswiderstand)."
            )
        st.write(f"**Datenverarbeitung:** `{konsole.datenverarbeitung_formel}`")
        st.write(f"**Firewall:** `{konsole.firewall_formel}`")
        st.write(f"**Rauschen:** `{konsole.rauschen_formel}`")
        if konsole.rauschen_pool_malus:
            st.caption(
                f"−{konsole.rauschen_pool_malus} Rauschen auf Steuern, Geschütze, "
                "Wahrnehmung, Elektronische Kriegsführung und Schleichen."
            )
        if stats.interface_mode is InterfaceMode.DIREKTVERBINDUNG:
            st.caption(
                "Direktverbindung: Initiative ist REA + INT + 4W6 "
                "(nicht Datenverarbeitung wie in VR). Rauschen wird ignoriert."
            )
        if stats.modifikatoren:
            st.markdown("**Modifikatoren**")
            for note in stats.modifikatoren:
                st.write(f"- {note}")
        else:
            st.caption("Keine zusätzlichen Modifikatoren.")
        active_veh_mods = [
            f"{label} {mods[field]:+d}"
            for field, label in VEHICLE_MOD_FIELDS
            if mods.get(field)
        ]
        if active_veh_mods:
            st.caption("Manuelle Fahrzeug-Mods: " + ", ".join(active_veh_mods))

        selected_vorteile = character.vorteile or [
            item.name for item in character.qualities
        ]
        st.markdown("### Aktive Vorteile & Effekte")
        if selected_vorteile:
            for name in selected_vorteile:
                effect = QUALITY_EFFECTS.get(name, "Kein hinterlegter Effekttext.")
                st.write(f"- **{name}:** {effect}")
        else:
            st.caption("Keine Vorteile ausgewählt.")


def main() -> None:
    try:
        catalog = load_catalog()
        weapon_catalog, skipped_weapons = load_weapons_pandas()
    except FileNotFoundError as error:
        st.error(f"Katalog konnte nicht geladen werden:\n\n{error}")
        st.stop()
    except CatalogLoadError as error:
        st.error(f"Katalog konnte nicht geladen werden:\n\n{error}")
        st.stop()

    character, steuergeraet, mode, autosofts = render_sidebar(
        catalog, weapon_catalog
    )
    console_stats = calculate_console_stats(
        character,
        steuergeraet,
        mode,
        autosoft_count=len(autosofts),
    )
    render_matrix_header(console_stats, character, mode)
    if skipped_weapons:
        preview = ", ".join(skipped_weapons[:3])
        more = "" if len(skipped_weapons) <= 3 else " …"
        st.caption(
            f"Hinweis: {len(skipped_weapons)} Waffenzeile(n) übersprungen "
            f"({preview}{more})."
        )

    slots = ensure_vehicle_slots()
    add_col, info_col = st.columns([2, 3])
    with add_col:
        if st.button(
            "+ Neues Fahrzeug / Drohne hinzufügen",
            disabled=len(slots) >= MAX_ACTIVE_VEHICLES,
        ):
            add_vehicle_slot()
            st.rerun()
    with info_col:
        st.caption(f"{len(slots)} / {MAX_ACTIVE_VEHICLES} Fahrzeuge")

    selected_idx = render_vehicle_nav(slots)
    render_vehicle_panel(
        catalog=catalog,
        weapon_catalog=weapon_catalog,
        character=character,
        steuergeraet=steuergeraet,
        mode=mode,
        autosofts=autosofts,
        vehicle_id=selected_idx,
    )


if __name__ == "__main__":
    main()
