# -*- coding: utf-8 -*-
"""Пиктограммы прототипа sport-live (виды спорта и служебные) через Nano Banana.

До 13.09.2026 страница показывала эмодзи (⚽ 🏀 🔴 📅 🎟️ …) — их вид зависит от
платформы игрока и не совпадает со стилем страницы. Теперь два набора флэт-иконок в
палитре сайта, отдельно под тёмную и светлую тему:

  dark  — золото (#FFB62F) с бирюзовым акцентом, детали navy; ложится на navy-фон;
  light — navy (#10263A) с золотым акцентом, детали белые; ложится на светлый фон.
          Делается image-to-image от тёмного варианта, чтобы силуэты совпадали.

Модель не умеет прозрачность, поэтому генерируем на ровном пурпуре (#FF00FF) и
вырезаем его (как в product-showcase/tools/nanobanana-mcp/key_magenta.py).

Тракт как у остальных генераций: локальная машина в РФ гео-блокирована Gemini API,
поэтому вызов исполняется на *10: ssh root@46.62.223.10 python3 /opt/nanobanana/gen.py.
Ключ — GEMINI_API_KEY из C:/_PARYAJPAM/.env; расход пишется в общий леджер
product-showcase/assets/generated/_usage.jsonl.

  python gen_icons.py dark [name …]    # тёмный набор → raw/dark/<name>.png (пропускает готовые)
  python gen_icons.py light [name …]   # светлый набор от тёмного → raw/light/<name>.png
  python gen_icons.py post             # вырезать пурпур, выровнять, ужать → ../../assets/icons/{dark,light}/
  python gen_icons.py sheet            # контрольный лист обоих наборов → sheet.png
"""
import base64, datetime, io, json, os, re, subprocess, sys, threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, 'raw')
OUT = os.path.abspath(os.path.join(HERE, '..', '..', 'assets', 'icons'))
ATLAS = 'C:/_PARYAJPAM'
LEDGER = os.path.join(ATLAS, '_repos/paryajpam/product-showcase/assets/generated/_usage.jsonl')
HOST = 'root@46.62.223.10'
REMOTE = '/opt/nanobanana/gen.py'
MODEL = 'gemini-2.5-flash-image'
WORKERS = 5
BUDGET_USD = 4.0            # предохранитель на один прогон
SIZE = 96                   # итоговый размер png (на странице 16–37 css-px, запас под 2x)

# (имя файла, что нарисовать). Имена s<id> — id вида спорта в фиде (live-data.js ICON).
ICONS = [
    ('s1',  'a classic soccer ball with pentagon panels'),
    ('s2',  'a video game controller gamepad, front view'),
    ('s3',  'a basketball with curved seam lines'),
    ('s4',  'a tennis racket with a tennis ball beside its head'),
    ('s5',  'a volleyball with swirling panel lines'),
    ('s6',  'a table tennis paddle with a small ball'),
    ('s7',  'an ice hockey stick with a puck'),
    ('s8',  'a handball held from below by an open hand, ball on top of the palm'),
    ('s12', 'an american football ball with laces, tilted diagonally'),
    ('s13', 'a small indoor football goal with a net and a ball in front of it'),
    ('s14', 'a baseball ball with two curved stitch lines'),
    ('s15', 'a fighting glove, fist facing forward'),
    ('sport',  'a round award medal hanging on a ribbon'),
    ('all',    'a dartboard target with concentric rings and a center dot'),
    ('live',   'a broadcast signal: a solid dot with three concentric arcs radiating to the upper right'),
    ('line',   'a calendar page with a ring binding and one highlighted day square'),
    ('coupon', 'a betting ticket stub with notched sides and a perforated tear line'),
    ('bets',   'a receipt sheet with a zigzag bottom edge and a check mark badge'),
    ('hot',    'a flame'),
    ('lock',   'a closed padlock'),
    ('sun',    'a sun: one solid round disc with eight short thick rays around it, plain disc without any pattern inside'),
    ('moon',   'a crescent moon'),
    ('warn',   'a triangular warning sign with an exclamation mark; the triangle is the only shape, nothing behind or around it'),
]

COMMON = (
    "Flat vector icon for a sportsbook app, single centered symbol, bold simple silhouette, "
    "thick chunky shapes, no thin hairlines, crisp edges, minimal detail, the symbol fills about "
    "70 percent of the canvas with even padding around it. Flat colors only: no gradients, no "
    "shadows, no glow, no outline stroke around the symbol, no 3d, no photorealism, no scene. "
    "Background: a completely flat, solid, uniform bright magenta (#FF00FF) covering the whole "
    "canvas, nothing else on it. Must stay readable when scaled down to 16 pixels. "
    "Absolutely no text, no letters, no numbers, no words, no watermark."
)
DARK = (
    "Icon subject: %s.\n\nColors: symbol filled warm gold (#FFB62F), one small teal (#2BB0C8) "
    "accent detail where it looks natural, inner detail lines in deep navy (#0A1A2F). " + COMMON
)
LIGHT = (
    "Recolor this exact icon for a light interface. Keep exactly the same shapes, silhouette, "
    "proportions and composition; change only the colors: main fill deep navy (#10263A), the "
    "accent detail warm gold (#FFB62F), inner detail lines white (#FFFFFF). " + COMMON
)

_lock = threading.Lock()
_spent = [0.0]


def api_key():
    with io.open(os.path.join(ATLAS, '.env'), encoding='utf-8') as f:
        m = re.search(r'^\s*GEMINI_API_KEY\s*=\s*(.+?)\s*$', f.read(), re.M)
    if not m:
        sys.exit('GEMINI_API_KEY не найден в .env')
    return m.group(1).strip().strip('"').strip("'")


def generate(key, prompt, dest, ref=None):
    req = {'key': key, 'model': MODEL, 'prompt': prompt, 'aspect_ratio': '1:1', 'references': []}
    if ref:
        with open(ref, 'rb') as f:
            req['references'].append({'mime': 'image/png', 'b64': base64.b64encode(f.read()).decode()})
    p = subprocess.run(['ssh', HOST, 'python3 ' + REMOTE],
                       input=json.dumps(req).encode(), capture_output=True, timeout=240)
    if p.returncode != 0:
        return None, 'ssh rc=%d %s' % (p.returncode, p.stderr.decode('utf-8', 'replace')[:200])
    try:
        res = json.loads(p.stdout.decode('utf-8', 'replace'))
    except ValueError:
        return None, 'bad json: %s' % p.stdout.decode('utf-8', 'replace')[:200]
    if not res.get('ok'):
        return None, str(res.get('error'))[:300]
    with open(dest, 'wb') as f:
        f.write(base64.b64decode(res['image_b64']))
    cost = res.get('cost_usd', 0.0)
    usage = res.get('usage', {})
    with _lock:
        _spent[0] += cost
        with io.open(LEDGER, 'a', encoding='utf-8') as f:
            f.write(json.dumps({
                'ts': datetime.datetime.now().isoformat(timespec='seconds'),
                'out': 'sport-live-icons/' + os.path.relpath(dest, RAW).replace('\\', '/'), 'model': MODEL,
                'cost_usd': cost, 'prompt_tokens': usage.get('promptTokenCount'),
                'output_tokens': usage.get('candidatesTokenCount'),
            }, ensure_ascii=False) + '\n')
    return cost, None


def task(key, theme, name, subject):
    dest = os.path.join(RAW, theme, name + '.png')
    if os.path.exists(dest) and os.path.getsize(dest) > 2000:
        return name, 'skip (есть)'
    with _lock:
        if _spent[0] >= BUDGET_USD:
            return name, 'бюджет прогона исчерпан'
    if theme == 'dark':
        prompt, ref = DARK % subject, None
    else:
        ref = os.path.join(RAW, 'dark', name + '.png')
        if not os.path.exists(ref):
            return name, 'нет тёмного варианта — сначала dark'
        prompt = LIGHT
    cost, err = generate(key, prompt, dest, ref)
    for _ in range(2):                      # два повтора при сбое
        if not err:
            break
        cost, err = generate(key, prompt, dest, ref)
    return name, ('ОШИБКА: ' + err) if err else 'ok $%.4f' % cost


# ── пост-обработка ──────────────────────────────────────────────────────────
def key_magenta(path):
    """Пурпурный фон → прозрачность, гашение пурпурной каймы, обрезка по содержимому."""
    im = np.asarray(Image.open(path).convert('RGB')).astype(np.float32)
    h, w, _ = im.shape
    corners = np.stack([im[2, 2], im[2, w - 3], im[h - 3, 2], im[h - 3, w - 3]])
    bg = corners.mean(axis=0)
    d = np.sqrt(((im - bg) ** 2).sum(axis=2))
    T1, T2 = 60.0, 115.0
    alpha = np.clip((d - T1) / (T2 - T1), 0, 1)
    r, g, b = im[..., 0], im[..., 1], im[..., 2]
    m = np.minimum(r, b) - g                        # остаточный пурпур на краях
    k = np.clip((m - 8) / 45.0, 0, 1) * (m > 8)
    r2 = r - (r - g) * 0.7 * k
    b2 = b - (b - g) * 0.7 * k
    rgba = np.dstack([np.clip(r2, 0, 255), g, np.clip(b2, 0, 255), alpha * 255]).astype(np.uint8)
    out = Image.fromarray(rgba, 'RGBA')
    bbox = out.split()[3].getbbox()
    return out.crop(bbox) if bbox else out


def fit_square(img, size=SIZE, extent=0.86):
    """Вписать символ в квадрат: самая длинная сторона = extent·size, центр по центру."""
    w, h = img.size
    scale = extent * size / max(w, h)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    small = img.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    canvas.paste(small, ((size - nw) // 2, (size - nh) // 2), small)
    return canvas


def postprocess(names=None):
    n = 0
    for theme in ('dark', 'light'):
        src_dir = os.path.join(RAW, theme)
        dst_dir = os.path.join(OUT, theme)
        os.makedirs(dst_dir, exist_ok=True)
        for name, _ in ICONS:
            if names and name not in names:
                continue
            src = os.path.join(src_dir, name + '.png')
            if not os.path.exists(src):
                continue
            icon = fit_square(key_magenta(src))
            icon.save(os.path.join(dst_dir, name + '.png'), optimize=True)
            n += 1
    print('пост-обработка: %d файлов → %s' % (n, OUT))


def sheet():
    """Контрольный лист: обе темы на своих фонах, подписи именами."""
    cell, pad = 72, 10
    cols = len(ICONS)
    W = pad + cols * (cell + pad)
    H = pad + 2 * (cell + pad + 14)
    img = Image.new('RGB', (W, H), (3, 18, 40))
    dr = ImageDraw.Draw(img)
    dr.rectangle((0, H // 2, W, H), fill=(238, 242, 247))
    for row, theme in enumerate(('dark', 'light')):
        y = pad + row * (cell + pad + 14) + (H // 2 - pad - (cell + pad + 14) if row else 0)
        for col, (name, _) in enumerate(ICONS):
            p = os.path.join(OUT, theme, name + '.png')
            x = pad + col * (cell + pad)
            if os.path.exists(p):
                ic = Image.open(p).convert('RGBA').resize((cell, cell), Image.LANCZOS)
                img.paste(ic, (x, y), ic)
            dr.text((x + 2, y + cell + 1), name, fill=(150, 170, 190) if theme == 'dark' else (80, 100, 120))
    p = os.path.join(HERE, 'sheet.png')
    img.save(p)
    print('лист:', p)


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ('dark', 'light', 'post', 'sheet'):
        sys.exit(__doc__)
    cmd, names = sys.argv[1], sys.argv[2:]
    if cmd == 'post':
        postprocess(names); return
    if cmd == 'sheet':
        sheet(); return
    os.makedirs(os.path.join(RAW, cmd), exist_ok=True)
    key = api_key()
    jobs = [(n, s) for n, s in ICONS if not names or n in names]
    print('%s: картинок к генерации %d (бюджет прогона $%.2f)' % (cmd, len(jobs), BUDGET_USD))
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(task, key, cmd, n, s) for n, s in jobs]
        for f in futs:
            name, status = f.result()
            print('  %-8s %s' % (name, status))
    print('потрачено за прогон: $%.4f' % _spent[0])
    postprocess([n for n, _ in jobs])


if __name__ == '__main__':
    main()
