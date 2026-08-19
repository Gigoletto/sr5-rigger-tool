"""Domain-Modell für die Shadowrun 5 Rigger-Anwendung.

Reine Pydantic-Datenstrukturen ohne CSV-Import und ohne Berechnungslogik.
Feld-Aliase entsprechen den Spaltennamen der bestehenden Katalog-CSVs.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CatalogModel(BaseModel):
    """Gemeinsame Konfiguration für Katalog-Templates."""

    model_config = ConfigDict(
        populate_by_name=True,
        str_strip_whitespace=True,
        extra="ignore",
    )


class DomainModel(BaseModel):
    """Gemeinsame Konfiguration für Charakter- und Einsatzobjekte."""

    model_config = ConfigDict(
        populate_by_name=True,
        str_strip_whitespace=True,
        extra="forbid",
    )


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class InterfaceMode(str, Enum):
    """Aktueller Interfacemodus eines Fahrzeugs bzw. einer Drohne."""

    AR = "AR"
    VR_COLD = "VR_COLD"
    VR_HOT = "VR_HOT"
    DIREKTVERBINDUNG = "DIREKTVERBINDUNG"
    AUTOPILOT = "AUTOPILOT"


class Fahrzeugfertigkeit(str, Enum):
    """Explizit gewählte Pilotieren-Fertigkeit (Drohnen sind mehrdeutig)."""

    BODENFAHRZEUGE = "bodenfahrzeuge"
    FLUGZEUGE = "flugzeuge"
    LAEUFER = "laeufer"
    WASSERFAHRZEUGE = "wasserfahrzeuge"


# ---------------------------------------------------------------------------
# 1. Stammdaten / Templates (Katalog-Items)
# ---------------------------------------------------------------------------


class VehicleTemplate(CatalogModel):
    """Fahrzeug- oder Drohnen-Eintrag aus Fahrzeuge_und_Drohnen.csv.

    Handling, Beschleunigung und Geschwindigkeit liegen im Katalog bereits
    getrennt vor (Straße/Gelände bzw. Normal/Sprint), daher als Zahlenfelder
    statt als kombinierter String wie \"4/3\".
    """

    name: str = Field(..., alias="Name")
    kategorie: str = Field(..., alias="Kategorie")
    geraetestufe: int = Field(..., alias="Gerätestufe", ge=0)
    handling_strasse: int = Field(..., alias="Handling_Strasse")
    handling_gelaende: int = Field(..., alias="Handling_Gelaende")
    beschleunigung_normal: int = Field(..., alias="Beschleunigung_Normal")
    beschleunigung_sprint: int = Field(..., alias="Beschleunigung_Sprint")
    geschwindigkeit_normal: int = Field(..., alias="Geschwindigkeit_Normal")
    geschwindigkeit_sprint: int = Field(..., alias="Geschwindigkeit_Sprint")
    pilot: int = Field(..., alias="Pilot", ge=0)
    rumpf: int = Field(..., alias="Rumpf", ge=0)
    panzerung: int = Field(..., alias="Panzerung", ge=0)
    sensor: int = Field(..., alias="Sensor", ge=0)
    sitze: int = Field(..., alias="Sitze", ge=0)
    erlaeuterungen: Optional[str] = Field(default=None, alias="Erläuterungen")
    quelle: Optional[str] = Field(default=None, alias="Quelle")
    seite: Optional[int] = Field(default=None, alias="Seite")


class WeaponTemplate(CatalogModel):
    """Waffeneintrag aus Waffen.csv.

    Schaden, Durchschlag und Feuermodus bleiben Strings, weil der Katalog
    Werte wie \"10K\", \"5(7)\", \"Granate\" oder \"HM/SM/AM\" enthält.
    """

    name: str = Field(..., alias="Name")
    waffentyp: str = Field(default="-", alias="Typ")
    praezision: str = Field(default="-", alias="Präzision")
    schaden: str = Field(default="-", alias="Schaden")
    durchschlag: str = Field(
        default="-",
        alias="DK",
        description="Leere CSV-Zellen werden als '-' gelesen.",
    )
    feuermodus: str = Field(
        default="-",
        alias="Reichw./Modus",
        description="Leere CSV-Zellen (z. B. Bögen) werden als '-' gelesen.",
    )
    rueckstosskompensation: int = Field(
        default=0,
        alias="RK",
        description="Rückstoßkompensation; '-' und leere CSV-Werte werden als 0 gelesen.",
    )
    munition: Optional[str] = Field(default=None, alias="Munition")
    verfuegbarkeit: Optional[str] = Field(
        default=None,
        description="Nicht im aktuellen Waffen-CSV enthalten.",
    )
    preis: Optional[float] = Field(
        default=None,
        ge=0,
        description="Nicht im aktuellen Waffen-CSV enthalten.",
    )
    quelle: Optional[str] = Field(default=None, alias="Fundstelle")
    zubehoer: Optional[str] = Field(default=None, alias="Zubehör")

    @field_validator(
        "waffentyp",
        "praezision",
        "schaden",
        "durchschlag",
        "feuermodus",
        mode="before",
    )
    @classmethod
    def _blank_weapon_text(cls, value: object) -> str:
        """Leere, fehlende oder verrutschte CSV-Zellen als '-' speichern."""
        if value is None:
            return "-"
        if isinstance(value, float) and value != value:
            return "-"
        text = str(value).strip()
        if text == "" or text.lower() in {"nan", "none"}:
            return "-"
        return text


class AutosoftTemplate(CatalogModel):
    """Autosoft-Eintrag aus Autosoft.csv."""

    name: str = Field(..., alias="Name")
    kategorie: str = Field(..., alias="Kategorie")
    minimale_stufe: int = Field(..., alias="minimale_Stufe", ge=1)
    maximale_stufe: int = Field(..., alias="maximale_Stufe", ge=1)
    erlaeuterungen: Optional[str] = Field(default=None, alias="Erläuterungen")
    quelle: Optional[str] = Field(default=None, alias="Quelle")
    seite: Optional[int] = Field(default=None, alias="Seite")


class CommlinkTemplate(CatalogModel):
    """Kommlink-Eintrag aus Kommlinks.csv."""

    name: str = Field(..., alias="Name")
    kategorie: str = Field(default="Kommlinks", alias="Kategorie")
    geraetestufe: int = Field(..., alias="Gerätestufe", ge=0)
    datenverarbeitung: int = Field(..., alias="Datenverarbeitung", ge=0)
    firewall: int = Field(..., alias="Firewall", ge=0)
    quelle: Optional[str] = Field(default=None, alias="Quelle")
    seite: Optional[int] = Field(default=None, alias="Seite")


class RiggerConsoleTemplate(CatalogModel):
    """Riggerkonsolen-Eintrag aus Riggerkonsolen.csv."""

    name: str = Field(..., alias="Name")
    kategorie: str = Field(default="Riggerkonsole", alias="Kategorie")
    geraetestufe: int = Field(..., alias="Gerätestufe", ge=0)
    datenverarbeitung: int = Field(..., alias="Datenverarbeitung", ge=0)
    firewall: int = Field(..., alias="Firewall", ge=0)
    rauschunterdrueckung: int = Field(
        default=0,
        ge=0,
        description="Rauschunterdrückung der Konsole (nicht als eigene CSV-Spalte vorhanden).",
    )
    programme: int = Field(
        default=0,
        alias="Programme",
        ge=0,
        description="Anzahl teilbarer Programme / Sharing-Slots.",
    )
    quelle: Optional[str] = Field(default=None, alias="Quelle")
    seite: Optional[int] = Field(default=None, alias="Seite")


# ---------------------------------------------------------------------------
# 2. Charakter-Ebene (manuelle Erfassung)
# ---------------------------------------------------------------------------


class Attribute(DomainModel):
    """Körperliche und geistige Attribute nach SR5 (ganzzahlig)."""

    KON: int = Field(default=1, ge=1, description="Konstitution")
    GES: int = Field(default=1, ge=1, description="Geschicklichkeit")
    REA: int = Field(default=1, ge=1, description="Reaktion")
    STR: int = Field(default=1, ge=1, description="Stärke")
    WIL: int = Field(default=1, ge=1, description="Willenskraft")
    LOG: int = Field(default=1, ge=1, description="Logik")
    INT: int = Field(default=1, ge=1, description="Intuition")
    CHA: int = Field(default=1, ge=1, description="Charisma")
    EDG: int = Field(default=1, ge=1, description="Edge")
    MAG: Optional[int] = Field(default=None, ge=0, description="Magie, falls vorhanden")
    RES: Optional[int] = Field(default=None, ge=0, description="Resonanz, falls vorhanden")
    ESS: float = Field(default=6.0, ge=0, le=6, description="Essenz")


class Skills(DomainModel):
    """Rigger-relevante Fertigkeiten (Würfel ohne Attribut, 0 = untrainiert)."""

    bodenfahrzeuge: int = Field(default=0, ge=0)
    flugzeuge: int = Field(default=0, ge=0)
    wasserfahrzeuge: int = Field(default=0, ge=0)
    laeufer: int = Field(default=0, ge=0, description="Pilotieren (Läufer)")
    geschuetze: int = Field(default=0, ge=0)
    wahrnehmung: int = Field(default=0, ge=0)
    elektronische_kriegsfuehrung: int = Field(default=0, ge=0)
    schleichen: int = Field(default=0, ge=0, description="Stealth")
    hardware: int = Field(default=0, ge=0)
    computer: int = Field(default=0, ge=0)


class SkillSpecializations(DomainModel):
    """Ausgewählte Spezialisierungen der Fahrzeug- und Geschützfertigkeiten."""

    bodenfahrzeuge: List[str] = Field(default_factory=list)
    flugzeuge: List[str] = Field(default_factory=list)
    wasserfahrzeuge: List[str] = Field(default_factory=list)
    laeufer: List[str] = Field(default_factory=list)
    geschuetze: List[str] = Field(default_factory=list)


class Quality(DomainModel):
    """Vorteil oder Nachteil, z. B. Fahrzeugempathie."""

    name: str
    beschreibung: str = ""


class RiggerConsole(DomainModel):
    """Laufende Programme und gespiegelte Basiswerte des Steuergeräts."""

    geraetestufe: int = Field(default=1, ge=0, description="Gerätestufe des Steuergeräts.")
    basis_datenverarbeitung: int = Field(default=0, ge=0)
    basis_firewall: int = Field(default=0, ge=0)
    programme: List[str] = Field(
        default_factory=list,
        description="Laufende Cyberprogramme der Konsole.",
    )


class Character(DomainModel):
    """Manuell erfasster Rigger-Charakter."""

    name: str = ""
    attribute: Attribute = Field(default_factory=Attribute)
    skills: Skills = Field(default_factory=Skills)
    spezialisierungen: SkillSpecializations = Field(default_factory=SkillSpecializations)
    riggerkonsole: RiggerConsole = Field(default_factory=RiggerConsole)
    programme: List[str] = Field(
        default_factory=list,
        description="Alias der laufenden Cyberprogramme; primär riggerkonsole.programme.",
    )
    rauschen: int = Field(
        default=0,
        ge=0,
        description="Umgebungsrauschen (Noise) der Matrix/Funkstrecke.",
    )
    rigger_control_level: int = Field(
        default=0,
        ge=0,
        le=3,
        description="Stufe der Riggersteuerung (Control Rig), 0 = nicht vorhanden.",
    )
    riggerkontrollbooster: int = Field(
        default=0,
        ge=0,
        le=3,
        description="Wird zur Riggerkontrolle addiert (effektive Stufe).",
    )
    qualities: List[Quality] = Field(default_factory=list)
    vorteile: List[str] = Field(
        default_factory=list,
        description="Ausgewählte Rigger-Vorteile (kanonische Namen).",
    )
    smartlink_active: bool = Field(
        default=False,
        description="Smartlink-Bonus +2 auf den Geschütze-Pool (nicht Autopilot).",
    )

    @model_validator(mode="after")
    def _sync_programme(self) -> "Character":
        """Hält Character.programme und riggerkonsole.programme deckungsgleich."""
        console_progs = [item for item in self.riggerkonsole.programme if item]
        char_progs = [item for item in self.programme if item]
        if console_progs:
            self.programme = list(console_progs)
        elif char_progs:
            self.riggerkonsole.programme = list(char_progs)
        return self


# ---------------------------------------------------------------------------
# 3. Instanziierte Objekte (konkretes Setup im Einsatz)
# ---------------------------------------------------------------------------


class Zustandsmonitor(DomainModel):
    """Aktueller physischer Zustand eines Fahrzeugs oder einer Drohne."""

    aktuell: int = Field(default=0, ge=0, description="Aktuelle Schadenskästchen")
    maximum: Optional[int] = Field(
        default=None,
        ge=0,
        description="Maximale Kästchen; wird später aus Rumpf berechnet.",
    )


MAX_MOUNTED_WEAPONS = 4


class MountedWeapon(DomainModel):
    """Am Fahrzeug oder an der Drohne montierte Waffe (max. 4 Slots)."""

    name: str
    typ: str = "-"
    praezision: str = "-"
    schaden: str = "-"
    dk: str = "-"
    modus: str = "-"
    rk: int = 0
    munition: Optional[str] = None
    up_kosten: int = Field(default=0, ge=0, description="Upgrade-Punkte (Drohnen).")
    slot_kosten: int = Field(default=0, ge=0, description="Halterungs-Slots (Fahrzeuge).")
    smartlink: bool = False
    spezialisierung_aktiv: bool = False
    feste_halterung: bool = Field(
        default=False,
        description="True: unbewegliche Halterung (Fahrzeugfertigkeit / Manövrieren).",
    )
    munitionsart: Optional[str] = Field(
        default=None,
        description="z. B. Regular, APDS, Spreng, Stick-n-Shock",
    )
    munition_aktuell: Optional[int] = Field(default=None, ge=0)

    @field_validator("typ", "praezision", "schaden", "dk", "modus", mode="before")
    @classmethod
    def _blank_mounted_text(cls, value: object) -> str:
        if value is None:
            return "-"
        if isinstance(value, float) and value != value:
            return "-"
        text = str(value).strip()
        if text == "" or text.lower() in {"nan", "none"}:
            return "-"
        return text

    @classmethod
    def from_template(
        cls,
        template: WeaponTemplate,
        *,
        up_kosten: int,
        slot_kosten: int,
        smartlink: bool = False,
        spezialisierung_aktiv: bool = False,
        feste_halterung: bool = False,
    ) -> "MountedWeapon":
        """Baut eine Montage-Instanz aus einem Katalogeintrag."""
        return cls(
            name=template.name,
            typ=template.waffentyp,
            praezision=template.praezision or "-",
            schaden=template.schaden or "-",
            dk=template.durchschlag or "-",
            modus=template.feuermodus or "-",
            rk=template.rueckstosskompensation,
            munition=template.munition,
            up_kosten=up_kosten,
            slot_kosten=slot_kosten,
            smartlink=smartlink,
            spezialisierung_aktiv=spezialisierung_aktiv,
            feste_halterung=feste_halterung,
        )


class ActiveVehicle(DomainModel):
    """Konkretes Fahrzeug oder konkrete Drohne im aktuellen Setup."""

    template: VehicleTemplate
    bezeichnung: Optional[str] = Field(
        default=None,
        description="Optionaler Einsatzname, z. B. 'Spotter-1'.",
    )
    waffen: List[MountedWeapon] = Field(
        default_factory=list,
        max_length=MAX_MOUNTED_WEAPONS,
        description="Bis zu vier montierte Waffen.",
    )
    autosofts: List[AutosoftTemplate] = Field(default_factory=list)
    fahrzeugfertigkeit: Fahrzeugfertigkeit = Field(
        default=Fahrzeugfertigkeit.BODENFAHRZEUGE,
        description="Manuell gewählte Fertigkeit für den Steuer-Würfelpool.",
    )
    interface_mode: InterfaceMode = InterfaceMode.AUTOPILOT
    zustandsmonitor: Zustandsmonitor = Field(default_factory=Zustandsmonitor)
    spezialisierung_steuern: bool = Field(
        default=False,
        description="Manuell: +2 Spezialisierung auf den Steuern-Pool.",
    )
    spezialisierung_geschuetze: bool = Field(
        default=False,
        description="Manuell: +2 Spezialisierung auf den Geschütze-Pool.",
    )


class RiggerState(DomainModel):
    """Wurzelobjekt: ein Charakter, ein Steuergerät, die aktiven Fahrzeuge."""

    character: Character
    steuergeraet: Union[CommlinkTemplate, RiggerConsoleTemplate]
    fahrzeuge: List[ActiveVehicle] = Field(default_factory=list)
