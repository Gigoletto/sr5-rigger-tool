"""Shadowrun 5 Rigger-Berechnungslogik.

Reine Funktionen auf den Pydantic-Objekten aus ``models.py``.
Kein CSV-Import, keine Benutzeroberfläche.

Quellen (SR5 Grundregelwerk, sinngemäß S. 199, 230, 266, 452):
- Riggerkontrolle wirkt nur im eingesprungenen Zustand (VR kalt/heiß/Direktverbindung).
- Heißes Sim: +2 auf Matrix-/Fahrzeug-Würfelpools; keine Limit-Erhöhung.
  Eingesprungene Limits steigen nur um die Riggerkontrolle.
- Autopilot nutzt Pilot + passende Autosoft; Initiative Pilot × 2 + 4W6.
- Autopilot-Ausweichen: Pilot + Ausweichen-Autosoft (sonst 0).
- Autopilot-Geschütze (beweglich): Pilot + Zielerfassung, plus +2 bei aktiver Smartsoft.
- Autopilot-Geschütze (fest): Pilot + Manövrieren, plus +2 bei aktiver Smartsoft.
- Manuelles Geschütz (beweglich): LOG + Geschütze + Waffen-Spezialisierung + Boni.
- Manuelles Geschütz (fest): LOG + Fahrzeugfertigkeit + Fahrzeug-Spezialisierung + Boni.
- Manuelles Geschütz: +2 Smartlink, sofern aktiv (nicht Autopilot).
- VR-Initiative: finale Datenverarbeitung der Riggerkonsole + INT + 3W6/4W6.
- Direktverbindung-Initiative: REA + INT + 4W6 (nicht Datenverarbeitung).
- Toolbox +1 DV, Verschlüsselung +1 Firewall, Signalreiniger Rauschunterdrückung 2.
- Virtuelle Maschine: +2 Programmslots; Programmlimit = Gerätestufe des Steuergeräts.
- Umgebungsrauschen: effektiv max(0, Rauschen − Rauschunterdrückung); Direktverbindung 0.
  Malus auf Steuern, Geschütze, Wahrnehmung, EW, Schleichen; nicht im Autopilot.
- Zustandsmonitor: −1 Würfel je 3 volle Kästchen auf Fahrzeug-Würfelpools (nicht auf
  Limits, Initiative oder Schadenswiderstand); Pool-Minimum 0.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from models import (
    ActiveVehicle,
    AutosoftTemplate,
    Character,
    CommlinkTemplate,
    InterfaceMode,
    MountedWeapon,
    RiggerConsoleTemplate,
    RiggerState,
    Skills,
    VehicleTemplate,
    WeaponTemplate,
)

# SR5: Heißes Sim +2 auf Matrixproben (eingesprungene Fahrzeugproben zählen mit).
# Manche Frühauflagen/Zusammenfassungen nennen +1; hier gilt der Kernregelwert.
HOT_SIM_DICE_BONUS = 2
AR_LIMIT_BONUS = 1
SPEZIALISIERUNG_DICE_BONUS = 2
SPEZIALISIERUNG_AUTOPILOT_HINWEIS = "Spezialisierung ignoriert wegen Autopilot"
TOOLBOX_DV_BONUS = 1
ENCRYPTION_FW_BONUS = 1
SIGNAL_SCRUBBER_NOISE_REDUCTION = 2
VIRTUAL_MACHINE_EXTRA_SLOTS = 2

# Autosoft-Templates haben noch keine installierte Stufe am Fahrzeug;
# bis dahin gilt die maximale Katalogstufe als installierte Stufe.
DEFAULT_AUTOSOFT_USES_MAX_RATING = True

DEFAULT_WEAPON_MOUNT_COSTS: tuple[int, int] = (3, 1)

# Typ-String aus Waffen.csv → (UP_Kosten, Slot_Kosten).
WEAPON_TYPE_MAPPING: dict[str, tuple[int, int]] = {
    "Leichte Pistole": (1, 1),
    "Taser": (1, 1),
    "Nahkampf": (1, 1),
    "Schwere Pistole": (2, 1),
    "Automatikpistole": (2, 1),
    "Maschinenpistole": (3, 1),
    "Sturmgewehr": (3, 1),
    "Schrotflinten": (3, 1),
    "Scharfschützengewehr": (3, 1),
    "Projektilwaffen": (3, 1),
    "Granatwerfer": (4, 1),
    "Leichtes Maschinengewehr": (5, 2),
    "Mittleres Maschinengewehr": (5, 2),
    "Schweres Maschinengewehr": (6, 2),
    "Sturmkanone": (6, 2),
    "Raketenwerfer": (6, 2),
    "Laserwaffen": (6, 2),
}

_ACCURACY_RE = re.compile(r"(\d+)\s*(?:\(\s*(\d+)\s*\))?")


class CalculatedWeaponStats(BaseModel):
    """Einsatzwerte einer montierten Waffe inkl. RK und Präzision."""

    model_config = ConfigDict(extra="forbid")

    name: str
    typ: str = ""
    schaden: str = ""
    durchschlag: str = ""
    modus: str = ""
    munition: Optional[str] = None
    praezision: str = ""
    praezision_basis: Optional[int] = None
    praezision_final: Optional[int] = None
    smartlink_aktiv: bool = False
    angriff_limit: Optional[int] = None
    angriff_pool: int = 0
    angriff_formel: str = ""
    feste_halterung: bool = False
    geschuetz_kategorie: str = ""
    spezialisierung_angewandt: bool = False
    spezialisierung_hinweis: str = ""
    rk_waffe: int = 0
    rk_gesamt: int = 0
    up_kosten: int = 0
    slot_kosten: int = 0


class CalculatedConsoleStats(BaseModel):
    """Finale Matrixattribute der Riggerkonsole inkl. Programme."""

    model_config = ConfigDict(extra="forbid")

    geraetestufe: int = 1
    basis_datenverarbeitung: int = 0
    basis_firewall: int = 0
    datenverarbeitung: int = 0
    datenverarbeitung_formel: str = ""
    firewall: int = 0
    firewall_formel: str = ""
    rauschunterdrueckung: int = 0
    rauschunterdrueckung_formel: str = ""
    programme: list[str] = Field(default_factory=list)
    max_programme: int = 0
    programme_ueber_limit: bool = False
    toolbox_aktiv: bool = False
    verschluesselung_aktiv: bool = False
    signalreiniger_aktiv: bool = False
    virtuelle_maschine_aktiv: bool = False
    rauschen: int = 0
    effektives_rauschen: int = 0
    rauschen_pool_malus: int = 0
    rauschen_formel: str = ""
    autosoft_anzahl: int = 0
    programm_slots_genutzt: int = 0


class CalculatedVehicleStats(BaseModel):
    """Verrechnete Einsatzwerte eines aktiven Fahrzeugs bzw. einer Drohne."""

    model_config = ConfigDict(extra="forbid")

    bezeichnung: str
    template_name: str
    kategorie: str
    interface_mode: InterfaceMode
    eingesprungen: bool

    finales_handling: int = Field(description="Handling-Limit Straße")
    handling_formel: str = ""
    finales_handling_gelaende: int
    finales_limit_geschwindigkeit: int = Field(
        description="Geschwindigkeits-Limit (Normalwert, für Verfolgungsjagden)."
    )
    finales_geschwindigkeit_sprint: int
    finales_limit_sensor: int

    finaler_wuerfelpool_steuern: int
    wuerfelpool_steuern_formel: str = ""
    fertigkeit_steuern: str = ""
    finaler_wuerfelpool_geschuetze: int = 0
    wuerfelpool_geschuetze_formel: str = ""
    finaler_wuerfelpool_wahrnehmung: int = 0
    wuerfelpool_wahrnehmung_formel: str = ""
    finaler_wuerfelpool_elektronische_kriegsfuehrung: int = 0
    wuerfelpool_ew_formel: str = ""
    finaler_wuerfelpool_schleichen: int = 0
    wuerfelpool_schleichen_formel: str = ""
    finaler_wuerfelpool_ausweichen: int = 0
    wuerfelpool_ausweichen_formel: str = ""
    ausweichen_limit: int = 0
    ausweichen_limit_gelaende: int = 0
    finaler_wuerfelpool_schadenswiderstand: int = 0
    wuerfelpool_schadenswiderstand_formel: str = ""

    initiative_wert: int = Field(description="Initiativwert vor den Würfeln")
    initiative_wuerfel: int = Field(description="Anzahl W6")
    initiative_formel: str = ""

    riggerkontrolle_stufe: int = 0
    riggerkontrolle_aktiv: bool = False
    schwellenwert_mod: int = Field(
        default=0,
        description="Modifikator auf Schwellenwerte von Fahrzeugproben (negativ = Erleichterung).",
    )

    waffen: list[CalculatedWeaponStats] = Field(default_factory=list)
    ist_drohne: bool = False
    kapazitaet_genutzt: int = 0
    kapazitaet_maximum: int = 0
    kapazitaet_ueberladen: bool = False
    rueckstoss_fahrzeugbonus: int = 0
    zustandsmonitor_aktuell: int = 0
    zustandsmonitor_maximum: int = 0
    schadensmodifikator: int = 0
    angewandte_vorteile: list[str] = Field(default_factory=list)
    nicht_ausgewertete_vorteile: list[str] = Field(default_factory=list)
    modifikatoren: list[str] = Field(default_factory=list)
    konsole: CalculatedConsoleStats = Field(default_factory=CalculatedConsoleStats)


@dataclass(frozen=True)
class QualityRule:
    """Regelwirkung eines Vorteils oder Nachteils."""

    canonical_name: str
    aliases: tuple[str, ...]
    description: str
    handling_mod: int = 0
    speed_mod: int = 0
    sensor_mod: int = 0
    vehicle_dice_mod: int = 0
    manual_only: bool = False
    allowed_modes: tuple[InterfaceMode, ...] | None = None


@dataclass
class QualityModifiers:
    """Summierte Vorteils-Modifikatoren für ein Fahrzeug."""

    handling: int = 0
    speed: int = 0
    sensor: int = 0
    vehicle_dice: int = 0
    angewandt: list[str] = field(default_factory=list)
    unbekannt: list[str] = field(default_factory=list)


QUALITY_RULES: tuple[QualityRule, ...] = (
    QualityRule(
        canonical_name="Fahrzeugempathie",
        aliases=("fahrzeugempathie", "fahrzeug empathie", "vehicle empathy"),
        description=(
            "+1 auf Steuern-Pool und Handling-Limit bei AR."
        ),
        handling_mod=1,
        vehicle_dice_mod=1,
        allowed_modes=(InterfaceMode.AR,),
    ),
    QualityRule(
        canonical_name="Rennpilot",
        aliases=("rennpilot",),
        description=(
            "Boni auf schwierige Manöver/Stunts, kurzzeitige Erhöhung von "
            "Geschwindigkeit/Handling möglich."
        ),
    ),
    QualityRule(
        canonical_name="Meisterfahrer",
        aliases=("meisterfahrer",),
        description="Geländemodifikatoren werden um 1 gesenkt.",
    ),
    QualityRule(
        canonical_name="Raser",
        aliases=("raser", "speed demon", "lead foot"),
        description=(
            "+1 auf Fahrzeugproben bei hohen Geschwindigkeiten "
            "(Geschwindigkeit 3+, bei Flugzeugen 4+)."
        ),
    ),
    QualityRule(
        canonical_name="Unauffälligkeit",
        aliases=("unauffaelligkeit",),
        description=(
            "Beobachter erhalten -2 auf Wahrnehmungsproben "
            "(gilt auch für selbstgesteuerte Drohnen)."
        ),
    ),
    QualityRule(
        canonical_name="Geborener Schrauber",
        aliases=("geborener schrauber",),
        description="+1 Würfelpool auf alle Proben der Mechanik-Fertigkeitsgruppe.",
    ),
    QualityRule(
        canonical_name="Technisches Improvisationstalent",
        aliases=("technisches improvisationstalent",),
        description="Erleichtert Reparaturen unter erschwerten Bedingungen.",
    ),
    QualityRule(
        canonical_name="Übertakter",
        aliases=("uebertakter",),
        description=(
            "Ermöglicht Leistungssteigerung von Matrix-Hardware/Konsolen "
            "über das Standardlimit hinaus."
        ),
    ),
)

_FLUG_KEYS = (
    "rotor",
    "starrfl",
    "senkrecht",
    "luftschiff",
    "flugzeug",
    "rakete",
    "vektorschub",
)
_WASSER_KEYS = ("schiff", "u-boot", "uboot", "yacht", "wasser")
_LAEUFER_KEYS = ("anthropomorph", "laeufer", "walker", "anthroform")


def _fold(text: str) -> str:
    """Kleinschreibung, Umlaute auflösen, Whitespace normalisieren."""
    lowered = text.strip().lower()
    for src, dst in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        lowered = lowered.replace(src, dst)
    return " ".join(lowered.split())


def _contains_any(haystack: str, needles: tuple[str, ...]) -> bool:
    return any(needle in haystack for needle in needles)


def is_drone(kategorie: str) -> bool:
    """True, wenn die Fahrzeugkategorie eine Drohne bezeichnet."""
    return "drohne" in _fold(kategorie)


def condition_monitor_max(rumpf: int, kategorie: str) -> int:
    """Maximale Zustandsmonitor-Kästchen nach Rumpf und Kategorie."""
    rumpf = max(0, int(rumpf))
    basis = 6 if is_drone(kategorie) else 12
    return basis + math.ceil(rumpf / 2)


def condition_monitor_modifier(current_damage: int) -> int:
    """SR5: −1 auf Proben je 3 volle Kästchen."""
    return -(max(0, int(current_damage)) // 3)


def weapon_mount_costs(typ: str) -> tuple[int, int]:
    """UP- und Slot-Kosten anhand des Waffen-Typs.

    Unbekannte Typen fallen auf ``DEFAULT_WEAPON_MOUNT_COSTS`` (3, 1) zurück.
    Katalogtypen im Plural oder mit Klammerzusatz werden über Teilstring-Vergleich
    den Mapping-Schlüsseln zugeordnet (z. B. „Leichte Pistolen“ → „Leichte Pistole“).
    """
    folded = _fold(typ)
    if not folded:
        return DEFAULT_WEAPON_MOUNT_COSTS
    if "granat" in folded and "werfer" in folded:
        return WEAPON_TYPE_MAPPING["Granatwerfer"]
    for key, costs in WEAPON_TYPE_MAPPING.items():
        if _fold(key) == folded:
            return costs
    for key, costs in sorted(
        WEAPON_TYPE_MAPPING.items(), key=lambda item: len(item[0]), reverse=True
    ):
        key_folded = _fold(key)
        if key_folded and key_folded in folded:
            return costs
    return DEFAULT_WEAPON_MOUNT_COSTS


def make_mounted_weapon(
    template: WeaponTemplate,
    *,
    smartlink: bool = False,
    spezialisierung_aktiv: bool = False,
    feste_halterung: bool = False,
) -> MountedWeapon:
    """Katalogwaffe plus UP-/Slot-Kosten aus ``WEAPON_TYPE_MAPPING``."""
    up_kosten, slot_kosten = weapon_mount_costs(template.waffentyp)
    return MountedWeapon.from_template(
        template,
        up_kosten=up_kosten,
        slot_kosten=slot_kosten,
        smartlink=smartlink,
        spezialisierung_aktiv=spezialisierung_aktiv,
        feste_halterung=feste_halterung,
    )


def classify_gunnery_weapon(typ: str, name: str = "") -> tuple[str, frozenset[str]]:
    """Ordnet einen Waffen-Typ den Geschütz-Spezialisierungen zu.

    Rückgabe: Anzeige-Kategorie und die Spezialisierungen, die den Bonus auslösen.
    """
    haystack = _fold(f"{typ} {name}")
    if any(key in haystack for key in ("laser", "plasma", "energie")):
        return "Energiewaffen", frozenset({"Energiewaffen"})
    if "lenk" in haystack:
        return "Lenkraketen", frozenset({"Lenkraketen", "Raketen", "Artillerie"})
    if "rakete" in haystack:
        return "Raketen", frozenset({"Raketen", "Artillerie", "Lenkraketen"})
    if "maschinengewehr" in haystack and "leicht" in haystack:
        return "Feuerwaffen", frozenset({"Feuerwaffen"})
    if any(
        key in haystack
        for key in (
            "sturmkanone",
            "kanone",
            "maschinengewehr",
            "granat",
            "torpedo",
            "moerser",
            "morser",
            "artillerie",
        )
    ):
        return "Artillerie", frozenset({"Artillerie"})
    if any(
        key in haystack
        for key in (
            "pistole",
            "taser",
            "maschinenpistole",
            "sturmgewehr",
            "sportgewehr",
            "schrot",
            "scharfschuetzen",
            "holdout",
            "karabiner",
        )
    ):
        return "Feuerwaffen", frozenset({"Feuerwaffen"})
    return "Unbekannt", frozenset()


def gunnery_spec_bonus(
    *,
    checkbox: bool,
    typ: str,
    name: str = "",
    selected_specs: list[str],
    mode: InterfaceMode | None = None,
) -> tuple[int, str, str]:
    """+2 nur bei gesetzter Checkbox und passender Geschütz-Spezialisierung.

    Im Autopilot ist der Bonus immer 0.

    Returns
    -------
    bonus, kategorie, hinweis
    """
    category, matching = classify_gunnery_weapon(typ, name)
    if mode is InterfaceMode.AUTOPILOT:
        if checkbox:
            return 0, category, SPEZIALISIERUNG_AUTOPILOT_HINWEIS
        return 0, category, ""
    if not checkbox:
        return 0, category, ""
    selected = {_fold(item) for item in selected_specs if item}
    matching_folded = {_fold(item) for item in matching}
    if selected and selected & matching_folded:
        return SPEZIALISIERUNG_DICE_BONUS, category, ""
    return (
        0,
        category,
        f"Spezialisierung gilt nicht für diesen Waffentyp ({category}).",
    )


def apply_vehicle_mod(base_val, mod: int):
    """Addiert einen manuellen Modifikator auf einen Fahrzeugwert.

    Slash-Werte wie ``4/3`` oder ``4 / 3`` werden auf beiden Seiten erhöht.
    """
    if mod == 0:
        return base_val
    if isinstance(base_val, str) and "/" in base_val:
        left_raw, right_raw = base_val.split("/", 1)
        left = int(str(left_raw).strip()) + mod
        right = int(str(right_raw).strip()) + mod
        return f"{left} / {right}"
    return int(base_val) + mod


def vehicle_recoil_bonus(rumpf: int, drone: bool) -> int:
    """Fahrzeug- bzw. Drohnenbonus auf die Rückstoßkompensation."""
    if drone:
        return 1 + math.ceil(rumpf / 3)
    return rumpf


def weapon_total_rk(weapon: MountedWeapon, rumpf: int, drone: bool) -> int:
    """Gesamte RK einer montierten Waffe inkl. Chassis-Bonus."""
    return vehicle_recoil_bonus(rumpf, drone) + weapon.rk


def mount_capacity(
    template: VehicleTemplate, weapons: list[MountedWeapon]
) -> tuple[int, int, bool]:
    """Genutzte und maximale Kapazität (UP bei Drohnen, Halterungen sonst)."""
    drone = is_drone(template.kategorie)
    if drone:
        used = sum(weapon.up_kosten for weapon in weapons)
        maximum = template.rumpf
    else:
        used = sum(weapon.slot_kosten for weapon in weapons)
        maximum = math.floor(template.rumpf / 3)
    return used, maximum, drone


def _skill_value(skills: Skills, name: str) -> int:
    """Liest eine Fertigkeitsstufe; unbekannte Keys ergeben 0."""
    return int(getattr(skills, name, 0) or 0)


def vehicle_skill_name(kategorie: str) -> str:
    """Ordnet eine Fahrzeugkategorie der passenden Pilotieren-Fertigkeit zu."""
    folded = _fold(kategorie)
    if _contains_any(folded, _FLUG_KEYS):
        return "flugzeuge"
    if _contains_any(folded, _WASSER_KEYS):
        return "wasserfahrzeuge"
    if _contains_any(folded, _LAEUFER_KEYS):
        return "laeufer"
    return "bodenfahrzeuge"


def _format_parts(parts: list[tuple[str, int]] | tuple[tuple[str, int], ...]) -> tuple[int, list[str]]:
    """Baut Pool-Chunks im Stil ``LOG (4) + Geschütze (4) - Schaden (1)``."""
    total = 0
    chunks: list[str] = []
    for label, value in parts:
        if value == 0:
            continue
        total += value
        if not chunks:
            chunks.append(f"{label} ({value})" if label else str(value))
            continue
        if value < 0:
            chunks.append(
                f"- {label} ({abs(value)})" if label else f"- {abs(value)}"
            )
        else:
            chunks.append(f"+ {label} ({value})" if label else f"+ {value}")
    return max(total, 0), chunks


def _skill_display_name(skill_key: str) -> str:
    return {
        "bodenfahrzeuge": "Bodenfahrzeuge",
        "flugzeuge": "Flugzeuge",
        "laeufer": "Läufer",
        "wasserfahrzeuge": "Schiffe",
        "geschuetze": "Geschütze",
    }.get(skill_key, skill_key.replace("_", " ").title())


def _attack_formula(kind: str, parts: list[tuple[str, int]]) -> tuple[int, str]:
    """Baut den Angriffs-Formelstring im Stil ``Angriff (Fest): LOG (4) + … = 12``."""
    total, chunks = _format_parts(parts)
    prefix = f"Angriff ({kind})"
    if not chunks:
        return 0, f"{prefix}: 0"
    return total, f"{prefix}: " + " ".join(chunks) + f" = {total}"


def calculate_weapon_attack_pool(
    *,
    mounted: MountedWeapon,
    character: Character,
    vehicle: "ActiveVehicle",
    mode: InterfaceMode,
    template: VehicleTemplate,
    manoeuvre_rating: int,
    targeting_rating: int,
    smartsoft_active: bool,
    cr_pool: int,
    hot_sim: int,
    noise_malus: int,
    spec_drive: int,
    damage_mod: int = 0,
) -> tuple[int, str, int, str]:
    """Angriffspool einer Halterung: fest/beweglich × Autopilot/Rigger.

    Returns
    -------
    pool, formel, spec_dice, spec_hint
    """
    fixed = bool(getattr(mounted, "feste_halterung", False))
    kind = "Fest" if fixed else "Beweglich"
    if mode is InterfaceMode.AUTOPILOT:
        kind = f"{kind}, Autopilot"
        auto_rating = manoeuvre_rating if fixed else targeting_rating
        auto_label = "Manövrieren" if fixed else "Zielerfassung"
        smartsoft_bonus = 2 if smartsoft_active else 0
        pool, formula = _attack_formula(
            kind,
            [
                ("Pilot", template.pilot),
                (auto_label, auto_rating),
                ("Smartsoft", smartsoft_bonus),
                ("Schaden", damage_mod),
            ],
        )
        hint = ""
        if mounted.spezialisierung_aktiv or vehicle.spezialisierung_steuern:
            hint = SPEZIALISIERUNG_AUTOPILOT_HINWEIS
            formula += f" ({hint})"
        return pool, formula, 0, hint

    smartlink_bonus = 2 if character.smartlink_active else 0
    if fixed:
        skill_key = vehicle.fahrzeugfertigkeit.value
        skill = _skill_value(character.skills, skill_key)
        skill_label = _skill_display_name(skill_key)
        skill_dice, default_label = _defaulting_penalty(skill)
        parts: list[tuple[str, int]] = [
            ("LOG", character.attribute.LOG),
            (skill_label if skill > 0 else default_label, skill_dice if skill == 0 else skill),
            ("Riggerkontrolle", cr_pool),
            ("Heiße Sim", hot_sim),
            ("Fahrzeug-Spezialisierung", spec_drive),
            ("Smartlink", smartlink_bonus),
            ("Rauschen", -noise_malus),
            ("Schaden", damage_mod),
        ]
        pool, formula = _attack_formula("Fest", parts)
        return pool, formula, spec_drive, ""

    spec_dice, _category, spec_hint = gunnery_spec_bonus(
        checkbox=mounted.spezialisierung_aktiv,
        typ=mounted.typ,
        name=mounted.name,
        selected_specs=list(character.spezialisierungen.geschuetze),
        mode=mode,
    )
    gun_skill = character.skills.geschuetze
    gun_dice, gun_default_label = _defaulting_penalty(gun_skill)
    parts = [
        ("LOG", character.attribute.LOG),
        ("Geschütze" if gun_skill > 0 else gun_default_label, gun_dice if gun_skill == 0 else gun_skill),
        ("Riggerkontrolle", cr_pool),
        ("Heiße Sim", hot_sim),
        ("Waffen-Spezialisierung", spec_dice),
        ("Smartlink", smartlink_bonus),
        ("Rauschen", -noise_malus),
        ("Schaden", damage_mod),
    ]
    pool, formula = _attack_formula("Beweglich", parts)
    return pool, formula, spec_dice, spec_hint


def is_jumped_in(mode: InterfaceMode) -> bool:
    """True, wenn der Rigger per VR oder Direktverbindung eingesprungen ist."""
    return mode in (
        InterfaceMode.VR_COLD,
        InterfaceMode.VR_HOT,
        InterfaceMode.DIREKTVERBINDUNG,
    )


def rigger_control_limit_bonus(character: Character, jumped_in: bool) -> int:
    """Nur die Basis-Riggerkontrolle; Booster erhöht keine Limits."""
    return character.rigger_control_level if jumped_in else 0


def rigger_control_pool_bonus(character: Character, jumped_in: bool) -> int:
    """Riggerkontrolle plus Booster für Würfelpools."""
    if not jumped_in:
        return 0
    return character.rigger_control_level + character.riggerkontrollbooster


def effective_rigger_control(character: Character, jumped_in: bool) -> int:
    """Summe aus Riggerkontrolle und Booster für Würfelpools; 0 wenn nicht eingesprungen."""
    return rigger_control_pool_bonus(character, jumped_in)


def autosoft_rating(autosoft: AutosoftTemplate) -> int:
    """Installierte Autosoft-Stufe.

    ``ActiveVehicle`` speichert derzeit nur das Katalog-Template. Bis eine
    konkrete Stufe am Fahrzeug hängt, wird ``maximale_stufe`` verwendet.
    """
    if DEFAULT_AUTOSOFT_USES_MAX_RATING:
        return autosoft.maximale_stufe
    return autosoft.minimale_stufe


def find_autosoft(
    vehicle: ActiveVehicle, *keywords: str
) -> Optional[AutosoftTemplate]:
    """Erste Autosoft, deren Name eines der Schlüsselwörter enthält."""
    needles = tuple(_fold(k) for k in keywords)
    for autosoft in vehicle.autosofts:
        if _contains_any(_fold(autosoft.name), needles):
            return autosoft
    return None


def has_smartsoft(vehicle: ActiveVehicle) -> bool:
    """True, wenn eine Smartsoft auf dem Fahrzeug läuft."""
    return find_autosoft(vehicle, "smartsoft") is not None


def parse_praezision(raw: str) -> tuple[Optional[int], Optional[int]]:
    """Liest Basis- und Klammer-Präzision aus Katalogstrings wie ``5`` oder ``5 (7)``."""
    if not raw:
        return None, None
    match = _ACCURACY_RE.search(str(raw))
    if not match:
        return None, None
    basis = int(match.group(1))
    smart = int(match.group(2)) if match.group(2) else None
    return basis, smart


def calculate_weapon_accuracy(
    base_accuracy_str: str, smartlink_active: bool
) -> tuple[str, Optional[int]]:
    """Parst Präzision, addiert optional +2 Smartlink und liefert Anzeige plus Limitwert.

    Der Limit-Integer ist immer die erste Zahl (Wert vor der Klammer).
    Smartlink erhöht Basis und Klammerwert jeweils um 2; die Anzeige bleibt
    vollständig, z. B. ``7 (9)``.
    """
    text = "" if base_accuracy_str is None else str(base_accuracy_str).strip()
    if text in {"", "-", "–"}:
        return "-", None
    basis, klammer = parse_praezision(text)
    if basis is None:
        return text, None
    if smartlink_active:
        basis += 2
        if klammer is not None:
            klammer += 2
    if klammer is not None:
        display = f"{basis} ({klammer})"
        return display, basis
    return str(basis), basis


def calculate_weapon_limit(
    parsed_accuracy_int: int,
    mode: InterfaceMode,
    riggerkontrolle_stufe: int,
) -> int:
    """Geschütze-Limit einer Waffe aus Präzision, Modus und Riggerkontrolle."""
    if mode is InterfaceMode.AR:
        return parsed_accuracy_int + 1
    if mode in (
        InterfaceMode.VR_COLD,
        InterfaceMode.VR_HOT,
        InterfaceMode.DIREKTVERBINDUNG,
    ):
        return parsed_accuracy_int + riggerkontrolle_stufe
    return parsed_accuracy_int


def effective_praezision(waffe: MountedWeapon, smartlink_aktiv: bool) -> Optional[int]:
    """Präzisions-Integer für Limits (erste Zahl, nach Smartlink)."""
    _display, value = calculate_weapon_accuracy(waffe.praezision, smartlink_aktiv)
    return value


def selected_vorteile(character: Character) -> list[str]:
    """Liefert die Vorteilsliste; ``vorteile`` hat Vorrang vor ``qualities``."""
    if character.vorteile:
        seen: set[str] = set()
        ordered: list[str] = []
        for name in character.vorteile:
            text = str(name).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            ordered.append(text)
        return ordered
    return [quality.name for quality in character.qualities if quality.name.strip()]


def has_vorteil(vorteile: list[str], name: str) -> bool:
    folded = _fold(name).replace(" ", "")
    return any(_fold(item).replace(" ", "") == folded for item in vorteile)


def fahrzeugempathie_bonus(vorteile: list[str], mode: InterfaceMode) -> int:
    """+1 Handling und +1 Steuern, nur in AR (nicht VR/Direktverbindung)."""
    if not has_vorteil(vorteile, "Fahrzeugempathie"):
        return 0
    if mode is not InterfaceMode.AR:
        return 0
    return 1


def spezialisierung_bonus(aktiv: bool, mode: InterfaceMode | None = None) -> int:
    """+2 Würfel, wenn die Spezialisierungs-Checkbox gesetzt ist.

    Im Autopilot entfällt der Bonus vollständig.
    """
    if mode is InterfaceMode.AUTOPILOT:
        return 0
    return SPEZIALISIERUNG_DICE_BONUS if aktiv else 0


CYBERPROGRAM_OPTIONS: tuple[str, ...] = (
    "Biofeedback-Filter",
    "Irreführung (Sneak)",
    "Panzerung (Armor)",
    "Schild (Shell)",
    "Schutzschirm (Guard)",
    "Signalreiniger (Signal Scrubber)",
    "Toolbox",
    "Verschlüsselung (Encryption)",
    "Virtuelle Maschine",
)

CYBERPROGRAM_EFFECTS: dict[str, str] = {
    "Biofeedback-Filter": "+2 auf Widerstand gegen Biofeedback-Schaden.",
    "Irreführung (Sneak)": "+2 Verteidigung gegen Icon Aufspüren.",
    "Panzerung (Armor)": "+2 auf Widerstand gegen Matrixschaden.",
    "Schild (Shell)": "Reduziert Zusatz-Matrixschaden durch Marken um 1 pro Marke.",
    "Schutzschirm (Guard)": "+1 Widerstand gegen Matrix- und Biofeedback-Schaden (kumulativ).",
    "Signalreiniger (Signal Scrubber)": "Bietet Rauschunterdrückung 2.",
    "Toolbox": "+1 Datenverarbeitung.",
    "Verschlüsselung (Encryption)": "+1 Firewall.",
    "Virtuelle Maschine": (
        "+1 Kästchen Matrixschaden bei jedem Matrixangriff "
        "(nicht widerstehbar)."
    ),
}


def active_cyberprograms(character: Character) -> list[str]:
    """Laufende Programme der Konsole, sonst Character.programme."""
    console = getattr(character, "riggerkonsole", None)
    if console is not None and console.programme:
        return [item for item in console.programme if item]
    return [item for item in getattr(character, "programme", []) if item]


def has_cyberprogram(programme: list[str], *needles: str) -> bool:
    """True, wenn eines der Programme eines der Schlüsselwörter enthält."""
    folded_items = [_fold(item) for item in programme if item]
    keys = tuple(_fold(needle) for needle in needles if needle)
    return any(any(key in item for key in keys) for item in folded_items)


def program_slot_limit(geraetestufe: int, programme: list[str]) -> int:
    """Maximale laufende Programme: Gerätestufe, +2 bei Virtueller Maschine."""
    limit = max(int(geraetestufe), 0)
    if has_cyberprogram(programme, "virtuelle maschine"):
        limit += VIRTUAL_MACHINE_EXTRA_SLOTS
    return limit


def calculate_effective_noise(
    rauschen: int,
    rauschunterdrueckung: int,
    mode: InterfaceMode,
) -> int:
    """Effektives Rauschen nach Unterdrückung; Direktverbindung ignoriert Rauschen."""
    if mode is InterfaceMode.DIREKTVERBINDUNG:
        return 0
    return max(0, int(rauschen) - int(rauschunterdrueckung))


def noise_pool_penalty(effektives_rauschen: int, mode: InterfaceMode) -> int:
    """Würfelmalus durch Rauschen; Autopilot handelt autonom ohne Abzug."""
    if mode is InterfaceMode.AUTOPILOT:
        return 0
    return max(0, int(effektives_rauschen))


def calculate_console_stats(
    character: Character,
    steuergeraet: CommlinkTemplate | RiggerConsoleTemplate | None = None,
    mode: InterfaceMode | None = None,
    autosoft_count: int = 0,
) -> CalculatedConsoleStats:
    """Finale DV, Firewall, Rauschunterdrückung und effektives Rauschen."""
    device = steuergeraet
    console = getattr(character, "riggerkonsole", None)
    if device is not None:
        geraetestufe = int(getattr(device, "geraetestufe", 0) or 0)
        basis_dv = int(getattr(device, "datenverarbeitung", 0) or 0)
        basis_fw = int(getattr(device, "firewall", 0) or 0)
    else:
        geraetestufe = int(getattr(console, "geraetestufe", 1) or 1)
        basis_dv = int(getattr(console, "basis_datenverarbeitung", 0) or 0)
        basis_fw = int(getattr(console, "basis_firewall", 0) or 0)

    programme = active_cyberprograms(character)
    toolbox = has_cyberprogram(programme, "toolbox")
    encryption = has_cyberprogram(programme, "verschluesselung", "encryption")
    scrubber = has_cyberprogram(programme, "signalreiniger", "signal scrubber")
    virtual_machine = has_cyberprogram(programme, "virtuelle maschine")

    dv_bonus = TOOLBOX_DV_BONUS if toolbox else 0
    fw_bonus = ENCRYPTION_FW_BONUS if encryption else 0
    noise_reduction = SIGNAL_SCRUBBER_NOISE_REDUCTION if scrubber else 0
    datenverarbeitung = basis_dv + dv_bonus
    firewall = basis_fw + fw_bonus
    max_programme = program_slot_limit(geraetestufe, programme)
    autosofts = max(int(autosoft_count), 0)
    slots_used = len(programme) + autosofts
    rauschen = int(getattr(character, "rauschen", 0) or 0)
    active_mode = mode if mode is not None else InterfaceMode.AR
    effective = calculate_effective_noise(rauschen, noise_reduction, active_mode)
    pool_malus = noise_pool_penalty(effective, active_mode)

    dv_formula = (
        f"Konsole {basis_dv} + {dv_bonus} Toolbox = {datenverarbeitung}"
        if dv_bonus
        else f"Konsole {basis_dv} = {datenverarbeitung}"
    )
    fw_formula = (
        f"Konsole {basis_fw} + {fw_bonus} Verschlüsselung = {firewall}"
        if fw_bonus
        else f"Konsole {basis_fw} = {firewall}"
    )
    noise_reduction_formula = (
        f"Signalreiniger {noise_reduction}" if noise_reduction else "0 (kein Signalreiniger)"
    )
    if active_mode is InterfaceMode.DIREKTVERBINDUNG:
        rauschen_formula = (
            f"Rauschen {rauschen} → 0 (Direktverbindung, Kabel)"
        )
    elif active_mode is InterfaceMode.AUTOPILOT:
        rauschen_formula = (
            f"max(0, {rauschen} − {noise_reduction} Rauschunterdrückung) "
            f"= {effective} (kein Pool-Malus im Autopilot)"
        )
    else:
        rauschen_formula = (
            f"max(0, {rauschen} − {noise_reduction} Rauschunterdrückung) = {effective}"
        )

    return CalculatedConsoleStats(
        geraetestufe=geraetestufe,
        basis_datenverarbeitung=basis_dv,
        basis_firewall=basis_fw,
        datenverarbeitung=datenverarbeitung,
        datenverarbeitung_formel=dv_formula,
        firewall=firewall,
        firewall_formel=fw_formula,
        rauschunterdrueckung=noise_reduction,
        rauschunterdrueckung_formel=noise_reduction_formula,
        programme=programme,
        max_programme=max_programme,
        programme_ueber_limit=slots_used > max_programme,
        toolbox_aktiv=toolbox,
        verschluesselung_aktiv=encryption,
        signalreiniger_aktiv=scrubber,
        virtuelle_maschine_aktiv=virtual_machine,
        rauschen=rauschen,
        effektives_rauschen=effective,
        rauschen_pool_malus=pool_malus,
        rauschen_formel=rauschen_formula,
        autosoft_anzahl=autosofts,
        programm_slots_genutzt=slots_used,
    )


def evaluate_qualities(
    character: Character, mode: InterfaceMode
) -> QualityModifiers:
    """Wertet ``Character.vorteile`` gegen die bekannte Vorteilstabelle aus.

    Unbekannte Vorteile werden nicht stillschweigend verworfen, sondern in
    ``unbekannt`` gesammelt, damit die UI sie später anzeigen kann.
    """
    result = QualityModifiers()
    manual = mode != InterfaceMode.AUTOPILOT
    matched_rules: set[str] = set()

    for name in selected_vorteile(character):
        folded = _fold(name).replace(" ", "")
        rule = next(
            (
                item
                for item in QUALITY_RULES
                if folded == _fold(item.canonical_name).replace(" ", "")
                or folded in {_fold(alias).replace(" ", "") for alias in item.aliases}
            ),
            None,
        )
        if rule is None:
            result.unbekannt.append(name)
            continue
        if rule.canonical_name in matched_rules:
            continue
        matched_rules.add(rule.canonical_name)
        if rule.allowed_modes is not None and mode not in rule.allowed_modes:
            continue
        if rule.manual_only and not manual:
            continue
        result.handling += rule.handling_mod
        result.speed += rule.speed_mod
        result.sensor += rule.sensor_mod
        result.vehicle_dice += rule.vehicle_dice_mod
        result.angewandt.append(rule.canonical_name)
    return result


def _pool(*parts: tuple[str, int]) -> tuple[int, str]:
    """Addiert benannte Pool-Bestandteile und baut eine Formelzeichenkette."""
    total, chunks = _format_parts(parts)
    if not chunks:
        return 0, "0"
    return total, " ".join(chunks) + f" = {total}"


def _defaulting_penalty(skill: int) -> tuple[int, str]:
    """SR5: Untrainierte Fertigkeit wird mit Attribut − 1 gewürfelt."""
    if skill > 0:
        return skill, ""
    return -1, "Defaulting"


def _non_defaultable_pool(skill: int, skill_label: str) -> tuple[int, str] | None:
    """None, wenn die Fertigkeit würfelbar ist; sonst Pool 0 ohne Improvisation."""
    if skill > 0:
        return None
    return 0, f"0 ({skill_label}: Fertigkeit nicht improvisierbar)"


def calculate_active_vehicle(
    state: RiggerState, vehicle_index: int
) -> CalculatedVehicleStats:
    """Berechnet finale Limits, Würfelpools und Initiative eines Fahrzeugs.

    Parameters
    ----------
    state:
        Aktueller Rigger-Zustand (Charakter, Steuergerät, Fahrzeugliste).
    vehicle_index:
        Index in ``state.fahrzeuge``.

    Returns
    -------
    CalculatedVehicleStats
        Verrechnete Werte inklusive Formeln und angewandter Modifikatoren.

    Raises
    ------
    IndexError
        Wenn ``vehicle_index`` außerhalb der Fahrzeugliste liegt.
    """
    if vehicle_index < 0 or vehicle_index >= len(state.fahrzeuge):
        raise IndexError(
            f"Kein Fahrzeug an Index {vehicle_index} "
            f"(vorhanden: {len(state.fahrzeuge)})."
        )

    vehicle = state.fahrzeuge[vehicle_index]
    character = state.character
    mode = vehicle.interface_mode
    jumped = is_jumped_in(mode)
    cr_limit = rigger_control_limit_bonus(character, jumped)
    cr_pool = rigger_control_pool_bonus(character, jumped)
    cr_base = character.rigger_control_level + character.riggerkontrollbooster
    hot_sim_mode = mode in (InterfaceMode.VR_HOT, InterfaceMode.DIREKTVERBINDUNG)
    hot_sim = HOT_SIM_DICE_BONUS if hot_sim_mode else 0
    vorteile = selected_vorteile(character)
    qualities = evaluate_qualities(character, mode)
    empathie = fahrzeugempathie_bonus(vorteile, mode)
    notes: list[str] = []
    console_stats = calculate_console_stats(
        character,
        state.steuergeraet,
        mode,
        autosoft_count=len(vehicle.autosofts),
    )
    noise_malus = console_stats.rauschen_pool_malus

    if jumped:
        notes.append(
            f"Eingesprungen ({mode.value}): Limits +{cr_limit} (nur Riggerkontrolle), "
            f"Würfelpools +{cr_pool} (Riggerkontrolle {character.rigger_control_level} "
            f"+ Booster {character.riggerkontrollbooster})."
        )
        if cr_limit:
            notes.append(f"Schwellenwerte von Fahrzeugproben −{cr_limit} (Minimum 1).")
    elif mode is InterfaceMode.AR:
        notes.append(
            f"AR-Fernsteuerung: keine Riggerkontrolle; "
            f"fahrzeugbezogene Limits +{AR_LIMIT_BONUS}."
        )
    else:
        notes.append("Autopilot: Pilotprogramm + Autosofts, keine Riggerkontrolle.")

    if hot_sim:
        notes.append(
            f"Heiße Sim: +{hot_sim} Würfel auf Fahrzeug-/Matrixproben "
            "(keine Limit-Erhöhung)."
        )

    if console_stats.toolbox_aktiv:
        notes.append("Toolbox: +1 Datenverarbeitung.")
    if console_stats.verschluesselung_aktiv:
        notes.append("Verschlüsselung: +1 Firewall.")
    if console_stats.signalreiniger_aktiv:
        notes.append("Signalreiniger: Rauschunterdrückung 2.")
    if console_stats.virtuelle_maschine_aktiv:
        notes.append("Virtuelle Maschine: +1 Kästchen Matrixschaden (nicht widerstehbar).")
    if console_stats.programme_ueber_limit:
        notes.append(
            f"Zu viele Programme: {console_stats.programm_slots_genutzt}/"
            f"{console_stats.max_programme} (Cyberprogramme + Autosofts, "
            f"Gerätestufe {console_stats.geraetestufe})."
        )
    if mode is InterfaceMode.DIREKTVERBINDUNG:
        notes.append("Direktverbindung: Rauschen 0 (Kabel ignoriert Rauschen).")
    elif mode is InterfaceMode.AUTOPILOT:
        notes.append("Autopilot: kein Rausch-Malus (Drohne handelt autonom).")
    elif noise_malus:
        notes.append(
            f"Effektives Rauschen {noise_malus}: −{noise_malus} auf Steuern, "
            "Geschütze, Wahrnehmung, Elektronische Kriegsführung und Schleichen."
        )

    template = vehicle.template
    monitor_max = condition_monitor_max(template.rumpf, template.kategorie)
    current_damage = min(
        max(0, int(vehicle.zustandsmonitor.aktuell or 0)),
        monitor_max,
    )
    damage_mod = condition_monitor_modifier(current_damage)
    if damage_mod:
        notes.append(
            f"Zustandsmonitor {current_damage}/{monitor_max}: "
            f"{damage_mod} auf Fahrzeug-Würfelpools."
        )
    ar_limit = AR_LIMIT_BONUS if mode is InterfaceMode.AR else 0
    handling, handling_formula = _pool(
        ("Handling", template.handling_strasse),
        ("Riggerkontrolle", cr_limit),
        ("AR", ar_limit),
        ("Fahrzeugempathie", empathie),
    )
    handling_off, _handling_off_formula = _pool(
        ("Handling Gelände", template.handling_gelaende),
        ("Riggerkontrolle", cr_limit),
        ("AR", ar_limit),
        ("Fahrzeugempathie", empathie),
    )
    speed = template.geschwindigkeit_normal + cr_limit + qualities.speed
    speed_sprint = template.geschwindigkeit_sprint + cr_limit + qualities.speed
    sensor = template.sensor + cr_limit + qualities.sensor + ar_limit

    if cr_limit:
        notes.append(
            f"Limits +{cr_limit} Riggerkontrolle ohne Booster "
            f"(Handling {template.handling_strasse}→{handling}, "
            f"Geschwindigkeit {template.geschwindigkeit_normal}→{speed}, "
            f"Sensor {template.sensor}→{sensor})."
        )
    if ar_limit:
        notes.append(
            f"AR-Limits +{ar_limit} (Handling, Handling Gelände, Sensor)."
        )
    for name in qualities.angewandt:
        notes.append(f"Vorteil {name} angewandt.")

    skill_name = vehicle.fahrzeugfertigkeit.value
    notes.append(f"Steuer-Fertigkeit: {skill_name} (manuell gewählt).")
    # Manövrieren gilt für jedes Fahrzeugmodell, sobald die Autosoft geladen ist.
    manoeuvre = find_autosoft(vehicle, "manoevrieren", "manovrieren", "maneuvering")
    targeting = find_autosoft(vehicle, "zielerfassung", "targeting")
    clearsight = find_autosoft(vehicle, "clearsight", "wahrnehmung")
    ew_soft = find_autosoft(
        vehicle, "elektronische kriegsfuehrung", "electronic warfare"
    )
    stealth_soft = find_autosoft(vehicle, "stealth", "schleichen")
    evasion_soft = find_autosoft(vehicle, "ausweichen", "evasion")
    smartsoft = has_smartsoft(vehicle)
    spec_drive = spezialisierung_bonus(vehicle.spezialisierung_steuern, mode)
    spec_drive_ignored = bool(
        mode is InterfaceMode.AUTOPILOT and vehicle.spezialisierung_steuern
    )
    man_rating = autosoft_rating(manoeuvre) if manoeuvre else 0
    tgt_rating = autosoft_rating(targeting) if targeting else 0

    ew_pool = 0
    ew_formula = "0"
    stealth_pool = 0
    stealth_formula = "0"

    if mode is InterfaceMode.AUTOPILOT:
        man_rating = autosoft_rating(manoeuvre) if manoeuvre else 0
        tgt_rating = autosoft_rating(targeting) if targeting else 0
        cs_rating = autosoft_rating(clearsight) if clearsight else 0
        ew_rating = autosoft_rating(ew_soft) if ew_soft else 0
        stealth_rating = autosoft_rating(stealth_soft) if stealth_soft else 0
        smartsoft_bonus = 2 if smartsoft else 0
        drive_pool, drive_formula = _pool(
            ("Pilot", template.pilot),
            ("Manövrieren-Autosoft", man_rating),
            ("Schaden", damage_mod),
        )
        if spec_drive_ignored:
            drive_formula += f" ({SPEZIALISIERUNG_AUTOPILOT_HINWEIS})"
            notes.append(SPEZIALISIERUNG_AUTOPILOT_HINWEIS + " (Steuern).")
        gun_pool, gun_formula = _pool(
            ("Pilot", template.pilot),
            ("Zielerfassung", tgt_rating),
            ("Smartsoft", smartsoft_bonus),
            ("Schaden", damage_mod),
        )
        perc_pool, perc_formula = _pool(
            ("Pilot", template.pilot),
            ("Clearsight-Autosoft", cs_rating),
            ("Schaden", damage_mod),
        )
        ew_pool, ew_formula = _pool(
            ("Pilot", template.pilot),
            ("EW-Autosoft", ew_rating),
            ("Schaden", damage_mod),
        )
        stealth_pool, stealth_formula = _pool(
            ("Pilot", template.pilot),
            ("Schleichen-Autosoft", stealth_rating),
            ("Schaden", damage_mod),
        )
        if manoeuvre is None:
            notes.append("Keine Manövrieren-Autosoft: Steuerpool nur Pilot.")
        if targeting is None:
            if smartsoft:
                notes.append(
                    "Keine Zielerfassung-Autosoft: Geschützpool Pilot + Smartsoft."
                )
            else:
                notes.append("Keine Zielerfassung-Autosoft: Geschützpool nur Pilot.")
        if smartsoft:
            notes.append("Smartsoft: +2 auf den Geschütz-Würfelpool (Autopilot).")
        if clearsight is None:
            notes.append("Keine Clearsight-Autosoft: Wahrnehmung nur Pilot.")
        skill_label = "Steuern (Autopilot)"
        init_value = template.pilot * 2
        init_dice = 4
        init_formula = f"Pilot {template.pilot} × 2 + 4W6 = {init_value} + 4W6"
    else:
        skill = _skill_value(character.skills, skill_name)
        default_mod, default_label = _defaulting_penalty(skill)
        drive_parts: list[tuple[str, int]] = [
            ("REA", character.attribute.REA),
            (skill_name.replace("_", " ").title() if skill > 0 else default_label, default_mod if skill == 0 else skill),
            ("Riggerkontrolle", cr_pool),
            ("Heiße Sim", hot_sim),
            ("Fahrzeugempathie", empathie),
            ("Spezialisierung", spec_drive),
            ("Rauschen", -noise_malus),
            ("Schaden", damage_mod),
        ]
        drive_pool, drive_formula = _pool(*drive_parts)

        gun_skill = character.skills.geschuetze
        gun_default, gun_default_label = _defaulting_penalty(gun_skill)
        smartlink_bonus = 2 if character.smartlink_active else 0
        gun_parts: list[tuple[str, int]] = [
            ("LOG", character.attribute.LOG),
            ("Geschütze" if gun_skill > 0 else gun_default_label, gun_default if gun_skill == 0 else gun_skill),
            ("Riggerkontrolle", cr_pool),
            ("Heiße Sim", hot_sim),
            ("Smartlink", smartlink_bonus),
            ("Rauschen", -noise_malus),
            ("Schaden", damage_mod),
        ]
        gun_pool, gun_formula = _pool(*gun_parts)
        if smartlink_bonus:
            notes.append("Smartlink: +2 auf den Geschütz-Würfelpool.")

        perc_skill = character.skills.wahrnehmung
        perc_default, perc_default_label = _defaulting_penalty(perc_skill)
        perc_parts: list[tuple[str, int]] = [
            ("INT", character.attribute.INT),
            ("Wahrnehmung" if perc_skill > 0 else perc_default_label, perc_default if perc_skill == 0 else perc_skill),
            ("Riggerkontrolle", cr_pool),
            ("Heiße Sim", hot_sim),
            ("Rauschen", -noise_malus),
            ("Schaden", damage_mod),
        ]
        perc_pool, perc_formula = _pool(*perc_parts)

        ew_skill = character.skills.elektronische_kriegsfuehrung
        blocked = _non_defaultable_pool(ew_skill, "Elektronische Kriegsführung")
        if blocked is not None:
            ew_pool, ew_formula = blocked
            notes.append(
                "Elektronische Kriegsführung: Fertigkeit nicht improvisierbar, Pool 0."
            )
        else:
            ew_parts: list[tuple[str, int]] = [
                ("LOG", character.attribute.LOG),
                ("Elektronische Kriegsführung", ew_skill),
                ("Riggerkontrolle", cr_pool),
                ("Heiße Sim", hot_sim),
                ("Rauschen", -noise_malus),
                ("Schaden", damage_mod),
            ]
            ew_pool, ew_formula = _pool(*ew_parts)

        stealth_skill = character.skills.schleichen
        stealth_default, stealth_default_label = _defaulting_penalty(stealth_skill)
        stealth_parts: list[tuple[str, int]] = [
            ("INT", character.attribute.INT),
            (
                "Schleichen" if stealth_skill > 0 else stealth_default_label,
                stealth_default if stealth_skill == 0 else stealth_skill,
            ),
            ("Riggerkontrolle", cr_pool),
            ("Heiße Sim", hot_sim),
            ("Rauschen", -noise_malus),
            ("Schaden", damage_mod),
        ]
        stealth_pool, stealth_formula = _pool(*stealth_parts)
        skill_label = skill_name
        dp = console_stats.datenverarbeitung
        if mode is InterfaceMode.VR_HOT:
            init_value = dp + character.attribute.INT
            init_dice = 4
            init_formula = (
                f"Datenverarbeitung {dp} + INT {character.attribute.INT} + 4W6 "
                f"= {init_value} + 4W6"
            )
        elif mode is InterfaceMode.VR_COLD:
            init_value = dp + character.attribute.INT
            init_dice = 3
            init_formula = (
                f"Datenverarbeitung {dp} + INT {character.attribute.INT} + 3W6 "
                f"= {init_value} + 3W6"
            )
        elif mode is InterfaceMode.DIREKTVERBINDUNG:
            init_value = character.attribute.REA + character.attribute.INT
            init_dice = 4
            init_formula = (
                f"REA {character.attribute.REA} + INT {character.attribute.INT} + 4W6 "
                f"= {init_value} + 4W6"
            )
        else:
            init_value = character.attribute.REA + character.attribute.INT
            init_dice = 1
            init_formula = (
                f"REA {character.attribute.REA} + INT {character.attribute.INT} + 1W6 "
                f"= {init_value} + 1W6"
            )

    soak_pool = template.rumpf + template.panzerung
    soak_formula = f"Rumpf {template.rumpf} + Panzerung {template.panzerung} = {soak_pool}"
    if mode is InterfaceMode.AUTOPILOT:
        evasion_rating = autosoft_rating(evasion_soft) if evasion_soft else 0
        dodge_pool, dodge_formula = _pool(
            ("Pilot", template.pilot),
            ("Ausweichen-Autosoft", evasion_rating),
            ("Schaden", damage_mod),
        )
        if evasion_rating == 0:
            notes.append("Keine Ausweichen-Autosoft: Ausweichen-Pool nur Pilot.")
    else:
        dodge_pool, dodge_formula = _pool(
            ("REA", character.attribute.REA),
            ("INT", character.attribute.INT),
            ("Heiße Sim", hot_sim),
            ("Schaden", damage_mod),
        )
    dodge_limit = handling
    dodge_limit_off = handling_off

    waffen_stats: list[CalculatedWeaponStats] = []
    drone = is_drone(template.kategorie)
    recoil_bonus = vehicle_recoil_bonus(template.rumpf, drone)
    used_capacity, max_capacity, _drone_flag = mount_capacity(template, vehicle.waffen)
    overloaded = used_capacity > max_capacity
    if drone:
        notes.append(
            f"Drohne: {used_capacity}/{max_capacity} UP für Waffen"
            + (" – überladen." if overloaded else ".")
        )
    else:
        notes.append(
            f"Fahrzeug: {used_capacity}/{max_capacity} Halterungen"
            + (" – überladen." if overloaded else ".")
        )
    notes.append(
        f"Rückstoßbonus {'Drohne 1 + ceil(Rumpf/3)' if drone else 'Rumpf'} "
        f"= {recoil_bonus}."
    )
    for mounted in vehicle.waffen:
        smart = mounted.smartlink or smartsoft or character.smartlink_active
        display, parsed = calculate_weapon_accuracy(mounted.praezision, smart)
        basis, _smart_listed = parse_praezision(mounted.praezision)
        limit = (
            None
            if parsed is None
            else calculate_weapon_limit(
                parsed, mode, character.rigger_control_level
            )
        )
        total_rk = weapon_total_rk(mounted, template.rumpf, drone)
        category, _matching = classify_gunnery_weapon(mounted.typ, mounted.name)
        angriff_pool, angriff_formel, spec_dice, spec_hint = (
            calculate_weapon_attack_pool(
                mounted=mounted,
                character=character,
                vehicle=vehicle,
                mode=mode,
                template=template,
                manoeuvre_rating=man_rating,
                targeting_rating=tgt_rating,
                smartsoft_active=smartsoft,
                cr_pool=cr_pool,
                hot_sim=hot_sim,
                noise_malus=noise_malus,
                spec_drive=spec_drive,
                damage_mod=damage_mod,
            )
        )
        if spec_dice and not mounted.feste_halterung:
            notes.append(
                f"{mounted.name}: +{spec_dice} Spezialisierung ({category})."
            )
        elif spec_dice and mounted.feste_halterung:
            notes.append(
                f"{mounted.name}: +{spec_dice} Fahrzeug-Spezialisierung "
                f"(feste Halterung, {_skill_display_name(skill_name)})."
            )
        elif spec_hint:
            notes.append(f"{mounted.name}: {spec_hint}")
        notes.append(f"{mounted.name}: {angriff_formel}")
        waffen_stats.append(
            CalculatedWeaponStats(
                name=mounted.name,
                typ=mounted.typ,
                schaden=mounted.schaden,
                durchschlag=mounted.dk,
                modus=mounted.modus,
                munition=mounted.munition,
                praezision=display,
                praezision_basis=basis,
                praezision_final=limit,
                smartlink_aktiv=smart,
                angriff_limit=limit,
                angriff_pool=angriff_pool,
                angriff_formel=angriff_formel,
                feste_halterung=bool(mounted.feste_halterung),
                geschuetz_kategorie=category,
                spezialisierung_angewandt=bool(spec_dice),
                spezialisierung_hinweis=spec_hint,
                rk_waffe=mounted.rk,
                rk_gesamt=total_rk,
                up_kosten=mounted.up_kosten,
                slot_kosten=mounted.slot_kosten,
            )
        )
        if smart:
            notes.append(f"{mounted.name}: Präzision {display} (Smartlink).")
        if limit is not None:
            notes.append(f"{mounted.name}: Angriff-Limit {limit}.")

    label = vehicle.bezeichnung or template.name
    return CalculatedVehicleStats(
        bezeichnung=label,
        template_name=template.name,
        kategorie=template.kategorie,
        interface_mode=mode,
        eingesprungen=jumped,
        finales_handling=handling,
        handling_formel=handling_formula,
        finales_handling_gelaende=handling_off,
        finales_limit_geschwindigkeit=speed,
        finales_geschwindigkeit_sprint=speed_sprint,
        finales_limit_sensor=sensor,
        finaler_wuerfelpool_steuern=drive_pool,
        wuerfelpool_steuern_formel=drive_formula,
        fertigkeit_steuern=skill_label,
        finaler_wuerfelpool_geschuetze=gun_pool,
        wuerfelpool_geschuetze_formel=gun_formula,
        finaler_wuerfelpool_wahrnehmung=perc_pool,
        wuerfelpool_wahrnehmung_formel=perc_formula,
        finaler_wuerfelpool_elektronische_kriegsfuehrung=ew_pool,
        wuerfelpool_ew_formel=ew_formula,
        finaler_wuerfelpool_schleichen=stealth_pool,
        wuerfelpool_schleichen_formel=stealth_formula,
        finaler_wuerfelpool_ausweichen=dodge_pool,
        wuerfelpool_ausweichen_formel=dodge_formula,
        ausweichen_limit=dodge_limit,
        ausweichen_limit_gelaende=dodge_limit_off,
        finaler_wuerfelpool_schadenswiderstand=soak_pool,
        wuerfelpool_schadenswiderstand_formel=soak_formula,
        initiative_wert=init_value,
        initiative_wuerfel=init_dice,
        initiative_formel=init_formula,
        riggerkontrolle_stufe=cr_base,
        riggerkontrolle_aktiv=bool(cr_pool),
        schwellenwert_mod=-cr_limit if cr_limit else 0,
        waffen=waffen_stats,
        ist_drohne=drone,
        kapazitaet_genutzt=used_capacity,
        kapazitaet_maximum=max_capacity,
        kapazitaet_ueberladen=overloaded,
        rueckstoss_fahrzeugbonus=recoil_bonus,
        zustandsmonitor_aktuell=current_damage,
        zustandsmonitor_maximum=monitor_max,
        schadensmodifikator=damage_mod,
        angewandte_vorteile=qualities.angewandt,
        nicht_ausgewertete_vorteile=qualities.unbekannt,
        modifikatoren=notes,
        konsole=console_stats,
    )
