"""Typed public catalog DTO; extra fields are rejected."""
from uuid import UUID
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CatalogSchema(BaseModel):
    model_config=ConfigDict(extra="forbid")


class BrandRead(CatalogSchema):
    id: UUID
    name: str = Field(min_length=1)


class ModelRead(CatalogSchema):
    id: UUID
    brandId: UUID
    name: str = Field(min_length=1)
    category: Literal["vacuum","window_cleaner","lawn_mower","pool_cleaner",
                      "companion","quadruped","humanoid","other"]
    supportStatus: Literal["candidate","evaluation","supported",
                           "restricted","not_supported","retired"]


class VariantRead(CatalogSchema):
    id: UUID
    modelId: UUID
    manufacturerSku: str = Field(min_length=1)
    regionCode: str = Field(min_length=1)
    hardwareRevision: str = Field(min_length=1)
    dockSku: str = Field(min_length=1)


class PartRead(CatalogSchema):
    id: UUID
    partNumber: str = Field(min_length=1)
    name: str = Field(min_length=1)
    compatibilityStatus: Literal["unverified","manufacturer_documented",
                                 "technician_tested","incompatible","revoked"]="unverified"
