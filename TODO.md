# TODO

## Desktop

- [ ] Implement proper code-signing for release builds (Windows, macOS, Linux)
- [ ] Re-add auto-updater once signing is in place (requires valid pubkey + signed artifacts)
- [ ] Replace placeholder icon.png with final branded asset (all required sizes)

## CI / Release

- [ ] Add artifact attestation / SBOM generation to release workflow
- [ ] Pin runner OS versions once GitHub stabilises ubuntu-latest/macos-latest labels
- [ ] Add integration test for sidecar health-check endpoint in CI

## General

- [ ] Audit remaining `DeprecationWarning`s in test suite
