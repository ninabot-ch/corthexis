---
name: transform-scale-breaks-iframe-coords
description: CSS transform scale() on an ancestor of an iframe silently desynchronizes pointer coordinates inside the frame — clicks land somewhere other than where they appear. Affects preview panes and zoom UIs; the fix is to size the frame instead of scaling it.
metadata:
  type: reference
  modified: 2026-09-16
---

`transform: scale(0.5)` on an ancestor of an `<iframe>` scales the *rendering*
but the frame's internal hit-testing keeps working in its own untransformed
coordinate space. Visually everything is fine. Clicks land in the wrong place,
and the offset grows with distance from the transform origin.

It bites hardest in exactly the place people reach for scale(): zoomable preview
panes and "fit to width" embeds.

**How to apply:** do not scale an ancestor of an interactive iframe. Give the
frame its real pixel dimensions and let the *content inside* adapt — or, if the
frame is genuinely non-interactive, render it to an image and scale that.

If you must keep the transform, treat every coordinate crossing the frame
boundary as needing manual correction, and write a test for it, because this
one is invisible to screenshots.
