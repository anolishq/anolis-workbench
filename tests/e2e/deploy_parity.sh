#!/usr/bin/env bash
# Deploy parity acceptance (Stage 1 criterion #6, #163):
# a workbench deploy and a direct `install.sh --stage` + `--local` install of
# the SAME config must produce byte-identical results — same /opt/anolis tree
# (contents, owner, mode), same single anolis-runtime.service unit, same
# anolis user + groups.
#
# Runs for real on a Linux host with sudo + systemd (CI runner). Uses the
# sim-quickstart template (no hardware) and --no-start (layout parity, no
# runtime side effects).
set -euo pipefail

PROJECT="parity-check"
PREFIX="/opt/anolis"
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT

log() { echo "==> $*"; }

snapshot() {
    local tag="$1"
    sudo find "${PREFIX}" -type f -exec sha256sum {} \; | sed "s|${PREFIX}/||" | sort \
        > "${WORK}/${tag}.files"
    sudo find "${PREFIX}" -mindepth 1 \( -type f -o -type d \) -printf '%P\t%u:%g\t%m\n' | sort \
        > "${WORK}/${tag}.meta"
    sha256sum /etc/systemd/system/anolis-runtime.service | awk '{print $1}' \
        > "${WORK}/${tag}.unit"
    id anolis > "${WORK}/${tag}.user"
    # Exactly one anolis unit — the canonical single service.
    ls /etc/systemd/system/anolis-*.service > "${WORK}/${tag}.units"
}

# --- Side A: workbench deploy (install.sh --project via deploy_local) -------
log "Side A: anolis-provision install (workbench deploy)"
uv run anolis-provision install --project "${PROJECT}" --template sim-quickstart --no-start
snapshot a

# Reuse the exact workspace system for side B so both derive from one config.
SYSTEM_JSON="${HOME}/.anolis/systems/${PROJECT}/system.json"
[[ -f "${SYSTEM_JSON}" ]] || { echo "workspace system.json missing"; exit 1; }

# --- Reset the target -------------------------------------------------------
log "Uninstalling (reset target)"
INSTALL_SH="${WORK}/install.sh"
curl -fsSL -o "${INSTALL_SH}" \
    "https://github.com/anolishq/anolis/releases/latest/download/install.sh"
chmod +x "${INSTALL_SH}"
sudo "${INSTALL_SH}" --uninstall
[[ ! -d "${PREFIX}" ]] || { echo "uninstall left ${PREFIX} behind"; exit 1; }

# --- Side B: stage an offline bundle from the same config, install it -------
log "Side B: anolis-provision bundle (install.sh --stage) + install.sh --local"
uv run anolis-provision bundle --project "${PROJECT}" --system "${SYSTEM_JSON}" \
    --arch x86_64 --out "${WORK}/bundles"
TARBALL=$(ls "${WORK}/bundles/anolis-${PROJECT}-"*.tar.gz)
sudo "${INSTALL_SH}" --local "${TARBALL}" --no-start
snapshot b

# --- Compare -----------------------------------------------------------------
log "Comparing snapshots"
fail=0
for aspect in files meta unit user units; do
    if ! diff -u "${WORK}/a.${aspect}" "${WORK}/b.${aspect}"; then
        echo "PARITY MISMATCH: ${aspect}"
        fail=1
    fi
done

if [[ ${fail} -ne 0 ]]; then
    echo ""
    echo "✗ workbench deploy and install.sh --local diverged"
    exit 1
fi

echo ""
echo "✓ parity: workbench deploy == install.sh --stage/--local"
echo "  $(wc -l < "${WORK}/a.files") files identical (contents, owner, mode)"
