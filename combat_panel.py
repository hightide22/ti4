from __future__ import annotations

from collections import defaultdict

import arcade

from movement_panel import INK, MUTED, ACCENT
from units import UNIT_TYPES, unit_profile


class CombatPanel:
    def __init__(self):
        self.advance_hit = None
        self.assignment_hits = []
        self.action_hits = []
        self.reroll_die_hits = []
        self.offset = [0.0, 0.0]
        self.bounds = None
        self.assault_page = 0

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
        action = next((action for action, left, right, bottom, top in reversed(self.reroll_die_hits)
                       if left <= x <= right and bottom <= y <= top), None)
        if action:
            return action
        return next((action for action, left, right, bottom, top in reversed(self.assignment_hits)
                     if left <= x <= right and bottom <= y <= top), None)

    def groups(self, window, session, faction):
        grouped = defaultdict(list)
        for unit in window.movement.combat_units(session, faction):
            profile = unit_profile(unit)
            grouped[(unit.kind, window.movement.combat_threshold(faction, profile),
                     int(profile.get('combatDieCount') or 1))].append(unit)
        return sorted(grouped.items(), key=lambda item: item[0][0])

    def draw_die(self, window, x, y, value, hit, size=19, selected=False):
        fill = (220, 174, 73) if hit else (43, 58, 76)
        border = ACCENT if selected else (255, 226, 153) if hit else (90, 111, 137)
        arcade.draw_lrbt_rectangle_filled(x, x + size, y, y + size, fill)
        arcade.draw_lrbt_rectangle_outline(x, x + size, y, y + size, border, 2 if selected else 1)
        window.text(('combat_die', x, y), str(value), x + 5, y + 4, 10, (19, 26, 36) if hit else INK)

    def draw_side(self, window, session, factions, x, y, width):
        if not factions:
            window.text(('combat_empty_side', x), 'Fleet eliminated', x + 12, y - 28, 14, MUTED)
            return
        for faction in factions:
            player = next((player for player in window.player_panel.players
                           if player.faction == faction), None)
            label = f'{faction.upper()} FLEET'
            if player:
                label = f'{faction.upper()} · {player.faction.title()}'
            window.text(('combat_faction', faction), label, x + 10, y, 13, ACCENT)
            y -= 24
            hits = session.combat_hits.get(faction, 0)
            assignments = session.combat_assignments.get(faction, [])
            capacity = window.movement.combat_hit_capacity(session, faction)
            required = min(hits, capacity)
            window.text(('combat_assignment_progress', faction),
                        f'Hits to assign: {len(assignments)}/{required}  ·  {hits} incoming',
                        x + 10, y, 10, INK if session.combat_needs_resolution else MUTED, width - 20)
            y -= 25
            units_for_faction = window.movement.combat_units(session, faction)
            if not units_for_faction:
                window.text(('combat_empty_faction', faction), 'No surviving ships', x + 12, y - 12, 11, MUTED)
                y -= 30
                continue
            for (kind, target, dice_count), group in self.groups(window, session, faction):
                top = y
                row_height = 59 if session.combat_round else 43
                arcade.draw_lrbt_rectangle_filled(x, x + width, top - row_height, top, (21, 35, 52))
                if player:
                    window.player_panel.image(f'units/{player.color_code}_{UNIT_TYPES[kind]["sprite"]}.png',
                                              x + 20, top - row_height / 2, 29)
                window.text(('combat_unit_group', faction, kind, target),
                            f'{UNIT_TYPES[kind]["name"]} ×{len(group)}  ·  hit on {target}+',
                            x + 44, top - 14 if session.combat_round else top - 16, 10, INK, width - 50)
                damage = sum(unit.damaged for unit in group)
                window.text(('combat_unit_damage', faction, kind),
                            f'{damage} damaged' if damage else '', x + 44, top - 30, 9, MUTED)
                rolls = [(index, roll) for index, roll in enumerate(session.combat_rolls.get(faction, []))
                         if roll['kind'] == kind and roll['unit_id'] in {unit.unit_id for unit in group}]
                dice_x, dice_y = x + min(185, width * .36), top - 54
                for index, (roll_index, roll) in enumerate(rolls):
                    row, column = divmod(index, 10)
                    die_x, die_y = dice_x + column * 23, dice_y + row * 22
                    selected = roll_index in session.reroll_selected.get(faction, set())
                    self.draw_die(window, die_x, die_y, roll['value'], roll['hit'], selected=selected)
                    if faction in session.munitions_available and not roll.get('rerolled'):
                        self.reroll_die_hits.append((('reroll_die', faction, roll_index),
                                                     die_x, die_x + 19, die_y, die_y + 19))
                assignments = session.combat_assignments.get(faction, [])
                available = (session.combat_needs_resolution and
                             window.movement.combat_assignment_target(session, faction, kind) is not None)
                action = ('assign_hit', faction, kind)
                if available and len(assignments) < min(session.combat_hits.get(faction, 0),
                                                        window.movement.combat_hit_capacity(session, faction)):
                    arcade.draw_lrbt_rectangle_outline(x, x + width, top - row_height, top, (83, 131, 153), 1)
                    self.assignment_hits.append((action, x, x + width, top - row_height, top))
                y -= row_height + 6
            y -= 8

    def draw(self, window, session):
        self.advance_hit = None
        self.assignment_hits.clear()
        self.action_hits.clear()
        self.reroll_die_hits.clear()
        arcade.draw_lrbt_rectangle_filled(0, window.width, 0, window.height, (3, 7, 14, 218))
        width = min(1180, window.width - 56)
        height = min(760, window.height - 56)
        base_left, base_bottom = (window.width - width) / 2, (window.height - height) / 2
        left = max(0, min(window.width - width, base_left + self.offset[0]))
        bottom = max(0, min(window.height - height, base_bottom + self.offset[1]))
        self.offset[:] = [left - base_left, bottom - base_bottom]
        self.bounds = (left, left + width, bottom, bottom + height)
        arcade.draw_lrbt_rectangle_filled(left, left + width, bottom, bottom + height, (13, 24, 39))
        arcade.draw_lrbt_rectangle_outline(left, left + width, bottom, bottom + height, (75, 138, 166), 2)
        title = 'GROUND COMBAT' if session.combat_type == 'ground' else 'SPACE COMBAT'
        window.text('combat_modal_title', title, left + 24, bottom + height - 31, 18, ACCENT)
        if session.combat_type == 'ground':
            planet = next(planet for planet in session.target.planets
                          if planet.planet_id == session.combat_planet_id)
            round_label = f'Round {session.combat_round}' if session.combat_round else 'Roll one die per combat die'
            subtitle = f'{planet.name} · {round_label}'
        else:
            subtitle = f'Round {session.combat_round}' if session.combat_round else 'Roll one die per combat die'
        window.text('combat_round', subtitle, left + 24, bottom + height - 58, 11, MUTED)
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
                for roll in rolls:
                    if afb_x > left + width - 35:
                        afb_rows += 1
                        afb_x = left + 24
                        afb_y -= 17
                    self.draw_die(window, afb_x, afb_y - 1, roll['value'], roll['hit'], size=14)
                    afb_x += 17
                afb_x += 8
        side_top = bottom + height - 94 - afb_rows * 17
        arcade.draw_line(left + width / 2, bottom + 84, left + width / 2, side_top + 12,
                         (53, 76, 99), 1)
        self.draw_side(window, session, (factions[0],) if factions else (), left + 16,
                       side_top, col_width)
        self.draw_side(window, session, defenders, left + width / 2 + 7,
                       side_top, col_width)

        if session.stage == 'assault_choice':
            victims = window.movement.assault_victims(session)
            pages = max(1, (len(victims) + 3) // 4)
            self.assault_page = min(self.assault_page, pages - 1)
            box_left, box_right = left + width * .19, left + width * .81
            arcade.draw_lrbt_rectangle_filled(box_left, box_right, bottom + 35,
                                               bottom + 330, (17, 33, 49))
            arcade.draw_lrbt_rectangle_outline(box_left, box_right, bottom + 35,
                                                bottom + 330, ACCENT, 2)
            attacker = session.assault_queue[0].upper()
            window.text('assault_title', f'{attacker} · ASSAULT CANNON',
                        box_left + 18, bottom + 298, 14, ACCENT)
            window.text('assault_help', 'The defender chooses 1 non-fighter ship to destroy.',
                        box_left + 18, bottom + 273, 10, INK)
            for index, unit in enumerate(victims[self.assault_page * 4:self.assault_page * 4 + 4]):
                top = bottom + 251 - index * 44
                arcade.draw_lrbt_rectangle_filled(box_left + 18, box_right - 18,
                                                   top - 35, top, (30, 62, 73))
                label = f'{unit.owner.upper()} · {UNIT_TYPES[unit.kind]["name"]}'
                if unit.damaged:
                    label += ' · damaged'
                window.text(('assault_victim', unit.unit_id), label,
                            box_left + 28, top - 23, 10, INK)
                self.action_hits.append((('assault_victim', unit.unit_id),
                                         box_left + 18, box_right - 18, top - 35, top))
            if pages > 1:
                window.text('assault_page', f'{self.assault_page + 1}/{pages}',
                            box_left + 65, bottom + 50, 10, MUTED)
                for delta, label, x in ((-1, 'PREV', box_left + 18),
                                        (1, 'NEXT', box_right - 88)):
                    arcade.draw_lrbt_rectangle_filled(x, x + 70, bottom + 44,
                                                       bottom + 73, (29, 66, 77))
                    window.text(('assault_page_button', delta), label,
                                x + 8, bottom + 54, 9, INK)
                    self.action_hits.append((('assault_page', delta), x, x + 70,
                                             bottom + 44, bottom + 73))
            return

        if session.stage == 'retreat_selection':
            window.text('retreat_title', 'RETREAT TO AN ADJACENT SYSTEM', left + 24, bottom + 77, 10, ACCENT)
            options = window.movement.retreat_options(session, session.retreat_announced)
            columns = min(6, max(1, len(options)))
            cell_width = (width - 48) / columns
            for index, tile in enumerate(options):
                row, column = divmod(index, 6)
                bx = left + 24 + column * cell_width
                by = bottom + 24 - row * 27
                arcade.draw_lrbt_rectangle_filled(bx, bx + cell_width - 5, by, by + 23, (31, 78, 83))
                label = f'Tile {tile.system_id}'
                window.text(('retreat_option', tile.position), label, bx + 6, by + 7, 8, INK,
                            cell_width - 14)
                self.action_hits.append((('retreat_to', tile.position), bx, bx + cell_width - 5, by, by + 23))
            if not options:
                window.text('retreat_no_options', 'No legal adjacent system remains.', left + 24,
                            bottom + 52, 10, MUTED)
            window.text('combat_help', 'Select a valid adjacent system for your fleet.',
                        left + 20, bottom + 73, 9, MUTED, width - 40)
        elif session.combat_needs_resolution:
            complete = window.movement.combat_assignments_complete(session)
            units_label = 'ground forces' if session.combat_type == 'ground' else 'ships'
            label = 'Resolve Hits · Continue' if complete else f'Assign hits to your {units_label}'
            color = (31, 94, 100) if complete else (34, 45, 59)
        else:
            complete = True
            label = 'Roll Combat Dice' if not session.combat_round else 'Next Combat Round'
            color = (31, 94, 100)
        if session.stage == 'retreat_selection':
            self.advance_hit = None
        else:
            bx, by, bw, bh = left + width / 2 - 120, bottom + 24, 240, 38
            arcade.draw_lrbt_rectangle_filled(bx, bx + bw, by, by + bh, color)
            arcade.draw_lrbt_rectangle_outline(bx, bx + bw, by, by + bh, (102, 207, 224), 1)
            window.text('combat_advance_button', label, bx + 14, by + 12, 11, INK if complete else MUTED)
            self.advance_hit = (bx, bx + bw, by, by + bh) if complete else None
        if session.stage == 'space_combat' and not session.retreat_announced:
            retreat_options = window.movement.retreat_options(session, session.player.faction)
            rx, ry, rw, rh = left + 20, bottom + 24, min(220, col_width - 24), 38
            enabled = bool(retreat_options) and not session.combat_needs_resolution
            arcade.draw_lrbt_rectangle_filled(rx, rx + rw, ry, ry + rh,
                                               (31, 78, 83) if enabled else (31, 39, 50))
            label = 'Announce Retreat' if retreat_options else 'No Adjacent Retreat'
            window.text('announce_retreat_button', label, rx + 10, ry + 13, 9,
                        INK if enabled else MUTED, rw - 18)
            if enabled:
                self.action_hits.append((('announce_retreat',), rx, rx + rw, ry, ry + rh))
            else:
                window.text('retreat_unavailable', 'Need your ships in a safe adjacent system.',
                            rx, ry + 43, 8, MUTED, rw)
        elif session.stage == 'space_combat' and session.retreat_announced:
            window.text('retreat_declared', 'Retreat declared; choose destination after this round.',
                        left + 22, bottom + 75, 9, ACCENT, col_width)
        letnev = next((player for player in window.player_panel.players if player.faction == 'letnev'), None)
        if session.stage == 'space_combat' and session.combat_type == 'space' and letnev and \
                'letnev' in session.combat_factions:
            button_x, button_y, button_w, button_h = left + width - 235, bottom + 24, 215, 38
            if not session.combat_needs_resolution:
                round_number = session.combat_round + 1
                can_spend = (letnev.trade_goods >= 2 and
                             ('letnev', round_number) not in session.munitions_spent_round)
                label = 'Munitions Reserves · 2 TG' if can_spend else 'Munitions Reserves unavailable'
                fill = (31, 78, 83) if can_spend else (31, 39, 50)
                border = (102, 207, 224) if can_spend else (48, 65, 82)
                arcade.draw_lrbt_rectangle_filled(button_x, button_x + button_w, button_y,
                                                   button_y + button_h, fill)
                arcade.draw_lrbt_rectangle_outline(button_x, button_x + button_w, button_y,
                                                    button_y + button_h, border, 1)
                window.text('munitions_button', label, button_x + 9, button_y + 13, 9,
                            INK if can_spend else MUTED, button_w - 18)
                if can_spend:
                    self.action_hits.append((('spend_munitions', 'letnev'), button_x,
                                             button_x + button_w, button_y, button_y + button_h))
            elif 'letnev' in session.munitions_available:
                selected_count = len(session.reroll_selected.get('letnev', set()))
                label = f'Re-roll {selected_count} dice' if selected_count else 'Select dice to re-roll'
                enabled = selected_count > 0
                fill = (31, 78, 83) if enabled else (31, 39, 50)
                border = (102, 207, 224) if enabled else (48, 65, 82)
                arcade.draw_lrbt_rectangle_filled(button_x, button_x + button_w, button_y,
                                                   button_y + button_h, fill)
                arcade.draw_lrbt_rectangle_outline(button_x, button_x + button_w, button_y,
                                                    button_y + button_h, border, 1)
                window.text('munitions_reroll_button', label, button_x + 9, button_y + 13, 9,
                            INK if enabled else MUTED, button_w - 18)
                if enabled:
                    self.action_hits.append((('reroll_dice', 'letnev'), button_x,
                                             button_x + button_w, button_y, button_y + button_h))
        if window.movement_error:
            window.text('combat_error', window.movement_error, left + width / 2, bottom + 104, 10,
                        (245, 142, 128), width - 40)
        if session.stage == 'retreat_selection':
            pass
        elif session.combat_needs_resolution:
            remaining = sum(max(0, min(session.combat_hits.get(faction, 0),
                                       window.movement.combat_hit_capacity(session, faction)) -
                                len(session.combat_assignments.get(faction, []))) for faction in factions)
            unit_label = 'ground forces' if session.combat_type == 'ground' else 'ship groups'
            window.text('combat_help', f'Click {unit_label} to assign incoming hits · {remaining} left',
                        left + 20, bottom + 77, 9, MUTED, width - 40)
        else:
            survivors = 'Ground forces' if session.combat_type == 'ground' else 'Ships'
            window.text('combat_help', f'A gold die is a hit. {survivors} that survive fire in the next round.',
                        left + 20, bottom + 77, 9, MUTED, width - 40)
