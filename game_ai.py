"""Deterministic opponents using the same turn and action controllers as players.

The weights are deliberately visible here: expansion is valuable to everyone,
while a contested human planet receives 65 points of pressure versus 35 for a
bot-owned planet.  Legality (movement, capacity, payment, combat) stays with the
game controllers rather than being reimplemented as a second rules engine.
"""
from __future__ import annotations

from collections import Counter
from collections import deque
from itertools import combinations
from math import ceil

from movement import MovementError, capital_ship, cargo_cost
from ai_combat import win_probability
from action_cards import canonical_action_card
from player import command_tokens_in_reinforcements
from technology import (available_technologies, missing_prerequisites,
                        production_allowed)
from units import Region, UNIT_TYPES, unit_profile


TECH_PRIORITY = {
    'gd': 100, 'st': 88, 'cv2': 84, 'ff2': 80, 'dn2': 75,
    'fl': 70, 'det': 67, 'hm': 65, 'ps': 58, 'ng': 55,
    'lwd': 52, 'ca2': 51, 'gf2': 48, 'nm': 46,
}
SHIP_VALUE = {'fighter': 0.8, 'destroyer': 1.4, 'cruiser': 2.4,
              'carrier': 1.7, 'dreadnought': 4.4, 'flagship': 6.5, 'warsun': 9.0}
LOSS_ORDER = {'fighter': 0, 'destroyer': 2, 'cruiser': 3,
              'carrier': 5, 'dreadnought': 6, 'flagship': 8, 'warsun': 10,
              'infantry': 0}
STRATEGY_NAMES = {1: 'Leadership', 2: 'Diplomacy', 3: 'Politics',
                  4: 'Construction', 5: 'Trade', 6: 'Warfare',
                  7: 'Technology', 8: 'Imperial'}
MIN_ATTACK_WIN_CHANCE = .80


def payment_plan(player, amount, value):
    """Choose ready planets with least overspend, then the fewest trade goods."""
    if amount <= 0:
        return (), 0
    cards = [card for card in player.planets if not card.exhausted and value(card) > 0]
    # Resource/influence targets are small; dynamic programming avoids the
    # exponential search over an established empire's planet cards.
    options = {0: ()}
    for card in cards:
        points = value(card)
        for total, chosen in tuple(options.items()):
            new_total = total + points
            candidate = chosen + (card.planet.planet_id,)
            if new_total not in options or len(candidate) < len(options[new_total]):
                options[new_total] = candidate
    feasible = []
    for total, chosen in options.items():
        goods = max(0, amount - total)
        if goods <= player.trade_goods:
            feasible.append((max(0, total - amount), goods, len(chosen), chosen))
    if not feasible:
        return None
    _, goods, _, chosen = min(feasible)
    return chosen, goods


class GameAI:
    def __init__(self, window, bot_factions):
        self.window = window
        self.bot_factions = set(bot_factions)
        self.wait = 0.0
        self.paused = False
        self.speed = 1.0
        self.events = deque(maxlen=3)
        self.focus_positions = ()
        self.attack_odds = None
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
        self.wait -= delta_time
        if self.wait > 0 or self.thinking or self.window.main_menu_visible:
            return
        self.wait = .55 / self.speed
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

    def report(self, player, message, duration=2.0):
        faction = player.faction.upper() if player else 'AI'
        self.events.appendleft((faction, message))
        self.wait = max(self.wait, duration / self.speed)

    def cycle_speed(self):
        speeds = (1.0, 2.0, 4.0, .5)
        self.speed = speeds[(speeds.index(self.speed) + 1) % len(speeds)]
        self.wait = min(self.wait, .55 / self.speed)

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
        if w.action_cards.pending or w.transaction.session:
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
        if turn.action_used or not turn.can_take_action:
            self.report(player, 'Ended the turn.', 1.3)
            w.pass_turn()
            return
        cards = [card for card in turn.strategy_assignments[player.faction]
                 if card not in turn.strategy_used[player.faction]]
        move = self.best_activation(player) if player.command_pools['tactical'] else None
        if self.opening_expansion(player, move):
            self.start_activation(player, move)
        elif cards and (not move or self.strategy_value(player, cards[0]) >= move[0] - 3):
            card = max(cards, key=lambda item: (self.strategy_value(player, item), -item))
            w.strategy.start(card)
            self.report(player, f'Played {STRATEGY_NAMES[card]}.')
            w.sync_strategy_actor()
        elif move:
            self.start_activation(player, move)
        elif cards:
            card = max(cards, key=lambda item: (self.strategy_value(player, item), -item))
            w.strategy.start(card)
            self.report(player, f'Played {STRATEGY_NAMES[card]}.')
            w.sync_strategy_actor()
        else:
            self.report(player, 'Passed for the rest of the round.', 2.2)
            w.pass_turn()

    def command_pool(self, player):
        pools = player.command_pools
        if pools['tactical'] < 3:
            return 'tactical'
        if pools['fleet'] < 4 and self.window.turn_order.round_number >= 2:
            return 'fleet'
        if pools['strategic'] < 2:
            return 'strategic'
        return min(('tactical', 'fleet', 'strategic'), key=lambda name: (pools[name],
                    {'tactical': 0, 'fleet': 1, 'strategic': 2}[name]))

    def strategy_value(self, player, card):
        pools = player.command_pools
        ready_resources = player.available_values[0] + player.trade_goods
        docks = sum(unit.owner == player.faction and unit.kind == 'spacedock'
                    for tile in self.window.board.values() for unit in tile.units)
        scores = {1: 13 + 5 * (pools['tactical'] <= 1),
                  2: 7 + 3 * sum(p.exhausted for p in player.planets),
                  3: 5 + 3 * (self.window.turn_order.speaker is not player),
                  4: 12 + 5 * (docks <= 1),
                  5: 11 + 3 * (player.trade_goods < 3),
                  6: 10 + 2 * any(player.faction in t.command_tokens for t in self.window.board.values()),
                  7: 15 + 3 * (ready_resources >= 6),
                  8: 1}
        if player.faction == 'jolnar' and card == 7:
            scores[7] += 5
        if player.faction == 'hacan' and card == 5:
            scores[5] += 4
        return scores[card]

    def choose_strategy_card(self, player):
        turn = self.window.turn_order
        already = set(turn.strategy_assignments[player.faction])
        return max(turn.available_strategy_cards, key=lambda card: (
            self.strategy_value(player, card) - (6 if card in already else 0), -card))

    def _tile_threat(self, tile, faction):
        return sum(SHIP_VALUE.get(unit.kind, 0) for unit in tile.units
                   if unit.owner != faction and unit.location.region == Region.SPACE)

    def _force(self, ships):
        return sum(SHIP_VALUE.get(unit.kind, 0) + (.6 if unit_profile(unit).get('sustainDamage') else 0)
                   for unit in ships)

    def best_activation(self, player):
        movement = self.window.movement
        best = None
        best_odds = None
        human = self.human_faction()
        flank = any(canonical_action_card(card) == 'flank_speed'
                    for card in player.action_cards)
        for target in self.window.board.values():
            if player.faction in target.command_tokens:
                continue
            friendly = [u for u in target.units if u.owner == player.faction and
                        u.location.region == Region.SPACE and UNIT_TYPES[u.kind]['ship']]
            enemies = [u for u in target.units if u.owner != player.faction and
                       u.location.region == Region.SPACE and UNIT_TYPES[u.kind]['ship']]
            cannons = [unit for area in (target, *movement.neighbors(target))
                       for unit in area.units if unit.kind == 'pds' and
                       unit.owner != player.faction and
                       unit_profile(unit).get('spaceCannonHitsOn') and
                       (area is target or unit_profile(unit).get('deepSpaceCannon'))]
            owned = [p for p in target.planets if target.planet_owners.get(p.planet_id) == player.faction]
            neutral = [p for p in target.planets if not target.planet_owners.get(p.planet_id)]
            enemy_planets = [p for p in target.planets if target.planet_owners.get(p.planet_id)
                             not in (None, player.faction)]
            target_owner = next((target.planet_owners[p.planet_id] for p in enemy_planets), None)
            pressure = 65 if target_owner == human else 35
            planet_value = sum(2 + p.resources + .5 * p.influence for p in neutral)
            planet_value += pressure / 10 * sum(1 + .3 * p.resources for p in enemy_planets)
            if enemy_planets and not neutral and self.window.turn_order.round_number == 1:
                planet_value *= .65
            dock = any(u.owner == player.faction and u.kind == 'spacedock' and
                       target.planet_owners.get(u.location.planet_id) == player.faction
                       for u in target.units)
            can_expand_from_here = (
                self.window.turn_order.round_number == 1 and
                any(p.faction_homeworld == player.faction for p in target.planets) and
                any(u.owner == player.faction and u.kind == 'infantry' for u in target.units) and
                any(u.owner == player.faction and u.kind == 'carrier' for u in target.units) and
                any(any(not neighbor.planet_owners.get(p.planet_id) for p in neighbor.planets)
                    and player.faction not in neighbor.command_tokens and
                    any(movement.route(target, neighbor, ship, player) for ship in target.units
                        if ship.owner == player.faction and ship.kind == 'carrier')
                    for neighbor in movement.neighbors(target)))
            if dock and not can_expand_from_here:
                budget = player.available_values[0] + player.trade_goods
                local_count = sum(u.owner == player.faction for u in target.units)
                score = (9 + min(6, budget) + (2 if local_count < 6 else 0)
                         if budget >= 1 else 0)
                candidate = (score, target.position, target.position)
                if best is None or candidate > best:
                    best = candidate
                    best_odds = None
            if planet_value <= 0:
                continue
            for source in self.window.board.values():
                if source is target or player.faction in source.command_tokens:
                    continue
                candidates = [u for u in source.units if u.owner == player.faction and
                              (capital_ship(u) or (u.kind == 'fighter' and
                                                   'ff2' in player.technologies and
                                                   u.location.region == Region.SPACE)) and
                              (movement.route(source, target, u, player) or
                               ('gd' in player.technologies and
                                movement.route(source, target, u, player, bonus=1)) or
                               (flank and movement.route(source, target, u, player, bonus=1)))]
                if not candidates:
                    continue
                slots = max(0, movement.fleet_supply(player) -
                            sum(capital_ship(unit) for unit in friendly))
                ranked = sorted(candidates, key=lambda u: (
                    -int(u.kind == 'carrier' and any(p.owner == player.faction and
                                                     p.kind == 'infantry' for p in source.units)),
                    -SHIP_VALUE.get(u.kind, 0), u.unit_id))
                keep = int(any(p.faction_homeworld == player.faction for p in source.planets)
                           and len(ranked) > 1)
                ships = ranked[:max(0, min(slots, len(ranked) - keep))]
                if not ships:
                    continue
                ship_ids = {unit.unit_id for unit in ships}
                auto_cargo = [unit for unit in source.units if unit.owner == player.faction and
                              unit.location.region == Region.TRANSPORT and
                              unit.location.carrier_id in ship_ids]
                capacity = max(0, sum(unit.capacity for unit in ships) -
                               sum(cargo_cost(unit) for unit in auto_cargo))
                ground = [unit for unit in auto_cargo if unit.kind == 'infantry']
                reserves = set()
                for planet in source.planets:
                    defenders = sorted((unit for unit in source.units if
                                        unit.owner == player.faction and unit.kind == 'infantry' and
                                        unit.location.planet_id == planet.planet_id),
                                       key=lambda unit: unit.unit_id)
                    if len(defenders) >= 2 and source.planet_owners.get(planet.planet_id) == player.faction:
                        reserves.add(defenders[0].unit_id)
                passengers = sorted((unit for unit in source.units if unit.owner == player.faction and
                                     unit.unit_id not in reserves and
                                     (unit.kind == 'infantry' and unit.location.region == Region.PLANET or
                                      unit.kind == 'fighter' and unit.location.region == Region.SPACE and
                                      unit.unit_id not in ship_ids)),
                                    key=lambda unit: (unit.kind != 'infantry', unit.unit_id))
                carried_fighters = [unit for unit in auto_cargo if unit.kind == 'fighter']
                for passenger in passengers:
                    cost = cargo_cost(passenger)
                    if cost <= capacity:
                        capacity -= cost
                        if passenger.kind == 'infantry':
                            ground.append(passenger)
                        else:
                            carried_fighters.append(passenger)
                if (neutral or enemy_planets) and not ground:
                    continue
                attack_fleet = ships + carried_fighters + friendly
                space_odds = (win_probability(attack_fleet, enemies,
                                              self.window.turn_order.players, cannons=cannons,
                                              require_transport=bool(neutral or enemy_planets))
                              if enemies or cannons else 1.0)
                if space_odds < MIN_ATTACK_WIN_CHANCE:
                    continue
                defenders = sum(u.owner != player.faction and u.kind == 'infantry'
                                for u in target.units)
                if enemy_planets and len(ground) < max(1, defenders):
                    continue
                ground_chance = None
                if enemy_planets and not neutral:
                    ground_odds = [win_probability(ground, [unit for unit in target.units
                                                            if unit.kind == 'infantry' and
                                                            unit.owner != player.faction and
                                                            unit.location.planet_id == planet.planet_id],
                                                   self.window.turn_order.players, space=False,
                                                   cannons=[unit for unit in target.units
                                                            if unit.kind == 'pds' and
                                                            unit.owner != player.faction and
                                                            unit.location.planet_id == planet.planet_id])
                                   for planet in enemy_planets]
                    ground_chance = max(ground_odds, default=1.0)
                    if ground_chance < MIN_ATTACK_WIN_CHANCE:
                        continue
                # Fewer ships are committed to a soft target; preserve home defence.
                source_defence = 1.5 if any(p.faction_homeworld == player.faction for p in source.planets) else 0
                score = 9 + planet_value + min(4, len(ground)) - .6 * len(ships) - source_defence
                score += 4 * (space_odds - MIN_ATTACK_WIN_CHANCE) if enemies or cannons else 0
                candidate = (score, target.position, source.position)
                if best is None or candidate > best:
                    best = candidate
                    best_odds = (space_odds if enemies or cannons else None, ground_chance)
        self.attack_odds = best_odds
        return best if best and best[0] > 0 else None

    def start_activation(self, player, move):
        _, target_position, source_position = move
        movement = self.window.movement
        session = movement.activate(player, target_position)
        if source_position != target_position:
            source = session.sources.get(source_position)
            if source is None:
                flank_index = next((index for index, card in enumerate(player.action_cards)
                                    if canonical_action_card(card) == 'flank_speed' and
                                    self.window.action_cards.can_play(player.faction, card, session)), None)
                if flank_index is not None:
                    self.window.action_cards.play(player, flank_index)
                    source = session.sources.get(source_position)
            if source:
                target_fleet = sum(u.owner == player.faction and capital_ship(u)
                                   for u in session.target.units)
                slots = max(0, movement.fleet_supply(player) - target_fleet)
                ships = sorted(source.ships, key=lambda u: (
                    -int(u.kind == 'carrier' and bool(source.passengers)),
                    -SHIP_VALUE.get(u.kind, 0), u.unit_id))
                keep = 1 if any(p.faction_homeworld == player.faction for p in source.tile.planets) and len(ships) > 1 else 0
                for ship in ships[:max(0, min(slots, len(ships) - keep))]:
                    try:
                        session.toggle(ship.unit_id)
                    except MovementError:
                        continue
                capacity = sum(u.capacity for u in session.ships(source))
                reserves = set()
                for planet in source.tile.planets:
                    defenders = sorted((u for u in source.passengers if u.kind == 'infantry' and
                                        u.location.planet_id == planet.planet_id),
                                       key=lambda u: u.unit_id)
                    if len(defenders) >= 2 and source.tile.planet_owners.get(planet.planet_id) == player.faction:
                        reserves.add(defenders[0].unit_id)
                cargo = sorted((u for u in source.passengers if u.unit_id not in reserves),
                               key=lambda u: (u.kind != 'infantry', u.unit_id))
                for unit in cargo:
                    if capacity <= 0:
                        break
                    try:
                        session.toggle(unit.unit_id)
                        capacity -= 1
                    except MovementError:
                        continue
        self.window.frame_action_route(source_position, target_position)
        self.focus_positions = (source_position, target_position)
        self.window.movement_panel.reset()
        ships = sum(u.kind not in ('fighter', 'infantry') for u in
                    (session.choices[unit_id] for unit_id in session.selected))
        cargo = len(session.selected) - ships
        target = session.target.name
        if source_position == target_position:
            self.report(player, f'Activated {target} to produce units.', 2.7)
        else:
            odds = self.attack_odds or (None, None)
            chance = next((value for value in odds if value is not None), None)
            forecast = f' · win chance ~{chance:.0%}' if chance is not None else ''
            self.report(player, f'Activated {target}: {ships} ships, {cargo} passengers{forecast}.', 2.7)

    def resolve_strategy(self):
        w = self.window
        ctl, s = w.strategy, w.strategy.session
        player = s.player
        if s.stage == 'offer':
            if s.card in (3, 5, 7) or (s.card == 1 and command_tokens_in_reinforcements(player, w.board)) or \
                    (s.card == 2 and ctl.readyable_planets(player)) or \
                    (s.card == 4 and any(p.planet.resources >= 1 for p in player.planets)) or \
                    (s.card == 6 and ctl.home_docks()):
                if not ctl.secondary_unavailable():
                    ctl.accept_secondary(brilliant=(player.faction == 'jolnar' and s.card == 7))
                    self.report(player, f'Accepted {STRATEGY_NAMES[s.card]} secondary.', 1.1)
                    return
            ctl.decline_secondary()
        elif s.stage == 'leadership':
            reserve = command_tokens_in_reinforcements(player, w.board) - s.base_gain
            budget = sum(c.planet.influence for c in player.planets if not c.exhausted) + player.trade_goods
            count = min(max(0, reserve), budget // 3, 2 if s.primary else 1)
            if count:
                plan = payment_plan(player, count * 3, lambda c: c.planet.influence)
                if plan:
                    s.purchases = count
                    for planet_id in plan[0]:
                        ctl.toggle_payment(planet_id)
                    s.trade_goods = plan[1]
            ctl.pay_leadership()
            self.report(player, f'Gained {s.base_gain + s.purchases} command tokens.', 1.7)
        elif s.stage == 'allocate':
            if player.pending_commands:
                ctl.allocate(self.command_pool(player))
            else:
                ctl.allocation_changed()
        elif s.stage == 'diplomacy_system':
            options = ctl.selectable_systems()
            if options:
                position = max(options, key=lambda pos: (
                    sum(p.resources + p.influence for p in w.board[pos].planets), pos))
                ctl.select_system(position)
                self.report(player, f'Diplomacy protected {w.board[position].name}.', 2.2)
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
            self.report(player, f'Chose {chosen.faction.upper()} as Speaker.', 2.0)
        elif s.stage == 'trade':
            allies = [p for p in w.turn_order.players if p is not player and self.is_bot(p)]
            if allies:
                s.free_trade.add(min(allies, key=lambda p: (p.trade_goods, p.faction)).faction)
            ctl.confirm_trade()
            self.report(player, 'Gained 3 trade goods and replenished commodities.', 2.0)
        elif s.stage == 'warfare_system':
            options = ctl.selectable_systems()
            if not options:
                ctl.continue_stage()
            else:
                position = max(options, key=lambda pos: (
                    sum(p.resources for p in w.board[pos].planets), pos))
                ctl.select_system(position)
                ctl.select_warfare_token(position, player.faction)
                ctl.confirm_warfare_removal()
                self.report(player, f'Warfare removed a token from {w.board[position].name}.', 1.7)
        elif s.stage == 'warfare_allocate':
            if player.pending_commands:
                ctl.allocate(self.command_pool(player))
            else:
                ctl.continue_stage()
        elif s.stage == 'construction':
            if s.builds_left == 1:
                s.structure = 'pds'
            else:
                s.structure = 'spacedock' if sum(u.owner == player.faction and u.kind == 'spacedock'
                                                 for t in w.board.values() for u in t.units) < 2 else 'pds'
            planets = ctl.buildable_planets(selected_only=False)
            if not planets:
                ctl.continue_stage()
            else:
                card = max(planets, key=lambda c: (c.planet.resources,
                            self._tile_threat(ctl.planet_system(c.planet.planet_id), player.faction),
                            c.planet.planet_id))
                tile = ctl.planet_system(card.planet.planet_id)
                kind = s.structure
                ctl.select_system(tile.position)
                ctl.build(card.planet.planet_id)
                self.report(player, f'Built {kind} on {card.planet.name}.', 2.2)
        elif s.stage == 'production_site':
            docks = ctl.home_docks()
            if docks:
                ctl.produce_at(max(docks, key=lambda item: (
                    next(p.resources for p in item[0].planets
                         if p.planet_id == item[1].location.planet_id), item[1].unit_id))[1].unit_id)
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
                self.report(player, f'Researched {alias}.', 2.2)
            else:
                ctl.continue_stage()
        elif s.stage == 'placeholder':
            ctl.continue_stage()
        elif s.stage == 'production':
            ctl.poll()

    def choose_technology(self, player, cost):
        cards = [c for c in player.planets if not c.exhausted]
        specialties = [c for c in cards if c.planet.tech_specialties]
        best = None
        for tech in available_technologies(player):
            if tech['alias'] in player.technologies:
                continue
            for n in range(min(2, len(specialties)) + 1):
                found = False
                for selected in combinations(specialties, n):
                    if missing_prerequisites(player, tech, selected):
                        continue
                    eligible = type('PaymentPlayer', (), {
                        'planets': [c for c in cards if c not in selected],
                        'trade_goods': player.trade_goods})()
                    payment = payment_plan(eligible, cost, lambda c: c.planet.resources)
                    if payment is None:
                        continue
                    alias = tech['alias']
                    score = (TECH_PRIORITY.get(alias, 32) +
                             (8 if alias == 'cv2' and self.window.turn_order.round_number <= 2 else 0) -
                             5 * n - 2 * payment[1])
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
        for unit in units:
            if ctl.combat_assignment_target(session, faction, unit.kind) is None:
                continue
            sustain = bool(unit_profile(unit).get('sustainDamage') and not unit.damaged and
                           unit.unit_id not in assigned)
            choices.append(((-1 if sustain else LOSS_ORDER.get(unit.kind, 4)), unit.kind))
        if choices:
            ctl.assign_combat_hit(faction, min(choices)[1])
            return True
        return False

    def play_combat_card(self, session):
        cards = self.window.action_cards
        for player in cards.participants(session):
            if not self.is_bot(player):
                continue
            for index, alias in cards.playable(player, session):
                key = canonical_action_card(alias)
                if key in ('morale_boost', 'fighter_prototype'):
                    if len(self.window.movement.combat_units(session, player.faction)) < 2:
                        continue
                if key == 'flank_speed' and session.sources:
                    continue
                if key == 'shields_holding' and session.combat_hits.get(player.faction, 0) < 2:
                    continue
                if key not in ('morale_boost', 'fighter_prototype', 'flank_speed',
                               'shields_holding', 'emergency_repairs', 'maneuvering_jets',
                               'salvage', 'intercept', 'bunker', 'disable'):
                    continue
                cards.play(player, index)
                self.report(player, f'Played action card {cards.name_for(alias)}.', 2.1)
                return True
        return False

    def resolve_movement(self):
        w = self.window
        ctl, s = w.movement, w.movement.session
        ai_turn = self.is_bot(s.player)
        if self.play_combat_card(s):
            return
        if s.stage in ('space_combat', 'ground_combat') and s.combat_needs_resolution:
            for faction in s.combat_factions:
                if faction in self.bot_factions and self._bot_hit(faction, s):
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
                self.report(s.player, f'Moved {ships} ships into {s.target.name}.', 2.5)
        elif s.stage == 'space_cannon_action':
            s.experimental_window_closed = True
            ctl.continue_after_movement(s, s.space_cannon_next_stage or 'invasion')
        elif s.stage in ('space_cannon_response', 'space_cannon_direct_hit'):
            ctl.continue_after_space_cannon(s)
        elif s.stage == 'invasion_start':
            ctl.continue_invasion_start(s)
        elif s.stage == 'bombardment':
            hostile = [p.planet_id for p in s.target.planets if
                       s.target.planet_owners.get(p.planet_id) not in (None, s.player.faction)]
            for unit_id in s.bombard_targets:
                s.bombard_targets[unit_id] = hostile[0] if hostile else None
            ctl.resolve_bombardment()
            self.report(s.player, f'Bombarded {s.target.name}.', 2.2)
        elif s.stage == 'invasion':
            targets = [p for p in s.target.planets if
                       s.target.planet_owners.get(p.planet_id) != s.player.faction]
            if not targets:
                targets = list(s.target.planets)
            undefended = sorted((p for p in targets if not any(
                u.owner != s.player.faction and u.kind == 'infantry' and
                u.location.planet_id == p.planet_id for u in s.target.units)),
                key=lambda p: (-(p.resources + p.influence), p.planet_id))
            defended = sorted((p for p in targets if p not in undefended),
                key=lambda p: (sum(u.owner != s.player.faction and u.kind == 'infantry' and
                                   u.location.planet_id == p.planet_id for u in s.target.units),
                               -(p.resources + p.influence), p.planet_id))
            for index, unit_id in enumerate(s.landings):
                if index < len(undefended):
                    s.landings[unit_id] = undefended[index].planet_id
                elif defended:
                    s.landings[unit_id] = defended[0].planet_id
                elif undefended:
                    s.landings[unit_id] = undefended[0].planet_id
            ctl.establish_control()
            if s.landed_planets:
                names = ', '.join(p.name for p in s.target.planets
                                  if p.planet_id in s.landed_planets)
                self.report(s.player, f'Landed infantry on {names}.', 2.5)
        elif s.stage == 'assault_choice':
            victims = ctl.assault_victims(s)
            if victims:
                ctl.choose_assault_victim(min(victims, key=lambda u: (
                    LOSS_ORDER.get(u.kind, 4), u.unit_id)).unit_id)
        elif s.stage in ('space_combat', 'ground_combat', 'combat_end', 'space_combat_won'):
            if s.combat_needs_resolution and not ctl.combat_assignments_complete(s):
                return
            if (s.stage == 'space_combat' and not s.combat_needs_resolution and
                    self._force(ctl.combat_units(s, s.player.faction)) <
                    .6 * sum(self._force(ctl.combat_units(s, faction)) for faction in s.combat_factions
                             if faction != s.player.faction) and ctl.retreat_options(s, s.player.faction)):
                ctl.announce_retreat()
            ctl.advance_combat()
            if s.combat_needs_resolution and s.combat_rolls:
                results = ', '.join(f'{faction.upper()} {sum(bool(roll["hit"]) for roll in rolls)} hits'
                                    for faction, rolls in s.combat_rolls.items())
                self.report(s.player, f'Combat round {s.combat_round}: {results}.', 3.0)
        elif s.stage == 'retreat_selection':
            options = ctl.retreat_options(s, s.retreat_announced)
            if options:
                ctl.resolve_retreat(max(options, key=lambda tile: (
                    sum(p.resources for p in tile.planets), tile.position)).position)
        elif s.stage == 'capacity_overflow':
            tile = w.board[s.capacity_position]
            choices = ctl.capacity_overflow_units(tile, s.capacity_faction)
            for unit in sorted(choices, key=lambda u: (u.kind != 'fighter', u.unit_id))[:s.capacity_required]:
                ctl.toggle_capacity_overflow_unit(unit.unit_id)
            ctl.resolve_capacity_overflow()
        elif s.stage == 'fleet_overflow':
            for unit in sorted(ctl.fleet_ships(s), key=lambda u: (
                    LOSS_ORDER.get(u.kind, 4), u.unit_id))[:s.overflow_required]:
                ctl.toggle_overflow_ship(unit.unit_id)
            ctl.resolve_fleet_overflow()
        elif s.stage == 'production':
            self.resolve_production()

    def resolve_production(self):
        ctl, s = self.window.movement, self.window.movement.session
        player = s.player
        budget = player.available_values[0] + player.trade_goods
        discount = int('st' in player.technologies)
        if budget + discount <= 0 or s.production_limit <= 0:
            ctl.skip_production()
            return
        local = Counter(u.kind for u in s.target.units if u.owner == player.faction)
        planned = Counter()
        neutral = any(s.target.planet_owners.get(p.planet_id) != player.faction for p in s.target.planets)
        neighbors = ctl.neighbors(s.target)
        frontier = any(any(n.planet_owners.get(p.planet_id) != player.faction for p in n.planets)
                       for n in neighbors)
        enemy_fighters = sum(u.kind == 'fighter' and u.owner != player.faction
                             for n in neighbors for u in n.units)
        dock_slots = 3 * local['spacedock']
        capacity = sum(u.capacity for u in s.target.units if u.owner == player.faction and
                       u.location.region == Region.SPACE)
        current_cargo = sum(u.owner == player.faction and
                            (u.location.region == Region.TRANSPORT or
                             (u.kind == 'fighter' and u.location.region == Region.SPACE))
                            for u in s.target.units)
        fleet_used = sum(u.owner == player.faction and capital_ship(u) for u in s.target.units)
        fleet_limit = ctl.fleet_supply(player)
        max_budget = min(budget + discount, 16)
        # Establish a transport and an escort before spending all production
        # slots on cheap infantry/fighters.  A dock without a carrier cannot
        # turn its ground army into territorial expansion.
        if frontier and not local['carrier'] and fleet_used < fleet_limit and max_budget >= 3:
            planned['carrier'] = 1
        opening_cost = ceil(sum(ctl.unit_cost(k, player) * n for k, n in planned.items()))
        combat_ships = local['destroyer'] + local['cruiser'] + local['dreadnought']
        if (combat_ships == 0 and fleet_used + planned['carrier'] < fleet_limit and
                sum(planned.values()) < s.production_limit and max_budget - opening_cost >= 1):
            available = max_budget - opening_cost
            if enemy_fighters >= 2:
                escort = 'destroyer'
            elif (self.window.turn_order.round_number >= 4 and available >= 8 and
                  not local['flagship']):
                escort = 'flagship'
            elif self.window.turn_order.round_number >= 2 and available >= 4:
                escort = 'dreadnought'
            else:
                escort = 'cruiser' if available >= 2 else 'destroyer'
            planned[escort] = 1
        while sum(planned.values()) < s.production_limit:
            options = []
            for kind in ('carrier', 'infantry', 'fighter', 'destroyer', 'cruiser', 'dreadnought',
                         'flagship', 'warsun'):
                if not production_allowed(player, kind):
                    continue
                cost = ctl.unit_cost(kind, player)
                new_cost = ceil(sum(ctl.unit_cost(k, player) * n for k, n in planned.items()) + cost)
                if new_cost > max_budget:
                    continue
                if kind not in ('infantry', 'fighter') and fleet_used + sum(
                        planned[k] for k in planned if k not in ('infantry', 'fighter')) >= fleet_limit:
                    continue
                available_capacity = capacity + planned['carrier'] * 4 + dock_slots - current_cargo - planned['infantry'] - planned['fighter']
                if kind == 'fighter' and available_capacity <= 0:
                    continue
                total = local[kind] + planned[kind]
                if kind == 'infantry' and total >= (4 if frontier else 2):
                    continue
                if kind == 'fighter' and total >= 4:
                    continue
                if kind == 'carrier':
                    value = 12 if frontier and total == 0 else 7 if total < 2 else 2
                elif kind == 'infantry':
                    value = 7 if frontier and total < 3 else 3 if total < 5 else .5
                elif kind == 'fighter':
                    value = 5.5 if total < 3 else 2.4 if total < 6 else .4
                elif kind == 'destroyer':
                    value = 8 if enemy_fighters >= 2 and total < 2 else 4 if total < 2 else 1
                elif kind == 'cruiser':
                    value = 9 if total < 2 else 5 if total < 4 else 1.5
                elif kind == 'dreadnought':
                    value = 11 if total < 2 else 7 if total < 4 else 2
                else:
                    value = 7 if total == 0 and self.window.turn_order.round_number >= 3 else 1
                if neutral and kind == 'infantry':
                    value += 2
                options.append((value / max(.5, cost), value, kind))
            if not options:
                break
            _, best_value, chosen = max(options)
            if best_value < 1:
                break
            planned[chosen] += 1
        if not planned:
            ctl.skip_production()
            return
        total_cost = max(0, ceil(sum(ctl.unit_cost(k, player) * n for k, n in planned.items())) -
                         discount)
        payment = payment_plan(player, total_cost, lambda c: c.planet.resources)
        if payment is None:
            ctl.skip_production()
            return
        s.production_choices.update(planned)
        for planet_id in payment[0]:
            ctl.toggle_production_planet(planet_id)
        s.trade_goods_to_spend = payment[1]
        ctl.produce()
        summary = ', '.join(f'{count} {kind}' for kind, count in sorted(planned.items()))
        self.report(player, f'Produced {summary} in {s.target.name}.', 3.0)
