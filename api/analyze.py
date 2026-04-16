"""
Rhiza GI MAP Bar Reader — Vercel serverless function.

POST /api/analyze
Body: multipart/form-data with 'pdf' file
Returns: JSON { markers: [{ name, zones, triangle_pct, flag }] }

The function reads the colored zone bars from a GI MAP patient PDF,
locates the black triangle on each bar, and reports which zone the
triangle sits in (red/yellow/green → HIGH/LOW/elevated/lower_than_ideal/normal).
"""
from http.server import BaseHTTPRequestHandler
import json
import io
import pdfplumber
from PIL import Image

# Bar geometry in PDF coordinates (from empirical measurement of DSL reports)
BAR_PDF_W = 190
BAR_PDF_H = 8
BAR_PDF_X = 272
SCALE = 300 / 72  # Render at 300 DPI

# Markers where yellow should be ignored — only flag HIGH or LOW (Michelle's rule)
EXTREMES_ONLY = {
    'Enterobacter spp.',
    'Klebsiella pneumoniae',
    'K. pneumoniae',
    'Enterococcus faecalis',
    'E. faecalis',
}


def classify_pixel(rgb):
    r, g, b = rgb
    if r > 220 and g < 200 and b < 180:
        return 'R'
    if r > 240 and g > 210 and b < 200:
        return 'Y'
    if g > 200 and r < 220 and b < 200:
        return 'G'
    return '?'


def find_zone_boundaries(pixels):
    w = len(pixels)
    for i, z in enumerate(pixels):
        if z == '?':
            for j in range(1, 5):
                if i - j >= 0 and pixels[i - j] != '?':
                    pixels[i] = pixels[i - j]; break
                if i + j < w and pixels[i + j] != '?':
                    pixels[i] = pixels[i + j]; break
    zones = []
    prev = pixels[0]
    start = 0
    for i in range(1, w):
        if pixels[i] != prev:
            zones.append((prev, 100 * start / w, 100 * i / w))
            prev = pixels[i]
            start = i
    zones.append((prev, 100 * start / w, 100.0))
    merged = []
    for z in zones:
        if z[2] - z[1] < 1.5:
            continue
        if merged and merged[-1][0] == z[0]:
            merged[-1] = (z[0], merged[-1][1], z[2])
        else:
            merged.append(list(z))
    return [{'zone': z[0], 'start': round(z[1], 1), 'end': round(z[2], 1)} for z in merged]


def find_triangle_x(im_crop):
    w, h = im_crop.size
    scores = []
    for x in range(w):
        dark_count = 0
        for y in range(h):
            p = im_crop.getpixel((x, y))
            r, g, b = p[:3]
            if r < 100 and g < 100 and b < 100:
                dark_count += 1
        scores.append(dark_count)
    max_score = max(scores) if scores else 0
    if max_score < 3:
        return None
    threshold = max_score * 0.5
    dark_cols = [i for i, s in enumerate(scores) if s >= threshold]
    if not dark_cols:
        return None
    return 100 * (sum(dark_cols) / len(dark_cols)) / w


def zone_to_flag(zone, tri_pct, marker_name):
    """Map triangle position + zone → flag. Apply extremes-only override."""
    if zone is None:
        return 'unknown'
    if zone == 'G':
        return 'normal'
    is_high_side = tri_pct is not None and tri_pct > 50
    if zone == 'Y':
        if marker_name in EXTREMES_ONLY:
            return 'normal'  # Ignore yellow for these markers
        return 'elevated' if is_high_side else 'lower_than_ideal'
    if zone == 'R':
        return 'HIGH' if is_high_side else 'LOW'
    return 'unknown'


def dedupe_doubled(s):
    """DSL PDF draws text twice for bold ('BBaacc' -> 'Bac')."""
    if len(s) < 2 or len(s) % 2 != 0:
        return s
    if all(s[i] == s[i + 1] for i in range(0, len(s), 2)):
        return s[::2]
    return s


def analyze_bar(page_img, pdf_top):
    x0 = int(BAR_PDF_X * SCALE)
    y0 = int((pdf_top - 10) * SCALE)
    x1 = int((BAR_PDF_X + BAR_PDF_W) * SCALE)
    y1 = int((pdf_top + BAR_PDF_H + 2) * SCALE)
    crop = page_img.crop((x0, y0, x1, y1)).convert('RGB')
    cw, ch = crop.size
    sample_y = int(ch * 0.85)
    pixels = [classify_pixel(crop.getpixel((x, sample_y))) for x in range(cw)]
    zones = find_zone_boundaries(pixels)
    tri = find_triangle_x(crop)
    triangle_zone = None
    if tri is not None:
        for z in zones:
            if z['start'] <= tri <= z['end']:
                triangle_zone = z['zone']
                break
        if triangle_zone is None:
            triangle_zone = zones[0]['zone'] if tri < 50 else zones[-1]['zone']
    return {'zones': zones, 'triangle_pct': round(tri, 1) if tri is not None else None, 'triangle_zone': triangle_zone}


def match_marker_name(page, bar_top):
    words = page.extract_words()
    candidates = [w for w in words if abs(w['top'] - bar_top) < 6 and w['x0'] < BAR_PDF_X - 10]
    if not candidates:
        return None
    candidates.sort(key=lambda w: w['x0'])
    name_parts = []
    for w in candidates:
        text = dedupe_doubled(w['text'])
        if text.replace(',', '').replace('.', '').replace('e', '').replace('+', '').replace('-', '').isdigit():
            break
        name_parts.append(text)
    return ' '.join(name_parts).strip()


def analyze_pdf_bytes(pdf_bytes):
    results = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            bars = [im for im in page.images
                    if abs(im['width'] - BAR_PDF_W) < 5
                    and abs(im['height'] - BAR_PDF_H) < 3
                    and abs(im['x0'] - BAR_PDF_X) < 10]
            if not bars:
                continue
            page_img = page.to_image(resolution=300).original.convert('RGB')
            for img in bars:
                pdf_top = img['top']
                marker_name = match_marker_name(page, pdf_top)
                if not marker_name:
                    continue
                bar = analyze_bar(page_img, pdf_top)
                flag = zone_to_flag(bar['triangle_zone'], bar['triangle_pct'], marker_name)
                results.append({
                    'page': page_idx + 1,
                    'marker': marker_name,
                    'zones': bar['zones'],
                    'triangle_pct': bar['triangle_pct'],
                    'triangle_zone': bar['triangle_zone'],
                    'flag': flag,
                })
    return results


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            content_type = self.headers.get('Content-Type', '')
            body = self.rfile.read(content_length)

            if 'multipart/form-data' in content_type:
                # Parse multipart: find the PDF bytes between boundaries
                boundary = content_type.split('boundary=')[1].encode()
                parts = body.split(b'--' + boundary)
                pdf_bytes = None
                for part in parts:
                    if b'Content-Type: application/pdf' in part or b'.pdf' in part[:200]:
                        idx = part.find(b'\r\n\r\n')
                        if idx >= 0:
                            pdf_bytes = part[idx + 4:].rstrip(b'\r\n-')
                            break
                if not pdf_bytes:
                    raise ValueError('No PDF found in multipart body')
            else:
                pdf_bytes = body  # raw PDF POST

            results = analyze_pdf_bytes(pdf_bytes)
            response = {'success': True, 'markers': results}
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())
        except Exception as e:
            import traceback
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                'success': False,
                'error': str(e),
                'trace': traceback.format_exc(),
            }).encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
