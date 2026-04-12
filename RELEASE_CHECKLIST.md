# Release Checklist

Use this checklist for each version bump.

## Versioning

- bump `app/settings.py` `ADDON_VERSION`
- bump `pyproject.toml` `version`
- update any example env files that pin `DLHD_ADDON_VERSION`

## Validation

- `python -m compileall app`
- `pytest`
- `CI` workflow passes
- run `Smoke` manually

## Deploy

- Railway env vars are still correct
- deploy completed successfully
- `https://<addon-domain>/healthz` returns `{"ok":true}`
- `https://<addon-domain>/manifest.json` returns the expected version

## Playback Check

- install or refresh the addon in Stremio
- test one known-good channel
- test one small live event
- confirm playback starts and stays open

## Release

- create git tag, e.g. `v0.1.3`
- create GitHub release if desired
- note the working Railway config for rollback
