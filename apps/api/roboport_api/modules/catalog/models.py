"""SQLAlchemy 2 mappings for Alembic-owned exact catalog schema."""
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class CatalogBase(DeclarativeBase):
    pass


class Brand(CatalogBase):
    __tablename__="catalog_brand"
    id: Mapped[UUID]=mapped_column(PG_UUID(as_uuid=True),primary_key=True)
    name: Mapped[str]=mapped_column(Text,nullable=False,unique=True)


class Model(CatalogBase):
    __tablename__="catalog_model"
    id: Mapped[UUID]=mapped_column(PG_UUID(as_uuid=True),primary_key=True)
    brand_id: Mapped[UUID]=mapped_column(PG_UUID(as_uuid=True),ForeignKey("catalog_brand.id"),nullable=False)
    name: Mapped[str]=mapped_column(Text,nullable=False)
    category: Mapped[str]=mapped_column(Text,nullable=False)


class Dock(CatalogBase):
    __tablename__="catalog_dock"
    id: Mapped[UUID]=mapped_column(PG_UUID(as_uuid=True),primary_key=True)
    dock_sku: Mapped[str]=mapped_column(Text,nullable=False)
    region_code: Mapped[str]=mapped_column(Text,nullable=False)
    hardware_revision: Mapped[str]=mapped_column(Text,nullable=False)


class Variant(CatalogBase):
    __tablename__="catalog_variant"
    id: Mapped[UUID]=mapped_column(PG_UUID(as_uuid=True),primary_key=True)
    model_id: Mapped[UUID]=mapped_column(PG_UUID(as_uuid=True),ForeignKey("catalog_model.id"),nullable=False)
    manufacturer_sku: Mapped[str]=mapped_column(Text,nullable=False)
    region_code: Mapped[str]=mapped_column(Text,nullable=False)
    hardware_revision: Mapped[str]=mapped_column(Text,nullable=False)
    dock_id: Mapped[UUID]=mapped_column(PG_UUID(as_uuid=True),ForeignKey("catalog_dock.id"),nullable=False)


class Part(CatalogBase):
    __tablename__="catalog_part"
    id: Mapped[UUID]=mapped_column(PG_UUID(as_uuid=True),primary_key=True)
    part_number: Mapped[str]=mapped_column(Text,nullable=False,unique=True)
    name: Mapped[str]=mapped_column(Text,nullable=False)


class Evidence(CatalogBase):
    __tablename__="catalog_evidence"
    id: Mapped[UUID]=mapped_column(PG_UUID(as_uuid=True),primary_key=True)
    source_uri: Mapped[str]=mapped_column(Text,nullable=False)
    sha256: Mapped[str]=mapped_column(String(64),nullable=False)
    created_by: Mapped[UUID]=mapped_column(PG_UUID(as_uuid=True),ForeignKey("app_user.id"),nullable=False)


class ServiceCapability(CatalogBase):
    __tablename__="service_capability"
    id: Mapped[UUID]=mapped_column(PG_UUID(as_uuid=True),primary_key=True)
    variant_id: Mapped[UUID]=mapped_column(PG_UUID(as_uuid=True),ForeignKey("catalog_variant.id"),nullable=False)
    operation_code: Mapped[str]=mapped_column(Text,nullable=False)
    evidence_id: Mapped[UUID]=mapped_column(PG_UUID(as_uuid=True),ForeignKey("catalog_evidence.id"),nullable=False)
    status: Mapped[str]=mapped_column(Text,nullable=False)


class PartCompatibility(CatalogBase):
    __tablename__="part_compatibility"
    id: Mapped[UUID]=mapped_column(PG_UUID(as_uuid=True),primary_key=True)
    part_id: Mapped[UUID]=mapped_column(PG_UUID(as_uuid=True),ForeignKey("catalog_part.id"),nullable=False)
    variant_id: Mapped[UUID]=mapped_column(PG_UUID(as_uuid=True),ForeignKey("catalog_variant.id"),nullable=False)
    evidence_id: Mapped[UUID]=mapped_column(PG_UUID(as_uuid=True),ForeignKey("catalog_evidence.id"),nullable=False)
    requested_status: Mapped[str]=mapped_column(Text,nullable=False)
    status: Mapped[str]=mapped_column(Text,nullable=False)
