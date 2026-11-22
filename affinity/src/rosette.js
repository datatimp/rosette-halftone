/*
Rosette for Affinity: turns the selected object into a black halftone of true vector dots.

Version 1.0
Copyright (C) 2026 datatimp
https://github.com/datatimp/rosette-halftone/tree/main/affinity
SPDX-License-Identifier: AGPL-3.0-or-later

Select one object and run the script. A dialog sets the cell size, screen angle, gamma and minimum
dot size, with a live preview; OK adds the dots above the object as one compound shape (or as
separate dots), in one undo step. The object itself is left as it was.

The dot math is Rosette's (src/rosette/halftone.py): Gaussian pre-blur at half a cell, a rotated
square grid centred on the art, bilinear sampling at each dot centre, gamma, and an
area-proportional radius capped at 0.47 of a cell. Transparent areas count as white paper, so a
shape's own edge never draws dots (Affinity's Live Halftone treats it as dark).

Written against Affinity's scripting API (beta, Affinity 3.3).
*/

'use strict';

// ROSETTE-MATH-BEGIN (plain JavaScript, no Affinity APIs: affinity/test/math.test.js runs it in Node)

// Maximum dot radius as a fraction of the cell, as in Rosette: below 0.5 so neighbouring dots
// never touch, which keeps a compound shape of dots from merging into blobs.
const MAX_R_FACTOR = 0.47;

// Ink below one 8-bit step counts as bare paper and gets no dot, even with a minimum dot size.
const WHITE_CUTOFF = 1 / 255;

// Blur and sample at about this many pixels per cell: halftoning a large print image at full
// resolution would take minutes, and one sample per cell gains nothing from more pixels.
const PIXELS_PER_CELL = 8;

// Ink density (0 = paper, 1 = full ink) of every pixel, as Float32Array of w * h.
// `format` is "RGBA8", "RGBA16" or "CMYKA8"; `data` the matching typed array. Each pixel is
// composited over white paper, so transparent pixels are no ink.
function densityFromPixels(data, w, h, format) {
    const out = new Float32Array(w * h);
    const max = format == "RGBA16" ? 65535 : 255;
    const premultiplied = format != "CMYKA8" && isPremultiplied(data, w * h);
    for (let i = 0; i < w * h; ++i) {
        if (format == "CMYKA8") {
            // Five channels: C, M, Y, K, alpha.
            const q = i * 5;
            const c = data[q] / max, m = data[q + 1] / max, y = data[q + 2] / max, k = data[q + 3] / max;
            const luma = (1 - k) * (0.2126 * (1 - c) + 0.7152 * (1 - m) + 0.0722 * (1 - y));
            out[i] = (data[q + 4] / max) * (1 - luma);
            continue;
        }
        const p = i * 4;
        const a = data[p + 3] / max;
        const luma = (0.2126 * data[p] + 0.7152 * data[p + 1] + 0.0722 * data[p + 2]) / max;
        // Over white: straight colour is a * colour + (1 - a); premultiplied colour already holds
        // the a * colour part.
        out[i] = Math.max(0, premultiplied ? a - luma : a * (1 - luma));
    }
    return out;
}

// True when no pixel has a colour channel above its alpha, which straight (unpremultiplied)
// colour almost always has somewhere when any pixel is partly transparent. Fully opaque images
// read the same either way.
function isPremultiplied(data, count) {
    for (let p = 0; p < count * 4; p += 4) {
        const a = data[p + 3];
        if (data[p] > a || data[p + 1] > a || data[p + 2] > a)
            return false;
    }
    return true;
}

// Averages `factor` × `factor` blocks; the result is ceil(w / factor) × ceil(h / factor).
function downsample(src, w, h, factor) {
    if (factor <= 1)
        return { data: src, w, h };
    const dw = Math.ceil(w / factor), dh = Math.ceil(h / factor);
    const out = new Float32Array(dw * dh);
    for (let dy = 0; dy < dh; ++dy) {
        const y0 = dy * factor, y1 = Math.min(h, y0 + factor);
        for (let dx = 0; dx < dw; ++dx) {
            const x0 = dx * factor, x1 = Math.min(w, x0 + factor);
            let sum = 0;
            for (let y = y0; y < y1; ++y)
                for (let x = x0; x < x1; ++x)
                    sum += src[y * w + x];
            out[dy * dw + dx] = sum / ((x1 - x0) * (y1 - y0));
        }
    }
    return { data: out, w: dw, h: dh };
}

// Separable Gaussian blur with standard deviation `sigma`, edges extended.
function gaussianBlur(src, w, h, sigma) {
    if (sigma < 0.1)
        return src;
    const radius = Math.ceil(sigma * 3);
    const kernel = new Float32Array(2 * radius + 1);
    let total = 0;
    for (let i = -radius; i <= radius; ++i)
        total += kernel[i + radius] = Math.exp(-(i * i) / (2 * sigma * sigma));
    for (let i = 0; i < kernel.length; ++i)
        kernel[i] /= total;
    const clamp = (v, hi) => (v < 0 ? 0 : v > hi ? hi : v);
    const tmp = new Float32Array(w * h);
    for (let y = 0; y < h; ++y)
        for (let x = 0; x < w; ++x) {
            let sum = 0;
            for (let k = -radius; k <= radius; ++k)
                sum += kernel[k + radius] * src[y * w + clamp(x + k, w - 1)];
            tmp[y * w + x] = sum;
        }
    const out = new Float32Array(w * h);
    for (let y = 0; y < h; ++y)
        for (let x = 0; x < w; ++x) {
            let sum = 0;
            for (let k = -radius; k <= radius; ++k)
                sum += kernel[k + radius] * tmp[clamp(y + k, h - 1) * w + x];
            out[y * w + x] = sum;
        }
    return out;
}

// Bilinear sample at (x, y), with pixel centres at whole numbers as in Rosette; edges extended.
function sampleBilinear(arr, w, h, x, y) {
    const cx = (v, hi) => (v < 0 ? 0 : v > hi ? hi : v);
    const x0 = Math.floor(x), y0 = Math.floor(y);
    const tx = x - x0, ty = y - y0;
    const get = (xi, yi) => arr[cx(yi, h - 1) * w + cx(xi, w - 1)];
    return get(x0, y0) * (1 - tx) * (1 - ty) + get(x0 + 1, y0) * tx * (1 - ty)
        + get(x0, y0 + 1) * (1 - tx) * ty + get(x0 + 1, y0 + 1) * tx * ty;
}

// Dots {x, y, r} in pixels of the density image: Rosette's rotated grid (halftone.py, _iter_dots)
// over the image's diagonal, centred on the image, keeping dots whose centre lies on the image.
// `minRadius` is a fraction (0–1) of the largest dot, as Rosette's --min-radius.
function computeDots(density, w, h, opts) {
    const { cellPx, angleDeg, gamma, minRadius } = opts;
    const factor = Math.max(1, Math.floor(cellPx / PIXELS_PER_CELL));
    const small = downsample(density, w, h, factor);
    const blurred = gaussianBlur(small.data, small.w, small.h, cellPx / 2 / factor);

    const maxR = cellPx * MAX_R_FACTOR;
    const rMin = Math.min(1, Math.max(0, minRadius)) * maxR;
    const rad = angleDeg * Math.PI / 180;
    const cosA = Math.cos(rad), sinA = Math.sin(rad);
    const halfGrid = Math.hypot(w, h);
    const count = Math.ceil(2 * halfGrid / cellPx) + 2;
    const dots = [];
    for (let row = 0; row < count; ++row) {
        const gy = -halfGrid + row * cellPx + cellPx * 0.5;
        for (let col = 0; col < count; ++col) {
            const gx = -halfGrid + col * cellPx + cellPx * 0.5;
            const px = w / 2 + gx * cosA - gy * sinA;
            const py = h / 2 + gx * sinA + gy * cosA;
            if (px < 0 || px > w || py < 0 || py > h)
                continue;
            // Pixel centres sit at whole numbers in the downsampled image's own pixels.
            const d = sampleBilinear(blurred, small.w, small.h, (px - factor / 2) / factor, (py - factor / 2) / factor);
            if (d < WHITE_CUTOFF)
                continue;
            const curved = gamma == 1 ? d : Math.pow(d, gamma);
            const r = Math.sqrt(Math.max(0, curved * (maxR * maxR - rMin * rMin) + rMin * rMin));
            if (r < 0.05)
                continue;
            dots.push({ x: px, y: py, r });
        }
    }
    return dots;
}

// ROSETTE-MATH-END

// Renders the object to RGBA8 pixels, as it appears with its fills and effects, without changing
// the document. Returns {density, w, h, box}: the ink of every pixel and the document rectangle
// the pixels cover.
function readPixels(node) {
    const { NodeRenderingEngine, NodeRenderingEngineOptions, RasterFormat } = require('/rasterobject.js');
    const box = node.getSpreadBaseBox(false);
    // One pixel per document unit, or fewer for very large art: past a few million pixels the
    // blur only gets slower, since every dot is sampled from a blurred image anyway.
    const options = NodeRenderingEngineOptions.create();
    options.scale = Math.min(1, Math.sqrt(MAX_RENDER_PIXELS / (box.width * box.height)));
    const engine = NodeRenderingEngine.create(node, RasterFormat.RGBA8, options);
    const w = engine.width, h = engine.height;
    const data = new Uint8Array(engine.createCompatibleBuffer(true).buffer);
    return { density: densityFromPixels(data, w, h, "RGBA8"), w, h, box };
}

const MAX_RENDER_PIXELS = 4e6;

// The command that adds the dots for `settings` ({cell, angle, gamma, minDot, separate}, cell in
// document units, minDot in percent) above the selection, named "Rosette halftone", as one undo
// step. Null when there are no dots to draw.
function dotsCommand(source, settings) {
    const { BlendMode } = require('affinity:common');
    const { SVG11 } = require('/colours.js');
    const { AddChildNodesCommandBuilder, CompoundCommandBuilder, DocumentCommand } = require('/commands.js');
    const { FillDescriptor } = require('/fills.js');
    const { Curve, PolyCurve, Rectangle } = require('/geometry.js');
    const { LineStyleDescriptor } = require('/linestyle.js');
    const { PolyCurveNodeDefinition } = require('/nodes.js');

    const box = source.box;
    const pxPerUnit = source.w / box.width;
    const dots = computeDots(source.density, source.w, source.h, {
        cellPx: settings.cell * pxPerUnit,
        angleDeg: settings.angle,
        gamma: settings.gamma,
        minRadius: settings.minDot / 100,
    });
    if (dots.length == 0)
        return null;

    const ink = FillDescriptor.createSolid(SVG11.black, BlendMode.Normal);
    const none = FillDescriptor.createNone();
    const noLine = LineStyleDescriptor.createDefault(0);
    // Pixels to document units: the rendered pixels cover `box` exactly.
    const ellipse = (d) => {
        const x = box.x + d.x / pxPerUnit, y = box.y + d.y / pxPerUnit, r = d.r / pxPerUnit;
        return Curve.createEllipse(new Rectangle(x - r, y - r, 2 * r, 2 * r));
    };
    const builder = AddChildNodesCommandBuilder.create();
    if (!settings.separate) {
        const poly = PolyCurve.create();
        for (const d of dots)
            poly.addCurve(ellipse(d));
        const def = PolyCurveNodeDefinition.create(poly, ink, none, noLine, none);
        def.userDescription = "Rosette halftone";
        builder.addNode(def);
        return builder.createCommand(true);
    }
    // Separate dots: add them selected, then group the selection and name the group.
    for (const d of dots) {
        const poly = PolyCurve.create();
        poly.addCurve(ellipse(d));
        builder.addNode(PolyCurveNodeDefinition.create(poly, ink, none, noLine, none));
    }
    return CompoundCommandBuilder.create()
        .addCommand(builder.createCommand(true))
        .addCommand(DocumentCommand.createGroupNodes(null))
        .addCommand(DocumentCommand.createSetDescription(null, "Rosette halftone"))
        .createCommand();
}

function main() {
    const { Dialog, DialogResult } = require('/dialog.js');
    const { Document } = require('/document.js');
    const { UnitType } = require('/units.js');

    const doc = Document.current;
    if (!doc) {
        alert("Rosette needs an open document.");
        return;
    }
    const nodes = doc.selection.nodes.toArray();
    if (nodes.length != 1) {
        alert("Select one object to halftone. To halftone several, group them first.");
        return;
    }
    const source = readPixels(nodes[0]);

    const dlg = Dialog.create("Rosette Halftone");
    const col = dlg.addColumn();
    const screen = col.addGroup("Screen");
    const cell = screen.addUnitValueEditor("Cell size", UnitType.Pixel, doc.units, Math.max(2, source.box.width / 60), 1)
        .setNoMaxValue();
    const angle = screen.addUnitValueEditor("Angle", UnitType.Number, UnitType.Number, 45, -90, 90)
        .setPrecision(1);
    const tone = col.addGroup("Tone");
    const gamma = tone.addUnitValueEditor("Gamma", UnitType.Number, UnitType.Number, 1.5, 0.5, 4)
        .setPrecision(2).setShowPopupSlider(true);
    const minDot = tone.addUnitValueEditor("Minimum dot (%)", UnitType.Number, UnitType.Number, 0, 0, 100)
        .setPrecision(0).setShowPopupSlider(true);
    const output = col.addGroup("Output");
    const shape = output.addButtonSet("Dots as", ["One compound shape", "Separate dots"], 0);
    dlg.initialWidth = 380;

    const update = (preview) => {
        const cmd = dotsCommand(source, {
            cell: cell.value, angle: angle.value, gamma: gamma.value, minDot: minDot.value,
            separate: shape.selectedIndex == 1,
        });
        if (cmd)
            doc.executeCommand(cmd, preview);
        else
            doc.clearPreviews();
        return cmd != null;
    };
    dlg.onControlValueChangedHandler = () => update(true);
    update(true);
    const result = dlg.runModal();
    doc.clearPreviews();
    if ((result?.value ?? result) == DialogResult.Ok.value && !update(false))
        alert("The object is all white or transparent, so there are no dots to draw.");
}

main();
