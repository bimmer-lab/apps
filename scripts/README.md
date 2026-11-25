# scripts/

- `deploy.sh`: bimmer-lab's deploy helper for the bench.
- `vendor_sync.py`: brings the apps in `vendor/` and the native components in `tools-prebuilt/` up to
  their origins (what and where: `vendor/sources.json`), applies our own changes again (Redline without
  the HUD), and checks the repo the way the MGU Store reads and builds it. `--check` only checks.

Cluster Themes (and the kit in `kit/`, `shared/`) comes from bimmer-lab/apps by a merge instead:

```sh
git remote add upstream https://github.com/bimmer-lab/apps   # once
git fetch --depth 60 upstream main
git merge upstream/main          # conflicts: keep both sides' features; ours are 0.19.2-0.19.6 (CHANGELOG)
```

bimmer-lab's HUD Widgets (`apps/idrive_hud_widgets`) and its pictures stay deleted: `git rm -r` them
again if a merge brings them back.
