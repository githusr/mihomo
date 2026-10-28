# Project goals

Maintain a personal patch fork of `MetaCubeX/mihomo`: unattended daily sync, manual dispatch, minimal manual maintenance, and usable Releases published only in this fork.

- Edit `automation`; export feature changes to the shared `patches/*.patch` series. Generated branches/tags may be overwritten. Preserve uncommitted work.
- Follow exact upstream Meta/Alpha commits and stable tags. Reuse upstream build workflows and keep patches small.
- Retain `Prerelease-Alpha` plus the latest two stable Releases. Rebuild all with current patches and overwrite existing Releases/tags. Keep working releases until replacements succeed.
- For authorized feature/build changes, test, push, run `Sync upstream`, and verify published refs, assets, versions, provenance, checksums, and downloaded binaries. Report incomplete delivery and untested behavior.
- Reviews stay within the requested scope. Documentation changes need content/diff checks, not rebuilds.

See [README.md](README.md) and [patches/README.md](patches/README.md) for details and checks.
