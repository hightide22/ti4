from __future__ import annotations

from collections import defaultdict
import math

import arcade
from ui_theme import (CARD, INK, MUTED, ACCENT, GOLD, BORDER, SELECTED, DISABLED, PRIMARY_TEXT,
                      DANGER, ROW_HEIGHT, surface, button, modal, meter)

from units import UNIT_TYPES, unit_profile


class CombatPanel:
    def __init__(self):
        self.advance_hit = None
        self.assignment_hits = []
        self.action_hits = []
        self.offset = [0.0, 0.0]
        self.bounds = None
        self.scroll = [0, 0]
        self.scroll_max = [0, 0]
        self.body_bottom = 0

    def scroll_by(self, x, amount):
        if self.bounds:
            column = int(x >= (self.bounds[0] + self.bounds[1]) / 2)
            self.scroll[column] = max(0, min(self.scroll_max[column], self.scroll[column] + amount))

    def drag_header(self, x, y):
        if not self.bounds:
            return False
        left, right, bottom, top = self.bounds
        return left <= x <= min(right, left + 370) and top - 68 <= y <= top - 8

    def move(self, dx, dy):
        self.offset[0] += dx
        self.offset[1] += dy

    def hit_test(self, x, y):
        if self.advance_hit and self.advance_hit[0] <= x <= self.advance_hit[1] and self.advance_hit[2] <= y <= self.advance_hit[3]:
            return ('advance',)
        action = next((action for action, left, right, bottom, top in reversed(self.action_hits)
                       if left <= x <= right and bottom <= y <= top), None)
        if action:
            return action
        return next((action for action, left, right, bottom, top in reversed(self.assignment_hits)
                     if left <= x <= right and bottom <= y <= top), None)

    def groups(self, window, session, faction):
        grouped = defaultdict(list)
        for unit in window.movement.combat_units(session, faction):
            profile = unit_profile(unit)
            grouped[(unit.kind, int(profile.get('combatHitsOn') or 10),
                     int(profile.get('combatDieCount') or 1))].append(unit)
        return sorted(grouped.items(), key=lambda item: item[0][0])

    def draw_die(self, window, x, y, value, hit, size=19, *, key):
        fill = GOLD if hit else DISABLED
        border = GOLD if hit else BORDER
        arcade.draw_lrbt_rectangle_filled(x, x + size, y, y + size, fill)
        arcade.draw_lrbt_rectangle_outline(x, x + size, y, y + size, border, 1)
        window.text(('combat_die', key), str(value), x + 5, y + 4, 10, PRIMARY_TEXT if hit else INK)

    def draw_side(self, window, session, factions, x, y, width, column=0):
        fixed_y = y
        viewport_top = y - 52
        start = viewport_top - 5
        y = start + self.scroll[column]
        if not factions:
            window.text(('combat_empty_side', x), 'Fleet eliminated', x + 12, y - 28, 14, MUTED)
            return
        old_scissor = window.ctx.scissor
        window.ctx.scissor = (int(x), int(self.body_bottom), int(width), max(1, int(viewport_top - self.body_bottom)))
        for faction in factions:
            player = next((player for player in window.player_panel.players
                           if player.faction == faction), None)
            if len(factions) > 1:
                window.text(('combat_faction', faction), faction.upper(), x + 10, y - 14, 11, ACCENT)
                y -= 30
            units_for_faction = window.movement.combat_units(session, faction)
            if not units_for_faction:
                window.text(('combat_empty_faction', faction), 'No surviving ships', x + 12, y - 12, 11, MUTED)
                y -= 30
                continue
            for (kind, target, dice_count), group in self.groups(window, session, faction):
                top = y
                rolls = [roll for roll in session.combat_rolls.get(faction, [])
                         if roll['kind'] == kind and roll['unit_id'] in {unit.unit_id for unit in group}]
                dice_columns = max(1, int((width - 58) // 25))
                row_height = ROW_HEIGHT + math.ceil(len(rolls) / dice_columns) * 25
                assigned_here = sum(unit_id in {u.unit_id for u in group}
                                    for unit_id in session.combat_assignments.get(faction, []))
                surface(x, x + width, top - row_height, top, SELECTED if assigned_here else CARD, None)
                if player:
                    window.player_panel.image(f'units/{player.color_code}_{UNIT_TYPES[kind]["sprite"]}.png',
                                              x + 20, top - row_height / 2, 29)
                window.text(('combat_unit_group', faction, kind, target),
                            f'{UNIT_TYPES[kind]["name"]} ×{len(group)}  ·  hit on {target}+',
                            x + 44, top - 16, 11, INK, max_width=width - 170)
                window.text(('combat_assigned', faction, kind), f'{assigned_here} hits assigned' if assigned_here else '',
                            x + width - 119, top - 16, 9, GOLD)
                damage = sum(unit.damaged for unit in group)
                window.text(('combat_unit_damage', faction, kind),
                            f'{damage} damaged' if damage else '', x + 44, top - 30, 9, MUTED)
                dice_x, dice_y = x + 44, top - 67
                for index, roll in enumerate(rolls):
                    row, col = divmod(index, dice_columns)
                    self.draw_die(window, dice_x + col * 25, dice_y - row * 25,
                                  roll['value'], roll['hit'], size=21, key=(faction, kind, target, index))
                assignments = session.combat_assignments.get(faction, [])
                available = (session.combat_needs_resolution and
                             window.movement.combat_assignment_target(session, faction, kind) is not None)
                action = ('assign_hit', faction, kind)
                if available and len(assignments) < min(session.combat_hits.get(faction, 0),
                                                        window.movement.combat_hit_capacity(session, faction)):
                    arcade.draw_lrbt_rectangle_outline(x, x + width, top - row_height, top, ACCENT, 1)
                    hit_bottom, hit_top = max(self.body_bottom, top - row_height), min(viewport_top, top)
                    if hit_top > hit_bottom:
                        self.assignment_hits.append((action, x, x + width, hit_bottom, hit_top))
                y -= row_height + 6
            y -= 8
        window.ctx.scissor = old_scissor
        self.scroll_max[column] = max(0, start - (y - self.scroll[column]) - (viewport_top - self.body_bottom))
        self.scroll[column] = min(self.scroll[column], self.scroll_max[column])
        role = 'ATTACKER' if session.player.faction in factions else 'DEFENDER'
        window.text(('combat_side_title', column), f'{role} / {", ".join(f.upper() for f in factions)}',
                    x + 10, fixed_y, 13, ACCENT, max_width=width - 20)
        required = sum(min(session.combat_hits.get(f, 0), window.movement.combat_hit_capacity(session, f)) for f in factions)
        assigned = sum(len(session.combat_assignments.get(f, [])) for f in factions)
        window.text(('combat_assignment_progress', column), f'Hits assigned  {assigned} / {required}',
                    x + 10, fixed_y - 25, 11, GOLD if required else MUTED)
        meter(x + 10, fixed_y - 38, width - 20, assigned, required, GOLD)

    def draw(self, window, session):
        self.advance_hit = None
        self.assignment_hits.clear()
        self.action_hits.clear()
        arcade.draw_lrbt_rectangle_filled(0, window.width, 0, window.height, (3, 7, 14, 218))
        width = min(1180, window.width - 56)
        group_height = []
        for faction in session.combat_factions:
            groups = self.groups(window, session, faction)
            dice = sum(len(rolls) for owner, rolls in session.combat_rolls.items() if owner == faction)
            group_height.append(len(groups) * 72 + math.ceil(dice / 12) * 25)
        height = min(max(520, 280 + max(group_height, default=0)), 760, window.height - 56)
        base_left, base_bottom = (window.width - width) / 2, (window.height - height) / 2
        left = max(0, min(window.width - width, base_left + self.offset[0]))
        bottom = max(0, min(window.height - height, base_bottom + self.offset[1]))
        self.offset[:] = [left - base_left, bottom - base_bottom]
        self.bounds = (left, left + width, bottom, bottom + height)
        modal(left, left + width, bottom, bottom + height)
        window.text('combat_drag_hint', 'DRAG HEADER TO MOVE', left + width - 177, bottom + height - 26, 8, MUTED)
        title = 'GROUND COMBAT' if session.combat_type == 'ground' else 'SPACE COMBAT'
        window.text('combat_modal_title', title, left + 24, bottom + height - 31, 18, ACCENT)
        if session.combat_type == 'ground':
            planet = next(planet for planet in session.target.planets
                          if planet.planet_id == session.combat_planet_id)
            round_label = f'Round {session.combat_round}' if session.combat_round else 'Roll one die per combat die'
            subtitle = f'{planet.name} · {round_label}'
        else:
            subtitle = f'Round {session.combat_round}' if session.combat_round else 'Roll one die per combat die'
        if session.stage == 'combat_end':
            subtitle = f'Round {session.combat_round} · End of combat round'
        window.text('combat_round', subtitle, left + 24, bottom + height - 58, 11, MUTED)
        combat_players = [player for player in window.player_panel.players
                          if player.faction in session.combat_factions]
        playable_cards = sum(len(window.action_cards.playable(player, session))
                             for player in combat_players)
        if playable_cards:
            card_x, card_y = left + width - 162, bottom + height - 68
            button(window, 'combat_action_cards', f'CARDS · {playable_cards}',
                   card_x, card_y, 138, 29, primary=True, size=9)
            self.action_hits.append((('action_cards',), card_x, card_x + 138,
                                     card_y, card_y + 29))
        factions = session.combat_factions
        defenders = factions[1:]
        col_gap = 22
        col_width = (width - 48 - col_gap) / 2
        afb_rows = 0
        if session.combat_type == 'space' and any(session.afb_rolls.values()):
            afb_rows = 1
            afb_x = left + 24
            afb_y = bottom + height - 81
            window.text('afb_title', 'ANTI-FIGHTER BARRAGE', afb_x, afb_y + 2, 8, MUTED)
            afb_x += 132
            for faction, rolls in session.afb_rolls.items():
                if not rolls:
                    continue
                label_width = 38
                window.text(('afb_faction', faction), faction.upper(), afb_x, afb_y + 2, 8, ACCENT)
                afb_x += label_width
                for roll_index, roll in enumerate(rolls):
                    if afb_x > left + width - 35:
                        afb_rows += 1
                        afb_x = left + 24
                        afb_y -= 17
                    self.draw_die(window, afb_x, afb_y - 1, roll['value'], roll['hit'], size=14, key=('barrage', faction, roll_index))
                    afb_x += 17
                afb_x += 8
        side_top = bottom + height - 94 - afb_rows * 17
        arcade.draw_line(left + width / 2, bottom + 84, left + width / 2, side_top + 12,
                         BORDER, 1)
        self.body_bottom = bottom + 130
        old_scissor = window.ctx.scissor
        try:
            window.ctx.scissor = (int(left + 16), int(self.body_bottom), int(width - 32),
                                  max(1, int(side_top + 18 - self.body_bottom)))
            self.draw_side(window, session, (factions[0],) if factions else (), left + 16,
                           side_top, col_width, 0)
            self.draw_side(window, session, defenders, left + width / 2 + 7,
                           side_top, col_width, 1)
        finally:
            window.ctx.scissor = old_scissor
        for column in range(2):
            if self.scroll_max[column]:
                sx = left + width * (column + 1) / 2 - 10
                arcade.draw_line(sx, self.body_bottom, sx, side_top + 12, BORDER, 3)
                sy = side_top + 12 - self.scroll[column] / self.scroll_max[column] * (side_top - self.body_bottom - 18)
                arcade.draw_line(sx, sy - 30, sx, sy, ACCENT, 3)
        arcade.draw_line(left + 20, bottom + 120, left + width - 20, bottom + 120, BORDER, 1)

        if session.stage == 'retreat_selection':
            window.text('retreat_title', 'RETREAT TO AN ADJACENT SYSTEM', left + 24, bottom + 77, 10, ACCENT)
            options = window.movement.retreat_options(session, session.retreat_announced)
            columns = min(6, max(1, len(options)))
            cell_width = (width - 48) / columns
            for index, tile in enumerate(options):
                row, column = divmod(index, 6)
                bx = left + 24 + column * cell_width
                by = bottom + 24 - row * 27
                arcade.draw_lrbt_rectangle_filled(bx, bx + cell_width - 5, by, by + 23, SELECTED)
                label = f'Tile {tile.system_id}'
                window.text(('retreat_option', tile.position), label, bx + 6, by + 7, 8, INK,
                            cell_width - 14)
                self.action_hits.append((('retreat_to', tile.position), bx, bx + cell_width - 5, by, by + 23))
            if not options:
                window.text('retreat_no_options', 'No legal adjacent system remains.', left + 24,
                            bottom + 52, 10, MUTED)
            window.text('combat_help', 'Select a valid adjacent system for your fleet.',
                        left + 24, bottom + 99, 11, MUTED, width - 48)
        elif session.combat_needs_resolution:
            complete = window.movement.combat_assignments_complete(session)
            units_label = 'ground forces' if session.combat_type == 'ground' else 'ships'
            label = 'Resolve Hits · Continue' if complete else f'Assign hits to your {units_label}'
            color = SELECTED if complete else DISABLED
        else:
            complete = True
            label = ('Finish Combat Round' if session.stage == 'combat_end' else
                     'Roll Combat Dice' if not session.combat_round else 'Next Combat Round')
            color = SELECTED
        if session.stage == 'retreat_selection':
            self.advance_hit = None
        else:
            bx, by, bw, bh = left + width - 320, bottom + 24, 296, 42
            button(window, 'combat_advance_button', label, bx, by, bw, bh,
                   primary=True, enabled=complete, size=11)
            self.advance_hit = (bx, bx + bw, by, by + bh) if complete else None
        if session.stage == 'space_combat' and not session.retreat_announced:
            retreat_options = window.movement.retreat_options(session, session.player.faction)
            rx, ry, rw, rh = left + 20, bottom + 24, min(220, col_width - 24), 38
            enabled = bool(retreat_options) and not session.combat_needs_resolution
            arcade.draw_lrbt_rectangle_filled(rx, rx + rw, ry, ry + rh,
                                               SELECTED if enabled else DISABLED)
            label = 'Announce Retreat' if retreat_options else 'No Adjacent Retreat'
            window.text('announce_retreat_button', label, rx + 10, ry + 13, 9,
                        INK if enabled else MUTED, rw - 18)
            if enabled:
                self.action_hits.append((('announce_retreat',), rx, rx + rw, ry, ry + rh))
            else:
                window.text('retreat_unavailable', 'Assign hits before announcing a retreat.' if retreat_options else 'Need your ships in a safe adjacent system.',
                            rx, ry + 43, 8, MUTED, rw)
        elif session.stage == 'space_combat' and session.retreat_announced:
            window.text('retreat_declared', 'Retreat declared; choose destination after this round.',
                        left + 22, bottom + 75, 9, ACCENT, col_width)
        if window.movement_error:
            window.text('combat_error', window.movement_error, left + width / 2, bottom + 76, 10,
                        DANGER, width - 40)
        if session.stage == 'retreat_selection':
            pass
        elif session.combat_needs_resolution:
            remaining = sum(max(0, min(session.combat_hits.get(faction, 0),
                                       window.movement.combat_hit_capacity(session, faction)) -
                                len(session.combat_assignments.get(faction, []))) for faction in factions)
            unit_label = 'ground forces' if session.combat_type == 'ground' else 'ship groups'
            window.text('combat_help', f'Click {unit_label} to assign incoming hits · {remaining} left',
                        left + 24, bottom + 99, 11, MUTED, width - 48)
        elif session.stage == 'combat_end':
            window.text('combat_help', 'Repair eligible units now, then continue to the next round.',
                        left + 24, bottom + 99, 11, MUTED, width - 48)
        else:
            survivors = 'Ground forces' if session.combat_type == 'ground' else 'Ships'
            window.text('combat_help', f'A gold die is a hit. {survivors} that survive fire in the next round.',
                        left + 24, bottom + 99, 11, MUTED, width - 48)
