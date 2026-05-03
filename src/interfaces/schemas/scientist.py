from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, Dict, Any, List


class PinDef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(description="Pin name as defined in the KiCad symbol (e.g., '1', 'A', 'VCC')")
    number: Optional[str] = Field(default=None, description="Pin number if available")
    electrical_type: Optional[str] = Field(default=None, description="Electrical type (input, output, power, etc.)")
    no_connect: bool = Field(
        default=False,
        description=(
            "True when this pin is intentionally left unconnected. The floating-pin "
            "detector skips NC pins; the validator (V12) rejects NC pins that still "
            "appear in a net's connections."
        ),
    )


class ComponentDef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ref: str = Field(description="Reference designator (e.g., 'R1', 'C2', 'U3')")
    category: str = Field(description="Broad component category (resistor, capacitor, op_amp, connector, etc.)")
    exact_part_name: str = Field(description="Exact KiCad symbol name (e.g., 'R', 'LED', 'USB_C_Receptacle_USB2.0_16P')")
    library: str = Field(description="KiCad library where the symbol resides (e.g., 'Device', 'Connector')")
    mpn: Optional[str] = Field(default=None, description="Manufacturer Part Number for BOM export. Never passed to SKiDL as a symbol name.")
    custom_symbol_available: bool = Field(default=False, description="True if a matching .kicad_sym file exists in assets/librarys/kicad_lib/")
    library_verified: bool = Field(default=True, description="False if the symbol was assigned via the R-RESOLVE fallback table rather than tool lookup")
    substitution_note: Optional[str] = Field(default=None, description="Explanation of any fallback substitution applied to this component")
    footprint: Optional[str] = Field(default=None, description="Recommended footprint (e.g., 'Resistor_SMD:R_0603_1608Metric')")
    value: Optional[str] = Field(default=None, description="Value/rating (e.g., '10k', '0.1uF', 'LM358')")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Additional key-value attributes (tolerance, voltage, etc.)")
    pins: List[PinDef] = Field(default_factory=list, description="List of pins available on this symbol")


class NetConnection(BaseModel):
    component_ref: str = Field(description="Reference designator of the component")
    pin_name: str = Field(description="Name of the pin to connect")


class NetDef(BaseModel):
    name: str = Field(description="Net name (e.g., 'VCC', 'GND', 'SIGNAL_IN')")
    connections: List[NetConnection] = Field(description="All pins that belong to this net")


class ReasoningStep(BaseModel):
    step_id: str = Field(description="Unique identifier for this reasoning step")
    summary: str = Field(description="Brief summary of the step")
    details: str = Field(description="Detailed explanation")


class UnresolvedPart(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intended_function: str = Field(description="What role this part plays in the circuit")
    searched_queries: List[str] = Field(default_factory=list, description="Queries tried against search_components")
    assigned_name: str = Field(description="Fallback exact_part_name that was ultimately used")
    assigned_library: Optional[str] = Field(default=None, description="Fallback library that was ultimately used")
    substitution_note: str = Field(description="Why this fallback was chosen")


class DesignConstraints(BaseModel):
    model_config = ConfigDict(extra="allow")

    input_voltage: Optional[str] = Field(default=None)
    output_voltage: Optional[str] = Field(default=None)
    max_current: Optional[str] = Field(default=None)
    operating_temp_range: Optional[str] = Field(default=None)


class BlueprintMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    total_components: int = 0
    verified_components: int = 0
    unverified_components: int = 0
    safety_components_included: List[str] = Field(default_factory=list)
    schema_version: str = "1.1"


class SystemBlueprint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    design_id: str = Field(description="Unique identifier for this design session")
    title: str = Field(description="Short, descriptive title of the circuit")
    description: str = Field(description="Clear, one-paragraph description of the circuit's function")
    assumptions: List[str] = Field(default_factory=list, description="Explicit assumptions made during design")
    components: List[ComponentDef] = Field(description="All components that will appear in the schematic")
    nets: List[NetDef] = Field(description="All electrical nets connecting the components")
    design_constraints: DesignConstraints = Field(default_factory=DesignConstraints, description="Key constraints (voltage, current, frequency, etc.)")
    unresolved_parts: List[UnresolvedPart] = Field(default_factory=list, description="Parts that required an R-RESOLVE fallback")
    reasoning_steps: List[ReasoningStep] = Field(default_factory=list, description="Structured Chain-of-Thought")
    metadata: BlueprintMetadata = Field(default_factory=BlueprintMetadata, description="Timestamps, version, counts, safety inventory")
