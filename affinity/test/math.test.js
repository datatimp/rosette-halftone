// Checks the Affinity script's dot math against Rosette's Python. Run from the repo root:
//
//     node --test affinity/test
//
// The math is the block between ROSETTE-MATH-BEGIN and ROSETTE-MATH-END in affinity/src/rosette.js,
// loaded on its own so the test needs nothing from Affinity.

'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function loadMath() {
    const src = fs.readFileSync(path.join(__dirname, '..', 'src', 'rosette.js'), 'utf8');
    // From the line after the BEGIN marker up to the END marker.
    const after = src.split('// ROSETTE-MATH-BEGIN')[1];
    const block = after.slice(after.indexOf('\n')).split('// ROSETTE-MATH-END')[0];
    const context = {};
    vm.runInNewContext(`${block}; this.math = { densityFromPixels, computeDots, MAX_R_FACTOR };`, context);
    return context.math;
}

const math = loadMath();

// A w × h RGBA8 image with every pixel the given grey and alpha.
function image(w, h, grey, alpha = 255) {
    const data = new Uint8Array(w * h * 4);
    for (let p = 0; p < data.length; p += 4)
        data.set([grey, grey, grey, alpha], p);
    return data;
}

const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixture.json'), 'utf8'));

for (const c of fixture) {
    test(`matches Rosette's dots: ${c.w}x${c.h}, ${c.dots_per_row} per row, ${c.angle}°, gamma ${c.gamma}`, () => {
        const density = math.densityFromPixels(image(c.w, c.h, 255 - c.ink), c.w, c.h, 'RGBA8');
        const dots = math.computeDots(density, c.w, c.h, {
            cellPx: c.w / c.dots_per_row, angleDeg: c.angle, gamma: c.gamma, minRadius: c.min_radius,
        });
        assert.equal(dots.length, c.dots.length);
        const full = Math.max(...c.dots.map(d => d[2]));
        dots.forEach((d, i) => {
            const [x, y, r] = c.dots[i];
            assert.ok(Math.abs(d.x - x) < 1e-3 && Math.abs(d.y - y) < 1e-3, `dot ${i} at ${d.x},${d.y}, Rosette ${x},${y}`);
            // Past the last pixel centre, Rosette's sampler mixes in zero ink from outside the
            // image, so its edge dots shrink. The script extends the edge instead (on a flat grey,
            // every dot is full size), which is what keeps a shape's edge free of odd dots.
            const onEdge = x > c.w - 1 || y > c.h - 1;
            const expected = onEdge ? full : r;
            assert.ok(Math.abs(d.r - expected) < 1e-3, `dot ${i} radius ${d.r}, expected ${expected}`);
        });
    });
}

test('transparent pixels are paper: a shape edge draws no dots', () => {
    // Black on the left half, fully transparent on the right half.
    const w = 120, h = 60;
    const data = image(w, h, 0);
    for (let y = 0; y < h; ++y)
        for (let x = w / 2; x < w; ++x)
            data[(y * w + x) * 4 + 3] = 0;
    const dots = math.computeDots(math.densityFromPixels(data, w, h, 'RGBA8'), w, h, {
        cellPx: 6, angleDeg: 45, gamma: 1.5, minRadius: 0,
    });
    // Well inside the transparent half (past the blur's reach of 1.5 cells) there are no dots.
    assert.ok(dots.length > 0);
    assert.ok(dots.every(d => d.x < w / 2 + 9), 'a dot was drawn deep in the transparent area');
});

test('white is bare paper, even with a minimum dot size', () => {
    const dots = math.computeDots(math.densityFromPixels(image(80, 80, 255), 80, 80, 'RGBA8'), 80, 80, {
        cellPx: 8, angleDeg: 45, gamma: 1.5, minRadius: 0.5,
    });
    assert.equal(dots.length, 0);
});

test('a white-to-black gradient grows the dots from left to right, with no heavy edge dots', () => {
    // The case Affinity's Live Halftone gets wrong: its cells along the edge draw large dots.
    const w = 300, h = 100;
    const data = new Uint8Array(w * h * 4);
    for (let y = 0; y < h; ++y)
        for (let x = 0; x < w; ++x)
            data.set([255 - Math.round(255 * x / (w - 1)), 255 - Math.round(255 * x / (w - 1)), 255 - Math.round(255 * x / (w - 1)), 255], (y * w + x) * 4);
    const dots = math.computeDots(math.densityFromPixels(data, w, h, 'RGBA8'), w, h, {
        cellPx: 10, angleDeg: 45, gamma: 1, minRadius: 0,
    });
    const maxR = 10 * math.MAX_R_FACTOR;
    // Every dot is about as big as the gradient's darkness where it sits: no dot along the top,
    // bottom or left edge is drawn heavier than its position calls for.
    for (const d of dots) {
        const expected = maxR * Math.sqrt(d.x / w);
        assert.ok(Math.abs(d.r - expected) < 0.35 * maxR, `dot at ${d.x.toFixed(1)},${d.y.toFixed(1)} radius ${d.r.toFixed(2)}, expected about ${expected.toFixed(2)}`);
    }
});

test('premultiplied and straight half-transparent black give the same ink', () => {
    const straight = math.densityFromPixels(new Uint8Array([0, 0, 0, 128, 255, 0, 0, 255]), 2, 1, 'RGBA8');
    const premul = math.densityFromPixels(new Uint8Array([0, 0, 0, 128]), 1, 1, 'RGBA8');
    assert.ok(Math.abs(straight[0] - 128 / 255) < 1e-6);
    assert.ok(Math.abs(premul[0] - 128 / 255) < 1e-6);
});
