"""Small, keyboard-free setup screen for selecting a starter galaxy and factions."""
from __future__ import annotations

import arcade

from board import RESOURCES


FACTIONS = (
    ('sol', 'Federation of Sol', 'orbital_drop'),
    ('jolnar', 'Universities of Jol-Nar', 'fragile'),
    ('letnev', 'Barony of Letnev', 'armada'),
    ('hacan', 'Emirates of Hacan', 'guild_ships'),
)
FACTION_NAMES = {alias: name for alias, name, _ in FACTIONS}
FACTION_ICONS = {alias: f'factions/{alias}.png' for alias, _, _ in FACTIONS}
MAPS = {
    3: ('Official Starter Galaxy', 'Three-player map · 28 systems'),
    4: ('Four-Player Galaxy', 'Four-player map · 37 systems'),
}
PANEL = (14, 23, 38)
CARD = (19, 33, 50)
INK = (223, 232, 244)
MUTED = (130, 151, 177)
ACCENT = (100, 207, 224)
GOLD = (245, 194, 103)


class MainMenu:
    def __init__(self):
        self.player_count = 3
        self.factions = ['sol', 'jolnar', 'hacan', 'letnev']
        self.vs_ai = False
        self.human_slot = 0
        self.hits = []
        self.textures = {}

    @property
    def active_factions(self):
        return tuple(self.factions[:self.player_count])

    def select_faction(self, slot, direction):
        if slot < 0 or slot >= self.player_count:
            return
        aliases = [alias for alias, _, _ in FACTIONS]
        index = aliases.index(self.factions[slot])
        choice = aliases[(index + direction) % len(aliases)]
        # Keep the inactive fourth slot unique too, so switching from three to
        # four players can never leave a duplicated faction behind.
        occupied = next((other for other in range(len(self.factions))
                         if other != slot and self.factions[other] == choice), None)
        if occupied is None:
            self.factions[slot] = choice
        else:
            self.factions[slot], self.factions[occupied] = self.factions[occupied], self.factions[slot]

    def hit_test(self, x, y):
        return next((action for action, left, right, bottom, top in reversed(self.hits)
                     if left <= x <= right and bottom <= y <= top), None)

    def _hit(self, action, left, bottom, width, height):
        self.hits.append((action, left, left + width, bottom, bottom + height))

    def _image(self, relative_path, x, y, size):
        path = RESOURCES / relative_path
        texture = self.textures.get(path)
        if texture is None:
            texture = arcade.load_texture(path)
            self.textures[path] = texture
        rect = arcade.XYWH(x, y, size * texture.width / max(texture.width, texture.height),
                           size * texture.height / max(texture.width, texture.height))
        arcade.draw_texture_rect(texture, rect)

    def draw(self, window):
        self.hits.clear()
        width = min(920, window.width - 36)
        height = min(680, window.height - 36)
        left, bottom = (window.width - width) / 2, (window.height - height) / 2
        top = bottom + height
        arcade.draw_lrbt_rectangle_filled(0, window.width, 0, window.height, (5, 10, 18))
        arcade.draw_lrbt_rectangle_filled(left, left + width, bottom, top, PANEL)
        arcade.draw_lrbt_rectangle_outline(left, left + width, bottom, top, (67, 133, 154), 2)
        window.text('menu_title', 'TWILIGHT IMPERIUM IV', left + 30, top - 47, 24, ACCENT)
        window.text('menu_subtitle', 'NEW GALAXY · CHOOSE A MAP AND FACTIONS',
                    left + 32, top - 75, 10, MUTED)

        map_top = top - 110
        gap = 14
        map_width = (width - 60 - gap) / 2
        map_height = 106
        for index, count in enumerate((3, 4)):
            x = left + 30 + index * (map_width + gap)
            y = map_top - map_height
            selected = self.player_count == count
            color = (27, 58, 73) if selected else CARD
            border = ACCENT if selected else (51, 76, 99)
            arcade.draw_lrbt_rectangle_filled(x, x + map_width, y, map_top, color)
            arcade.draw_lrbt_rectangle_outline(x, x + map_width, y, map_top, border, 2 if selected else 1)
            name, subtitle = MAPS[count]
            window.text(('menu_map_name', count), name.upper(), x + 16, map_top - 30,
                        13, ACCENT if selected else INK)
            window.text(('menu_map_subtitle', count), subtitle, x + 16, map_top - 54,
                        10, MUTED)
            window.text(('menu_map_select', count), 'SELECTED' if selected else 'SELECT',
                        x + 16, y + 13, 9, ACCENT if selected else GOLD)
            self._hit(('map', count), x, y, map_width, map_height)

        section_y = map_top - map_height - 32
        window.text('menu_factions_title', 'FACTIONS · EACH FACTION CAN BE USED ONCE',
                    left + 32, section_y, 10, MUTED)
        row_height, row_gap = 57, 7
        row_top = section_y - 13
        row_width = width - 60
        for slot in range(4):
            top_y = row_top - slot * (row_height + row_gap)
            row_bottom = top_y - row_height
            active = slot < self.player_count
            human = active and self.vs_ai and slot == self.human_slot
            background = (27, 58, 73) if human else CARD if active else (15, 23, 35)
            outline = ACCENT if human else (51, 76, 99) if active else (35, 45, 59)
            arcade.draw_lrbt_rectangle_filled(left + 30, left + 30 + row_width,
                                               row_bottom, top_y, background)
            arcade.draw_lrbt_rectangle_outline(left + 30, left + 30 + row_width,
                                                row_bottom, top_y, outline, 1)
            if active:
                alias = self.factions[slot]
                self._image(FACTION_ICONS[alias], left + 63, row_bottom + row_height / 2, 36)
                role = ('YOU' if slot == self.human_slot else 'AI') if self.vs_ai else f'PLAYER {slot + 1}'
                window.text(('menu_player_slot', slot), role, left + 93,
                            top_y - 18, 8, ACCENT if self.vs_ai and slot == self.human_slot else MUTED)
                window.text(('menu_faction_name', slot), FACTION_NAMES[alias], left + 93,
                            row_bottom + 14, 12, INK)
                bx = left + 30 + row_width - 80
                for direction, label, x in ((-1, '‹', bx), (1, '›', bx + 38)):
                    arcade.draw_lrbt_rectangle_filled(x, x + 32, row_bottom + 11,
                                                       row_bottom + 43, (29, 52, 70))
                    arcade.draw_lrbt_rectangle_outline(x, x + 32, row_bottom + 11,
                                                        row_bottom + 43, (70, 115, 140), 1)
                    window.text(('menu_faction_cycle', slot, direction), label,
                                x + 11, row_bottom + 18, 16, ACCENT)
                    self._hit(('faction', slot, direction), x, row_bottom + 11, 32, 32)
                self._hit(('human_slot', slot), left + 30, row_bottom, row_width - 90, row_height)
            else:
                window.text(('menu_player_slot', slot), f'PLAYER {slot + 1}', left + 48,
                            top_y - 21, 8, (93, 106, 124))
                window.text(('menu_faction_name', slot), 'Not in this game', left + 48,
                            row_bottom + 14, 11, (93, 106, 124))

        ai_bottom = row_top - 4 * (row_height + row_gap) - 28
        box_left = left + 32
        arcade.draw_lrbt_rectangle_filled(box_left, box_left + 22, ai_bottom, ai_bottom + 22,
                                           (31, 78, 83) if self.vs_ai else CARD)
        arcade.draw_lrbt_rectangle_outline(box_left, box_left + 22, ai_bottom, ai_bottom + 22,
                                            ACCENT, 2)
        if self.vs_ai:
            window.text('menu_ai_check', '✓', box_left + 4, ai_bottom + 3, 14, ACCENT)
        window.text('menu_ai_label', 'PLAY AGAINST AI · CLICK YOUR FACTION ABOVE',
                    box_left + 34, ai_bottom + 6, 11, INK if self.vs_ai else MUTED)
        self._hit(('toggle_ai',), box_left, ai_bottom, width - 64, 24)

        start_bottom = bottom + 24
        start_width, start_height = 224, 42
        start_left = left + (width - start_width) / 2
        arcade.draw_lrbt_rectangle_filled(start_left, start_left + start_width,
                                           start_bottom, start_bottom + start_height, (31, 78, 83))
        arcade.draw_lrbt_rectangle_outline(start_left, start_left + start_width,
                                            start_bottom, start_bottom + start_height, ACCENT, 1)
        window.text('menu_start', 'START GAME', start_left + 58, start_bottom + 14, 12, INK)
        self._hit(('start',), start_left, start_bottom, start_width, start_height)
