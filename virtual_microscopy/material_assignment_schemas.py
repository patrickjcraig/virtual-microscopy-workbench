"""Explicit scalar-material assumptions; no propagation request or defaults."""
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictFloat, field_validator, model_validator

from .schemas import MaterialId, StrictModel, Twin


class MaterialParameters(StrictModel):
    density_kg_m3: StrictFloat = Field(ge=1, le=30000)
    relaxed_modulus_gpa: StrictFloat = Field(ge=1e-6, le=1000)
    unrelaxed_modulus_gpa: StrictFloat = Field(ge=1e-6, le=1000)
    relaxation_time_us: StrictFloat = Field(ge=1e-6, le=100)

    @model_validator(mode="after")
    def ordered_moduli(self):
        if self.unrelaxed_modulus_gpa < self.relaxed_modulus_gpa:
            raise ValueError("Unrelaxed longitudinal modulus must be at least the relaxed modulus.")
        return self


class ManualMaterialOrigin(MaterialParameters):
    kind: Literal["manual"]


class ReportLayerMaterialOrigin(StrictModel):
    kind: Literal["report_layer"]
    report_id: str
    layer_index: int = Field(ge=0, le=7, strict=True)

    @field_validator("report_id")
    @classmethod
    def canonical_id(cls, value):
        try:
            if str(UUID(value)) != value:
                raise ValueError
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("Source report ID must be a canonical UUID.") from exc
        return value


class MaterialBinding(StrictModel):
    material_id: MaterialId
    name: str = Field(min_length=1, max_length=160)
    note: str = Field(min_length=1, max_length=2000)
    origin: Annotated[ManualMaterialOrigin | ReportLayerMaterialOrigin, Field(discriminator="kind")]

    @field_validator("name", "note")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Material names and explanatory notes cannot be blank.")
        return value


class SLSMaterialAssignmentRequest(StrictModel):
    kind: Literal["sls_material_assignment"]
    name: str = Field(min_length=1, max_length=160)
    twin: Twin
    include_defects: StrictBool = True
    coverage_scope: Literal["all_included", "selected_materials"] = "all_included"
    required_material_ids: list[MaterialId] = Field(default_factory=list, max_length=6)
    bindings: list[MaterialBinding] = Field(default_factory=list, max_length=6)

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Assignment name cannot be blank.")
        return value

    @model_validator(mode="after")
    def coherent_scope(self):
        selected = self.required_material_ids
        if len(selected) != len(set(selected)):
            raise ValueError("Required material IDs must be unique.")
        if (self.coverage_scope == "selected_materials") != bool(selected):
            raise ValueError("Only selected-material scope requires a nonempty required-material list.")
        supplied = [binding.material_id for binding in self.bindings]
        if len(supplied) != len(set(supplied)):
            raise ValueError("Each material ID may have at most one binding.")
        inventory = {obj.material for obj in self.twin.objects
                     if self.include_defects or obj.role != "defect"}
        if not set(selected).issubset(inventory) or not set(supplied).issubset(inventory):
            raise ValueError("Required and supplied material IDs must appear in included frozen primitives.")
        return self


class MaterialColumnRequest(StrictModel):
    x_mm: StrictFloat = Field(ge=0, le=100)
    y_mm: StrictFloat = Field(ge=0, le=100)
