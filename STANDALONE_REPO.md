# Standalone Repo Move

`dlhd-stremio-addon` is now self-contained enough to live as its own repository.

## Minimum contents to move

Move this directory as-is:

```text
dlhd-stremio-addon/
```

It already contains:

- application code
- Dockerfile
- deployment configs
- platform-specific guides
- its own `.gitignore`

## Recommended split process

1. Copy or move `dlhd-stremio-addon/` to a new folder outside the current monorepo.
2. Initialize a new git repository there.
3. Push that folder to its own GitHub repository.

Example:

```bash
mkdir ../dlhd-stremio-addon-repo
# copy the contents of dlhd-stremio-addon/ into that new folder
cd ../dlhd-stremio-addon-repo
git init
git add .
git commit -m "Initial standalone addon service"
```

## After split

- Railway root directory should be repository root
- Render root directory should be repository root
- VPS deploy commands should run from the standalone repository root

## Files to review before first public deploy

- `.env.production.example`
- `deploy/railway/README.md`
- `deploy/render/README.md`
- `deploy/vps/README.md`
- `deploy/vps/PRODUCTION_CHECKLIST.md`
