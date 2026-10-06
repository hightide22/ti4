from __future__ import annotations

import math
import arcade
from ui_theme import (PANEL, CARD, INK, MUTED, ACCENT, GOLD, BORDER, SELECTED, ROW_HEIGHT,
                      surface, button, meter)

from units import UNIT_TYPES, Region, unit_profile

PRODUCTION_ORDER = ('infantry', 'fighter', 'destroyer', 'cruiser', 'carrier', 'dreadnought',
                    'mech', 'flagship', 'warsun')


def amount(value):
    return str(int(value)) if float(value).is_integer() else f'{value:g}'


class MovementPanel:
    def __init__(self):
        self.scroll = 0
        self.timeline_expanded = False
        self.timeline_rows = ()
        self.scroll_max = 0
        self.hits = []
        self.buttons = []
        self.viewport = (0, 0, 0, 0)
        self.source_hits = []
        self.route_hits = []
        self.hovered_source_position = None
        self.hovered_route_id = None

    def reset(self):
        self.scroll = 0
        self.hovered_source_position = None
        self.hovered_route_id = None

    def scroll_by(self, amount):
        self.scroll = max(0, min(self.scroll_max, self.scroll + amount))

    def hit_test(self, x, y):
        for action, left, right, bottom, top in self.buttons:
            if left <= x <= right and bottom <= y <= top:
                return action
        vx, vy, vw, vh = self.viewport
        if vx <= x <= vx + vw and vy <= y <= vy + vh:
            return next((action for action, left, right, bottom, top in self.hits
                         if left <= x <= right and bottom <= y <= top), None)
        return None

    def update_hover(self, x, y):
        self.hovered_source_position = None
        self.hovered_route_id = None
        vx, vy, vw, vh = self.viewport
        if not (vx <= x <= vx + vw and vy <= y <= vy + vh):
            return
        route_hit = next(((position, unit_id) for position, unit_id, left, right, bottom, top
                          in reversed(self.route_hits)
                          if left <= x <= right and bottom <= y <= top), None)
        if route_hit:
            self.hovered_source_position, self.hovered_route_id = route_hit
            return
        source_hit = next(((position) for position, left, right, bottom, top in reversed(self.source_hits)
                           if left <= x <= right and bottom <= y <= top), None)
        self.hovered_source_position = source_hit

    def preview_route(self, session):
        if not session or session.stage != 'movement' or self.hovered_source_position is None:
            return None
        source = session.sources.get(self.hovered_source_position)
        if not source or not source.ships:
            return None
        unit_id = self.hovered_route_id or source.ships[0].unit_id
        return source.routes.get(unit_id)

    def rows(self, window, session, source, units, x, y, width, passenger=False):
        cell = (width - 8) / 2
        counts = {}
        for index, unit in enumerate(units):
            row, col = divmod(index, 2)
            left, top = x + col * (cell + 8), y - row * ROW_HEIGHT
            selected = unit.unit_id in session.selected
            arcade.draw_lrbt_rectangle_filled(left, left + cell, top - (ROW_HEIGHT - 5), top,
                                             SELECTED if unit.unit_id == self.hovered_route_id else
                                             SELECTED if selected else CARD)
            if selected or unit.unit_id == self.hovered_route_id:
                arcade.draw_lrbt_rectangle_outline(left, left + cell, top - (ROW_HEIGHT - 5), top,
                                                   GOLD if unit.unit_id == self.hovered_route_id else ACCENT,
                                                   2 if unit.unit_id == self.hovered_route_id else 1)
            window.player_panel.image(f'units/{unit.color_code}_{UNIT_TYPES[unit.kind]["sprite"]}.png',
                                      left + 17, top - 21, 25)
            counts[unit.kind] = counts.get(unit.kind, 0) + 1
            name = f'{UNIT_TYPES[unit.kind]["name"]} {counts[unit.kind]}'
            if passenger:
                detail = next((p.name for p in source.tile.planets if p.planet_id == unit.location.planet_id), 'Space')
            else:
                detail = f'Move {unit.move_value} · Capacity {unit.capacity}'
            window.text(('movement_name', unit.unit_id), name, left + 33, top - 17, 11, INK)
            window.text(('movement_detail', unit.unit_id), detail, left + 33, top - 34, 10, MUTED)
            self.hits.append((('unit', unit.unit_id), left, left + cell, top - (ROW_HEIGHT - 5), top))
            if not passenger and unit.unit_id in source.routes:
                self.route_hits.append((source.tile.position, unit.unit_id,
                                        left, left + cell, top - (ROW_HEIGHT - 5), top))
        return y - math.ceil(len(units) / 2) * ROW_HEIGHT

    def timeline(self, window, session, x, y, width):
        stage = session.stage
        movement_done = stage != 'movement'
        invasion_done = stage == 'complete'
        ships_selected = any(session.ships(source) for source in session.sources.values())
        landing_assigned = any(planet_id is not None for planet_id in session.landings.values())
        has_forces = bool(session.landings)
        invasion_current = stage == 'invasion'
        ground_current = stage == 'ground_combat'
        overflow_current = stage == 'fleet_overflow'
        production_current = stage == 'production'
        overflow_after_move = overflow_current and session.overflow_next_stage == 'invasion'
        overflow_after_production = overflow_current and session.overflow_next_stage == 'complete'
        has_production = bool(window.movement.production_sites(session))
        combat_current = stage in ('space_combat', 'retreat_selection')
        combat_resolved = session.space_combat_resolved
        invasion_reached = stage in ('bombardment', 'invasion', 'ground_combat', 'production', 'complete') or overflow_after_production
        production_reached = production_current or stage == 'complete' or overflow_after_production
        rows = (
            ('STEP 1 · ACTIVATION', 'Done'),
            ('STEP 2 · MOVEMENT', 'In progress' if stage == 'movement' or overflow_after_move else 'Done' if movement_done else 'Waiting'),
            ('  Move Ships', ('Done' if ships_selected else 'Skipped') if movement_done else 'Current'),
            ('  Space Cannon Offense', 'Done' if session.cannon_log else 'Skipped' if session.cannon_checked else 'Wait'),
            ('STEP 3 · SPACE COMBAT', 'In progress' if combat_current else 'Done' if combat_resolved else 'Skipped' if movement_done else 'Wait'),
            ('STEP 4 · INVASION', 'Done' if production_reached else 'In progress' if stage in ('bombardment', 'ground_combat') or (invasion_current and has_forces) else 'Current' if invasion_current else 'Wait'),
            ('  Bombardment', 'Current' if stage == 'bombardment' else 'Done' if session.bombardment_resolved and session.bombard_log and not session.bombardment_cancelled else 'Skipped' if invasion_reached else 'Wait'),
            ('  Commit Ground Forces', ('Done' if landing_assigned else 'Skipped') if production_reached or ground_current else ('Current' if invasion_current and has_forces else 'Skipped' if invasion_current else 'Wait')),
            ('  Space Cannon Defense', 'Done' if session.defense_checked and session.defense_log else 'Skipped' if session.defense_checked else 'Wait'),
            ('  Ground Combat', 'In progress' if ground_current else 'Done' if session.ground_planets and production_reached else 'Wait' if not invasion_reached or (invasion_current and has_forces) else 'Skipped'),
            ('  Establish Control', ('Done' if landing_assigned else 'Skipped') if production_reached else 'Wait' if ground_current else 'Current' if invasion_current and not has_forces else 'Wait'),
            ('STEP 5 · PRODUCTION', 'In progress' if production_current or overflow_after_production else 'Done' if stage == 'complete' else 'Skipped' if production_reached and not has_production else 'Wait'),
            ('  Produce Units', 'In progress' if overflow_after_production else 'Done' if stage == 'complete' else 'Current' if production_current else 'Skipped' if production_reached and not has_production else 'Wait'),
        )
        self.timeline_rows = rows
        indices = range(len(rows)) if self.timeline_expanded else (0, 1, 4, 5, 11)
        row_height = 19 if self.timeline_expanded else 25
        for index in indices:
            label, status = rows[index]
            current = status in ('Current', 'In progress')
            if current:
                surface(x - 3, x + width, y - 6, y + 15, SELECTED, None)
                arcade.draw_lrbt_rectangle_filled(x - 3, x, y - 6, y + 15, GOLD)
            color = GOLD if current else ACCENT if status == 'Done' else MUTED
            label = label.replace('STEP ', '').replace(' · ', '  ')
            window.text(('action_step', index), label, x + 8, y, 10, color, max_width=width * .65)
            window.text(('action_status', index), status, x + width * .72, y, 9, color, max_width=width * .27)
            y -= row_height
        label = 'Hide step details' if self.timeline_expanded else 'Show all step details'
        button(window, 'timeline_toggle', label, x, y - 21, width, 28, size=10)
        self.buttons.append((('timeline',), x, x + width, y - 21, y + 7))
        return y - 44

    def draw(self, window, session, left):
        self.hits.clear()
        self.buttons.clear()
        self.source_hits.clear()
        self.route_hits.clear()
        x, width = left + 18, window.sidebar - 36
        target = session.target
        window.text('move_header', 'WARFARE · SECONDARY PRODUCTION' if session.strategic_production else
                    'ACTIVATED SYSTEM', x, window.height - 26, 10, ACCENT)
        window.text('move_target', target.name, x, window.height - 62, 14, INK, width)
        pool = 'strategic' if session.strategic_production else 'tactical'
        window.text('move_token', f'{session.player.faction.upper()} · {pool.title()} reserve: {session.player.command_pools[pool]}',
                    x, window.height - 90, 11, MUTED)
        bottom = 164
        content_y = (window.height - 122 if session.strategic_production else
                     self.timeline(window, session, x, window.height - 121, width))
        viewport_top = max(bottom + 55, content_y + 18)
        self.viewport = (int(left), bottom, int(window.sidebar), max(1, int(viewport_top - bottom)))
        old_scissor = window.ctx.scissor
        window.ctx.scissor = self.viewport
        start = viewport_top - 18
        y = start + self.scroll
        try:
            notices = []
            if session.cannon_log:
                notices.append(('SPACE CANNON', session.cannon_log[-1]))
            if session.afb_log:
                notices.append(('ANTI-FIGHTER BARRAGE', session.afb_log[-1]))
            if session.retreat_log:
                notices.append(('RETREAT', session.retreat_log))
            if session.bombard_log:
                notices.append(('BOMBARDMENT', session.bombard_log[-1]))
            if session.defense_log:
                notices.append(('PLANETARY DEFENSE', session.defense_log[-1]))
            for index, (label, detail) in enumerate(notices[-3:]):
                window.text(('action_result_label', index), label, x, y, 8, ACCENT, width)
                y -= 12
                window.text(('action_result_detail', index), detail, x, y, 9, INK, width)
                y -= 20
            if session.stage == 'movement':
                window.text('move_sources', f'SOURCES FOR THIS ACTIVATION · {len(session.sources)}', x, y, 11, ACCENT)
                y -= 28
                if not session.sources:
                    window.text('move_empty', 'No ships can reach this system', x, y - 15, 12, MUTED)
                    y -= 45
                for source in session.sources.values():
                    title = f'Tile {source.tile.system_id} · {source.tile.name.split(" - ")[0]}'
                    source_top = y + 25
                    hovered = self.hovered_source_position == source.tile.position
                    header_color = SELECTED if hovered else CARD
                    header_border = GOLD if hovered else BORDER
                    arcade.draw_lrbt_rectangle_filled(x - 8, x + width + 8, y - 7, y + 19,
                                                      header_color)
                    arcade.draw_lrbt_rectangle_outline(x - 8, x + width + 8, y - 7, y + 19,
                                                       header_border, 1.5 if hovered else 1)
                    window.text(('move_source', source.tile.position), title, x + 5, y, 13, INK, max_width=width - 10)
                    y = self.rows(window, session, source, source.ships, x, y - 17, width) - 8
                    used, capacity = session.cargo_values(source)
                    window.text(('move_capacity', source.tile.position),
                                (f'Cargo {used}/{capacity} / select passengers' if capacity else 'Select a transport to load cargo')
                                if source.passengers else f'Capacity {capacity} / no cargo available',
                                x, y - 14, 10, ACCENT, width)
                    y -= 27
                    if source.passengers:
                        y = self.rows(window, session, source, source.passengers, x, y, width, passenger=True)
                    carried = session.carried(source)
                    if carried:
                        window.text(('move_aboard', source.tile.position), f'Already aboard: {len(carried)} (included)', x, y - 16, 11, MUTED)
                        y -= 26
                    y -= 13
                    source_bottom = y - 8
                    arcade.draw_lrbt_rectangle_outline(x - 8, x + width + 8, source_bottom,
                                                       source_top, header_border if hovered else BORDER,
                                                       1.5 if hovered else 1)
                    self.source_hits.append((source.tile.position, x - 8, x + width + 8,
                                             source_bottom, source_top))
                    y = source_bottom - 22
            elif session.stage == 'bombardment':
                window.text('bombardment_title', 'BOMBARDMENT TARGETS', x, y, 11, ACCENT)
                y -= 23
                for unit_id, planet_id in session.bombard_targets.items():
                    ship = next((unit for unit in target.units if unit.unit_id == unit_id), None)
                    if ship is None:
                        continue
                    planet = next((planet for planet in target.planets if planet.planet_id == planet_id), None)
                    label = planet.name if planet else 'Skip'
                    top = y
                    arcade.draw_lrbt_rectangle_filled(x, x + width, top - 38, top, CARD)
                    window.player_panel.image(
                        f'units/{ship.color_code}_{UNIT_TYPES[ship.kind]["sprite"]}.png', x + 18, top - 19, 24)
                    profile = unit_profile(ship)
                    dice = int(profile.get('bombardDieCount') or 1)
                    hits_on = int(profile.get('bombardHitsOn') or 10)
                    window.text(('bombard_ship', unit_id), f'{UNIT_TYPES[ship.kind]["name"]} · {dice} die(s) · {hits_on}+',
                                x + 36, top - 13, 9, INK)
                    window.text(('bombard_target', unit_id), f'Target: {label}  ›', x + 36, top - 29, 9, ACCENT)
                    self.hits.append((('bombard', unit_id), x, x + width, top - 38, top))
                    y -= 44
                window.text('bombardment_help', 'Click a ship to cycle target planets, or leave it on Skip.',
                            x, y, 9, MUTED, width)
                y -= 22
            elif session.stage == 'invasion':
                forces = window.movement.landing_forces(session)
                window.text('landing_title', 'COMMIT GROUND FORCES', x, y, 11, ACCENT)
                y -= 28
                if not forces:
                    window.text('landing_empty', 'No transported infantry or mechs can land.', x, y - 10, 11, MUTED, width)
                    y -= 38
                for unit in forces:
                    planet_id = session.landings.get(unit.unit_id)
                    planet = next((p for p in target.planets if p.planet_id == planet_id), None)
                    label = planet.name if planet else 'Keep aboard'
                    top = y
                    arcade.draw_lrbt_rectangle_filled(x, x + width, top - 38, top, CARD)
                    window.player_panel.image(f'units/{unit.color_code}_{UNIT_TYPES[unit.kind]["sprite"]}.png', x + 17, top - 19, 24)
                    carrier = next((u for u in target.units if u.unit_id == unit.location.carrier_id), None)
                    window.text(('landing_unit', unit.unit_id), f'{UNIT_TYPES[unit.kind]["name"]} · {carrier.kind.title() if carrier else "Transport"}', x + 34, top - 14, 10, INK)
                    window.text(('landing_planet', unit.unit_id), f'Land on: {label}  ›', x + 34, top - 30, 10, ACCENT)
                    self.hits.append((('landing', unit.unit_id), x, x + width, top - 38, top))
                    y -= 44
                window.text('landing_help', 'Click a force to cycle through planets or keep it aboard.', x, y - 4, 10, MUTED, width)
                y -= 25
            elif session.stage == 'fleet_overflow':
                ships = window.movement.fleet_ships(session)
                window.text('fleet_overflow_title', 'FLEET LIMIT CHECK', x, y, 11, ACCENT)
                y -= 20
                window.text('fleet_overflow_count',
                            f'Destroy {session.overflow_required} of {len(ships)} non-fighter ships',
                            x, y, 10, MUTED, width)
                y -= 27
                for unit in ships:
                    top = y
                    selected = unit.unit_id in session.overflow_selected
                    arcade.draw_lrbt_rectangle_filled(x, x + width, top - 35, top,
                                                       SELECTED if selected else CARD)
                    window.player_panel.image(f'units/{unit.color_code}_{UNIT_TYPES[unit.kind]["sprite"]}.png',
                                              x + 17, top - 17, 23)
                    window.text(('overflow_ship', unit.unit_id),
                                f'{UNIT_TYPES[unit.kind]["name"]} · {"Destroy" if selected else "Keep"}',
                                x + 34, top - 12, 10, INK)
                    self.hits.append((('overflow', unit.unit_id), x, x + width, top - 35, top))
                    y -= 39
                y -= 12
            elif session.stage == 'production':
                selected_cost = window.movement.production_cost(session)
                paid = window.movement.production_payment(session)
                produced = window.movement.production_total(session)
                window.text('production_title', 'PRODUCTION', x, y, 11, ACCENT)
                y -= 21
                window.text('production_capacity', f'Production limit: {produced}/{session.production_limit}',
                            x, y, 10, MUTED)
                meter(x, y - 11, width, produced, session.production_limit)
                y -= 30
                window.text('production_payment', f'Planets + trade goods: {amount(paid)}/{amount(selected_cost)}',
                            x, y, 10, INK)
                meter(x, y - 12, width, paid, selected_cost, GOLD)
                y -= 27
                surface(x, x + width, y - 40, y, SELECTED)
                window.text('production_trade_goods',
                            f'Trade goods  {session.trade_goods_to_spend} / {session.player.trade_goods}',
                            x + 10, y - 25, 11, INK, max_width=width - 98)
                for delta, left in ((-1, x + width - 78), (1, x + width - 38)):
                    button(window, ('production_tg_button', delta), '+' if delta > 0 else '−',
                           left, y - 36, 34, 32)
                    self.hits.append((('production_trade_goods', delta), left, left + 34, y - 36, y - 4))
                y -= 54
                for kind in PRODUCTION_ORDER:
                    top = y
                    count = session.production_choices.get(kind, 0)
                    cost = window.movement.unit_cost(kind, session.player)
                    surface(x, x + width, top - 42, top, SELECTED if count else CARD, None)
                    sprite_path = f'units/{session.player.color_code}_{UNIT_TYPES[kind]["sprite"]}.png'
                    if kind in ('infantry', 'fighter'):
                        window.player_panel.image(sprite_path, x + 13, top - 22, 20)
                        window.player_panel.image(sprite_path, x + 29, top - 22, 20)
                    else:
                        window.player_panel.image(sprite_path, x + 22, top - 22, 28)
                    displayed_cost = amount(cost * 2 if kind in ('infantry', 'fighter') else cost)
                    window.text(('production_unit', kind), UNIT_TYPES[kind]['name'],
                                x + 46, top - 16, 11, INK, max_width=width - 160)
                    window.text(('production_cost', kind), f'Cost {displayed_cost}',
                                x + 46, top - 32, 9, MUTED)
                    window.text(('production_count', kind), str(count), x + width - 70, top - 26, 14, ACCENT)
                    for delta, left in ((-1, x + width - 112), (1, x + width - 36)):
                        button(window, ('production_unit_button', kind, delta), '+' if delta > 0 else '−',
                               left, top - 37, 32, 32, enabled=count > 0 or delta > 0)
                        self.hits.append((('production_unit', kind, delta), left, left + 32, top - 37, top - 5))
                    y -= ROW_HEIGHT
                y -= 14
                window.text('production_help', 'Yellow planets pay resources; trade goods cover the rest.',
                            x, y, 9, MUTED, width)
                y -= 22
            self.scroll_max = max(0, start - (y - self.scroll) - (viewport_top - bottom) + 40)
            self.scroll_by(0)
        finally:
            window.ctx.scissor = old_scissor
        if self.scroll_max:
            track = viewport_top - bottom
            thumb = max(28, track * track / (track + self.scroll_max))
            thumb_top = viewport_top - self.scroll / self.scroll_max * (track - thumb)
            arcade.draw_lrbt_rectangle_filled(window.width - 10, window.width - 6, thumb_top - thumb, thumb_top, ACCENT)
        if session.stage == 'movement':
            self.update_hover(*window.mouse_position)
        ships = sum(len(session.ships(source)) for source in session.sources.values())
        cargo = sum(session.cargo_values(source)[0] for source in session.sources.values())
        landed = sum(planet_id is not None for planet_id in session.landings.values())
        summary = (f'{ships} ships · {cargo} passengers selected' if session.stage == 'movement' else
                   f'{window.movement.production_total(session)} units · Cost {amount(window.movement.production_cost(session))}'
                   if session.stage == 'production' else
                   f'{len(session.bombard_targets)} ships assigned to bombard' if session.stage == 'bombardment' else
                   f'{session.overflow_required} ships to destroy' if session.stage == 'fleet_overflow' else
                   f'{landed} ground force{"s" if landed != 1 else ""} assigned to planet{"s" if landed != 1 else ""}')
        surface(x - 18, x + width + 18, 0, 153, PANEL, None)
        arcade.draw_line(x, 153, x + width, 153, BORDER, 1)
        window.text('move_summary', summary, x, 128, 11, INK, max_width=width)
        split = x + width * .64
        if session.stage == 'movement':
            actions = (('confirm', f'Move {ships} ships' if ships else 'Finish movement', x, split - 6, SELECTED),
                       ('cancel', 'Cancel', split, x + width, CARD))
        elif session.stage == 'invasion':
            landing_assigned = any(planet_id is not None for planet_id in session.landings.values())
            actions = (('establish', 'Land forces' if landing_assigned else 'Skip invasion', x, split - 6, SELECTED),
                       ('cancel', 'Cancel', split, x + width, CARD))
        elif session.stage == 'bombardment':
            actions = (('resolve_bombardment', 'Resolve bombardment', x, split - 6, SELECTED),
                       ('cancel', 'Cancel', split, x + width, CARD))
        elif session.stage == 'fleet_overflow':
            ready = len(session.overflow_selected) == session.overflow_required
            actions = (('resolve_overflow', f'Destroy {session.overflow_required} ships' if ready else
                        f'Select {session.overflow_required} ships', x, split - 6, SELECTED),
                       ('cancel', 'Cancel', split, x + width, CARD))
        elif session.stage == 'production':
            can_produce = bool(session.production_choices) and window.movement.production_payment(session) >= window.movement.production_cost(session)
            label = 'Produce units' if can_produce else 'Add payment' if session.production_choices else 'Choose units'
            actions = (('produce', label, x, split - 6, SELECTED),
                       ('skip_production', 'Skip production', split, x + width, CARD))
        else:
            actions = (('continue', 'Continue', x, split - 6, SELECTED),
                       ('cancel', 'Cancel', split, x + width, CARD))
        for index, (action, label, lo, hi, color) in enumerate(actions):
            by = 78 if index == 0 else 35
            enabled = not (action == 'produce' and not can_produce)
            button(window, ('move_button', action), label, x, by, width, 36,
                   primary=index == 0, enabled=enabled)
            if enabled:
                self.buttons.append(((action,), x, x + width, by, by + 36))
        window.text('move_help', 'Ctrl+Z · undo last checkpoint', x, 14, 9, MUTED, max_width=width)
