"""Deterministic opponents using the same turn and action controllers as players.

The weights are deliberately visible: expansion is valuable to everyone,
while contested human planets have more strategic value than bot-owned ones.
Legality (movement, capacity, payment, combat) stays with the game controllers.
"""
from __future__ import annotations

from collections import deque
from itertools import combinations
from math import ceil

from movement import MovementError, capital_ship
from ai_combat import win_probability
from ai_planner import (SHIP_VALUE,
                        best_activation as plan_best_activation, plan_landings)
from ai_economy import build_plan, command_targets, technology_values
from ai_actions import (loss_value, plan_bombardment, play_useful_card,
                        resolve_card_choice, war_effort_plan)
from ai_strategy import construction_choice, diplomacy_value, warfare_value
from action_cards import canonical_action_card
from player import command_tokens_in_reinforcements
from technology import available_technologies, missing_prerequisites
from units import Region, UNIT_TYPES, unit_profile


TECH_PRIORITY = {
    'gd': 100, 'st': 88, 'cv2': 84, 'ac2': 85, 'ff2': 80, 'dn2': 75,
    'fl': 70, 'dd2': 67, 'hm': 65, 'ps': 58, 'nes': 76,
    'lwd': 52, 'cr2': 62, 'inf2': 55, 'so2': 60, 'nm': 46,
    'amd': 50, 'da': 52, 'asc': 58, 'td': 53, 'ie': 57,
    'pds2': 45, 'sd2': 48, 'ws': 52, 'gls': 42,
    'qdn': 63, 'pm': 56, 'ers': 55, 'scc': 48, 'l4': 42,
    'dxa': 35, 'md_base': 42, 'x89_base': 40,
}
LOSS_ORDER = {'fighter': 0, 'destroyer': 2, 'cruiser': 3,
              'carrier': 5, 'dreadnought': 6, 'flagship': 8, 'warsun': 10,
              'infantry': 0}
STRATEGY_NAMES = {1: 'Leadership', 2: 'Diplomacy', 3: 'Politics',
                  4: 'Construction', 5: 'Trade', 6: 'Warfare',
                  7: 'Technology', 8: 'Imperial'}
def payment_plan(player, amount, value, opportunity=None):
    """Pay exactly where possible while preserving useful alternative yields."""
    if amount <= 0:
        return (), 0
    cards = [card for card in player.planets if not card.exhausted and value(card) > 0]
    # Resource/influence targets are small; dynamic programming avoids the
    # exponential search over an established empire's planet cards.
    options = {0: ((), 0.0)}
    for card in cards:
        points = value(card)
        for total, (chosen, cost) in tuple(options.items()):
            new_total = total + points
            candidate = chosen + (card.planet.planet_id,)
            candidate_cost = cost + (opportunity(card) if opportunity else 0)
            if (new_total not in options or
                    (candidate_cost, len(candidate), candidate) <
                    (options[new_total][1], len(options[new_total][0]),
                     options[new_total][0])):
                options[new_total] = (candidate, candidate_cost)
    feasible = []
    for total, (chosen, cost) in options.items():
        goods = max(0, amount - total)
        if goods <= player.trade_goods:
            feasible.append((max(0, total - amount), cost + 1.5 * goods,
                             goods, len(chosen), chosen))
    if not feasible:
        return None
    _, _, goods, _, chosen = min(feasible)
    return chosen, goods


def resource_opportunity(card):
    planet = card.planet
    return .7 * planet.influence + (1.5 if getattr(planet, 'tech_specialties', ()) else 0)


def influence_opportunity(card):
    return .7 * card.planet.resources


class GameAI:
    def __init__(self, window, bot_factions):
        self.window = window
        self.bot_factions = set(bot_factions)
        self.wait = 0.0
        self.paused = False
        self.speed = 1.0
        self.events = deque(maxlen=3)
        self.focus_positions = ()
        self.activation_flash_position = None
        self.activation_flash_route = None
        self.activation_flash_remaining = 0.0
        self.activation_flash_duration = 0.0
        self.thinking = False
        self.last_error = None

    def is_bot(self, player):
        return bool(player and player.faction in self.bot_factions)

    def human_faction(self):
        return next((p.faction for p in self.window.turn_order.players
                     if p.faction not in self.bot_factions), None)

    def update(self, delta_time):
        if self.paused:
            return
        self.activation_flash_remaining = max(0.0, self.activation_flash_remaining - delta_time)
        self.wait -= delta_time
        if self.wait > 0 or self.thinking or self.window.main_menu_visible:
            return
        # Only decision outcomes need reading time. Mechanical transitions
        # should advance on the next brief scheduler tick.
        self.wait = .12 / self.speed
        self.thinking = True
        try:
            self.step()
            self.last_error = None
        except (ValueError, MovementError, StopIteration) as error:
            self.last_error = str(error)
            self.window.movement_error = f'AI: {error}'
            # A bad choice should remain visible, not retry every frame.
            self.wait = 1.5
        finally:
            self.thinking = False

    def report(self, player, message, duration=.7):
        faction = player.faction.upper() if player else 'AI'
        self.events.appendleft((faction, message))
        self.wait = max(self.wait, duration / self.speed)

    def cycle_speed(self):
        speeds = (1.0, 2.0, 4.0, .5)
        previous = self.speed
        self.speed = speeds[(speeds.index(self.speed) + 1) % len(speeds)]
        ratio = previous / self.speed
        self.wait *= ratio
        self.activation_flash_remaining *= ratio
        self.activation_flash_duration *= ratio

    def opening_expansion(self, player, move):
        if not move or self.window.turn_order.round_number != 1 or move[1] == move[2]:
            return False
        target = self.window.board[move[1]]
        return (any(not target.planet_owners.get(planet.planet_id) for planet in target.planets)
                and not any(player.faction in tile.command_tokens
                            for tile in self.window.board.values()))

    def step(self):
        w = self.window
        turn = w.turn_order
        if w.action_cards.pending:
            resolve_card_choice(self)
            return
        if w.transaction.session:
            return
        if turn.strategy_selection:
            if self.is_bot(turn.active_player):
                player = turn.active_player
                card = self.choose_strategy_card(player)
                turn.choose_strategy_card(card)
                self.report(player, f'Drafted {STRATEGY_NAMES[card]}.')
                w.sync_strategy_actor()
            return
        if turn.qdn_pending:
            if self.is_bot(next((p for p in turn.players if p.faction == 'hacan'), None)):
                turn.skip_qdn()
            return
        if turn.command_allocation:
            player = turn.active_player
            if self.is_bot(player):
                if player.pending_commands:
                    player.allocate_command(self.command_pool(player))
                if not player.pending_commands:
                    finished = turn.finish_player_command_allocation()
                    if finished and turn.strategy_enabled:
                        turn.begin_strategy_phase()
                        w.strategy_view = True
                    w.player_panel.active = w.player_panel.players.index(turn.active_player)
                    w.frame_player_systems(turn.active_player)
            return
        if w.movement.session:
            self.resolve_movement()
            return
        if w.strategy.session:
            if self.is_bot(w.strategy.player):
                self.resolve_strategy()
                w.sync_strategy_actor()
            return
        player = turn.active_player
        if not self.is_bot(player):
            return
        if not turn.can_take_action:
            self.report(player, 'Ended the turn.', .35)
            w.pass_turn()
            return
        cards = [card for card in turn.strategy_assignments[player.faction]
                 if card not in turn.strategy_used[player.faction]]
        move = self.best_activation(player) if player.command_pools['tactical'] else None
        def play_value(card):
            value = self.strategy_value(player, card)
            if (card == 6 and move and not any(
                    player.faction in tile.command_tokens for tile in w.board.values())):
                value -= 20  # wait until Warfare can unlock a used system
            return value

        best_card = max(cards, key=lambda card: (play_value(card), -card)) if cards else None
        reinforcement = war_effort_plan(self, player)
        if self.opening_expansion(player, move):
            self.start_activation(player, move)
        elif reinforcement and reinforcement[0] >= max(
                move[0] if move else 0, play_value(best_card) if best_card else 0):
            w.action_cards.play(player, reinforcement[1])
            resolve_card_choice(self)
            self.report(player, 'Played War Effort to reinforce the fleet.', .8)
        elif best_card and (not move or play_value(best_card) >= move[0] - 3):
            w.strategy.start(best_card)
            self.report(player, f'Played {STRATEGY_NAMES[best_card]}.')
            w.sync_strategy_actor()
        elif move:
            self.start_activation(player, move)
        elif best_card:
            w.strategy.start(best_card)
            self.report(player, f'Played {STRATEGY_NAMES[best_card]}.')
            w.sync_strategy_actor()
        else:
            self.report(player, 'Ended the turn.' if turn.action_used else
                        'Passed for the rest of the round.', .65)
            w.pass_turn()

    def command_pool(self, player):
        pools = player.command_pools
        if pools['tactical'] < 2:
            return 'tactical'
        if pools['strategic'] == 0:
            return 'strategic'
        targets = command_targets(player, self.window.board,
                                  self.window.turn_order.round_number)
        return max(('tactical', 'fleet', 'strategic'), key=lambda name: (
            (targets[name] - pools[name]) / max(1, targets[name]),
            {'tactical': 2, 'fleet': 1, 'strategic': 0}[name]))

    def strategy_value(self, player, card):
        pools = player.command_pools
        ready_resources = player.available_values[0] + player.trade_goods
        board = self.window.board
        units = [u for t in board.values() for u in t.units if u.owner == player.faction]
        docks = sum(unit.kind == 'spacedock' for unit in units)
        targets = command_targets(player, board, self.window.turn_order.round_number)
        shortage = sum(max(0, targets[key] - pools[key]) for key in pools)
        ready_value = sum(sorted((max(c.planet.resources, .8 * c.planet.influence)
                                 for c in player.planets if c.exhausted), reverse=True)[:2])
        # Warfare is valuable for releasing a fleet, not merely a marker on an
        # empty system. During the draft it can fund an additional expedition.
        locked = max((sum(SHIP_VALUE.get(u.kind, 0) for u in t.units
                          if u.owner == player.faction and u.location.region == Region.SPACE)
                      for t in board.values() if player.faction in t.command_tokens), default=0)
        transports = sum(u.capacity >= 3 and u.location.region == Region.SPACE for u in units)
        drafting = self.window.turn_order.strategy_selection
        warfare = 5 + min(14, locked * 2) + (min(7, transports * 3) if drafting else 0)
        tech_values, tech = {}, None
        if card == 7:
            tech_values = technology_values(player, board, self.window.turn_order.round_number)
            tech = self.choose_technology(player, 0, values=tech_values)
        scores = {1: 10 + min(20, 4 * shortage) + 6 * (pools['tactical'] <= 1),
                  2: 3 + 1.8 * ready_value,
                  3: 6 + 2 * (self.window.turn_order.speaker is not player) +
                     max(0, 4 - len(player.action_cards)),
                  4: 7 + 6 * (docks == 0) + 3 * (docks == 1 and len(player.planets) >= 4),
                  5: 11 + .9 * max(0, player.commodity_limit - player.commodities) +
                     2 * (ready_resources < 4),
                  6: warfare,
                  7: 8 + min(16, .35 * tech_values.get(tech[0], 0)) if tech else 1,
                  8: 1}
        if player.faction == 'jolnar' and card == 7:
            scores[7] += 9
        if player.faction == 'hacan' and card == 5:
            scores[5] += 3
        if drafting and card == 2:
            scores[2] += min(7, ready_resources * .5)
        return scores[card]

    def choose_strategy_card(self, player):
        turn = self.window.turn_order
        already = set(turn.strategy_assignments[player.faction])
        # The second card should complement the first: Trade can fund Technology
        # and Leadership already covers the command shortage of this round.
        synergy = {7: 2 if 5 in already else 0,
                   5: 2 if 7 in already else 0,
                   6: 2 if 1 in already else 0}
        return max(turn.available_strategy_cards, key=lambda card: (
            self.strategy_value(player, card) + synergy.get(card, 0), -card))

    def _tile_threat(self, tile, faction):
        return sum(SHIP_VALUE.get(unit.kind, 0) for unit in tile.units
                   if unit.owner != faction and unit.location.region == Region.SPACE)

    def _force(self, ships):
        return sum(SHIP_VALUE.get(unit.kind, 0) + (.6 if unit_profile(unit).get('sustainDamage') else 0)
                   for unit in ships)

    def best_activation(self, player):
        return plan_best_activation(self, player)

    def start_activation(self, player, move):
        _, target_position, source_position = move[:3]
        movement = self.window.movement
        session = movement.activate(player, target_position)
        try:
            if move.flank_speed:
                index = next((index for index, card in enumerate(player.action_cards)
                              if canonical_action_card(card) == 'flank_speed' and
                              self.window.action_cards.can_play(player.faction, card, session)), None)
                if index is None:
                    raise MovementError('Flank Speed was unavailable for the planned route.')
                self.window.action_cards.play(player, index)
            choices = session.choices
            for unit_id in move.ships + move.passengers:
                if unit_id not in choices:
                    raise MovementError(f'Planned unit {unit_id} cannot reach this system.')
                session.toggle(unit_id)
        except (MovementError, ValueError):
            movement.cancel()
            raise
        self.window.frame_action_route(source_position, target_position)
        self.focus_positions = (source_position, target_position)
        self.activation_flash_position = target_position
        self.activation_flash_duration = 1.3 / self.speed
        self.activation_flash_remaining = self.activation_flash_duration
        source = session.sources.get(source_position)
        selected_routes = (source.routes.get(unit.unit_id) for unit in source.ships
                           if unit.unit_id in session.selected) if source else ()
        self.activation_flash_route = next((route for route in selected_routes if route), None)
        self.window.movement_panel.reset()
        ships = sum(session.choices[unit_id].kind not in ('fighter', 'infantry')
                    for unit_id in session.selected)
        cargo = len(session.selected) - ships
        target = session.target.name
        if not move.ships and not move.passengers:
            self.report(player, f'Activated {target} to produce units.', .22)
        else:
            odds = move.odds or (None, None)
            chance = next((value for value in odds if value is not None), None)
            forecast = f' · win chance ~{chance:.0%}' if chance is not None else ''
            self.report(player, f'Activated {target}: {ships} ships, {cargo} passengers{forecast}.', .22)

    def _secondary_worthwhile(self, ctl, session):
        player = session.player
        card = session.card
        targets = command_targets(player, self.window.board,
                                  self.window.turn_order.round_number)
        strategic = player.command_pools['strategic']
        if card == 1:
            return (command_tokens_in_reinforcements(player, self.window.board) > 0 and
                    sum(player.command_pools.values()) < sum(targets.values()) and
                    payment_plan(player, 3, lambda item: item.planet.influence,
                                 influence_opportunity) is not None)
        if card == 2:
            ready = sorted((max(c.planet.resources, .8 * c.planet.influence)
                            for c in ctl.readyable_planets(player)), reverse=True)[:2]
            return sum(ready) >= (4 if strategic <= 1 else 3)
        if card == 3:
            return len(player.action_cards) <= (2 if strategic <= 1 else 5)
        if card == 4:
            if strategic <= 1 and len(player.planets) < 4:
                return False
            owned = [unit for tile in self.window.board.values() for unit in tile.units
                     if unit.owner == player.faction]
            for planet in player.planets:
                tile = ctl.planet_system(planet.planet.planet_id)
                if tile and tile.planet_owners.get(planet.planet.planet_id) == player.faction:
                    for kind, limit, per_planet in (('pds', 6, 2), ('spacedock', 3, 1)):
                        structures = [unit for unit in owned if unit.kind == kind]
                        if len(structures) < limit and sum(
                                unit.location.planet_id == planet.planet.planet_id
                                for unit in structures) < per_planet:
                            return True
            return False
        if card == 5:
            return (player.commodity_limit - player.commodities >=
                    (3 if strategic <= 1 else 2) or
                    player.faction in session.free_trade or player.faction == 'hacan')
        if card == 6:
            return (bool(ctl.home_docks()) and
                    player.available_values[0] + player.trade_goods +
                    int('st' in player.technologies) >= 1)
        if card == 7:
            cost = 0 if player.faction == 'jolnar' else ctl.technology_cost()
            return self.choose_technology(player, cost) is not None
        return False

    def resolve_strategy(self):
        w = self.window
        ctl, s = w.strategy, w.strategy.session
        player = s.player
        if s.stage == 'offer':
            if not ctl.secondary_unavailable() and self._secondary_worthwhile(ctl, s):
                ctl.accept_secondary(brilliant=(player.faction == 'jolnar' and s.card == 7))
                self.report(player, f'Accepted {STRATEGY_NAMES[s.card]} secondary.', .3)
                return
            ctl.decline_secondary()
        elif s.stage == 'leadership':
            reserve = command_tokens_in_reinforcements(player, w.board) - s.base_gain
            budget = sum(c.planet.influence for c in player.planets if not c.exhausted) + player.trade_goods
            pools = player.command_pools
            desired_total = sum(command_targets(player, w.board,
                                                w.turn_order.round_number).values())
            current_total = sum(pools.values()) + player.pending_commands + s.base_gain
            count = min(max(0, reserve), budget // 3,
                        max(0, desired_total - current_total))
            if count:
                plan = payment_plan(player, count * 3, lambda c: c.planet.influence,
                                    influence_opportunity)
                if plan:
                    s.purchases = count
                    for planet_id in plan[0]:
                        ctl.toggle_payment(planet_id)
                    s.trade_goods = plan[1]
            ctl.pay_leadership()
            self.report(player, f'Gained {s.base_gain + s.purchases} command tokens.', .8)
        elif s.stage == 'allocate':
            if player.pending_commands:
                ctl.allocate(self.command_pool(player))
            else:
                ctl.allocation_changed()
        elif s.stage == 'diplomacy_system':
            options = ctl.selectable_systems()
            if options:
                position = max(options, key=lambda pos: (
                    diplomacy_value(self, w.board[pos], player)))
                ctl.select_system(position)
                self.report(player, f'Diplomacy protected {w.board[position].name}.', .8)
            else:
                ctl.continue_stage()
        elif s.stage == 'ready_planets':
            for card in sorted(ctl.readyable_planets(player),
                               key=lambda c: (-max(c.planet.resources, c.planet.influence),
                                              c.planet.planet_id))[:2]:
                ctl.toggle_ready(card.planet.planet_id)
            ctl.confirm_ready()
        elif s.stage == 'speaker':
            choices = [p for p in w.turn_order.players if p is not w.turn_order.speaker]
            chosen = player if player in choices else next((p for p in choices if self.is_bot(p)), choices[0])
            ctl.choose_speaker(chosen.faction)
            self.report(player, f'Chose {chosen.faction.upper()} as Speaker.', .8)
        elif s.stage == 'trade':
            allies = [p for p in w.turn_order.players if p is not player and self.is_bot(p)]
            if allies:
                s.free_trade.add(min(allies, key=lambda p: (p.trade_goods, p.faction)).faction)
            ctl.confirm_trade()
            self.report(player, 'Gained 3 trade goods and replenished commodities.', .8)
        elif s.stage == 'warfare_system':
            options = ctl.selectable_systems()
            if not options:
                ctl.continue_stage()
            else:
                position = max(options, key=lambda pos: (
                    warfare_value(self, w.board[pos], player)))
                ctl.select_system(position)
                ctl.select_warfare_token(position, player.faction)
                ctl.confirm_warfare_removal()
                self.report(player, f'Warfare removed a token from {w.board[position].name}.', .8)
        elif s.stage == 'warfare_allocate':
            if player.pending_commands:
                ctl.allocate(self.command_pool(player))
            else:
                ctl.continue_stage()
        elif s.stage == 'construction':
            choice = construction_choice(self, ctl, s)
            if choice is None:
                ctl.continue_stage()
            else:
                _, kind, planet_id, position = choice
                s.structure = kind
                ctl.select_system(position)
                planet = next(p for p in w.board[position].planets if p.planet_id == planet_id)
                ctl.build(planet_id)
                self.report(player, f'Built {kind} on {planet.name}.', 1.0)
        elif s.stage == 'production_site':
            docks = ctl.home_docks()
            if docks:
                ctl.produce_at(max(docks, key=lambda item: (
                    sum(p.resources for p in item[0].planets
                        if p.planet_id == item[1].location.planet_id) +
                    int(unit_profile(item[1]).get('productionValue') or 0),
                    item[1].unit_id))[1].unit_id)
            else:
                ctl._participant_done()
        elif s.stage == 'technology':
            choice = self.choose_technology(player, ctl.technology_cost())
            if choice:
                alias, specialty_ids, payment_ids, goods = choice
                ctl.select_technology(alias)
                for planet_id in specialty_ids:
                    ctl.toggle_technology_planet(planet_id, specialty=True)
                for planet_id in payment_ids:
                    ctl.toggle_technology_planet(planet_id)
                s.trade_goods = goods
                ctl.research_selected()
                self.report(player, f'Researched {alias}.', 1.2)
            else:
                ctl.continue_stage()
        elif s.stage == 'placeholder':
            ctl.continue_stage()
        elif s.stage == 'production':
            ctl.poll()

    def choose_technology(self, player, cost, *, values=None):
        cards = [c for c in player.planets if not c.exhausted]
        specialties = [c for c in cards if c.planet.tech_specialties]
        if values is None:
            values = technology_values(player, self.window.board,
                                       self.window.turn_order.round_number)
        best = None
        for tech in available_technologies(player):
            if tech['alias'] in player.technologies:
                continue
            for n in range(min(len(tech.get('requirements', '')), len(specialties)) + 1):
                found = False
                for selected in combinations(specialties, n):
                    if missing_prerequisites(player, tech, selected):
                        continue
                    eligible = type('PaymentPlayer', (), {
                        'planets': [c for c in cards if c not in selected],
                        'trade_goods': player.trade_goods})()
                    payment = payment_plan(eligible, cost, lambda c: c.planet.resources,
                                           resource_opportunity)
                    if payment is None:
                        continue
                    alias = tech['alias']
                    specialty_cost = sum(max(c.planet.resources, .7 * c.planet.influence)
                                         for c in selected)
                    score = (values.get(alias, 3) - 1.25 * specialty_cost -
                             1.5 * payment[1])
                    candidate = (score, alias, tuple(c.planet.planet_id for c in selected),
                                 payment[0], payment[1])
                    if best is None or candidate > best:
                        best = candidate
                    found = True
                if found:
                    break
        return best[1:] if best else None

    def _bot_hit(self, faction, session):
        ctl = self.window.movement
        required = min(session.combat_hits.get(faction, 0), ctl.combat_hit_capacity(session, faction))
        if len(session.combat_assignments.get(faction, [])) >= required:
            return False
        units = ctl.combat_units(session, faction)
        choices = []
        assigned = session.combat_assignments.get(faction, [])
        for candidate in units:
            unit = ctl.combat_assignment_target(session, faction, candidate.kind, candidate.unit_id)
            if unit is None:
                continue
            sustain = bool(unit_profile(unit).get('sustainDamage') and not unit.damaged and
                           unit.unit_id not in assigned)
            value = (loss_value(session.target, unit) if hasattr(session, 'target') else
                     LOSS_ORDER.get(unit.kind, 4))
            choices.append((-10 if sustain else value, unit.kind, unit.unit_id))
        if choices:
            _, kind, unit_id = min(choices)
            ctl.assign_combat_hit(faction, kind, unit_id)
            return True
        return False

    def play_combat_card(self, session):
        return play_useful_card(self, session)

    def plan_invasion(self, session):
        player = session.player
        target = session.target
        troops = [unit for unit_id in session.landings
                  if (unit := next((item for item in target.units
                                   if item.unit_id == unit_id), None)) is not None]
        assignments = plan_landings(self, target, player, troops)
        for unit_id in session.landings:
            session.landings[unit_id] = assignments.get(unit_id)

    def resolve_bot_retreat(self, session):
        ctl = self.window.movement
        options = ctl.retreat_options(session, session.retreat_announced)
        if not options:
            return
        faction = session.retreat_announced
        player = next(p for p in self.window.turn_order.players if p.faction == faction)
        fleeing = sum(capital_ship(unit) for unit in ctl.combat_units(session, faction))

        def safety(tile):
            friendly = [unit for unit in tile.units if unit.owner == faction and
                        unit.location.region == Region.SPACE]
            hostile_neighbors = sum(self._tile_threat(other, faction)
                                    for other in ctl.neighbors(tile))
            overflow = max(0, fleeing + sum(capital_ship(unit) for unit in friendly) -
                           ctl.fleet_supply(player))
            owned = sum(tile.planet_owners.get(planet.planet_id) == faction
                        for planet in tile.planets)
            return (3 * owned + self._force(friendly) - 8 * overflow -
                    .25 * hostile_neighbors, tile.position)

        ctl.resolve_retreat(max(options, key=safety).position)

    def resolve_movement(self):
        w = self.window
        ctl, s = w.movement, w.movement.session
        ai_turn = self.is_bot(s.player)
        if s.fire_team_pending and s.fire_team_pending not in self.bot_factions:
            return
        # Assault Cannon's target chooses which of their own ships is
        # destroyed. Resolve that choice only when the defending faction is
        # controlled by the AI; the technology owner does not choose for them.
        if s.stage == 'assault_choice':
            victims = ctl.assault_victims(s)
            if victims and victims[0].owner in self.bot_factions:
                ctl.choose_assault_victim(min(victims, key=lambda u: (
                    loss_value(s.target, u), u.unit_id)).unit_id)
            return
        # Destination selection belongs to the faction that announced the
        # retreat. Never let the tactical-action owner choose for a human
        # defender just because the bot initiated the combat.
        if s.stage == 'retreat_selection':
            if s.retreat_announced in self.bot_factions:
                self.resolve_bot_retreat(s)
            return
        # The human defender gets the first retreat decision before a bot
        # attacker rolls. They can announce a retreat or explicitly stay.
        if (s.stage == 'space_combat' and ai_turn and not s.combat_needs_resolution and
                not s.retreat_announced and s.retreat_declined_round != s.combat_round + 1 and
                s.retreat_blocked_round != s.combat_round + 1):
            human_defender = next((faction for faction in s.combat_factions
                                   if faction != s.player.faction and
                                   faction not in self.bot_factions), None)
            if human_defender and ctl.retreat_options(s, human_defender):
                return
        if self.play_combat_card(s):
            return
        if s.stage in ('space_combat', 'ground_combat') and s.combat_needs_resolution:
            for faction in s.combat_factions:
                if faction in self.bot_factions and self._bot_hit(faction, s):
                    return
        if (s.stage == 'space_combat' and not s.combat_needs_resolution and
                not s.retreat_announced and
                s.retreat_blocked_round != s.combat_round + 1):
            # A defending bot must make its retreat decision even during a
            # human attack; the active player is still the human attacker.
            for faction in s.combat_factions:
                if faction == s.player.faction or faction not in self.bot_factions:
                    continue
                own = ctl.combat_units(s, faction)
                opposing = [unit for other in s.combat_factions if other != faction
                            for unit in ctl.combat_units(s, other)]
                if (own and opposing and ctl.retreat_options(s, faction) and
                        win_probability(own, opposing, ctl.players) < .25):
                    ctl.announce_retreat(faction)
                    self.report(next((p for p in ctl.players if p.faction == faction),
                                     None), f'Announced retreat from {s.target.name}.', .4)
                    return
        if s.stage == 'space_cannon_choose_target':
            if ai_turn:
                options = ctl.space_cannon_target_options(s)
                if options:
                    target = max(options, key=lambda faction: (
                        sum(SHIP_VALUE.get(unit.kind, 1) for unit in s.target.units
                            if unit.owner == faction and unit.location.region == Region.SPACE),
                        faction))
                    ctl.choose_space_cannon_target(target)
                    self.report(s.player, f'Targeted {target.upper()} with Space Cannon.', .4)
            return
        if s.stage == 'space_cannon_assign':
            event = s.space_cannon_events[0] if s.space_cannon_events else {}
            target_faction = event.get('target', s.player.faction)
            if target_faction not in self.bot_factions:
                return
            candidates = ctl.space_cannon_assignment_candidates(s)
            if candidates:
                target = min(candidates, key=lambda unit: (
                    loss_value(s.target, unit, sustain=True), unit.unit_id))
                ctl.assign_space_cannon_hit(target.unit_id)
                target_player = ctl.faction_player(target_faction) or s.player
                self.report(target_player, f'Assigned a Space Cannon hit to a {target.kind}.', .45)
            return
        if s.stage == 'capacity_overflow':
            if s.capacity_faction in self.bot_factions:
                tile = w.board[s.capacity_position]
                choices = ctl.capacity_overflow_units(tile, s.capacity_faction)
                selected = sorted(choices, key=lambda u: (u.kind != 'fighter', u.unit_id))
                for unit in selected[:s.capacity_required]:
                    ctl.toggle_capacity_overflow_unit(unit.unit_id)
                ctl.resolve_capacity_overflow()
            return
        if s.stage == 'fleet_overflow':
            if (s.overflow_faction or s.player.faction) in self.bot_factions:
                tile = w.board.get(s.overflow_position, s.target)
                for unit in sorted(ctl.fleet_ships(s), key=lambda u: (
                        loss_value(tile, u), u.unit_id))[:s.overflow_required]:
                    ctl.toggle_overflow_ship(unit.unit_id)
                ctl.resolve_fleet_overflow()
            return
        if not ai_turn:
            return
        # Give a human participant the action-card window and hit assignment.
        if any(not self.is_bot(p) for p in w.action_cards.participants(s)):
            return
        if s.stage == 'movement':
            ships = sum(s.choices[unit_id].kind not in ('infantry', 'fighter')
                        for unit_id in s.selected)
            ctl.confirm()
            if ships:
                self.report(s.player, f'Moved {ships} ships into {s.target.name}.', .9)
        elif s.stage == 'space_cannon_action':
            s.experimental_window_closed = True
            ctl.continue_after_movement(s, s.space_cannon_next_stage or 'invasion')
        elif s.stage in ('space_cannon_response', 'space_cannon_direct_hit'):
            ctl.continue_after_space_cannon(s)
        elif s.stage == 'invasion_start':
            ctl.continue_invasion_start(s)
        elif s.stage == 'bombardment':
            plan_bombardment(s)
            ctl.resolve_bombardment()
            self.report(s.player, f'Bombarded {s.target.name}.', 1.2)
        elif s.stage == 'invasion':
            self.plan_invasion(s)
            ctl.establish_control()
            if s.landed_planets:
                names = ', '.join(p.name for p in s.target.planets
                                  if p.planet_id in s.landed_planets)
                self.report(s.player, f'Landed infantry on {names}.', 1.2)
        elif s.stage in ('space_combat', 'ground_combat', 'combat_end', 'space_combat_won'):
            if s.combat_needs_resolution and not ctl.combat_assignments_complete(s):
                return
            if (s.stage == 'space_combat' and not s.combat_needs_resolution and
                    not s.retreat_announced and
                    s.retreat_blocked_round != s.combat_round + 1 and
                    ctl.retreat_options(s, s.player.faction)):
                own = ctl.combat_units(s, s.player.faction)
                opposing = [unit for faction in s.combat_factions
                            if faction != s.player.faction for unit in ctl.combat_units(s, faction)]
                if own and opposing and win_probability(
                        own, opposing, ctl.players) < .25:
                    ctl.announce_retreat()
            ctl.advance_combat()
            if s.combat_needs_resolution and s.combat_rolls:
                results = ', '.join(f'{faction.upper()} {sum(bool(roll["hit"]) for roll in rolls)} hits'
                                    for faction, rolls in s.combat_rolls.items())
                self.report(s.player, f'Combat round {s.combat_round}: {results}.', 2.1)
        elif s.stage == 'production':
            self.resolve_production()

    def resolve_production(self):
        ctl, s = self.window.movement, self.window.movement.session
        player = s.player
        planned = build_plan(ctl, s, self.window.turn_order.round_number)
        if not planned:
            ctl.skip_production()
            return
        discount = int('st' in player.technologies and not s.integrated_current)
        total_cost = max(0, ceil(sum(ctl.unit_cost(k, player) * n for k, n in planned.items())) -
                         discount)
        payment = payment_plan(player, total_cost, lambda c: c.planet.resources,
                               resource_opportunity)
        if payment is None:
            ctl.skip_production()
            return
        s.production_choices.update(planned)
        for planet_id in payment[0]:
            ctl.toggle_production_planet(planet_id)
        s.trade_goods_to_spend = payment[1]
        ctl.produce()
        summary = ', '.join(f'{count} {kind}' for kind, count in sorted(planned.items()))
        self.report(player, f'Produced {summary} in {s.target.name}.', 1.4)
