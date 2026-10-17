# CLAUDE.md - PartDesignTimeline

FreeCAD addon (MIT, public repo): a Fusion-360-style horizontal history timeline for PartDesign. A dock widget scrubs the active Body's feature history via `Body.Tip` and rebuilds the model at each step. Requires FreeCAD 1.0+ (`AttachmentSupport`, topological-naming fix). See `README.md` for features and install.

## Layout

- `InitGui.py` - trivial loader. FreeCAD execs it with split globals/locals, which breaks deferred callbacks that reference module-level names, so it only puts the addon dir on `sys.path`, imports `partdesign_timeline_panel` as a real module, and `QTimer.singleShot(1500, ...install_timeline)` after the main window is up. Keep it trivial. `Init.py` is a one-line no-op (GUI-only addon).
- `partdesign_timeline_panel.py` (~650 lines) - everything else: `TimelineWidget` (chips, slider, `_apply` rolls Tip, context menu, rename, force recompute, drag reorder `_reorder`, `_delete`, `eventFilter`), `_DocObserver` / `_SelObserver` (refresh on doc/selection changes), `install_timeline()`. Helpers `_active_body`, `_features`, `_is_sketch/_is_datum/_is_solid`, `_qt_exec`.
- `package.xml` (Addon Manager metadata, version), `Resources/icons/`, `LICENSE`.
- `FeatureTimeline` (sibling repo) is the earlier, smaller version without reorder/delete; this repo is the one to extend.

## Run / test

No build or test suite. Install by cloning into a FreeCAD `Mod` directory (e.g. `~/.local/share/FreeCAD/<version>/Mod/PartDesignTimeline`) or symlinking the working tree there, then restart FreeCAD; the Timeline dock appears at the bottom. Verify changes by hand in FreeCAD on a Body with sketches/pads/fillets.

## Conventions and gotchas

- Per-Body linear history (matches FreeCAD's model), not a global assembly timeline.
- Reorder rewires the `BaseFeature` chain then recomputes; if the move breaks a dependency it must be reverted with a message naming what broke. Delete bridges the chain (dependents reconnect to the deleted feature's base, tip steps back) and reverts if a downstream feature breaks; both are undoable (Ctrl+Z). Preserve both revert paths.
- Inserting a feature mid-history while scrubbed back can need **Force recompute**.
- The rename dialog is a plain `QDialog` (a styled input dialog was cramped on Wayland).
- Bump `package.xml` version/date when releasing.
- No em dashes or en dashes in docs or code; commit and push after each change.
