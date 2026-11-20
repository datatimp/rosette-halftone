# Rosette for Affinity

A script for Affinity 3.3 or later that turns the selected object into a black halftone made of
true vector circles, using Rosette's dot math. It doesn't rasterize the result or trace it: every
dot is an exact ellipse at the size Rosette calculates.

Affinity's scripting is in beta, so a future Affinity update may need the script updated too.

## Install

1. Download [`rosette.js`](rosette.js).
2. In Affinity, open the Scripting studio and import `rosette.js` (or open it and save it to your
   scripts). The first time, Affinity asks whether to trust the script.

## Use

1. Select one object: a shape, a gradient, a placed image or a group. To halftone several objects
   together, group them first.
2. Run **Rosette**. The dialog previews the dots live on the canvas:
   - **Cell size:** the distance between dot centres.
   - **Angle:** the screen angle; 45° is the usual angle for black.
   - **Gamma:** above 1 opens up the highlights (smaller dots in light areas); below 1 makes
     the dots heavier. 1.5 is Rosette's default.
   - **Minimum dot (%):** the smallest dot drawn in any inked area, as a percentage of the largest
     dot. White stays bare paper either way.
   - **Dots as:** one compound shape (lightest, easiest to recolor) or separate dots in a group.
3. Click **OK**. The dots are added above the object, named "Rosette halftone", in one undo step.
   The object itself is unchanged; hide or delete it if you only want the dots.

## How it works

The script renders the object to pixels in memory, with its fills and effects as they show on the
canvas, without changing the document. Very large art is rendered at a reduced scale, up to about
4 million pixels, which keeps it fast. Then it runs Rosette's math:

- a Gaussian blur of half a cell,
- a square grid rotated to the screen angle and centred on the object,
- one sample per dot, with gamma, and
- a dot whose area grows with the ink, up to 0.47 of a cell so that dots never touch.

Transparent areas count as white paper, and the image's edge is extended rather than faded. So a
shape's edge never gets a border of heavy dots, unlike Affinity's Live Halftone filter.

## Limits

- Black only. CMYK plates are planned.
- It runs once. To change the settings, undo and run it again.
- Colors are read as brightness: a color's lightness decides the dot size.

## Tests

The dot math is checked against Rosette's Python, so the two stay in step. From the repo root:

```bash
node --test affinity/test
```

`affinity/test/fixture.json` holds the Python's dots for a few test cases. After changing
Rosette's dot math, regenerate it and commit it:

```bash
PYTHONPATH=src .venv/bin/python affinity/test/make_fixture.py
```

## License

AGPL-3.0-or-later, as the rest of Rosette.
