"""Deterministic opponents using the same turn and action controllers as players.

The weights are deliberately visible: expansion is valuable to everyone,
while contested human planets have more strategic value than bot-owned ones.
Legality (movement, capacity, payment, combat) stays with the game controllers.
"""
from __future__ import annotations

from collections import deque
from itertools import combinations
from math import ceil

from movement import MovementError, Snapshot, capital_ship
from ai_combat import win_probability
from ai_planner import (SHIP_VALUE,
                        best_activation as plan_best_activation, plan_landings)
from ai_economy import MODEL_LIMITS, build_plan, command_targets, technology_values
from ai_actions import (loss_value, plan_bombardment, play_useful_card,
                        refit_plan, resolve_card_choice, war_effort_plan)
from ai_strategy import construction_choice, diplomacy_value, warfare_value
from action_cards import canonical_action_card
from player import command_tokens_in_reinforcements
from technology import (available_technologies, can_use_specialty_planet,
                        missing_prerequisites, production_allowed,
                        unit_upgrade_kind)
from units import Region, UNIT_TYPES, unit_profile


TECH_PRIORITY = {
    'gd': 100, 'st': 88, 'cv2': 84, 'ac2': 85, 'ff2': 80, 'dn2': 75,
    'fl': 70, 'dd2': 67, 'hm': 65, 'ps': 58, 'nes': 76,
    'lwd': 52, 'cr2': 62, 'inf2': 55, 'so2': 60, 'nm': 46,
    'amd': 50, 'da': 52, 'asc': 58, 'td': 53, 'ie': 57,
    'pds2': 45, 'sd2': 48, 'ws': 52, 'gls': 42,
    'qdn': 63, 'pm': 56, 'ers': 55, 'scc': 48, 'l4': 42,
    'dxa': 35, 'md': 42, 'x89c4': 40,
    'det': 30, 'sr': 49, 'pa': 46, 'bs': 43,
    'aida': 57, 'sar': 41, 'sdn': 44, 'pi': 43,
}
LOSS_ORDER = {'fighter': 0, 'destroyer': 2, 'cruiser': 3,
              'carrier': 5, 'dreadnought': 6, 'flagship': 8, 'warsun': 10,
              'infantry': 0, 'mech': 3}
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
        self.hero_secondary_queue: deque[tuple[str, int]] = deque()

    def is_bot(self, player):
        return bool(player and player.faction in self.bot_factions)

    def human_faction(self):
        return next((p.faction for p in self.window.turn_order.players
                     if p.faction not in self.bot_factions), None)

    def _planet_values(self, player, card):
        exploration = getattr(self.window, 'exploration', None)
        resources, influence = (exploration.planet_values(card.planet)
                                if exploration else
                                (card.planet.resources, card.planet.influence))
        participants = getattr(self.window.turn_order, 'players', (player,))
        if any(getattr(card.planet, 'planet_id', None) in
               getattr(participant, 'hero_attachments', ())
               for participant in participants):
            resources += 3
            influence += 3
        return resources, influence

    def _available_values(self, player):
        ready = [card for card in player.planets if not card.exhausted]
        return (sum(self._planet_values(player, card)[0] for card in ready),
                sum(self._planet_values(player, card)[1] for card in ready))

    def _specialties(self, card):
        exploration = getattr(self.window, 'exploration', None)
        return (exploration.planet_specialties(card.planet) if exploration else
                card.planet.tech_specialties)

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
        exploration = getattr(w, 'exploration', None)
        if exploration is not None and exploration.secret_objectives.pending_returns:
            pending_returns = exploration.secret_objectives.pending_returns
            for faction in sorted(pending_returns):
                owner = exploration.players[faction]
                if self.is_bot(owner):
                    hand = exploration.secret_objectives.hands[faction]
                    catalog = exploration.secret_objectives.catalog
                    alias = min(hand, key=lambda item: (
                        catalog[item].points, catalog[item].phase != 'Action', item))
                    exploration.secret_objectives.return_unscored(faction, alias)
                    w.movement.resolve_exploration_queue()
                    advance = getattr(w, 'advance_hero_frontier', None)
                    if advance:
                        advance()
                    self.report(owner, 'Returned an excess secret objective.', .2)
                return
        if exploration is not None and exploration.terragenesis_pending is not None:
            owner = exploration.players[exploration.terragenesis_pending.faction]
            if self.is_bot(owner):
                sources = w.movement.terragenesis_sources()
                source = min(sources, key=lambda entry: (
                    entry[0].planet_owners.get(entry[1].planet_id) != owner.faction,
                    not any(unit.owner == owner.faction and unit.kind == 'pds' and
                            unit.location.planet_id == entry[1].planet_id
                            for unit in entry[0].units),
                    entry[1].planet_id)) if sources else None
                w.movement.resolve_terragenesis(source[1].planet_id if source else None)
                self.report(owner, 'Resolved Terragenesis sleeper placement.', .2)
            return
        if exploration is not None and exploration.pending:
            pending = exploration.pending
            owner = exploration.players[pending.faction]
            if self.is_bot(owner):
                choice = self._exploration_choice(owner, pending)
                w.movement.resolve_exploration_choice(choice)
                advance = getattr(w, 'advance_hero_frontier', None)
                if advance:
                    advance()
                self.report(owner, 'Resolved exploration.', .2)
            return
        if exploration is not None and exploration.freelancer_offer:
            owner = exploration.players[exploration.freelancer_offer[0]]
            if self.is_bot(owner):
                self._resolve_freelancers(owner)
            return
        if w.action_cards.pending:
            resolve_card_choice(self)
            return
        over_limit = next((candidate for candidate in turn.players
                           if self.is_bot(candidate) and candidate.faction != 'yssaril' and
                           len(candidate.action_cards) > 7), None)
        if over_limit is not None:
            # A Codex relic or leader can add cards beyond the normal draw cap.
            discard_index = self._discard_excess_card(over_limit)
            w.action_cards.discard.append(over_limit.action_cards.pop(discard_index))
            panel = getattr(w, 'relic_action_panel', None)
            if panel and panel.open and panel.alias == 'hand_discard' and \
                    panel.owner_faction == over_limit.faction and len(over_limit.action_cards) <= 7:
                panel.close()
            self.report(over_limit, 'Discarded an excess action card.', .2)
            return
        player = turn.active_player
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
                        patronage = getattr(w, 'apply_council_patronage', None)
                        if patronage:
                            patronage()
                        w.strategy_view = True
                    w.player_panel.active = w.player_panel.players.index(turn.active_player)
                    w.frame_player_systems(turn.active_player)
            return
        if w.movement.session:
            session = w.movement.session
            if (getattr(w.leader_panel, 'open', False) and
                    getattr(w.leader_panel, 'reaction_owner_faction', None)):
                return
            if not session.activation_agent_resolved:
                if self.resolve_activation_agent_window(session):
                    return
            if session.ai_move_plan is not None:
                self.apply_activation_plan(session)
                return
            self.resolve_movement()
            advance = getattr(w, 'advance_hero_frontier', None)
            if advance:
                advance()
            return
        if w.strategy.session:
            if self.is_bot(w.strategy.player):
                self.resolve_strategy()
                w.sync_strategy_actor()
            return
        if self.hero_secondary_queue:
            faction, card = self.hero_secondary_queue[0]
            if player and player.faction == faction:
                w.strategy.start_hero_secondary(player, card)
                self.hero_secondary_queue.popleft()
                self.report(player, f'Naaz hero offered {STRATEGY_NAMES[card]} secondary.', .2)
                return
            self.hero_secondary_queue.clear()
        if not self.is_bot(player):
            return
        if player.pending_commands:
            pool = self.command_pool(player)
            player.allocate_command(pool)
            self.report(player, f'Allocated a gained command token to {pool}.', .2)
            return
        if not turn.can_take_action:
            if self.use_legendary_planet(player):
                return
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
        refitting = refit_plan(self, player)
        if self.opening_expansion(player, move):
            self.start_activation(player, move)
        elif self._play_component_action(player):
            return
        elif reinforcement and reinforcement[0] >= max(
                move[0] if move else 0, play_value(best_card) if best_card else 0):
            w.action_cards.play(player, reinforcement[1])
            resolve_card_choice(self)
            self.report(player, 'Played War Effort to reinforce the fleet.', .8)
        elif refitting and refitting[0] >= max(
                move[0] if move else 0, play_value(best_card) if best_card else 0):
            w.action_cards.play(player, refitting[1])
            while w.action_cards.pending:
                resolve_card_choice(self)
            self.report(player, 'Played Refit Troops to strengthen the frontier.', .8)
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
            if self.use_legendary_planet(player):
                return
            self.report(player, 'Ended the turn.' if turn.action_used else
                        'Passed for the rest of the round.', .65)
            w.pass_turn()

    def legendary_destination(self, player, planet_ids):
        """Reinforce a threatened frontier with a route to use the new troops."""
        movement = self.window.movement
        destinations = {planet.planet_id: tile for tile in self.window.board.values()
                        for planet in tile.planets if planet.planet_id in planet_ids}

        def value(planet_id):
            tile = destinations[planet_id]
            neighbors = movement.neighbors(tile, player.faction)
            open_planets = sum(neighbor.planet_owners.get(planet.planet_id) != player.faction
                               for neighbor in neighbors for planet in neighbor.planets)
            enemy_fleet = sum(unit.owner != player.faction and unit.location.region == Region.SPACE
                              for neighbor in neighbors for unit in neighbor.units)
            transports = sum(unit.capacity for unit in tile.units
                             if unit.owner == player.faction and unit.location.region == Region.SPACE)
            garrison = sum(unit.owner == player.faction and unit.kind in ('infantry', 'mech') and
                           unit.location.planet_id == planet_id for unit in tile.units)
            return (min(open_planets, 3) * 4 + min(enemy_fleet, 3) * 2 +
                    min(transports, 4) - min(garrison, 5), planet_id)

        return max(destinations, key=value) if destinations else None

    def use_legendary_planet(self, player):
        """Use ready end-of-turn abilities before passing, via the normal controller."""
        movement = self.window.movement
        options = movement.legendary_planet_options(player)
        if 'primor' in options:
            infantry = options['primor']['infantry']
            destination = self.legendary_destination(player, infantry)
            if destination:
                count = options['primor']['maximum']
                movement.use_legendary_planet(player, 'primor',
                                              destination_planet_id=destination, count=count)
                self.report(player, f'Primor placed {count} infantry on {destination}.', .2)
                return True
        if 'hopesend' in options:
            mech = options['hopesend'].get('mech', ())
            destination = self.legendary_destination(player, mech)
            if destination:
                movement.use_legendary_planet(player, 'hopesend', choice='mech',
                                              destination_planet_id=destination)
                self.report(player, f"Hope's End placed a mech on {destination}.", .2)
                return True
            if options['hopesend'].get('card'):
                movement.use_legendary_planet(player, 'hopesend', choice='card')
                self.report(player, "Hope's End drew an action card.", .2)
                return True
        if 'mallice' in options:
            choice = ('convert_commodities' if player.commodities > 2 and
                      options['mallice']['convert_commodities'] else 'trade_goods')
            movement.use_legendary_planet(player, 'mallice', choice=choice)
            self.report(player, 'Used Mallice to fund the fleet.', .2)
            return True
        return False

    @staticmethod
    def _discard_excess_card(player):
        priorities = {'flank_speed': 8, 'morale_boost': 7,
                      'direct_hit': 7, 'skilled_retreat': 6,
                      'war_effort': 5, 'refit': 5}
        return min(range(len(player.action_cards)), key=lambda index: (
            priorities.get(canonical_action_card(player.action_cards[index]), 2),
            canonical_action_card(player.action_cards[index]), index))

    def _exploration_choice(self, player, pending):
        exploration = self.window.exploration
        choices = pending.choices
        if not choices:
            return None
        if pending.offered_aliases:
            def card_value(alias):
                card = exploration.catalog[alias]
                score = {'attach': 8, 'fragment': 6}.get(card.resolution, 4)
                if alias.startswith(('mo', 'vfs', 'ms', 'lf')):
                    score += 2
                return score, alias
            return max(choices, key=card_value)
        priorities = {
            'use_mech': 12, 'place_mech_commodity': 11,
            'place_mech_trade_good': 10, 'place_infantry': 9,
            'remove_infantry': 8, 'convert_commodities': 7,
            'gain_commodities': 6, 'replenish_commodities': 6,
            'gain_commodity': 5, 'draw_card_commodity': 5,
            'draw_card_trade_good': 4, 'alpha': 3, 'beta': 2,
            'decline': 0, 'produce_one_unit': 8,
        }
        return max(choices, key=lambda choice: (priorities.get(choice, 1), choice))

    def _freelancer_payment(self, player, cost):
        if cost <= 0:
            return (), (), 0, 0.0
        movement = self.window.movement
        options = {0: ((), (), 0.0)}
        for card in sorted((item for item in player.planets if not item.exhausted),
                           key=lambda item: item.planet.planet_id):
            planet_id = card.planet.planet_id
            resources = movement.planet_resources(card.planet)
            influence = movement.planet_influence(card.planet)
            opportunity = max(resources, influence) + .15
            for total, (paid_resources, paid_influence, burden) in tuple(options.items()):
                for mode, points in (('resources', resources), ('influence', influence)):
                    if points <= 0:
                        continue
                    new_total = total + points
                    candidate = ((paid_resources + (planet_id,) if mode == 'resources'
                                  else paid_resources),
                                 (paid_influence + (planet_id,) if mode == 'influence'
                                  else paid_influence), burden + opportunity)
                    prior = options.get(new_total)
                    if prior is None or (candidate[2], candidate[:2]) < (prior[2], prior[:2]):
                        options[new_total] = candidate
        affordable = []
        for total, (resources, influence, burden) in options.items():
            goods = max(0, cost - total)
            if goods <= player.trade_goods:
                affordable.append((burden + 1.5 * goods + .3 * max(0, total - cost),
                                   len(resources) + len(influence), resources,
                                   influence, goods))
        if not affordable:
            return None
        score, _, resources, influence, goods = min(affordable)
        return resources, influence, goods, score

    def _resolve_freelancers(self, player):
        exploration = self.window.exploration
        movement = self.window.movement
        _, position, offered_planet = exploration.freelancer_offer
        tile = self.window.board[position]
        friendly_ships = [unit for unit in tile.units if unit.owner == player.faction and
                          unit.location.region == Region.SPACE and
                          UNIT_TYPES[unit.kind]['ship']]
        hostile_ships = [unit for unit in tile.units if unit.owner != player.faction and
                         unit.location.region == Region.SPACE and
                         UNIT_TYPES[unit.kind]['ship']]
        fleet_room = len(movement._fleet_ships_at(tile, player.faction)) < \
            movement.fleet_supply(player)
        planets = [planet for planet in tile.planets
                   if tile.planet_owners.get(planet.planet_id) == player.faction and
                   not exploration.planet_is_blocked(planet.planet_id) and
                   planet.planet_id not in exploration.destroyed_planets]
        ground_destination = (offered_planet if any(p.planet_id == offered_planet
                                                  for p in planets) else
                              min((p.planet_id for p in planets), default=None))
        garrison = sum(unit.owner == player.faction and unit.kind in ('infantry', 'mech') and
                       unit.location.region == Region.PLANET and
                       unit.location.planet_id == ground_destination for unit in tile.units)
        owned_counts = {kind: sum(unit.owner == player.faction and unit.kind == kind
                                  for system in self.window.board.values()
                                  for unit in system.units)
                        for kind in ('carrier', 'cruiser', 'destroyer', 'dreadnought',
                                     'infantry', 'mech')}
        choices = []
        for kind in ('carrier', 'infantry', 'mech', 'dreadnought', 'cruiser', 'destroyer'):
            if not production_allowed(player, kind) or \
                    owned_counts[kind] >= MODEL_LIMITS.get(kind, float('inf')):
                continue
            ship = UNIT_TYPES[kind]['ship']
            if ship and (hostile_ships or not fleet_room):
                continue
            if not ship and ground_destination is None:
                continue
            cost = exploration.freelancers_cost(player, movement, kind)
            payment = self._freelancer_payment(player, cost)
            if payment is None:
                continue
            preference = {
                'carrier': 11 if not any(unit.kind == 'carrier' for unit in friendly_ships) else 5,
                'infantry': 9 if garrison == 0 else 4,
                'mech': 7 if garrison <= 1 else 3,
                'dreadnought': 8, 'cruiser': 6, 'destroyer': 5,
            }[kind]
            value = preference - .7 * cost - .16 * payment[3]
            if value > 0:
                choices.append((value, -cost, kind, payment))
        if choices:
            _, _, kind, (resources, influence, goods, _) = max(choices)
            snapshot = Snapshot.capture(self.window.board, player, movement.players,
                                        self.window.action_cards, exploration)
            exploration.produce_freelancers(
                player, movement, kind, planet_id=ground_destination,
                resource_planets=resources, influence_planets=influence,
                trade_goods=goods)
            movement.check_freelancers_limits(player, position, snapshot)
            self.report(player, f'Freelancers produced {kind} in {tile.name}.', .35)
        else:
            exploration.produce_freelancers(player, movement, None)
            self.report(player, 'Declined Freelancers production.', .2)
            movement.resolve_exploration_queue()
        advance = getattr(self.window, 'advance_hero_frontier', None)
        if advance:
            advance()

    def _fragment_set(self, player):
        exploration = self.window.exploration
        fragments = exploration.fragments[player.faction]
        required = 2 if player.faction == 'naaz' else 3
        # Preserve wild fragments when a natural set exists.
        for selected in combinations(sorted(fragments), required):
            traits = {exploration.catalog[alias].trait for alias in selected}
            traits.discard('frontier')
            if len(traits) <= 1:
                return selected
        return None

    def _play_component_action(self, player):
        w = self.window
        exploration = getattr(w, 'exploration', None)
        if exploration is None or not w.turn_order.can_take_action:
            return False
        from leaders import leader_available
        from leader_effects import (cabal_hero_targets, use_cabal_hero,
                                    use_empyrean_hero, use_naaz_hero,
                                    use_titans_hero)
        fragment_set = self._fragment_set(player)
        if fragment_set and exploration.relics.deck:
            relic = exploration.purge_fragments_for_relic(
                player.faction, fragment_set, required=len(fragment_set))
            w.turn_order.mark_action_completed()
            self.report(player, f'Assembled relic {relic.alias or "from empty deck"}.', .8)
            return True
        owned_relics = exploration.relics.owned[player.faction]
        if 'nanoforge' in owned_relics:
            eligible = [(self._planet_values(player, card)[0] +
                         self._planet_values(player, card)[1], card.planet.planet_id)
                        for card in player.planets
                        if not card.planet.faction_homeworld and
                        not exploration.planet_is_legendary(card.planet) and
                        card.planet.planet_id not in exploration.destroyed_planets]
            if eligible:
                _, planet_id = max(eligible)
                exploration.use_nano_forge(player, planet_id)
                w.turn_order.mark_action_completed()
                self.report(player, f'Attached Nano-Forge to {planet_id}.', .8)
                return True
        if ('dynamiscore' in owned_relics and
                player.trade_goods < 5 and w.turn_order.round_number >= 2):
            outcome = exploration.relics.use_dynamis_core(player)
            w.turn_order.mark_action_completed()
            self.report(player, outcome.events[0], .8)
            return True
        if player.faction == 'naaz' and leader_available(player, 'naazhero'):
            outcome = use_naaz_hero(player, exploration)
            w.turn_order.mark_action_completed()
            # Free Technology and Trade/Leadership are usually useful even if
            # one offer must be declined after its normal legality checks.
            self.hero_secondary_queue.extend((player.faction, card) for card in (7, 5))
            self.report(player, f'Used hero; gained relic {outcome.alias or "from empty deck"}.', 1.0)
            return True
        if player.faction == 'titans' and leader_available(player, 'titanshero') and \
                any(card.planet.planet_id == 'elysium' for card in player.planets):
            use_titans_hero(player)
            w.turn_order.mark_action_completed()
            self.report(player, 'Attached Ul to Elysium.', .8)
            return True
        if player.faction == 'empyrean' and leader_available(player, 'empyreanhero'):
            targets = [tile for tile in w.board.values() if not tile.planets and
                       any(unit.owner == player.faction and unit.location.region == Region.SPACE
                           for unit in tile.units)]
            if len(targets) >= 2:
                tiles = use_empyrean_hero(player, exploration, w.board)
                w.hero_frontier_queue = [(player, tile.position) for tile in tiles]
                w.advance_hero_frontier()
                w.turn_order.mark_action_completed()
                self.report(player, 'Explored frontier systems with hero.', .8)
                return True
        if player.faction == 'cabal' and leader_available(player, 'cabalhero'):
            if len(cabal_hero_targets(w.board, player)) >= 2:
                results = use_cabal_hero(player, w.board, exploration.rng)
                w.turn_order.mark_action_completed()
                self.report(player, f'Hero threatened {len(results)} enemy ships.', 1.0)
                return True
        return False

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
        ready_resources = self._available_values(player)[0] + player.trade_goods
        board = self.window.board
        units = [u for t in board.values() for u in t.units if u.owner == player.faction]
        docks = sum(unit.kind == 'spacedock' for unit in units)
        targets = command_targets(player, board, self.window.turn_order.round_number)
        shortage = sum(max(0, targets[key] - pools[key]) for key in pools)
        ready_value = sum(sorted((max(self._planet_values(player, c)[0],
                                      .8 * self._planet_values(player, c)[1])
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
            tech_values = technology_values(
                player, board, self.window.turn_order.round_number,
                exploration=getattr(self.window, 'exploration', None))
            tech = self.choose_technology(player, 0, values=tech_values)
        scores = {1: 10 + min(20, 4 * shortage) + 6 * (pools['tactical'] <= 1),
                  2: 3 + 1.8 * ready_value,
                  3: 6 + 2 * (self.window.turn_order.speaker is not player) +
                     max(0, 4 - len(player.action_cards)),
                  4: 7 + 6 * (docks == 0) + 3 * (docks == 1 and len(player.planets) >= 4),
                  5: 11 + .9 * max(0, (self.window.exploration.relics.commodity_limit(player)
                                      if getattr(self.window, 'exploration', None)
                                      else player.commodity_limit) - player.commodities) +
                     2 * (ready_resources < 4),
                  6: warfare,
                  7: 8 + min(16, .35 * tech_values.get(tech[0], 0)) if tech else 1,
                  8: 1}
        if player.faction == 'jolnar' and card == 7:
            scores[7] += 9
        if player.faction == 'hacan' and card == 5:
            scores[5] += 3
        if player.faction == 'nomad' and card == 5:
            scores[5] += 3
        if player.faction == 'titans' and card == 4:
            scores[4] += 3
        if player.faction == 'mahact' and card == 1:
            scores[1] += 2
        if player.faction == 'naaz' and card == 7:
            scores[7] += 2
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
        session.ai_move_plan = move
        prompt = getattr(self.window, 'prompt_activation_agent', None)
        if prompt is None:
            # The lightweight AI harness has no UI reaction window.
            session.activation_agent_resolved = True
            self.apply_activation_plan(session)
        else:
            prompt(session)
        self.window.frame_action_route(source_position, target_position)
        self.focus_positions = (source_position, target_position)
        self.activation_flash_position = target_position
        self.activation_flash_duration = 1.3 / self.speed
        self.activation_flash_remaining = self.activation_flash_duration
        self.activation_flash_route = None
        self.window.movement_panel.reset()

    def resolve_activation_agent_window(self, session):
        """Resolve the active activation agent, pausing for a human owner."""
        from leaders import leader_available
        from leader_effects import (use_ghost_agent, use_l1z1x_agent,
                                    use_saar_agent, l1z1x_agent_mech_available)

        movement = self.window.movement
        if not session.activation_agent_owner:
            prompt = getattr(self.window, 'prompt_activation_agent', None)
            if prompt is None:
                session.activation_agent_resolved = True
                return False
            if prompt(session):
                return True
            if session.activation_agent_resolved:
                return False
        faction = session.activation_agent_owner
        alias = session.activation_agent_alias
        owner = movement.faction_player(faction) if faction else None
        player = session.player
        if owner is None or not leader_available(owner, alias):
            self.window.finish_activation_agent_reaction(session)
            return self.resolve_activation_agent_window(session) if not session.activation_agent_resolved else False
        if not self.is_bot(owner):
            prompt = getattr(self.window, 'prompt_activation_agent', None)
            return bool(prompt and prompt(session))

        board = self.window.board
        if alias == 'l1z1xagent':
            if not l1z1x_agent_mech_available(player, session.target, board):
                targets = []
            else:
                targets = [unit for unit in session.target.units
                           if unit.owner == player.faction and unit.kind == 'infantry' and
                           unit.location.region in (Region.PLANET, Region.TRANSPORT)]
            targets.sort(key=lambda unit: (unit.location.region != Region.PLANET,
                                           unit.location.planet_id or '', unit.unit_id))
            for infantry in targets:
                try:
                    replacement = use_l1z1x_agent(owner, player, session.target,
                                                  infantry.unit_id, board)
                except ValueError:
                    continue
                if (session.ai_move_plan is not None and
                        infantry.unit_id in session.ai_move_plan.passengers):
                    session.ai_move_plan = session.ai_move_plan._replace(
                        passengers=tuple(replacement.unit_id if unit_id == infantry.unit_id
                                         else unit_id
                                         for unit_id in session.ai_move_plan.passengers))
                movement.refresh_activation_routes(session)
                self.report(owner, f'I48S replaced infantry in {session.target.name} with a mech.', .3)
                break
        elif alias == 'ghostagent':
            before = {unit_id: route for source in session.sources.values()
                      for unit_id, route in source.routes.items()}
            session.ghost_wormhole_adjacency = True
            movement.refresh_activation_routes(session)
            after = {unit_id: route for source in session.sources.values()
                     for unit_id, route in source.routes.items()}
            useful = any(unit_id not in before or len(route) < len(before[unit_id])
                         for unit_id, route in after.items())
            if useful:
                use_ghost_agent(owner, session.target, board)
                self.report(owner, f'Emissary Taivra opened wormhole routes to {session.target.name}.', .3)
            else:
                session.ghost_wormhole_adjacency = False
                movement.refresh_activation_routes(session)
        elif alias == 'saaragent':
            highest = max((unit.move_value for tile in board.values() for unit in tile.units
                           if unit.location.region == Region.SPACE and UNIT_TYPES[unit.kind]['ship']),
                          default=0)
            ships = sorted((unit for tile in board.values() for unit in tile.units
                            if unit.owner == player.faction and
                            unit.location.region == Region.SPACE and
                            UNIT_TYPES[unit.kind]['ship']), key=lambda unit: unit.unit_id)
            for ship in ships:
                old_value = session.activation_move_values.get(ship.unit_id, ship.move_value)
                if highest <= old_value:
                    continue
                before = next((source.routes[ship.unit_id]
                               for source in session.sources.values()
                               if ship.unit_id in source.routes), None)
                session.activation_move_values[ship.unit_id] = highest
                movement.refresh_activation_routes(session)
                after = next((source.routes[ship.unit_id]
                              for source in session.sources.values()
                              if ship.unit_id in source.routes), None)
                if after and (before is None or len(after) < len(before)):
                    use_saar_agent(owner, player, board, ship.unit_id)
                    self.report(owner, f'Captain Mendosa boosted a {ship.kind} to move {highest}.', .3)
                    break
                session.activation_move_values.pop(ship.unit_id, None)
                movement.refresh_activation_routes(session)

        self.window.finish_activation_agent_reaction(session)
        if session.activation_agent_resolved:
            return False
        next_owner = movement.faction_player(session.activation_agent_owner)
        if next_owner and self.is_bot(next_owner):
            return self.resolve_activation_agent_window(session)
        return bool(next_owner)

    def apply_activation_plan(self, session):
        """Apply the precomputed movement after all activation reactions resolve."""
        movement = self.window.movement
        move = session.ai_move_plan
        session.ai_move_plan = None
        try:
            if move.flank_speed:
                index = next((index for index, card in enumerate(session.player.action_cards)
                              if canonical_action_card(card) == 'flank_speed' and
                              self.window.action_cards.can_play(session.player.faction, card, session)), None)
                if index is None:
                    raise MovementError('Flank Speed was unavailable for the planned route.')
                self.window.action_cards.play(session.player, index)
            choices = session.choices
            for unit_id in move.ships + move.passengers:
                if unit_id not in choices:
                    raise MovementError(f'Planned unit {unit_id} cannot reach this system.')
                session.toggle(unit_id)
        except (MovementError, ValueError):
            movement.cancel()
            raise
        source_position = move[2]
        target_position = move[1]
        self.window.frame_action_route(source_position, target_position)
        self.focus_positions = (source_position, target_position)
        source = session.sources.get(source_position)
        selected_routes = (source.routes.get(unit.unit_id) for unit in source.ships
                           if unit.unit_id in session.selected) if source else ()
        self.activation_flash_route = next((route for route in selected_routes if route), None)
        ships = sum(session.choices[unit_id].kind not in ('fighter', 'infantry', 'mech')
                    for unit_id in session.selected)
        cargo = len(session.selected) - ships
        target = session.target.name
        if not move.ships and not move.passengers:
            self.report(session.player, f'Activated {target} to produce units.', .22)
        else:
            odds = move.odds or (None, None)
            chance = next((value for value in odds if value is not None), None)
            forecast = f' · win chance ~{chance:.0%}' if chance is not None else ''
            self.report(session.player,
                        f'Activated {target}: {ships} ships, {cargo} passengers{forecast}.', .22)

    def _secondary_worthwhile(self, ctl, session):
        player = session.player
        card = session.card
        targets = command_targets(player, self.window.board,
                                  self.window.turn_order.round_number)
        strategic = player.command_pools['strategic']
        if card == 1:
            return (command_tokens_in_reinforcements(player, self.window.board) > 0 and
                    sum(player.command_pools.values()) < sum(targets.values()) and
                    payment_plan(player, 3,
                                 lambda item: self._planet_values(player, item)[1],
                                 lambda item: .7 * self._planet_values(player, item)[0])
                    is not None)
        if card == 2:
            ready = sorted((max(self._planet_values(player, c)[0],
                                .8 * self._planet_values(player, c)[1])
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
            cap = (self.window.exploration.relics.commodity_limit(player)
                   if getattr(self.window, 'exploration', None) else player.commodity_limit)
            return (cap - player.commodities >=
                    (3 if strategic <= 1 else 2) or
                    player.faction in session.free_trade or player.faction == 'hacan')
        if card == 6:
            return (bool(ctl.home_docks()) and
                    self._available_values(player)[0] + player.trade_goods +
                    int('st' in player.technologies) >= 1)
        if card == 7:
            if player.faction == 'nekro':
                return command_tokens_in_reinforcements(player, self.window.board) > 0
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
            budget = self._available_values(player)[1] + player.trade_goods
            pools = player.command_pools
            desired_total = sum(command_targets(player, w.board,
                                                w.turn_order.round_number).values())
            current_total = sum(pools.values()) + player.pending_commands + s.base_gain
            count = min(max(0, reserve), budget // 3,
                        max(0, desired_total - current_total))
            if count:
                plan = payment_plan(player, count * 3,
                                    lambda c: self._planet_values(player, c)[1],
                                    lambda c: .7 * self._planet_values(player, c)[0])
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
                               key=lambda c: (-max(self._planet_values(player, c)),
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
                    sum(self._planet_values(player, card)[0] for card in player.planets
                        if card.planet.planet_id == item[1].location.planet_id) +
                    int(unit_profile(item[1]).get('productionValue') or 0),
                    item[1].unit_id))[1].unit_id)
            else:
                ctl._participant_done()
        elif s.stage == 'technology':
            if player.faction == 'nekro':
                ctl.resolve_propagation()
                self.report(player, 'Used Propagation to gain command tokens.', 1.2)
                return
            choice = self.choose_technology(player, ctl.technology_cost())
            if choice:
                alias, specialty_ids, payment_ids, goods = choice
                ctl.select_technology(alias)
                tech = next(card for card in available_technologies(player)
                            if card['alias'] == alias)
                if (player.faction == 'cabal' and
                        player.captured_units.get(unit_upgrade_kind(player, tech), 0) and
                        missing_prerequisites(player, tech,
                            [card for card in player.planets
                             if card.planet.planet_id in specialty_ids],
                            exploration=getattr(w, 'exploration', None))):
                    ctl.toggle_riftmeld()
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
        exploration = getattr(self.window, 'exploration', None)
        specialties = [c for c in player.planets if can_use_specialty_planet(
            player, c, exploration=exploration)]
        if values is None:
            values = technology_values(
                player, self.window.board, self.window.turn_order.round_number,
                exploration=getattr(self.window, 'exploration', None))
        best = None
        for tech in available_technologies(player):
            if tech['alias'] in player.technologies:
                continue
            for n in range(min(len(tech.get('requirements', '')), len(specialties)) + 1):
                found = False
                for selected in combinations(specialties, n):
                    prereqs_missing = missing_prerequisites(
                        player, tech, selected, exploration=exploration)
                    riftmeld_kind = unit_upgrade_kind(player, tech)
                    can_riftmeld = (player.faction == 'cabal' and riftmeld_kind and
                                    player.captured_units.get(riftmeld_kind, 0) > 0)
                    if prereqs_missing and not can_riftmeld:
                        continue
                    # Psychoarchaeology uses a specialty without exhausting it;
                    # that planet may also pay resources for this research.
                    eligible = type('PaymentPlayer', (), {
                        'planets': cards if 'pa' in player.technologies else
                                   [c for c in cards if c not in selected],
                        'trade_goods': player.trade_goods})()
                    payment = payment_plan(
                        eligible, cost, lambda c: self._planet_values(player, c)[0],
                        lambda c: .7 * self._planet_values(player, c)[1] +
                                  (1.5 if self._specialties(c) else 0))
                    if payment is None:
                        continue
                    alias = tech['alias']
                    specialty_cost = (0 if 'pa' in player.technologies else
                                      sum(max(self._planet_values(player, c)[0],
                                              .7 * self._planet_values(player, c)[1])
                                          for c in selected))
                    score = (values.get(alias, 3) - 1.25 * specialty_cost -
                             1.5 * payment[1] -
                             (3 if prereqs_missing and can_riftmeld else 0))
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
        if s.stage == 'foresight':
            faction = s.foresight_faction
            if faction not in self.bot_factions:
                return
            options = ctl.foresight_options(s)
            if options:
                def value(tile):
                    empty_planets = [planet for planet in tile.planets
                                     if tile.planet_owners.get(planet.planet_id) is None]
                    enemy_neighbors = sum(self._tile_threat(other, faction)
                                          for other in ctl.neighbors(tile, faction))
                    friendly = sum(SHIP_VALUE.get(unit.kind, 1) for unit in tile.units
                                   if unit.owner == faction and unit.location.region == Region.SPACE)
                    return (sum(planet.resources * 1.4 + planet.influence * .5
                                for planet in empty_planets) + friendly - .4 * enemy_neighbors,
                            tuple(-part for part in tile.position))
                destination = max(options, key=value)
                ctl.resolve_foresight(destination.position)
                self.report(ctl.faction_player(faction),
                            f'Foresight moved the fleet to {destination.name}.', .6)
            else:
                ctl.resolve_foresight()
            return
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
                selected = sorted(choices, key=lambda u: (
                    u.kind != 'fighter', u.kind != 'infantry', u.unit_id))
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
            if (s.player.faction == 'titans' and
                    s.titans_sleepers_at_activation & s.target.sleeper_tokens):
                try:
                    placed = ctl.awaken_titans_sleepers()
                except MovementError as error:
                    self.report(s.player, str(error), .6)
                else:
                    self.report(s.player, s.titans_awaken_log or
                                f'Awakened {len(placed)} sleeper token(s) into PDS.', .6)
            ships = sum(s.choices[unit_id].kind not in ('infantry', 'mech', 'fighter')
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
                self.report(s.player, f'Landed ground forces on {names}.', 1.2)
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
            if not s.combat_needs_resolution and s.stage in ('space_combat', 'ground_combat'):
                for faction in s.combat_factions:
                    if faction not in self.bot_factions:
                        continue
                    options = ctl.combat_agent_die_options(faction)
                    if options:
                        unit, _alias = min(options, key=lambda option: (
                            ctl.combat_threshold(faction, unit_profile(option[0])),
                            option[0].unit_id))
                        ctl.use_combat_agent_die(faction, unit.unit_id)
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
        discount = int('st' in player.technologies and not s.integrated_current
                       and not s.agent_production)
        total_cost = max(0, ceil(sum(ctl.unit_cost(k, player) * n for k, n in planned.items())) -
                         discount)
        payment = payment_plan(
            player, total_cost, lambda c: self._planet_values(player, c)[0],
            lambda c: .7 * self._planet_values(player, c)[1] +
                      (1.5 if self._specialties(c) else 0))
        if payment is None:
            ctl.skip_production()
            return
        s.production_choices.update(planned)
        for kind in planned:
            required = ctl.production_reclaim_required(s, kind)
            if required:
                candidates = ctl.production_reclaim_candidates(s, kind)
                candidates.sort(key=lambda row: (
                    -sum(unit.owner == player.faction and unit.kind == kind
                         for unit in row[0].units),
                    self._tile_threat(row[0], player.faction),
                    row[0].name, row[1].unit_id))
                for _, unit in candidates[:required]:
                    ctl.toggle_production_reclaim(kind, unit.unit_id)
        for planet_id in payment[0]:
            ctl.toggle_production_planet(planet_id)
        s.trade_goods_to_spend = payment[1]
        ctl.produce()
        summary = ', '.join(f'{count} {kind}' for kind, count in sorted(planned.items()))
        self.report(player, f'Produced {summary} in {s.target.name}.', 1.4)
