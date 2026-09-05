"""Bounded, explicit scalar layered-acoustic experiments, separate from SAM volumes."""
from typing import Literal

from pydantic import Field, StrictFloat, field_validator, model_validator

from .schemas import StrictModel, Twin


class LayeredMedium(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    impedance_mrayl: StrictFloat = Field(ge=.0001, le=100)
    sound_speed_m_s: StrictFloat = Field(ge=100, le=20000)


class AcousticLayer(LayeredMedium):
    thickness_mm: StrictFloat = Field(ge=0, le=6)
    pressure_loss_db_mm: StrictFloat = Field(default=0, ge=0, le=100)
    material_id: Literal["water", "silicon", "copper", "solder", "epoxy", "fr4", "air"] | None = None


class LayeredStack(StrictModel):
    incident: LayeredMedium
    terminal: LayeredMedium
    layers: list[AcousticLayer] = Field(max_length=256)

    @model_validator(mode="after")
    def thickness_bound(self):
        if sum(layer.thickness_mm for layer in self.layers) > 6 + 1e-12:
            raise ValueError("The finite layered stack may span at most 6 mm.")
        return self


class LayeredColumnRequest(StrictModel):
    twin: Twin
    x_mm: StrictFloat = Field(ge=0, le=100)
    y_mm: StrictFloat = Field(ge=0, le=100)
    include_defects: bool = True

    @model_validator(mode="after")
    def inside_twin(self):
        if self.x_mm > self.twin.size_mm[0] or self.y_mm > self.twin.size_mm[1]:
            raise ValueError("Column X/Y must lie inside the specimen.")
        return self


class LayeredSpectrumSettings(StrictModel):
    start_mhz: StrictFloat = Field(default=0, ge=0, le=300)
    end_mhz: StrictFloat = Field(default=150, gt=0, le=300)
    samples: int = Field(default=2049, ge=2, le=8193, strict=True)

    @model_validator(mode="after")
    def ordered(self):
        if self.end_mhz <= self.start_mhz:
            raise ValueError("Spectrum end must exceed its start frequency.")
        return self


class SlabPulseSettings(StrictModel):
    center_frequency_mhz: StrictFloat = Field(default=50, ge=10, le=150)
    fractional_bandwidth: StrictFloat = Field(default=.5, ge=.2, le=1)
    sample_rate_mhz: StrictFloat = Field(default=400, gt=0, le=2400)
    record_start_us: StrictFloat = Field(default=0, ge=0, le=12)
    record_duration_us: StrictFloat = Field(default=2, ge=.05, le=12)
    surface_standoff_mm: StrictFloat = Field(default=0, ge=0, le=5)
    absolute_tolerance: StrictFloat = Field(default=1e-8, ge=1e-12, le=1e-3)
    max_echoes: int = Field(default=100000, ge=1, le=100000, strict=True)

    @model_validator(mode="after")
    def sampling(self):
        if self.record_start_us + self.record_duration_us > 12 + 1e-12:
            raise ValueError("The pulse recording must end at or before 12 us.")
        if self.sample_rate_mhz < 8*self.center_frequency_mhz:
            raise ValueError("Slab RF requires at least eight samples per carrier period.")
        if int(self.record_duration_us*self.sample_rate_mhz + 1e-9) + 1 > 16384:
            raise ValueError("Slab RF supports at most 16,384 recorded samples.")
        return self


class CausalGammaPulseSettings(StrictModel):
    center_frequency_mhz: StrictFloat = Field(default=50, ge=10, le=150)
    fractional_bandwidth: StrictFloat = Field(default=.5, ge=.2, le=1)
    sample_rate_mhz: StrictFloat = Field(default=400, gt=0, le=2400)
    record_start_us: StrictFloat = Field(default=0, ge=0, le=12)
    record_duration_us: StrictFloat = Field(default=2, ge=.05, le=12)
    surface_standoff_mm: StrictFloat = Field(default=0, ge=0, le=5)
    absolute_tolerance: StrictFloat = Field(default=1e-7, ge=1e-12, le=1e-3)
    gamma_order: int = Field(default=12, ge=4, le=24, strict=True)
    precision_bits: Literal[64, 96, 128, 192, 256] = 128

    @field_validator("precision_bits", mode="before")
    @classmethod
    def exact_precision(cls, value):
        if type(value) is not int:
            raise ValueError("Precision must be an integer bit count.")
        return value

    @model_validator(mode="after")
    def sampling(self):
        if self.record_start_us + self.record_duration_us > 12 + 1e-12:
            raise ValueError("The causal pulse recording must end at or before 12 us.")
        if self.sample_rate_mhz < 8*self.center_frequency_mhz:
            raise ValueError("Causal RF requires at least eight samples per carrier period.")
        if int(self.record_duration_us*self.sample_rate_mhz + 1e-9) + 1 > 2049:
            raise ValueError("Causal RF supports at most 2,049 recorded samples.")
        return self


class LayeredAnalysisRequest(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    stack: LayeredStack
    spectrum: LayeredSpectrumSettings = Field(default_factory=LayeredSpectrumSettings)
    pulse: SlabPulseSettings | None = None
    causal_pulse: CausalGammaPulseSettings | None = None
    source_column: LayeredColumnRequest | None = None

    @model_validator(mode="after")
    def supported_pulse(self):
        if not self.name.strip():
            raise ValueError("Name cannot be blank.")
        if self.pulse is not None and self.causal_pulse is not None:
            raise ValueError("Choose either the Gaussian slab pulse or the causal gamma pulse for one report.")
        if self.pulse is not None and (len(self.stack.layers) != 1 or self.stack.layers[0].thickness_mm <= 0):
            raise ValueError("Gaussian slab RF requires exactly one positive-thickness finite layer. Choose the causal gamma pulse for a multilayer response.")
        return self
