# -*- coding: utf-8 -*-
"""Проверка готовых наборов пиктограмм на отклонения от выбранного стиля.

Что ловим:
  teal   — бирюзовые/голубые/зелёные пиксели (их в палитре быть не должно ни в одном наборе);
  тёмный набор  — должен состоять из золота и navy;
  светлый набор — один сплошной navy, без золота и без белых заливок.
Доля считается от непрозрачных пикселей; края после кеинга дают шум, поэтому порог 1.5 %.

  python check_icons.py            # оба набора
  python check_icons.py light      # один
"""
import os, sys, colorsys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(os.path.join(HERE, '..', '..', 'assets', 'icons'))
LIMIT = 1.5      # процент пикселей, ниже которого считаем шумом края


def shares(path):
    im = np.asarray(Image.open(path).convert('RGBA')).astype(np.float32)
    a = im[..., 3] / 255.0
    solid = a > 0.75
    n = max(1, solid.sum())
    rgb = im[..., :3][solid] / 255.0
    mx = rgb.max(axis=1); mn = rgb.min(axis=1)
    v = mx; sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    # оттенок в градусах
    hue = np.zeros(len(rgb))
    for i, (r, g, b) in enumerate(rgb):
        hue[i] = colorsys.rgb_to_hsv(r, g, b)[0] * 360
    # только ЗАМЕТНАЯ бирюза: тёмные сине-серые пиксели контура (#103040 и подобные)
    # имеют тот же оттенок, но на экране читаются как navy, поэтому порог по яркости 0.45
    teal = ((hue > 140) & (hue < 205) & (sat > 0.3) & (v > 0.45)).sum() / n * 100
    gold = ((hue > 25) & (hue < 60) & (sat > 0.35) & (v > 0.45)).sum() / n * 100
    navy = (v < 0.35).sum() / n * 100
    white = ((v > 0.85) & (sat < 0.15)).sum() / n * 100
    return teal, gold, navy, white


def full_bleed(raw_path):
    """Признак подложки: после вырезания фона знак занимает почти весь холст — значит
    модель нарисовала плитку, рамку или карточку, а не отдельный символ."""
    if not os.path.exists(raw_path):
        return None
    import gen_icons
    im = gen_icons.key_magenta(raw_path)
    src = Image.open(raw_path)
    return im.width / src.width > 0.97 and im.height / src.height > 0.97


def main():
    themes = sys.argv[1:] or ['dark', 'light']
    bad = 0
    for theme in themes:
        d = os.path.join(OUT, theme)
        print('== %s' % theme)
        for fn in sorted(os.listdir(d)):
            if not fn.endswith('.png'):
                continue
            teal, gold, navy, white = shares(os.path.join(d, fn))
            flags = []
            if full_bleed(os.path.join(HERE, 'raw', theme, fn)):
                flags.append('подложка во весь холст')
            if teal > LIMIT:
                flags.append('БИРЮЗА %.1f%%' % teal)
            if theme == 'light':
                if gold > LIMIT:
                    flags.append('золото %.1f%%' % gold)
                if white > LIMIT:
                    flags.append('белое %.1f%%' % white)
                if navy < 50:
                    flags.append('мало navy %.0f%%' % navy)
            else:
                if gold < 20:
                    flags.append('мало золота %.0f%%' % gold)
                if navy < 5:
                    flags.append('нет navy-обводки %.0f%%' % navy)
            if flags:
                bad += 1
                print('  %-10s %s' % (fn[:-4], ', '.join(flags)))
        print('  проверено, отклонений: %d' % bad)
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
