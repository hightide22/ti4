"""Research dialog and faction unit sheet, using the bundled technology cards."""
from __future__ import annotations

import textwrap

import arcade

from technology import (available_technologies, is_unit_upgrade, missing_prerequisites,
                        technology_catalog, technology_image, unit_stats, unit_upgrade)
from units import UNIT_TYPES


INK = (225, 235, 246)
MUTED = (137, 155, 180)
ACCENT = (100, 207, 224)
GOLD = (245, 194, 103)
GREEN = (126, 220, 166)
CARD = (23, 39, 57)
COLORS = {'PROPULSION': (111, 174, 239), 'BIOTIC': (112, 207, 150),
          'WARFARE': (235, 119, 130), 'CYBERNETIC': (241, 205, 102)}
TABS = ('UNITS', 'PROPULSION', 'BIOTIC', 'WARFARE', 'CYBERNETIC')


class TechnologyPanel:
    def __init__(self):
        self.tab = 'UNITS'
        self.preview_alias = None
        self.planet_mode = 'resources'
        self.scroll = 0
        self.action_page = 0
        self.hits = []
        self.bounds = None
        self.list_bounds = None
        self.offset = [0.0, 0.0]
        self.textures = {}

    def hit_test(self, x, y):
        return next((action for action, left, right, bottom, top in reversed(self.hits)
                     if left <= x <= right and bottom <= y <= top), None)

    def drag_header(self, x, y):
        if self.bounds is None:
            return False
        left, right, bottom, top = self.bounds
        return left <= x <= min(right, left + 390) and top - 58 <= y <= top - 5

    def move(self, dx, dy):
        self.offset[0] += dx
        self.offset[1] += dy

    def scroll_by(self, delta):
        self.scroll = max(0, self.scroll + int(delta) * 45)

    def _image(self, path, x, y, max_width, max_height):
        if path is None or not path.is_file():
            return
        texture = self.textures.get(path)
        if texture is None:
            texture = arcade.load_texture(path)
            self.textures[path] = texture
        ratio = min(max_width / texture.width, max_height / texture.height)
        arcade.draw_texture_rect(texture, arcade.XYWH(x, y, texture.width * ratio,
                                                       texture.height * ratio))

    def _button(self, w, action, label, x, y, width, height=30, enabled=True, selected=False):
        background = (36, 83, 91) if selected else (28, 65, 77) if enabled else (27, 36, 48)
        arcade.draw_lrbt_rectangle_filled(x, x + width, y, y + height, background)
        arcade.draw_lrbt_rectangle_outline(x, x + width, y, y + height,
                                            ACCENT if selected else (67, 109, 127), 1)
        w.text(('technology_button', action), label, x + 8, y + (height - 11) / 2 + 1,
               9, INK if enabled else MUTED, width - 16)
        if enabled:
            self.hits.append((action, x, x + width, y, y + height))

    @staticmethod
    def _summary(profile):
        values = []
        for key, label in (('cost', 'Cost'), ('combatHitsOn', 'Combat'),
                           ('moveValue', 'Move'), ('capacityValue', 'Capacity')):
            value = profile.get(key)
            if value is not None:
                values.append(f'{label} {value}{"+" if key == "combatHitsOn" else ""}')
        for key, label in (('spaceCannonHitsOn', 'Cannon'), ('bombardHitsOn', 'Bombard'),
                           ('afbHitsOn', 'Barrage'), ('productionValue', 'Production')):
            value = profile.get(key)
            if value is not None:
                values.append(f'{label} {value}{"+" if key != "productionValue" else ""}')
        if profile.get('sustainDamage'):
            values.append('Sustain Damage')
        return '  ·  '.join(values)

    @staticmethod
    def _row_summary(profile):
        values = []
        for key, label in (('cost', 'Cost'), ('combatHitsOn', 'Combat'),
                           ('moveValue', 'Move'), ('capacityValue', 'Capacity')):
            value = profile.get(key)
            if value is not None:
                values.append(f'{label} {value}{"+" if key == "combatHitsOn" else ""}')
        return '  ·  '.join(values)

    def _rows(self, player):
        if self.tab == 'UNITS':
            return [(kind, unit_upgrade(player, kind)) for kind in UNIT_TYPES]
        return [(tech['alias'], tech) for tech in available_technologies(player)
                if self.tab in tech.get('types', ()) and not is_unit_upgrade(tech)]

    def draw(self, w, player, controller=None):
        self.hits.clear()
        width = min(1080, w.width - 48)
        height = min(740, w.height - w.player_panel.HEIGHT - 24)
        base_x = (w.width - width) / 2
        base_y = w.player_panel.HEIGHT + (w.height - w.player_panel.HEIGHT - height) / 2
        x = max(0, min(w.width - width, base_x + self.offset[0]))
        bottom = max(w.player_panel.HEIGHT, min(w.height - height, base_y + self.offset[1]))
        self.offset[:] = [x - base_x, bottom - base_y]
        top = bottom + height
        self.bounds = (x, x + width, bottom, top)
        arcade.draw_lrbt_rectangle_filled(0, w.width, w.player_panel.HEIGHT, w.height,
                                           (3, 8, 17, 211))
        arcade.draw_lrbt_rectangle_filled(x, x + width, bottom, top, (13, 24, 39))
        arcade.draw_lrbt_rectangle_outline(x, x + width, bottom, top, (77, 146, 164), 2)
        research = bool(controller and controller.session and controller.session.stage == 'technology')
        s = controller.session if research else None
        title = f'{player.faction.upper()} · TECHNOLOGY' if not research else \
                f'{player.faction.upper()} · RESEARCH TECHNOLOGY'
        w.text('technology_title', title, x + 24, top - 35, 17, ACCENT)
        if research:
            phase = 'PRIMARY' if s.primary else 'BRILLIANT' if s.technology_brilliant else 'SECONDARY'
            w.text('technology_phase', f'{phase} · research {s.technology_count + 1}',
                   x + 24, top - 57, 10, GOLD)
        else:
            ending_turn = w.technology_actions.end_turn_player is player
            self._button(w, ('finish_turn',) if ending_turn else ('close',),
                         'FINISH TURN' if ending_turn else 'CLOSE',
                         x + width - (151 if ending_turn else 100), top - 54,
                         127 if ending_turn else 77, 29)

        tab_y = top - 100
        tab_width = (width - 48 - 32) / 5
        for index, tab in enumerate(TABS):
            tab_x = x + 24 + index * (tab_width + 8)
            self._button(w, ('tab', tab), tab, tab_x, tab_y, tab_width, 29,
                         selected=self.tab == tab)

        left = x + 24
        list_width = min(420, width * .41)
        right = left + list_width + 22
        right_width = x + width - right - 24
        list_top = tab_y - 22
        list_bottom = bottom + (65 if research else 27)
        self.list_bounds = (left, left + list_width, list_bottom, list_top)
        rows = self._rows(player)
        row_height = 48
        self.scroll = min(self.scroll, max(0, len(rows) * row_height - (list_top - list_bottom)))
        old_scissor = w.ctx.scissor
        w.ctx.scissor = (int(left), int(list_bottom), int(list_width), int(list_top - list_bottom))
        try:
            for index, (kind_or_alias, tech) in enumerate(rows):
                y = list_top - 48 - index * row_height + self.scroll
                if y + row_height < list_bottom or y > list_top:
                    continue
                alias = tech['alias'] if tech else None
                selected = (alias and self.preview_alias == alias) or \
                           (self.tab == 'UNITS' and self.preview_alias == kind_or_alias)
                owned = alias in player.technologies if alias else False
                fill = (30, 66, 69) if selected else (24, 55, 53) if owned else CARD
                arcade.draw_lrbt_rectangle_filled(left, left + list_width, y, y + 44, fill)
                arcade.draw_lrbt_rectangle_outline(left, left + list_width, y, y + 44,
                                                    GREEN if owned else ACCENT if selected else (49, 77, 98), 1)
                if self.tab == 'UNITS':
                    kind = kind_or_alias
                    profile = unit_stats(player, kind)
                    w.player_panel.image(f'units/{player.color_code}_{UNIT_TYPES[kind]["sprite"]}.png',
                                         left + 22, y + 23, 26)
                    w.text(('tech_unit_name', kind), UNIT_TYPES[kind]['name'], left + 43,
                           y + 27, 11, INK)
                    w.text(('tech_unit_stat', kind), self._row_summary(profile), left + 43,
                           y + 10, 8, MUTED, list_width - 110)
                    label = 'II' if owned else 'I' if tech else '—'
                else:
                    self._image(technology_image(tech), left + 20, y + 22, 27, 36)
                    w.text(('tech_name', alias), tech['name'], left + 42, y + 27,
                           11, INK, list_width - 100)
                    w.text(('tech_requirement', alias),
                           f'{tech.get("requirements", "No prerequisites")} · {tech["source"].upper()}',
                           left + 42, y + 10, 8, MUTED)
                    label = 'OWNED' if owned else 'READY'
                w.text(('tech_row_status', kind_or_alias), label,
                       left + list_width - 60, y + 24, 8, GREEN if owned else MUTED)
                hit = ('unit', kind_or_alias) if self.tab == 'UNITS' else ('select', alias)
                self.hits.append((hit, left, left + list_width,
                                  max(y, list_bottom), min(y + 44, list_top)))
        finally:
            w.ctx.scissor = old_scissor
        if self.scroll:
            w.text('tech_scroll_hint', 'Scroll for more', left + 4, list_bottom - 17, 8, MUTED)

        selected_tech = None
        selected_kind = None
        if self.tab == 'UNITS':
            selected_kind = self.preview_alias if self.preview_alias in UNIT_TYPES else 'carrier'
            selected_tech = unit_upgrade(player, selected_kind)
        elif self.preview_alias in technology_catalog():
            tech = technology_catalog()[self.preview_alias]
            if tech in available_technologies(player) and self.tab in tech.get('types', ()):
                selected_tech = tech
        if selected_tech:
            self._image(technology_image(selected_tech), right + min(116, right_width * .22),
                        list_top - 132, min(235, right_width * .43), 225)
            detail_x = right + min(244, right_width * .47)
            w.text('tech_detail_name', selected_tech['name'], detail_x, list_top - 38,
                   14, GOLD, right + right_width - detail_x)
            w.text('tech_detail_prerequisites',
                   f'Requires: {selected_tech.get("requirements", "none")}  ·  '
                   f'{"Researched" if selected_tech["alias"] in player.technologies else "Not researched"}',
                   detail_x, list_top - 63, 10, MUTED, right + right_width - detail_x)
            lines = textwrap.wrap(selected_tech.get('text', '').replace('\n', ' '),
                                  width=max(25, int((right + right_width - detail_x) / 6)))
            for index, line in enumerate(lines[:8]):
                w.text(('tech_detail_text', index), line, detail_x,
                       list_top - 88 - 15 * index, 9, INK, right + right_width - detail_x)
            if selected_kind:
                profile = unit_stats(player, selected_kind)
                w.text('tech_current_stats', f'CURRENT · {profile["name"]}', right,
                       list_top - 264, 10, GREEN if selected_tech['alias'] in player.technologies else ACCENT)
                w.text('tech_current_values', self._summary(profile), right,
                       list_top - 282, 9, INK, right_width)
        elif selected_kind:
            profile = unit_stats(player, selected_kind)
            self._image(technology_image(profile), right + 115, list_top - 125, 215, 225)
            w.text('tech_no_upgrade', f'{UNIT_TYPES[selected_kind]["name"]} · No upgrade card',
                   right + 240, list_top - 40, 13, INK, right_width - 250)
            w.text('tech_current_values', self._summary(profile), right,
                   list_top - 264, 10, INK, right_width)
        else:
            w.text('tech_choose', 'Choose a technology to inspect its card and requirements.',
                   right, list_top - 38, 11, MUTED, right_width)

        if research:
            cost = controller.technology_cost()
            paid = controller.technology_payment()
            selected = s.technology_selected == (selected_tech['alias'] if selected_tech else None)
            status = f'Resources selected: {paid}/{cost}  ·  Trade goods: {s.trade_goods}/{player.trade_goods}'
            w.text('tech_payment', status, right, bottom + 191, 11, GOLD, right_width)
            w.text('tech_planet_help', 'Click planet cards below for resources or technology specialties.',
                   right, bottom + 170, 9, MUTED, right_width)
            self._button(w, ('mode', 'resources'), 'PAY RESOURCES', right, bottom + 127,
                         141, selected=self.planet_mode == 'resources')
            self._button(w, ('mode', 'specialty'), 'USE SPECIALTY', right + 149,
                         bottom + 127, 141, selected=self.planet_mode == 'specialty')
            self._button(w, ('goods', -1), '−', right + 305, bottom + 127, 35,
                         enabled=s.trade_goods > 0)
            self._button(w, ('goods', 1), '+', right + 346, bottom + 127, 35,
                         enabled=s.trade_goods < player.trade_goods)
            if selected_tech:
                specialties = [card for card in player.planets
                               if card.planet.planet_id in s.technology_planets]
                shortfall = missing_prerequisites(player, selected_tech, specialties,
                                                  s.technology_use_aida)
                w.text('tech_missing', f'Unmet prerequisites: {shortfall}', right,
                       bottom + 109, 10, GREEN if shortfall == 0 else GOLD)
            else:
                shortfall = 1
            if 'aida' in player.technologies and 'aida' not in player.exhausted_technologies \
                    and selected_tech and is_unit_upgrade(selected_tech):
                self._button(w, ('aida',), 'USE AI DEVELOPMENT ALGORITHM', right,
                             bottom + 75, min(285, right_width), 26, selected=s.technology_use_aida)
            can_research = bool(selected and selected_tech and shortfall == 0 and paid >= cost)
            self._button(w, ('research',), 'RESEARCH', right, bottom + 27,
                         right_width * .55 - 5, 34, can_research)
            self._button(w, ('skip',), 'SKIP RESEARCH', right + right_width * .55 + 5,
                         bottom + 27, right_width * .45 - 5, 34)
        elif selected_tech and selected_tech['alias'] == 'td' and 'td' in player.technologies:
            state = w.technology_actions.transit
            choosing_planet = bool(state and state.selected_unit)
            options = (w.technology_actions.transit_planets(player) if choosing_planet else
                       w.technology_actions.transit_units(player))
            w.text('tech_action_heading',
                   f'TRANSIT DIODES · {len(state.moved_ids) if state else 0}/4 moved',
                   right, bottom + 232, 10, ACCENT)
            w.text('tech_transit_help',
                   'Choose a destination planet.' if choosing_planet else
                   'Choose up to 4 ground forces.', right, bottom + 210, 9, MUTED)
            pages = max(1, (len(options) + 2) // 3)
            self.action_page = min(self.action_page, pages - 1)
            for index, (target_id, label) in enumerate(
                    options[self.action_page * 3:self.action_page * 3 + 3]):
                self._button(w, ('transit_planet' if choosing_planet else 'transit_unit', target_id),
                             label, right, bottom + 168 - index * 37, right_width, 30,
                             enabled=choosing_planet or not state or len(state.moved_ids) < 4)
            if pages > 1:
                self._button(w, ('action_page', -1), 'PREV', right, bottom + 47, 68, 25,
                             enabled=self.action_page > 0)
                w.text('tech_action_page', f'{self.action_page + 1}/{pages}',
                       right + 75, bottom + 57, 9, MUTED)
                self._button(w, ('action_page', 1), 'NEXT', right + 112,
                             bottom + 47, 68, 25, enabled=self.action_page + 1 < pages)
            if choosing_planet:
                self._button(w, ('transit_back',), 'BACK TO UNITS', right,
                             bottom + 10, right_width, 30)
            elif state and state.moved_ids:
                self._button(w, ('transit_finish',), 'FINISH TRANSIT', right,
                             bottom + 10, right_width, 30)
        elif selected_tech and selected_tech['alias'] in ('pa', 'sr', 'x89_base', 'pm', 'bs', 'pi'):
            alias = selected_tech['alias']
            targets = w.technology_actions.targets(player, alias)
            w.text('tech_action_heading', 'AVAILABLE USES', right, bottom + 232, 10, ACCENT)
            if not targets:
                w.text('tech_action_empty', 'No eligible target right now.', right,
                       bottom + 206, 10, MUTED, right_width)
            else:
                pages = max(1, (len(targets) + 3) // 4)
                self.action_page = min(self.action_page, pages - 1)
                for index, (target_id, label) in enumerate(
                        targets[self.action_page * 4:self.action_page * 4 + 4]):
                    self._button(w, ('use', alias, target_id), label, right,
                                 bottom + 188 - index * 36, right_width, 30)
                if pages > 1:
                    w.text('tech_action_page', f'{self.action_page + 1}/{pages}',
                           right + 74, bottom + 18, 10, MUTED)
                    self._button(w, ('action_page', -1), 'PREV', right,
                                 bottom + 10, 68, 28, enabled=self.action_page > 0)
                    self._button(w, ('action_page', 1), 'NEXT', right + 112,
                                 bottom + 10, 68, 28, enabled=self.action_page + 1 < pages)
