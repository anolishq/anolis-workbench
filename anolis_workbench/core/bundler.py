"""Bundle builder for offline provisioning (pass 4).

Assembles a self-contained bundle directory that can be transferred to an
air-gapped RPi and installed via the included install.sh script.

No HTTP, no subprocess, no sudo — pure file I/O.
"""

from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from anolis_workbench.core import paths as paths_module
from anolis_workbench.core import renderer as renderer_module
from anolis_workbench.core.installer import ComponentSpec

_RUNTIME_BINARY_NAME = "anolis-runtime"


@dataclass
class BundleResult:
    """Result from building a bundle."""

    bundle_path: Path
    components: list[ComponentSpec]
    platform: str
    install_prefix: str


def build_bundle(
    components: list[ComponentSpec],
    tarballs: list[tuple[ComponentSpec, bytes]],
    template_name: str,
    project_name: str,
    platform_str: str,
    out_dir: Path,
    *,
    install_prefix: Path = Path("/usr/local"),
    workbench_version: str = "",
) -> BundleResult:
    """Assemble a complete bundle directory for offline provisioning.

    Args:
        components: All resolved components (for manifest).
        tarballs: List of (component, tarball_bytes) tuples.
        template_name: Template to render configs from.
        project_name: Target project name.
        platform_str: Target platform (e.g. "linux-arm64").
        out_dir: Directory to write the bundle into. Must not exist.
        install_prefix: Install prefix for path patching (default /usr/local).
        workbench_version: Workbench version for manifest metadata.

    Returns:
        BundleResult with the bundle path and metadata.
    """
    if out_dir.exists():
        raise ValueError(f"Output directory already exists: {out_dir}")

    out_dir.mkdir(parents=True)

    # --- binaries/ ---
    binaries_dir = out_dir / "binaries"
    binaries_dir.mkdir()

    checksum_lines: list[str] = []
    for comp, data in tarballs:
        tarball_name = f"{comp.name}-{comp.version}-{platform_str}.tar.gz"
        tarball_path = binaries_dir / tarball_name
        tarball_path.write_bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        checksum_lines.append(f"{sha}  binaries/{tarball_name}")

    # --- checksums.sha256 ---
    (out_dir / "checksums.sha256").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")

    # --- manifest.json ---
    manifest = {
        "schema_version": 1,
        "bundle_format": "anolis-bundle-v1",
        "created": datetime.now(timezone.utc).isoformat(),
        "project": project_name,
        "target_platform": platform_str,
        "install_prefix": str(install_prefix),
        "components": [{"name": c.name, "version": c.version, "binary": c.binary_name} for c in components],
        "workbench_version": workbench_version,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    # --- project/ (rendered configs) ---
    project_dir = out_dir / "project"
    project_dir.mkdir()
    (project_dir / "providers").mkdir()

    system = _render_project_configs(template_name, project_name, install_prefix)
    (project_dir / "system.json").write_text(json.dumps(system, indent=2) + "\n", encoding="utf-8")

    rendered = renderer_module.render(system, project_name, systems_dir_name="systems")
    for rel_path, content in rendered.items():
        out_path = project_dir / rel_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")

    # --- install.sh ---
    install_script = _generate_install_sh(project_name, components)
    install_sh_path = out_dir / "install.sh"
    install_sh_path.write_text(install_script, encoding="utf-8")
    install_sh_path.chmod(install_sh_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    return BundleResult(
        bundle_path=out_dir,
        components=components,
        platform=platform_str,
        install_prefix=str(install_prefix),
    )


def _render_project_configs(template_name: str, project_name: str, install_prefix: Path) -> dict[str, Any]:
    """Load template, patch paths for target prefix. Returns the patched system dict."""
    tpl_path = paths_module.TEMPLATES_ROOT / template_name / "system.json"
    if not tpl_path.exists():
        raise FileNotFoundError(f"Template '{template_name}' not found at {tpl_path}")

    system: dict = json.loads(tpl_path.read_text(encoding="utf-8"))

    # Patch meta
    system["meta"]["name"] = project_name
    system["meta"]["created"] = datetime.now(timezone.utc).isoformat()

    # Patch paths
    bin_dir = install_prefix / "bin"
    system["paths"]["runtime_executable"] = str(bin_dir / _RUNTIME_BINARY_NAME)

    for _provider_id, provider_data in system["paths"].get("providers", {}).items():
        original_exe = Path(provider_data.get("executable", ""))
        binary_name = original_exe.name
        provider_data["executable"] = str(bin_dir / binary_name)

    return system


def _generate_install_sh(project_name: str, components: list[ComponentSpec]) -> str:
    """Generate the install.sh script content."""
    verify_lines = "\n".join(f'"{c.binary_name}" --version' for c in components)

    return f'''#!/usr/bin/env bash
set -euo pipefail

BUNDLE_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
INSTALL_PREFIX="${{ANOLIS_INSTALL_PREFIX:-/usr/local}}"
PROJECT_NAME="{project_name}"
SYSTEMS_ROOT="${{ANOLIS_DATA_DIR:-$HOME/.anolis}}/systems"

echo "=== Anolis Bundle Installer ==="
echo "Bundle:  $BUNDLE_DIR"
echo "Prefix:  $INSTALL_PREFIX"
echo "Project: $SYSTEMS_ROOT/$PROJECT_NAME"
echo ""

# 1. Verify integrity
echo "Verifying checksums..."
cd "$BUNDLE_DIR"
sha256sum -c checksums.sha256
echo "✓ Checksums OK"
echo ""

# 2. Install binaries
echo "Installing binaries to $INSTALL_PREFIX ..."
for tarball in "$BUNDLE_DIR/binaries/"*.tar.gz; do
    echo "  + $(basename "$tarball")"
    sudo tar -xz -C "$INSTALL_PREFIX" < "$tarball"
done
echo ""

# 3. Install project configs
echo "Installing project configs..."
mkdir -p "$SYSTEMS_ROOT/$PROJECT_NAME/providers"
cp "$BUNDLE_DIR/project/system.json" "$SYSTEMS_ROOT/$PROJECT_NAME/system.json"
cp "$BUNDLE_DIR/project/anolis-runtime.yaml" "$SYSTEMS_ROOT/$PROJECT_NAME/anolis-runtime.yaml"
cp "$BUNDLE_DIR/project/providers/"*.yaml "$SYSTEMS_ROOT/$PROJECT_NAME/providers/"
echo "✓ Project written to $SYSTEMS_ROOT/$PROJECT_NAME/"
echo ""

# 4. Verify
echo "Verifying binaries..."
{verify_lines}
echo ""

echo "=== Done ==="
echo ""
echo "Next steps:"
echo "  pip install anolis-workbench"
echo "  anolis-workbench"
echo "  → open http://127.0.0.1:3010 → $PROJECT_NAME → Launch"
'''
