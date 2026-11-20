"""Write fixture.json: Rosette's own dots (src/rosette/halftone.py) for math.test.js to match.

Each case is a flat grey image, so Rosette's pre-blur leaves it unchanged and the two
implementations should agree to rounding on every dot that lies on the image. Run from the repo
root, then commit the fixture:

    PYTHONPATH=src .venv/bin/python affinity/test/make_fixture.py
"""

import json
from pathlib import Path

import numpy as np

from rosette.halftone import _iter_dots

CASES = [
    {"w": 200, "h": 120, "dots_per_row": 20, "angle": 45.0, "gamma": 1.5, "min_radius": 0.0, "ink": 102},
    {"w": 200, "h": 120, "dots_per_row": 5, "angle": 15.0, "gamma": 1.0, "min_radius": 0.0, "ink": 178},
    {"w": 160, "h": 160, "dots_per_row": 16, "angle": -30.0, "gamma": 2.0, "min_radius": 0.2, "ink": 40},
]


def main() -> None:
    out = []
    for case in CASES:
        w, h = case["w"], case["h"]
        # Rosette quantises each channel to 8 bits before blurring, so ink is given in 8-bit steps.
        channel = np.full((h, w), case["ink"] / 255.0, dtype=np.float32)
        dots = [
            [round(x, 4), round(y, 4), round(r, 4)]
            for x, y, r in _iter_dots(
                channel, w, h, case["dots_per_row"], case["angle"], case["min_radius"], "linear", case["gamma"]
            )
            if 0 <= x <= w and 0 <= y <= h
        ]
        out.append({**case, "dots": dots})
    path = Path(__file__).with_name("fixture.json")
    path.write_text(json.dumps(out, indent=1) + "\n")
    print(f"wrote {path} ({sum(len(c['dots']) for c in out)} dots)")


if __name__ == "__main__":
    main()
