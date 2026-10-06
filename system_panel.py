from __future__ import annotations

import math
from dataclasses import dataclass

import arcade
from ui_theme import (CARD, INK, MUTED, ACCENT, BORDER, SELECTED, DISABLED, DANGER, ROW_HEIGHT,
                      surface)

from units import UNIT_TYPES, Unit, system_inventory, unit_profile

PLANET_TYPES = {"CULTURAL": "Cultural", "INDUSTRIAL": "Industrial", "HAZARDOUS": "Hazardous", "FACTION": "Homeworld", "MR": "Mecatol Rex"}
ORDER = ("warsun", "flagship", "dreadnought", "carrier", "cruiser", "destroyer", "fighter", "infantry", "mech", "pds", "spacedock")


@dataclass
class InventoryHit:
    units: tuple[Unit, ...]
    left: float
    right: float
    bottom: float
    top: float


class SystemPanel:
    def __init__(self):
        self.scroll = 0
        self.scroll_max = 0
        self.hits = []
        self.viewport = (0, 0, 0, 0)
        self.remove_token_hit = None
        self.add_token_hit = None
        self.strategy_tab_hit = None

    def reset(self):
        self.scroll = 0

    def clamp(self):
        self.scroll = max(0, min(self.scroll, self.scroll_max))

    def hit_test(self, x, y):
        vx, vy, vw, vh = self.viewport
        if not (vx <= x <= vx + vw and vy <= y <= vy + vh):
            return None
        return next((h for h in self.hits if h.left <= x <= h.right and h.bottom <= y <= h.top), None)

    def draw_counts(self, window, key, groups, x, y, width):
        if not groups:
            window.text((key, 'empty'), 'No units', x + 12, y - 21, 12, MUTED)
            return y - 36
        kinds = [kind for kind in ORDER if kind in groups]
        stride = ROW_HEIGHT - 8
        for index, kind in enumerate(kinds):
            top = y - index * stride
            members = tuple(groups[kind])
            selected = any(u.unit_id in window.selected_units for u in members)
            surface(x, x + width, top - (stride - 6), top, SELECTED if selected else CARD, ACCENT if selected else None)
            unit = members[0]
            window.player_panel.image(f'units/{unit.color_code}_{UNIT_TYPES[kind]["sprite"]}.png',
                                      x + 21, top - 17, 26)
            label = 'Space dock' if kind == 'spacedock' else UNIT_TYPES[kind]['name']
            window.text((key, kind, 'name'), label, x + 45, top - 22, 11, INK, max_width=width - 94)
            window.text((key, kind, 'count'), f'×{len(members)}', x + width - 43, top - 23, 14, ACCENT)
            self.hits.append(InventoryHit(members, x, x + width, top - (stride - 6), top))
        return y - len(kinds) * stride

    def draw(self, window, tile, left):
        self.remove_token_hit = None
        self.add_token_hit = None
        px, width = left + 18, window.sidebar - 36
        self.strategy_tab_hit = (px + width - 90, px + width,
                                 window.height - 69, window.height - 43)
        text = window.text
        text('system_label', 'SELECTED SYSTEM', px, window.height - 26, 10, MUTED)
        name = tile.name.split(' - ')[0]
        text('system_name', name, px, window.height - 62, 17, INK, max_width=width - 108)
        arcade.draw_lrbt_rectangle_filled(self.strategy_tab_hit[0], self.strategy_tab_hit[1],
                                           self.strategy_tab_hit[2], self.strategy_tab_hit[3], SELECTED)
        text('strategy_tab', 'STRATEGY', px + width - 83, window.height - 61, 9, ACCENT)
        shields = list(dict.fromkeys(unit.owner.upper() for unit in tile.units if unit.kind == 'pds' and
                                     unit_profile(unit).get('planetaryShield')))
        shield_text = f' · PLANETARY SHIELD: {", ".join(shields)}' if shields else ''
        text('system_id', f'Tile {tile.system_id} / {len(tile.units)} units{shield_text}',
             px, window.height - 88, 10, MUTED, max_width=width)
        system_owners = list(dict.fromkeys(tile.planet_owners.values()))
        owner_text = ', '.join(faction.upper() for faction in system_owners) if system_owners else 'None'
        text('owner', f'SYSTEM CONTROL: {owner_text}', px, window.height - 111, 10, ACCENT if system_owners else MUTED, width)
        token_players = [player for player in window.player_panel.players if player.faction in tile.command_tokens]
        if token_players:
            text('token_info_title', 'COMMAND TOKENS', px, window.height - 143, 9, MUTED)
            cell_width = width / 3
            for index, player in enumerate(token_players):
                row, column = divmod(index, 3)
                x, y = px + column * cell_width, window.height - 165 - row * 20
                window.player_panel.image(f'command_token/command_{player.color_code}.png', x + 9, y + 6, 15)
                window.player_panel.image(f'factions/{player.faction}.png', x + 9, y + 6, 8)
                text(('token_info', player.faction), player.faction.upper(), x + 20, y + 2, 9, INK, cell_width - 22)
        else:
            text('token_info_title', 'COMMAND TOKENS', px, window.height - 143, 9, MUTED)
            text('token_info_empty', 'None', px + width - 43, window.height - 143, 9, MUTED)
        if window.token_context and window.token_context[0] == tile.position:
            faction = window.token_context[1]
            if faction in tile.command_tokens:
                label = f'Remove {faction.upper()} token · debug'
                bx, by, bw, bh = px, window.height - 201, width, 23
                arcade.draw_lrbt_rectangle_filled(bx, bx + bw, by, by + bh, SELECTED)
                arcade.draw_lrbt_rectangle_outline(bx, bx + bw, by, by + bh, DANGER, 1)
                text('remove_token_button', label, bx + 8, by + 6, 10, INK, bw - 16)
                self.remove_token_hit = (bx, bx + bw, by, by + bh)
            elif faction is None:
                player = window.player_panel.player
                available = player.faction not in tile.command_tokens and player.command_pools['tactical'] > 0
                label = (f'Add {player.faction.upper()} token · 1 Tactical' if available else
                         f'{player.faction.upper()} tactical reserve is empty' if player.command_pools['tactical'] <= 0 else
                         f'{player.faction.upper()} already has a token here')
                bx, by, bw, bh = px, window.height - 201, width, 23
                arcade.draw_lrbt_rectangle_filled(bx, bx + bw, by, by + bh,
                                                   SELECTED if available else DISABLED)
                arcade.draw_lrbt_rectangle_outline(bx, bx + bw, by, by + bh,
                                                    ACCENT if available else BORDER, 1)
                text('add_token_button', label, bx + 8, by + 6, 10, INK if available else MUTED, bw - 16)
                if available:
                    self.add_token_hit = (bx, bx + bw, by, by + bh)
        inventory = system_inventory(tile)
        top, bottom = window.height - 222, 100
        self.viewport = (int(left), bottom, int(window.sidebar), max(1, int(top - bottom)))
        self.hits.clear()
        previous_scissor = window.ctx.scissor
        window.ctx.scissor = self.viewport
        try:
            start = top - 21
            y = start + self.scroll
            text('fleet_title', 'SPACE FLEET', px, y, 12, ACCENT)
            y = self.draw_counts(window, 'fleet', inventory['fleet'], px, y - 15, width) - 10
            if inventory['cargo']:
                text('cargo_title', 'TRANSPORTED UNITS', px, y, 11, MUTED)
                y = self.draw_counts(window, 'cargo', inventory['cargo'], px, y - 15, width) - 10
            for planet in tile.planets:
                arcade.draw_line(px, y + 6, px + width, y + 6, BORDER, 1)
                text((planet.planet_id, 'name'), planet.name, px, y - 17, 15, INK, max_width=width - 106)
                label = PLANET_TYPES.get(planet.planet_type, planet.planet_type or '')
                text((planet.planet_id, 'type'), label, px + width - 92, y - 17, 10, MUTED)
                owner = tile.planet_owners.get(planet.planet_id)
                text((planet.planet_id, 'owner'), f'Owner: {owner.upper() if owner else "None"}', px, y - 37, 10, ACCENT if owner else MUTED)
                text((planet.planet_id, 'values'), f'Resources: {planet.resources}   Influence: {planet.influence}', px, y - 55, 11, ACCENT)
                y = self.draw_counts(window, planet.planet_id, inventory['planets'][planet.planet_id], px, y - 65, width) - 10
            if not tile.planets:
                text('no_planets', 'No planets in this system', px, y - 18, 13, MUTED)
                y -= 45
            if tile.wormholes:
                text('wormholes', 'Wormholes: ' + ', '.join(tile.wormholes), px, y - 18, 12, ACCENT)
                y -= 40
            self.scroll_max = max(0, start - (y - self.scroll) - (top - bottom) + 35)
            self.clamp()
        finally:
            window.ctx.scissor = previous_scissor
        if self.scroll_max:
            track_top = top - 5
            arcade.draw_line(window.width - 9, bottom, window.width - 9, track_top, BORDER, 2)
            thumb = max(35, (track_top - bottom) ** 2 / (track_top - bottom + self.scroll_max))
            thumb_top = track_top - self.scroll / self.scroll_max * (track_top - bottom - thumb)
            arcade.draw_lrbt_rectangle_filled(window.width - 12, window.width - 6, thumb_top - thumb, thumb_top, ACCENT)
        arcade.draw_line(px, 90, px + width, 90, BORDER, 1)
        picked = next((u for u in tile.units if u.unit_id in window.selected_units), None)
        if picked:
            location = next((p.name for p in tile.planets if p.planet_id == picked.location.planet_id), 'Space')
            count = sum(u.unit_id in window.selected_units for u in tile.units)
            text('selection', f'{UNIT_TYPES[picked.kind]["name"]}: {count} · {location}', px, 69, 11, ACCENT)
        else:
            text('selection_tip', 'Double-click a system to activate', px, 69, 11, MUTED)
        text('help', 'Space: detail · F: fit · Right drag: pan\nScroll here for system information', px, 37, 10, MUTED, width)
