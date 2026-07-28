# PartDesignTimeline

A **Fusion-360-style history timeline** for FreeCAD's PartDesign — a dockable
scrubber that lets you travel back and forth through a Body's feature history and
watch the model rebuild at each step.

FreeCAD has no native horizontal timeline; this addon adds one on top of the
existing `Body.Tip` mechanism.

## Features

- **Draggable scrubber** — drag the slider (or click a chip) and the model
  rebuilds to that point in time.
- **Drag a chip to reorder** — drag a feature left/right to move it in the
  history; the BaseFeature chain is rewired and the model recomputed. If the move
  would break the model (a feature depends on geometry that wouldn't exist yet)
  it's reverted and you're told what it would have broken — the same dependency
  limit Fusion has.
- **Compact feature chips** — feature-type icon + short label, scales to long
  histories, scrolls horizontally.
- **True time-travel** — shows the model's *solid state* at that step; if you
  land on a sketch or datum it overlays that on the solid-so-far.
- **Right-click a chip** → Rename, Edit feature, Toggle visibility,
  Select in 3D/tree, **Force recompute** (regenerate), **Delete feature**.
- **Delete a feature** — right-click → Delete. The BaseFeature chain is bridged
  around it (dependents reconnect to its base; the tip steps back). If deleting it
  would break a downstream feature the delete is reverted; a successful delete is
  undoable with Ctrl+Z.
- **Ctrl+click** → multi-select features into FreeCAD's selection for grouping /
  running native operations on several at once.
- **Double-click** → edit that feature.

## Install

**Addon Manager** (once listed): *Tools → Addon Manager → PartDesignTimeline*.

**Manual:** clone into your FreeCAD `Mod` directory, e.g.

```bash
git clone https://github.com/Josiah05230/PartDesignTimeline.git \
  ~/.local/share/FreeCAD/<version>/Mod/PartDesignTimeline
```

Restart FreeCAD. The **Timeline** dock appears at the bottom.

## Requirements

- FreeCAD **1.0+** (uses `AttachmentSupport` / the 1.0 topological-naming fix that
  makes history rollback safe).
- An active **PartDesign Body**. The panel scrubs the *active* Body's history
  (per-Body, matching FreeCAD's model of linear history — not a global assembly
  timeline).

## Notes / limits

- Per-Body, not a single global cross-assembly tape like Fusion.
- Inserting a feature *mid-history* (while scrubbed back) can need the **Force
  recompute** action to fully regenerate — that's partly FreeCAD's own PartDesign
  behavior.

## License

MIT — see [LICENSE](LICENSE).
