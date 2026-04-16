"""
Rhiza GI MAP Bar Reader — Vercel serverless function (Flask).
POST /api/analyze — accepts PDF, returns zone flags.
"""
from flask import Flask, request, jsonify
import io
import pdfplumber
from PIL import Image

app = Flask(__name__)

BAR_PDF_W = 190
BAR_PDF_H = 8
BAR_PDF_X = 272
SCALE = 300 / 72

EXTREMES_ONLY = {'Enterobacter spp.', 'Klebsiella pneumoniae', 'K. pneumoniae',
                 'Enterococcus faecalis', 'E. faecalis'}


def classify_pixel(rgb):
    r, g, b = rgb
    if r > 220 and g < 200 and b < 180: return 'R'
    if r > 240 and g > 210 and b < 200: return 'Y'
    if g > 200 and r < 220 and b < 200: return 'G'
    return '?'


def find_zone_boundaries(pixels):
    w = len(pixels)
    for i in range(w):
        if pixels[i] == '?':
            for j in range(1, 5):
                if i - j >= 0 and pixels[i - j] != '?': pixels[i] = pixels[i - j]; break
                if i + j < w and pixels[i + j] != '?': pixels[i] = pixels[i + j]; break
    zones, prev, start = [], pixels[0], 0
    for i in range(1, w):
        if pixels[i] != prev:
            zones.append((prev, 100 * start / w, 100 * i / w))
            prev, start = pixels[i], i
    zones.append((prev, 100 * start / w, 100.0))
    merged = []
    for z in zones:
        if z[2] - z[1] < 1.5: continue
        if merged and merged[-1][0] == z[0]: merged[-1] = (z[0], merged[-1][1], z[2])
        else: merged.append(list(z))
    return [{'zone': z[0], 'start': round(z[1], 1), 'end': round(z[2], 1)} for z in merged]


def find_triangle_x(im_crop):
    w, h = im_crop.size
    scores = []
    for x in range(w):
        dark = sum(1 for y in range(h) if all(c < 100 for c in im_crop.getpixel((x, y))[:3]))
        scores.append(dark)
    mx = max(scores) if scores else 0
    if mx < 3: return None
    cols = [i for i, s in enumerate(scores) if s >= mx * 0.5]
    return 100 * (sum(cols) / len(cols)) / w if cols else None


def dedupe_doubled(s):
    if len(s) < 2 or len(s) % 2 != 0: return s
    if all(s[i] == s[i + 1] for i in range(0, len(s), 2)): return s[::2]
    return s


def analyze_pdf_bytes(pdf_bytes):
    results = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for pi, page in enumerate(pdf.pages):
            bars = [im for im in page.images
                    if abs(im['width'] - BAR_PDF_W) < 5
                    and abs(im['height'] - BAR_PDF_H) < 3
                    and abs(im['x0'] - BAR_PDF_X) < 10]
            if not bars: continue
            page_img = page.to_image(resolution=300).original.convert('RGB')
            for img in bars:
                top = img['top']
                words = page.extract_words()
                cands = sorted([w for w in words if abs(w['top'] - top) < 6 and w['x0'] < BAR_PDF_X - 10],
                               key=lambda w: w['x0'])
                parts = []
                for w in cands:
                    t = dedupe_doubled(w['text'])
                    if t.replace(',','').replace('.','').replace('e','').replace('+','').replace('-','').isdigit(): break
                    parts.append(t)
                marker = ' '.join(parts).strip()
                if not marker: continue
                x0 = int(BAR_PDF_X * SCALE)
                y0 = int((top - 10) * SCALE)
                x1 = int((BAR_PDF_X + BAR_PDF_W) * SCALE)
                y1 = int((top + BAR_PDF_H + 2) * SCALE)
                crop = page_img.crop((x0, y0, x1, y1)).convert('RGB')
                cw, ch = crop.size
                pixels = [classify_pixel(crop.getpixel((x, int(ch * 0.85)))) for x in range(cw)]
                zones = find_zone_boundaries(pixels)
                tri = find_triangle_x(crop)
                tz = None
                if tri is not None:
                    for z in zones:
                        if z['start'] <= tri <= z['end']: tz = z['zone']; break
                    if tz is None: tz = zones[0]['zone'] if tri < 50 else zones[-1]['zone']
                flag = 'unknown'
                if tz == 'G': flag = 'normal'
                elif tz == 'Y':
                    flag = 'normal' if marker in EXTREMES_ONLY else ('elevated' if tri and tri > 50 else 'lower_than_ideal')
                elif tz == 'R':
                    flag = 'HIGH' if tri and tri > 50 else 'LOW'
                results.append({'page': pi + 1, 'marker': marker, 'zones': zones,
                                'triangle_pct': round(tri, 1) if tri else None,
                                'triangle_zone': tz, 'flag': flag})
    return results


@app.route('/api/analyze', methods=['POST'])
def analyze():
    try:
        if 'pdf' in request.files:
            pdf_bytes = request.files['pdf'].read()
        else:
            pdf_bytes = request.get_data()
        if not pdf_bytes:
            return jsonify({'success': False, 'error': 'No PDF provided'}), 400
        results = analyze_pdf_bytes(pdf_bytes)
        return jsonify({'success': True, 'markers': results})
    except Exception as e:
        import traceback
        return jsonify({'success': False, 'error': str(e), 'trace': traceback.format_exc()}), 500


@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})
