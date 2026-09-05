"""Strict manual single-relaxation scalar materials; no legacy/source inference."""
from fractions import Fraction
from typing import Literal

from pydantic import Field, StrictFloat, field_validator, model_validator

from .schemas import StrictModel


class SLSMedium(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    impedance_mrayl: StrictFloat = Field(ge=.0001, le=100)
    sound_speed_m_s: StrictFloat = Field(ge=100, le=20000)


class SLSLayer(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    thickness_mm: StrictFloat = Field(ge=0, le=6)
    density_kg_m3: StrictFloat = Field(ge=1, le=30000)
    relaxed_modulus_gpa: StrictFloat = Field(ge=1e-6, le=1000)
    unrelaxed_modulus_gpa: StrictFloat = Field(ge=1e-6, le=1000)
    relaxation_time_us: StrictFloat = Field(ge=1e-6, le=100)

    @model_validator(mode="after")
    def passive_moduli(self):
        if self.unrelaxed_modulus_gpa < self.relaxed_modulus_gpa:
            raise ValueError("The unrelaxed longitudinal modulus must be at least the relaxed modulus.")
        return self


class SLSStack(StrictModel):
    incident: SLSMedium
    terminal: SLSMedium
    layers: list[SLSLayer] = Field(max_length=8)

    @model_validator(mode="after")
    def total_thickness(self):
        if sum((Fraction(layer.thickness_mm) for layer in self.layers), Fraction()) > 6:
            raise ValueError("The represented finite SLS stack may span at most 6 mm.")
        return self


class SLSSpectrumSettings(StrictModel):
    start_mhz: StrictFloat = Field(default=0., ge=0, le=300)
    end_mhz: StrictFloat = Field(default=150., gt=0, le=300)
    samples: int = Field(default=2049, ge=2, le=8193, strict=True)

    @model_validator(mode="after")
    def ordered(self):
        if self.end_mhz <= self.start_mhz:
            raise ValueError("Spectrum end must exceed its start frequency.")
        return self


class SLSCausalPulseSettings(StrictModel):
    center_frequency_mhz: StrictFloat = Field(default=50., ge=10, le=150)
    fractional_bandwidth: StrictFloat = Field(default=.5, ge=.2, le=1)
    sample_rate_mhz: StrictFloat = Field(default=400., gt=0, le=2400)
    record_start_us: StrictFloat = Field(default=0., ge=0, le=12)
    record_duration_us: StrictFloat = Field(default=2., ge=.05, le=12)
    surface_standoff_mm: StrictFloat = Field(default=0., ge=0, le=5)
    absolute_tolerance: StrictFloat = Field(default=1e-7, ge=1e-12, le=1e-3)
    gamma_order: int = Field(default=12, ge=4, le=24, strict=True)
    precision_bits: Literal[64, 96, 128, 192, 256] = 128

    @field_validator("precision_bits", mode="before")
    @classmethod
    def precision_type(cls, value):
        if type(value) is not int:
            raise ValueError("Precision requires an integer bit count.")
        return value

    @model_validator(mode="after")
    def recording(self):
        if self.record_start_us+self.record_duration_us > 12+1e-12:
            raise ValueError("The causal recording must end at or before 12 us.")
        if self.sample_rate_mhz < 8*self.center_frequency_mhz:
            raise ValueError("Causal RF requires at least eight samples per carrier period.")
        if int(self.record_duration_us*self.sample_rate_mhz+1e-9)+1 > 2049:
            raise ValueError("Causal RF supports at most 2,049 actual time centers.")
        return self


class SLSAnalysisRequest(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    stack: SLSStack
    spectrum: SLSSpectrumSettings = Field(default_factory=SLSSpectrumSettings)
    causal_pulse: SLSCausalPulseSettings | None = None

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Report name cannot be blank.")
        return value
