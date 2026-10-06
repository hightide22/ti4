"""Shared visual language for the Command interface concept."""
import arcade

VARIANT = 'Command'
FONT = 'Segoe UI'
BG = (8, 14, 24)
PANEL = (17, 27, 40)
SHELL = (12, 21, 33)
CARD = (26, 40, 56)
INK = (235, 242, 248)
MUTED = (164, 181, 198)
ACCENT = (104, 219, 203)
GOLD = (245, 196, 115)
BORDER = (57, 78, 98)
SELECTED = (36, 70, 78)
DISABLED = (33, 42, 53)
PRIMARY = (101, 214, 197)
PRIMARY_TEXT = (9, 35, 37)
DANGER = (255, 159, 145)
SIDEBAR_WIDTH = 388
ROSTER_WIDTH = 216
DASHBOARD_HEIGHT = 172
ROW_HEIGHT = 48


def surface(left, right, bottom, top, color=CARD, border=BORDER):
    arcade.draw_lrbt_rectangle_filled(left, right, bottom, top, color)
    if border:
        arcade.draw_lrbt_rectangle_outline(left, right, bottom, top, border, 1)


def button(w, key, label, x, y, width, height=36, *, primary=False,
           selected=False, enabled=True, size=11):
    mouse = getattr(w, 'mouse_position', (-1, -1))
    hover = enabled and x <= mouse[0] <= x + width and y <= mouse[1] <= y + height
    color = DISABLED if not enabled else PRIMARY if primary else SELECTED if hover or selected else CARD
    border = ACCENT if hover or selected else BORDER
    surface(x, x + width, y, y + height, color, border)
    text_color = MUTED if not enabled else PRIMARY_TEXT if primary else INK
    w.text(key, label, x + 10, y + (height - size * 1.35) / 2 + 2,
           size, text_color, max_width=width - 20)


def modal(left, right, bottom, top):
    arcade.draw_lrbt_rectangle_filled(left - 7, right + 7, bottom - 9, top + 5, (0, 0, 0, 65))
    surface(left, right, bottom, top, PANEL)
    arcade.draw_lrbt_rectangle_filled(left, right, top - 3, top, ACCENT)
    arcade.draw_line(left + 20, top - 75, right - 20, top - 75, BORDER, 1)


def meter(x, y, width, used, total, color=ACCENT):
    arcade.draw_lrbt_rectangle_filled(x, x + width, y, y + 4, BORDER)
    fraction = min(1, max(0, used / total)) if total else 0
    if fraction:
        arcade.draw_lrbt_rectangle_filled(x, x + width * fraction, y, y + 4, color)
