from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from .normalize import normalize_entity, normalize_text, sha256_parts

_LOCALITY_TYPES = (
    ("город", ("г ", "г.", "город ")),
    ("поселок городского типа", ("пгт ", "пгт.", "поселок городского типа ")),
    ("село", ("с ", "с.", "село ")),
    ("деревня", ("д ", "д.", "деревня ")),
    ("поселок", ("п ", "п.", "поселок ", "посёлок ")),
)
_TERRITORY_TYPES = (
    ("СНТ", ("снт ", "садоводческое некоммерческое товарищество ")),
    ("ДНТ", ("днт ", "дачное некоммерческое товарищество ")),
    ("ТСН", ("тсн ", "товарищество собственников недвижимости ")),
    ("коттеджный поселок", ("кп ", "коттеджный поселок ")),
    ("территория", ("территория ", "тер. ", "массив ")),
)
_HOUSE_RE = re.compile(r"(?:^|[\s,;])(?:д\.?|дом)\s*([0-9а-яa-z][0-9а-яa-z\-/]*)", re.IGNORECASE)
_COORD_RE = re.compile(r"(-?\d{1,3}(?:[.,]\d+)?)")


def _first(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    raw = row.get("raw")
    if isinstance(raw, dict):
        normalized = {normalize_text(str(key)): value for key, value in raw.items()}
        for key in keys:
            value = normalized.get(normalize_text(key))
            if value not in (None, ""):
                return str(value).strip()
    return ""


def _split_type(value: str, definitions: tuple[tuple[str, tuple[str, ...]], ...]) -> tuple[str, str]:
    cleaned = " ".join(value.strip().split())
    key = normalize_text(cleaned)
    for type_name, prefixes in definitions:
        for prefix in prefixes:
            normalized_prefix = normalize_text(prefix)
            if key == normalized_prefix:
                return type_name, ""
            if key.startswith(normalized_prefix + " "):
                words = cleaned.split()
                prefix_words = len(prefix.strip().replace(".", "").split())
                return type_name, " ".join(words[prefix_words:]).strip(" ,.-")
    return "", cleaned


def _coordinate(value: str) -> float | None:
    match = _COORD_RE.search(value.replace(",", "."))
    if not match:
        return None
    try:
        return round(float(match.group(1).replace(",", ".")), 6)
    except ValueError:
        return None


def _house(row: dict[str, Any], street: str) -> tuple[str, str]:
    explicit = _first(row, "house", "дом", "номер дома")
    if explicit:
        return explicit, street
    for candidate in (street, _first(row, "text", "raw_text", "адрес", "исходный текст")):
        match = _HOUSE_RE.search(candidate)
        if match:
            house = match.group(1)
            cleaned_street = _HOUSE_RE.sub(" ", street).strip(" ,;")
            return house, cleaned_street
    return "", street


@dataclass(frozen=True)
class CanonicalAddress:
    municipal_district: str = ""
    urban_district: str = ""
    intracity_district: str = ""
    locality: str = ""
    locality_type: str = ""
    territory: str = ""
    territory_type: str = ""
    street: str = ""
    house: str = ""
    latitude: float | None = None
    longitude: float | None = None
    branch: str = ""
    res: str = ""

    @property
    def coordinate_cell(self) -> str:
        if self.latitude is None or self.longitude is None:
            return ""
        return f"{round(self.latitude, 4):.4f}:{round(self.longitude, 4):.4f}"

    @property
    def address_type(self) -> str:
        if self.territory_type in {"СНТ", "ДНТ", "ТСН"}:
            return "садовое товарищество"
        if self.territory:
            return "территория"
        if self.house:
            return "дом"
        if self.street:
            return "улица"
        if self.locality:
            return "населенный пункт"
        return "неопределенный"

    @property
    def context_key(self) -> str:
        return sha256_parts(
            [
                normalize_entity(self.municipal_district),
                normalize_entity(self.urban_district),
                normalize_entity(self.intracity_district),
                self.coordinate_cell,
            ]
        )

    @property
    def ambiguity_key(self) -> str:
        if self.territory:
            return sha256_parts(
                ["territory", normalize_text(self.territory_type), normalize_entity(self.territory)]
            )
        return sha256_parts(
            ["locality", normalize_text(self.locality_type), normalize_entity(self.locality)]
        )

    @property
    def canonical_key(self) -> str:
        coordinate_discriminator = ""
        if self.territory or not (
            self.municipal_district or self.urban_district or self.intracity_district
        ):
            coordinate_discriminator = self.coordinate_cell
        return sha256_parts(
            [
                normalize_entity(self.municipal_district),
                normalize_entity(self.urban_district),
                normalize_entity(self.intracity_district),
                normalize_text(self.locality_type),
                normalize_entity(self.locality),
                normalize_text(self.territory_type),
                normalize_entity(self.territory),
                normalize_entity(self.street),
                normalize_entity(self.house),
                coordinate_discriminator,
            ]
        )

    @property
    def has_region_context(self) -> bool:
        return bool(self.municipal_district or self.urban_district or self.intracity_district)

    @property
    def completeness(self) -> float:
        score = 0.0
        if self.locality:
            score += 0.25
        if self.locality_type:
            score += 0.05
        if self.has_region_context:
            score += 0.25
        if self.territory:
            score += 0.10
        if self.street:
            score += 0.12
        if self.house:
            score += 0.08
        if self.latitude is not None and self.longitude is not None:
            score += 0.15
        return round(min(1.0, score), 3)

    def payload(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "canonical_key": self.canonical_key,
            "ambiguity_key": self.ambiguity_key,
            "context_key": self.context_key,
            "coordinate_cell": self.coordinate_cell,
            "address_type": self.address_type,
            "completeness": self.completeness,
        }


def canonicalize_address(row: dict[str, Any]) -> CanonicalAddress:
    locality_type, locality = _split_type(
        _first(row, "locality", "населенный пункт"), _LOCALITY_TYPES
    )
    explicit_locality_type = _first(row, "locality_type", "тип населенного пункта")
    if explicit_locality_type:
        locality_type = explicit_locality_type

    territory_value = _first(row, "territory", "settlement", "снт", "территория")
    territory_type, territory = _split_type(territory_value, _TERRITORY_TYPES)
    explicit_territory_type = _first(row, "territory_type", "тип территории")
    if explicit_territory_type:
        territory_type = explicit_territory_type

    street = _first(row, "street", "улица")
    house, street = _house(row, street)
    latitude = _coordinate(_first(row, "latitude", "lat", "широта"))
    longitude = _coordinate(_first(row, "longitude", "lon", "lng", "долгота"))

    municipal = _first(row, "municipal_district", "district", "муниципальный район", "район")
    urban = _first(row, "urban_district", "городской округ")
    intracity = _first(row, "intracity_district", "внутригородской район")

    return CanonicalAddress(
        municipal_district=municipal,
        urban_district=urban,
        intracity_district=intracity,
        locality=locality,
        locality_type=locality_type,
        territory=territory,
        territory_type=territory_type,
        street=street,
        house=house,
        latitude=latitude,
        longitude=longitude,
        branch=_first(row, "branch", "branch_name", "филиал"),
        res=_first(row, "res", "res_name", "рэс"),
    )
