"""Deterministic raster output of a single validated scene; no image-model calls."""
from io import BytesIO
from pathlib import Path
import textwrap
from PIL import Image, ImageDraw, ImageFont
from .geometry import AXES, GeometryError, compile_scene, projection

MODEL = 'orthogonal-drawing-v1'


def font(size):
    for path in (Path('C:/Windows/Fonts/arial.ttf'),
                 Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
                 Path('/Library/Fonts/Arial.ttf')):
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    raise GeometryError('Не установлен шрифт для русских подписей чертежа.')


def number(value):
    return format(value, '.12g')


def render_png(plan):
    scene = compile_scene(plan)
    scale = 740 / max(scene.size)
    panels = [('front', False, 'Спереди — двери условно сняты'),
              ('front', True, 'Спереди — фасады'),
              ('side', False, 'Сбоку — схема элементов'),
              ('top', False, 'Сверху — схема элементов')]
    # Each arrow has its own external lane. Text never covers the furniture.
    lane_counts = []
    for view, closed, _ in panels:
        axes = AXES[view]
        mm = [] if closed else [m for m in scene.measures if m.view == view]
        lane_counts.append((sum(m.axis == axes[1] for m in mm), sum(m.axis == axes[0] for m in mm)))
    cell_w = max(1150, 900 + max(v for v, h in lane_counts) * 38)
    cell_h = max(1050, 900 + max(h for v, h in lane_counts) * 38)
    width = cell_w * 2 + 80
    descriptions = []
    if any(b.kind == 'rod' and b.low[1:] == b.high[1:] for b in scene.boxes):
        descriptions.append('Штанги показаны условно спереди. Их расположение по глубине на этом листе не задано.')
    for i, dimension in enumerate(plan.dimensions, 1):
        suffix = ' (расчёт)' if dimension.formula else ''
        descriptions.extend(textwrap.wrap(f'D{i}: {dimension.target} — {number(dimension.value_mm)} мм{suffix}', width=100) or [''])
    height = cell_h * 2 + 200 + len(descriptions) * 32
    if width * height > 32_000_000:
        raise GeometryError('Слишком много размерных линий для одного листа. Разделите изделие на отдельные проекты.')
    image = Image.new('RGB', (width, height), 'white')
    draw = ImageDraw.Draw(image)
    regular, small, heading = font(25), font(22), font(30)
    draw.text((40, 20), 'Схема изделия с размерами • мм', fill='black', font=heading)
    draw.text((40, 62), 'Все виды построены из одной схемы. Крепёж и технологические припуски здесь не рассчитываются.', fill='black', font=small)
    labels = {d.id: f'D{i}' for i, d in enumerate(plan.dimensions, 1)}

    for index, (view, closed, title) in enumerate(panels):
        left = 40 + (index % 2) * cell_w
        top = 120 + (index // 2) * cell_h
        axes = tuple('xyz'.index(a) for a in AXES[view])
        vertical_count, _ = lane_counts[index]
        ox = left + 120 + vertical_count * 38
        oy = top + 65 + scene.size[axes[1]] * scale

        def project(p):
            return ox + p[axes[0]] * scale, oy - p[axes[1]] * scale

        draw.text((left + 20, top), title, font=heading, fill='black')
        # The facades view shares every part, but closed doors cover contents.
        boxes = sorted(scene.boxes, key=lambda b: b.kind == 'door')
        for box in boxes:
            if box.kind == 'rod' and box.low[1:] == box.high[1:] and view != 'front':
                continue  # The schematic rod has no invented depth coordinate.
            if box.kind == 'door' and view == 'front' and not closed:
                continue
            low, high = projection(box, view)
            rect = (ox + low[0] * scale, oy - high[1] * scale,
                    ox + high[0] * scale, oy - low[1] * scale)
            draw.rectangle(rect, fill='white' if box.kind == 'door' and closed else None,
                           outline='black', width=2)
            if view == 'front' and box.kind in ('drawer_front', 'door'):
                label = 'Фасад ящика' if box.kind == 'drawer_front' else 'Дверь'
                if rect[2] - rect[0] > small.getlength(label) + 10 and rect[3] - rect[1] > 30:
                    draw.text(((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2), label,
                              font=small, fill='black', anchor='mm')
        if closed:
            continue
        vertical = horizontal = 0
        annotations = []
        for m in sorted((m for m in scene.measures if m.view == view), key=lambda m: m.value):
            a, b = project(m.start), project(m.end)
            label = labels[m.dimension] + ' = ' + number(m.value)
            if 'xyz'.index(m.axis) == axes[0]:
                y = oy + 40 + horizontal * 38
                horizontal += 1
                x1, x2 = sorted((a[0], b[0]))
                draw.line((a[0], a[1], a[0], y + 7), fill='#888888', width=1)
                draw.line((b[0], b[1], b[0], y + 7), fill='#888888', width=1)
                draw.line((x1, y, x2, y), fill='black', width=1)
                draw.line((x1 - 5, y + 5, x1 + 5, y - 5), fill='black', width=2)
                draw.line((x2 - 5, y + 5, x2 + 5, y - 5), fill='black', width=2)
                annotations.append(('text', ((x1 + x2) / 2, y - 3), label))
            else:
                x = ox - 40 - vertical * 38
                vertical += 1
                y1, y2 = sorted((a[1], b[1]))
                draw.line((a[0], a[1], x - 7, a[1]), fill='#888888', width=1)
                draw.line((b[0], b[1], x - 7, b[1]), fill='#888888', width=1)
                draw.line((x, y1, x, y2), fill='black', width=1)
                draw.line((x - 5, y1 + 5, x + 5, y1 - 5), fill='black', width=2)
                draw.line((x - 5, y2 + 5, x + 5, y2 - 5), fill='black', width=2)
                # Vertical labels live in separate columns, rotated as on drawings.
                tile = Image.new('RGBA', (small.getlength(label).__ceil__() + 12, 32), 'white')
                ImageDraw.Draw(tile).text((5, 1), label, font=small, fill='black')
                tile = tile.rotate(90, expand=True)
                annotations.append(('tile', (round(x - 30), round((y1 + y2 - tile.height) / 2)), tile))
        for kind, position, content in annotations:
            if kind == 'tile':
                image.paste(content, position, content)
            else:
                draw.rectangle(draw.textbbox(position, content, font=small, anchor='mb'), fill='white')
                draw.text(position, content, fill='black', font=small, anchor='mb')
    y = cell_h * 2 + 145
    for description in descriptions:
        draw.text((40, y), description, fill='black', font=regular)
        y += 32
    output = BytesIO()
    image.save(output, format='PNG', optimize=True)
    if output.tell() > 10 * 1024 * 1024:
        raise GeometryError('Чертёж превышает размер одного изображения Telegram.')
    return output.getvalue()
