"""Save-time validation for canonical projects (#255).

Two jobs the schemas cannot do on their own:

1. **Cross-artifact coherence** — provider ids appear in the profile, in every
   runtime variant's `providers[]`, and as config files; they must agree.
2. **Mirror install.sh's hard gates** so a project that will be refused at
   `sudo install.sh` time is refused at SAVE time instead, with a message that
   names the fix. The three gates are: the `manual` variant must be inert,
   every provider command must resolve to a PINNED kind, and config path
   tokens must carry the profile's own machine_id.
"""

from __future__ import annotations

import os
from typing import Any

from anolis_workbench.core import canonical


def _resolve_workbench_port(default: int = 3010) -> int:
    raw = os.getenv("ANOLIS_WORKBENCH_PORT")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _parse_i2c_address(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text == "":
        return None
    for base in (0, 16):
        try:
            return int(text, base)
        except ValueError:
            continue
    return None


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def validate_project(document: dict[str, Any]) -> list[str]:
    """Errors that must block a save. Empty list means the project is valid."""
    errors: list[str] = []
    profile = _as_mapping(document.get("profile"))
    variants = _as_mapping(document.get("variants"))
    providers = _as_mapping(document.get("providers"))

    machine_id = profile.get("machine_id")
    if not isinstance(machine_id, str) or machine_id == "":
        errors.append("machine-profile is missing machine_id.")
        machine_id = ""

    errors.extend(_variant_errors(profile, variants, machine_id))
    errors.extend(_provider_coherence_errors(profile, variants, providers))
    errors.extend(_pinned_provider_errors(profile, providers))
    errors.extend(_i2c_conflict_errors(providers))
    errors.extend(_port_errors(variants))
    return errors


def _variant_errors(profile: dict[str, Any], variants: dict[str, Any], machine_id: str) -> list[str]:
    errors: list[str] = []
    declared = profile.get("runtime_profiles")
    if not isinstance(declared, dict) or not declared:
        return ["machine-profile declares no runtime_profiles."]

    if canonical.MANUAL_VARIANT not in declared:
        errors.append(
            f"No '{canonical.MANUAL_VARIANT}' runtime variant. install.sh boots the inert "
            f"'{canonical.MANUAL_VARIANT}' variant and refuses a profile without one."
        )

    for variant, doc in variants.items():
        if not isinstance(doc, dict):
            errors.append(f"Runtime variant '{variant}' is not a mapping.")
            continue
        for message in canonical.runtime_config_errors(doc):
            errors.append(f"Runtime variant '{variant}': {message}")
        if machine_id:
            errors.extend(f"Runtime variant '{variant}': {p}" for p in canonical.assert_deploy_tokens(machine_id, doc))

    # GATE 1 (install.sh verify_inert_runtime_config): the manual variant must
    # be inert, or a fresh install is refused outright.
    manual = variants.get(canonical.MANUAL_VARIANT)
    if isinstance(manual, dict):
        violation = canonical.inertness_violation(manual)
        if violation is not None:
            errors.append(
                f"The '{canonical.MANUAL_VARIANT}' variant is not inert ({violation}). "
                "install.sh refuses to install a non-inert variant — author automation "
                f"into the '{canonical.AUTOMATION_VARIANT}' variant instead."
            )
    return errors


def _provider_coherence_errors(
    profile: dict[str, Any], variants: dict[str, Any], providers: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    profile_providers = profile.get("providers")
    profile_ids = set(profile_providers) if isinstance(profile_providers, dict) else set()
    document_ids = set(providers)

    for pid in sorted(profile_ids - document_ids):
        errors.append(f"Provider '{pid}' is declared in the machine-profile but has no config.")
    for pid in sorted(document_ids - profile_ids):
        errors.append(f"Provider '{pid}' has a config but is not declared in the machine-profile.")

    for variant, doc in variants.items():
        if not isinstance(doc, dict):
            continue
        entries = doc.get("providers")
        if not isinstance(entries, list):
            continue
        variant_ids = [str(e.get("id")) for e in entries if isinstance(e, dict) and e.get("id") is not None]
        if len(variant_ids) != len(set(variant_ids)):
            errors.append(f"Runtime variant '{variant}' has duplicate provider ids.")
        for pid in sorted(set(variant_ids) - profile_ids):
            errors.append(
                f"Runtime variant '{variant}' runs provider '{pid}' which the machine-profile does not declare."
            )
        for pid in sorted(profile_ids - set(variant_ids)):
            errors.append(f"Provider '{pid}' is declared but never run by variant '{variant}'.")
    return errors


def _pinned_provider_errors(profile: dict[str, Any], providers: dict[str, Any]) -> list[str]:
    """GATE 2 (install.sh #226 cross-validation): every provider must resolve to
    a kind present in the pinned components, or assemble_bundle hard-fails."""
    pinned = canonical.pinned_kinds(profile)
    if not pinned:
        # No components block at all — deploy already fails with its own,
        # clearer message ("resolve component pins"); don't double-report.
        return []
    errors: list[str] = []
    for pid, entry in sorted(providers.items()):
        kind = entry.get("kind") if isinstance(entry, dict) else None
        if isinstance(kind, str) and kind not in pinned:
            errors.append(
                f"Provider '{pid}' is kind '{kind}', which is not in the profile's pinned "
                f"components ({', '.join(sorted(pinned))}). install.sh refuses a provider "
                "command that does not resolve to a pinned component."
            )
    return errors


def _i2c_conflict_errors(providers: dict[str, Any]) -> list[str]:
    """Cross-provider bus conflicts — capability-driven, no kind list.

    Within-provider duplicates are the provider schema's job (x-anolis-unique).
    """
    errors: list[str] = []
    owned: dict[tuple[str, int], str] = {}
    for pid, entry in sorted(providers.items()):
        config = entry.get("config") if isinstance(entry, dict) else None
        if not isinstance(config, dict):
            continue
        hardware = config.get("hardware")
        if not isinstance(hardware, dict):
            continue
        bus_path = hardware.get("bus_path")
        if not isinstance(bus_path, str) or bus_path == "":
            continue
        devices = config.get("devices")
        if not isinstance(devices, list):
            continue
        for device in devices:
            if not isinstance(device, dict):
                continue
            address = _parse_i2c_address(device.get("address"))
            if address is None:
                continue
            key = (bus_path, address)
            if key in owned and owned[key] != pid:
                errors.append(
                    f"I2C address 0x{address:02X} on bus '{bus_path}' is claimed by both '{owned[key]}' and '{pid}'."
                )
            elif key not in owned:
                owned[key] = pid
    return errors


def _port_errors(variants: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    reserved = _resolve_workbench_port()
    for variant, doc in variants.items():
        if not isinstance(doc, dict):
            continue
        http = doc.get("http")
        if not isinstance(http, dict):
            continue
        port = http.get("port")
        if isinstance(port, str):
            try:
                port = int(port)
            except ValueError:
                port = None
        if port == reserved:
            errors.append(
                f"Runtime variant '{variant}' uses HTTP port {reserved}, which conflicts "
                "with the workbench control server."
            )
    return errors


def path_token_warnings(profile: dict[str, Any], project_dir_name: str) -> list[str]:
    """GATE 3 (install.sh keys path rewrites on the deploy dir basename)."""
    machine_id = profile.get("machine_id")
    if not isinstance(machine_id, str) or machine_id == project_dir_name:
        return []
    return [
        f"machine_id '{machine_id}' differs from the deploy directory name "
        f"'{project_dir_name}'; install.sh keys its path rewrites on the directory name."
    ]


def unknown_kind_errors(providers: dict[str, Any]) -> list[str]:
    """Kinds without a vendored config schema cannot be form-edited."""
    from anolis_workbench.core import provider_schemas

    errors: list[str] = []
    for pid, entry in sorted(providers.items()):
        kind = entry.get("kind") if isinstance(entry, dict) else None
        if kind is None:
            errors.append(
                f"Provider '{pid}' has no derivable kind (check components.providers and the config filename)."
            )
        elif provider_schemas.get_envelope(kind) is None:
            known = ", ".join(provider_schemas.available_kinds())
            errors.append(f"Provider '{pid}' has unknown kind '{kind}' (known: {known}).")
    return errors


__all__ = ["validate_project", "path_token_warnings", "unknown_kind_errors"]
