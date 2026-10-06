"""Public Provider SDK contracts: descriptor, registration, and capability protocols."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, TypeVar, runtime_checkable

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from governance.domain.graph import GovernanceGraph
from governance.domain.lineage import ColumnLineageAssertion
from governance.domain.models import GovernanceModel
from governance.domain.observations import PropertyObservationSet
from governance.providers.capabilities import (
    SUPPORTED_CAPABILITIES,
    CapabilityId,
    parse_capability_id,
    sorted_capability_values,
)
from governance.providers.errors import (
    CODE_INVALID_CAPABILITY_BINDING,
    CODE_INVALID_CONFIG_VALIDATOR,
    CODE_INVALID_DESCRIPTOR,
    CODE_INVALID_PROVIDER_ID,
    CODE_INVALID_PROVIDER_VERSION,
    CODE_INVALID_SDK_COMPATIBILITY,
    CODE_UNKNOWN_CAPABILITY,
    ProviderDescriptorError,
    ProviderDiagnostic,
    ProviderRegistrationError,
)

PROVIDER_SDK_API_VERSION = "1"
PROVIDER_ENTRY_POINT_GROUP = "governance.providers"

_PROVIDER_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")

CapabilityT_co = TypeVar("CapabilityT_co", covariant=True)
RemoteStateRequestT_contra = TypeVar("RemoteStateRequestT_contra", contravariant=True)
RemoteStateT_co = TypeVar("RemoteStateT_co", covariant=True)
RemoteStateT_contra = TypeVar("RemoteStateT_contra", contravariant=True)
DesiredStateT_contra = TypeVar("DesiredStateT_contra", contravariant=True)
PlanT_co = TypeVar("PlanT_co", covariant=True)
MutationRequestT_contra = TypeVar("MutationRequestT_contra", contravariant=True)
MutationResultT_co = TypeVar("MutationResultT_co", covariant=True)
PreflightResultT_co = TypeVar("PreflightResultT_co", covariant=True)


@dataclass(frozen=True, slots=True)
class ProviderRuntimeContext:
    """Vendor-neutral runtime context for capability factory construction.

    Holds the resolved provider-specific payload and the config file directory.
    Core owns global config resolution; secrets and operational I/O are out of
    scope here. ``config`` is omitted from ``repr`` so resolved secret values
    are not leaked via logging or exception formatting. ``config_root`` is
    ``None`` when not provided (do not treat ``""`` as cwd).
    """

    config: Mapping[str, object] = field(default_factory=dict, repr=False)
    config_root: str | None = None


class CapabilityFactory(Protocol[CapabilityT_co]):
    """Construct a capability collaborator from runtime context.

    Factories MUST NOT run during registration or discovery. They receive
    provider-bounded config only when the core performs runtime construction.
    """

    def __call__(self, context: ProviderRuntimeContext) -> CapabilityT_co: ...


@runtime_checkable
class ProviderConfigValidator(Protocol):
    """Read-only validation boundary for a provider-specific config payload."""

    def validate(self, config: Mapping[str, object]) -> None:
        """Validate ``config`` in place without transforming global config.

        Success returns None. Failure raises ProviderError (or subclass) with
        deterministic diagnostics. MUST NOT perform HTTP, DB I/O, secret
        resolution, profile merge, mutation, or lifecycle decisions.
        """
        ...


class MetadataDiscoveryCapability(Protocol):
    def discover(self) -> GovernanceModel: ...


class GovernanceGraphCapability(Protocol):
    def load_graph(self) -> GovernanceGraph: ...


class PropertyObservationsCapability(Protocol):
    def load_observations(self) -> PropertyObservationSet: ...


class LineageCapability(Protocol):
    def load_lineage(self) -> Sequence[ColumnLineageAssertion]: ...


class RemoteStateReadCapability(Protocol[RemoteStateRequestT_contra, RemoteStateT_co]):
    """Read managed remote governance state for an explicit request scope.

    The request type is provider-specific (for example desired-state scope).
    Targets that need no scope MUST declare ``None`` and receive ``None``
    explicitly. Core supplies the request; providers MUST NOT discover scope
    from hidden mutable state or config alone.
    """

    def read_remote_state(self, request: RemoteStateRequestT_contra) -> RemoteStateT_co: ...


class TargetPlanningCapability(Protocol[DesiredStateT_contra, RemoteStateT_contra, PlanT_co]):
    """Build a plan from explicit desired state and previously read remote state.

    Planning MUST NOT mutate remote governance state and MUST NOT perform a
    second remote-state read. Remote state is supplied by the core from
    ``remote_state_read`` (or equivalent), not discovered implicitly.
    """

    def build_plan(
        self,
        desired_state: DesiredStateT_contra,
        remote_state: RemoteStateT_contra,
    ) -> PlanT_co: ...


class CompatibilityPreflightCapability(Protocol[PreflightResultT_co]):
    def run_preflight(self) -> PreflightResultT_co: ...


class AuthorizedMutationCapability(Protocol[MutationRequestT_contra, MutationResultT_co]):
    """Execute a mutation already authorized by the core.

    Presence of this capability NEVER constitutes authorization. The core
    MUST deliver already-authorized work; the provider MUST NOT self-authorize.
    """

    def execute_authorized(self, request: MutationRequestT_contra) -> MutationResultT_co: ...


def _validate_provider_id(provider_id: object) -> str:
    if not isinstance(provider_id, str) or not provider_id:
        raise ProviderDescriptorError(
            [
                ProviderDiagnostic(
                    code=CODE_INVALID_PROVIDER_ID,
                    path="/provider_id",
                    message="provider_id must be a non-empty string",
                )
            ]
        )
    if _PROVIDER_ID_RE.fullmatch(provider_id) is None:
        raise ProviderDescriptorError(
            [
                ProviderDiagnostic(
                    code=CODE_INVALID_PROVIDER_ID,
                    path="/provider_id",
                    message=(
                        f"provider_id {provider_id!r} is invalid; "
                        "expected lowercase dotted identifier matching "
                        r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$"
                    ),
                )
            ]
        )
    return provider_id


def _validate_display_name(display_name: object) -> str:
    if not isinstance(display_name, str) or not display_name.strip():
        raise ProviderDescriptorError(
            [
                ProviderDiagnostic(
                    code=CODE_INVALID_DESCRIPTOR,
                    path="/display_name",
                    message="display_name must be a non-empty string",
                )
            ]
        )
    return display_name


def _validate_provider_version(provider_version: object) -> str:
    if not isinstance(provider_version, str) or not provider_version:
        raise ProviderDescriptorError(
            [
                ProviderDiagnostic(
                    code=CODE_INVALID_PROVIDER_VERSION,
                    path="/provider_version",
                    message="provider_version must be a non-empty PEP 440 version string",
                )
            ]
        )
    try:
        Version(provider_version)
    except InvalidVersion as exc:
        raise ProviderDescriptorError(
            [
                ProviderDiagnostic(
                    code=CODE_INVALID_PROVIDER_VERSION,
                    path="/provider_version",
                    message=f"provider_version {provider_version!r} is not a valid PEP 440 version",
                )
            ]
        ) from exc
    return provider_version


def _validate_sdk_compatibility_syntax(sdk_compatibility: object) -> str:
    if not isinstance(sdk_compatibility, str) or not sdk_compatibility.strip():
        raise ProviderDescriptorError(
            [
                ProviderDiagnostic(
                    code=CODE_INVALID_SDK_COMPATIBILITY,
                    path="/sdk_compatibility",
                    message="sdk_compatibility must be a non-empty PEP 440 specifier string",
                )
            ]
        )
    try:
        SpecifierSet(sdk_compatibility)
    except InvalidSpecifier as exc:
        raise ProviderDescriptorError(
            [
                ProviderDiagnostic(
                    code=CODE_INVALID_SDK_COMPATIBILITY,
                    path="/sdk_compatibility",
                    message=(
                        f"sdk_compatibility {sdk_compatibility!r} is not a valid "
                        "PEP 440 version specifier"
                    ),
                )
            ]
        ) from exc
    return sdk_compatibility


def _normalize_capabilities(
    capabilities: Sequence[CapabilityId | str],
) -> tuple[CapabilityId, ...]:
    if not isinstance(capabilities, Sequence) or isinstance(capabilities, (str, bytes)):
        raise ProviderDescriptorError(
            [
                ProviderDiagnostic(
                    code=CODE_INVALID_DESCRIPTOR,
                    path="/capabilities",
                    message="capabilities must be a sequence of capability ids",
                )
            ]
        )

    parsed: list[CapabilityId] = []
    seen: set[CapabilityId] = set()
    diagnostics: list[ProviderDiagnostic] = []

    for index, raw in enumerate(capabilities):
        path = f"/capabilities/{index}"
        try:
            capability = parse_capability_id(raw, path=path)
        except ProviderDescriptorError as exc:
            diagnostics.extend(exc.errors)
            continue
        if capability not in SUPPORTED_CAPABILITIES:
            diagnostics.append(
                ProviderDiagnostic(
                    code=CODE_UNKNOWN_CAPABILITY,
                    path=path,
                    message=f"unknown capability id {capability.value!r}",
                )
            )
            continue
        if capability in seen:
            diagnostics.append(
                ProviderDiagnostic(
                    code=CODE_INVALID_DESCRIPTOR,
                    path=path,
                    message=f"duplicate capability declaration {capability.value!r}",
                )
            )
            continue
        seen.add(capability)
        parsed.append(capability)

    if diagnostics:
        raise ProviderDescriptorError(diagnostics)

    # Deterministic storage order by capability value (declaration order preserved
    # only after uniqueness; public diagnostics always sort by value when sets used).
    return tuple(sorted(parsed, key=lambda item: item.value))


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    """Public immutable provider descriptor (structural validation only)."""

    provider_id: str
    display_name: str
    provider_version: str
    sdk_compatibility: str
    capabilities: tuple[CapabilityId, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_id", _validate_provider_id(self.provider_id))
        object.__setattr__(self, "display_name", _validate_display_name(self.display_name))
        object.__setattr__(
            self, "provider_version", _validate_provider_version(self.provider_version)
        )
        object.__setattr__(
            self,
            "sdk_compatibility",
            _validate_sdk_compatibility_syntax(self.sdk_compatibility),
        )
        object.__setattr__(self, "capabilities", _normalize_capabilities(self.capabilities))

    def specifier_set(self) -> SpecifierSet:
        return SpecifierSet(self.sdk_compatibility)


@dataclass(frozen=True, slots=True)
class CapabilityBinding:
    """Associates a declared capability with a deferred runtime factory."""

    capability_id: CapabilityId
    factory: CapabilityFactory[object]

    def __post_init__(self) -> None:
        try:
            capability = parse_capability_id(self.capability_id, path="/capability_id")
        except ProviderDescriptorError as exc:
            # Binding validation is part of registration coherence, not descriptor
            # construction; surface as ProviderRegistrationError.
            raise ProviderRegistrationError(exc.errors) from exc
        object.__setattr__(self, "capability_id", capability)
        if not callable(self.factory):
            raise ProviderRegistrationError(
                [
                    ProviderDiagnostic(
                        code=CODE_INVALID_CAPABILITY_BINDING,
                        path=f"/bindings/{capability.value}/factory",
                        message="capability factory must be callable",
                    )
                ]
            )


def _validate_config_validator(validator: object | None) -> ProviderConfigValidator | None:
    if validator is None:
        return None
    validate = getattr(validator, "validate", None)
    if not callable(validate):
        raise ProviderRegistrationError(
            [
                ProviderDiagnostic(
                    code=CODE_INVALID_CONFIG_VALIDATOR,
                    path="/config_validator",
                    message="config_validator must provide a callable validate(config) method",
                )
            ]
        )
    return validator  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class ProviderRegistration:
    """Entry-point result: descriptor plus deferred capability bindings."""

    descriptor: ProviderDescriptor
    bindings: tuple[CapabilityBinding, ...]
    config_validator: ProviderConfigValidator | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.descriptor, ProviderDescriptor):
            raise ProviderRegistrationError(
                [
                    ProviderDiagnostic(
                        code=CODE_INVALID_DESCRIPTOR,
                        path="/descriptor",
                        message="descriptor must be a ProviderDescriptor",
                    )
                ]
            )
        object.__setattr__(
            self, "config_validator", _validate_config_validator(self.config_validator)
        )
        object.__setattr__(self, "bindings", self._normalize_bindings(self.bindings))

    def _normalize_bindings(
        self, bindings: Sequence[CapabilityBinding]
    ) -> tuple[CapabilityBinding, ...]:
        if not isinstance(bindings, Sequence) or isinstance(bindings, (str, bytes)):
            raise ProviderRegistrationError(
                [
                    ProviderDiagnostic(
                        code=CODE_INVALID_CAPABILITY_BINDING,
                        path="/bindings",
                        message="bindings must be a sequence of CapabilityBinding",
                    )
                ]
            )

        diagnostics: list[ProviderDiagnostic] = []
        by_capability: dict[CapabilityId, CapabilityBinding] = {}

        for index, binding in enumerate(bindings):
            path = f"/bindings/{index}"
            if not isinstance(binding, CapabilityBinding):
                diagnostics.append(
                    ProviderDiagnostic(
                        code=CODE_INVALID_CAPABILITY_BINDING,
                        path=path,
                        message="binding must be a CapabilityBinding",
                    )
                )
                continue
            capability = binding.capability_id
            if capability in by_capability:
                diagnostics.append(
                    ProviderDiagnostic(
                        code=CODE_INVALID_CAPABILITY_BINDING,
                        path=f"/bindings/{capability.value}",
                        message=f"duplicate capability binding {capability.value!r}",
                    )
                )
                continue
            by_capability[capability] = binding

        declared = frozenset(self.descriptor.capabilities)
        bound = frozenset(by_capability)

        missing = declared - bound
        if missing:
            for capability_value in sorted_capability_values(missing):
                diagnostics.append(
                    ProviderDiagnostic(
                        code=CODE_INVALID_CAPABILITY_BINDING,
                        path=f"/bindings/{capability_value}",
                        message=(f"declared capability {capability_value!r} has no binding"),
                    )
                )

        undeclared = bound - declared
        if undeclared:
            for capability_value in sorted_capability_values(undeclared):
                diagnostics.append(
                    ProviderDiagnostic(
                        code=CODE_INVALID_CAPABILITY_BINDING,
                        path=f"/bindings/{capability_value}",
                        message=(f"binding for undeclared capability {capability_value!r}"),
                    )
                )

        if diagnostics:
            raise ProviderRegistrationError(diagnostics)

        ordered = tuple(
            by_capability[capability]
            for capability in sorted(by_capability, key=lambda item: item.value)
        )
        return ordered

    def binding_for(self, capability_id: CapabilityId | str) -> CapabilityBinding:
        capability = parse_capability_id(capability_id, path="/capability_id")
        for binding in self.bindings:
            if binding.capability_id == capability:
                return binding
        raise ProviderRegistrationError(
            [
                ProviderDiagnostic(
                    code=CODE_INVALID_CAPABILITY_BINDING,
                    path=f"/bindings/{capability.value}",
                    message=f"no binding for capability {capability.value!r}",
                )
            ]
        )


# Re-export factory type alias for documentation clarity.
CapabilityFactoryCallable = Callable[[ProviderRuntimeContext], object]
