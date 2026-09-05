"""Immutable SAM recipes and explicit, bounded one-variable case proposals."""
from datetime import datetime
from typing import Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt, StrictStr, field_validator, model_validator

from .datasets import checked_id
from .schemas import StrictModel
from .volume_schemas import SamVolumeRequest


def _recipe_name(value):
    if not value.strip():
        raise ValueError("Recipe name cannot be blank.")
    return value


def _optional_id(value):
    return checked_id(value) if value is not None else None


class RecipeGate(StrictModel):
    start_us: float = Field(ge=0, le=12)
    end_us: float = Field(gt=0, le=12)

    @model_validator(mode="after")
    def ordered(self):
        if self.end_us <= self.start_us:
            raise ValueError("Gate end must exceed gate start.")
        return self


class RecipeCreate(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    request: SamVolumeRequest
    parent_recipe_id: str | None = None
    default_gate: RecipeGate | None = None

    _nonblank = field_validator("name")(_recipe_name)
    _parent = field_validator("parent_recipe_id")(_optional_id)


class RecipeFromDataset(StrictModel):
    dataset_id: str
    name: str = Field(min_length=1, max_length=160)
    default_gate: RecipeGate | None = None

    _identifier = field_validator("dataset_id")(checked_id)
    _nonblank = field_validator("name")(_recipe_name)


class RecipeProvenance(StrictModel):
    source_dataset_id: str
    source_input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_manifest: dict

    _identifier = field_validator("source_dataset_id")(checked_id)


class RecipeRecord(StrictModel):
    # Keep historical requests as frozen JSON. Reading/importing a completed
    # recording must not invoke today's geometry constructors or forward model.
    kind: Literal["sam_acquisition_recipe"] = "sam_acquisition_recipe"
    schema_version: Literal[1] = 1
    recipe_id: str
    parent_recipe_id: str | None = None
    name: str = Field(min_length=1, max_length=160)
    created_at: str
    request: dict
    default_gate: RecipeGate | None = None
    provenance: RecipeProvenance | None = None
    request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    recipe_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    _identifier = field_validator("recipe_id")(checked_id)
    _parent = field_validator("parent_recipe_id")(_optional_id)
    _nonblank = field_validator("name")(_recipe_name)

    @field_validator("created_at")
    @classmethod
    def timestamp(cls, value):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("Recipe creation time must include its UTC offset.")
        return value


SweepField = Literal["frequency_mhz", "focus_mm", "fractional_bandwidth", "path_model",
                     "depth_samples", "defect", "include_defects"]


class CaseProposal(StrictModel):
    recipe_id: str
    field: SweepField
    values: list[StrictBool | StrictInt | StrictFloat | StrictStr] = Field(min_length=2, max_length=4)
    assembly_id: str | None = Field(default=None, min_length=1, max_length=64)
    defect_id: str | None = Field(default=None, min_length=1, max_length=100)

    _identifier = field_validator("recipe_id")(checked_id)

    @model_validator(mode="after")
    def validate_cases(self):
        if any(value == earlier for i, value in enumerate(self.values) for earlier in self.values[:i]):
            raise ValueError("Cases must contain two to four unique values.")
        if self.field in {"defect", "include_defects"}:
            if len(self.values) != 2 or any(type(value) is not bool for value in self.values):
                raise ValueError("A defect pair requires exactly false and true, in the requested order.")
        elif self.field == "path_model":
            if any(value not in ("voxel_centers_v1", "continuous_columns_v1") for value in self.values):
                raise ValueError("Path cases must explicitly select voxel_centers_v1 and continuous_columns_v1.")
        elif self.field == "depth_samples":
            if any(type(value) is not int or value not in (128, 256, 512, 1024) for value in self.values):
                raise ValueError("Depth cases must be integer values 128, 256, 512 or 1024.")
        elif any(type(value) not in (float, int) for value in self.values):
            raise ValueError("Acquisition parameter cases require finite numbers, not text or booleans.")
        if self.field == "defect":
            if self.assembly_id is None or self.defect_id is None:
                raise ValueError("An isolated defect pair requires its assembly_id and authored defect_id.")
        elif self.assembly_id is not None or self.defect_id is not None:
            raise ValueError("Assembly and defect IDs apply only to an isolated defect pair.")
        return self


class BatchSubmission(CaseProposal):
    idempotency_key: str = Field(min_length=1, max_length=128)
