"""Playable base-game, Prophecy of Kings, and Codex action cards."""
from __future__ import annotations

import random
from uuid import uuid4

from player import command_tokens_in_reinforcements
from units import Region, UNIT_TYPES, Unit, UnitLocation, unit_profile
from technology import copy_technology, technology_catalog, upgrade_profile
from unit_supply import (can_place_fighter, fighter_area_counts,
                         fighter_areas_remaining, unit_supply_remaining)


ACTION_CARD_DEFS = {
    'flank_speed': {
        'name': 'Flank Speed', 'window': 'After you activate a system',
        'effect': '+1 movement for each ship during this tactical action.', 'implemented': True,
    },
    'morale_boost': {
        'name': 'Morale Boost', 'window': 'At the start of a combat round',
        'effect': '+1 to your units’ combat rolls this round.', 'implemented': True,
    },
    'fighter_prototype': {
        'name': 'Fighter Prototype', 'window': 'At the start of the first space-combat round',
        'effect': '+2 to your fighters’ combat rolls this round.', 'implemented': True,
    },
    'shields_holding': {
        'name': 'Shields Holding', 'window': 'Before assigning hits to your ships in space combat',
        'effect': 'Cancel up to 2 hits against your ships.', 'implemented': True,
    },
    'emergency_repairs': {
        'name': 'Emergency Repairs', 'window': 'At the start or end of a combat round',
        'effect': 'Repair your damaged units with Sustain Damage in the active system.', 'implemented': True,
    },
    'skilled_retreat': {
        'name': 'Skilled Retreat', 'window': 'At the start of a space-combat round',
        'effect': 'Retreat all your ships to an eligible adjacent system; end combat in a draw.', 'implemented': True,
    },
    'bunker': {
        'name': 'Bunker', 'window': 'At the start of an invasion',
        'effect': 'Apply −4 to bombardment rolls against planets you control during this invasion.', 'implemented': True,
    },
    'courageous': {
        'name': 'Courageous to the End', 'window': 'After one of your ships is destroyed in space combat',
        'effect': 'Roll 2 dice; each hit makes an opponent destroy one of their ships.', 'implemented': True,
    },
    'direct_hit': {
        'name': 'Direct Hit', 'window': 'After an opponent’s ship uses Sustain Damage against your hit',
        'effect': 'Destroy that ship.', 'implemented': True,
    },
    'disable': {
        'name': 'Disable', 'window': 'At the start of an invasion against opposing PDS units',
        'effect': 'Opposing PDS lose Planetary Shield and Space Cannon for this invasion.', 'implemented': True,
    },
    'experimental_battlestation': {
        'name': 'Experimental Battlestation', 'window': 'After ships move into the active system',
        'effect': 'A nearby Space Dock fires Space Cannon 5 (×3) at the active player’s ships.', 'implemented': True,
    },
    'fire_team': {
        'name': 'Fire Team', 'window': 'After your ground forces roll in ground combat',
        'effect': 'Choose any number of your dice to reroll.', 'implemented': True,
    },
    'in_the_silence_of_space': {
        'name': 'In the Silence of Space', 'window': 'After you activate a system',
        'effect': 'Ships in one system can move through systems containing enemy ships this action.', 'implemented': True,
    },
    'intercept': {
        'name': 'Intercept', 'window': 'After an opponent declares a retreat in space combat',
        'effect': 'That opponent cannot retreat during this combat round.', 'implemented': True,
    },
    'maneuvering_jets': {
        'name': 'Maneuvering Jets', 'window': 'Before assigning hits from an opponent’s Space Cannon roll',
        'effect': 'Cancel 1 Space Cannon hit.', 'implemented': True,
    },
    'salvage': {
        'name': 'Salvage', 'window': 'After you win a space combat',
        'effect': 'Take all commodities from the defeated opponent.', 'implemented': True,
    },
    'upgrade': {
        'name': 'Upgrade', 'window': 'After activating a system with your ships',
        'effect': 'Replace one of your cruisers there with a dreadnought.', 'implemented': True,
    },
    'war_effort': {
        'name': 'War Effort', 'window': 'As an action',
        'effect': 'Place one cruiser from your reinforcements in a system with your ships.', 'implemented': True,
    },
    'rally': {
        'name': 'Rally', 'window': 'After you activate a system that contains another player’s ships',
        'effect': 'Place 2 command tokens from reinforcements in your fleet pool.', 'implemented': True,
    },
    'harness': {
        'name': 'Harness Energy', 'window': 'After you activate an anomaly',
        'effect': 'Replenish your commodities.', 'implemented': True,
    },
    'counterstroke': {
        'name': 'Counterstroke', 'window': 'After another player activates a system with your command token',
        'effect': 'Return that token to your tactic pool.', 'implemented': True,
    },
    'forward_supply_base': {
        'name': 'Forward Supply Base', 'window': 'After another player activates a system with your units',
        'effect': 'Gain 3 trade goods, then choose another player to gain 1.', 'implemented': True,
    },
    'blitz': {
        'name': 'Blitz', 'window': 'At the start of an invasion',
        'effect': 'Your non-fighter ships without Bombardment gain Bombardment 6 this invasion.',
        'implemented': True,
    },
    'solar_flare': {
        'name': 'Solar Flare', 'window': 'After you activate a system',
        'effect': 'Other players cannot use Space Cannon against your ships during movement.',
        'implemented': True,
    },
    'war_machine': {
        'name': 'War Machine', 'window': 'When one or more of your units use Production',
        'effect': '+4 total Production value and −1 combined cost of produced units.',
        'implemented': True,
    },
    'refit': {
        'name': 'Refit Troops', 'window': 'As an action',
        'effect': 'Replace 1 or 2 infantry on the board with mechs from your reinforcements.',
        'implemented': True,
    },
    'plagiarize': {
        'name': 'Plagiarize', 'window': 'As an action',
        'effect': 'Spend 5 influence to gain a non-faction technology owned by a neighbor.',
        'implemented': True,
    },
    'master_plan': {
        'name': 'Master Plan', 'window': 'After you perform an action',
        'effect': 'Perform an additional action this turn.', 'implemented': True,
    },
    'fighter_conscription': {
        'name': 'Fighter Conscription', 'window': 'As an action',
        'effect': 'Place one fighter in each eligible system without enemy ships.',
        'implemented': True,
    },
}


# AsyncTI4 has separate aliases for cards with alternate scans. Keep the
# physical alias in players' hands and normalize only when applying the effect.
CARD_EFFECT_ALIASES = {
    **{alias: 'flank_speed' for alias in ('flank_speed', 'fs1', 'fs2', 'fs3', 'fs4')},
    **{alias: 'morale_boost' for alias in ('morale_boost', 'mb1', 'mb2', 'mb3', 'mb4')},
    **{alias: 'fighter_prototype' for alias in ('fighter_prototype', 'f_prototype')},
    **{alias: 'shields_holding' for alias in ('shields_holding', 'sh1', 'sh2', 'sh3', 'sh4')},
    **{alias: 'emergency_repairs' for alias in ('emergency_repairs', 'emergency')},
    **{alias: 'skilled_retreat' for alias in ('skilled_retreat', 's_retreat1', 's_retreat2',
                                               's_retreat3', 's_retreat4')},
    'bunker': 'bunker', 'courageous': 'courageous',
    **{alias: 'direct_hit' for alias in ('direct_hit', 'dh1', 'dh2', 'dh3', 'dh4')},
    'disable': 'disable',
    **{alias: 'experimental_battlestation' for alias in ('experimental_battlestation', 'experimental')},
    'fire_team': 'fire_team',
    **{alias: 'in_the_silence_of_space' for alias in ('in_the_silence_of_space', 'silence_space')},
    'intercept': 'intercept',
    **{alias: 'maneuvering_jets' for alias in ('maneuvering_jets', 'mjets1', 'mjets2', 'mjets3', 'mjets4')},
    'salvage': 'salvage', 'upgrade': 'upgrade', 'war_effort': 'war_effort',
    'rally': 'rally', 'harness': 'harness', 'counterstroke': 'counterstroke',
    'fsb': 'forward_supply_base', 'blitz': 'blitz', 'solar_flare': 'solar_flare',
    **{alias: 'war_machine' for alias in ('war_machine1', 'war_machine2',
                                         'war_machine3', 'war_machine4')},
    'refit': 'refit',
    'plagiarize': 'plagiarize', 'master_plan': 'master_plan',
    'f_conscription': 'fighter_conscription',
}


def canonical_action_card(alias):
    return CARD_EFFECT_ALIASES.get(alias, alias)


class ActionCardController:
    def __init__(self, players, movement, deck=None, rng=None, turn=None):
        self.players = list(players)
        self.movement = movement
        self.turn = turn
        self.rng = rng or random.Random()
        self.deck = deck if deck is not None else self._default_deck()
        if deck is None:
            self.rng.shuffle(self.deck)
        self.discard: list[str] = []
        self.pending: dict | None = None
        self._master_plan_waiting: tuple[int, int] | None = None
        self.movement.action_cards = self

    @staticmethod
    def _default_deck():
        return [
            'fs1', 'fs2', 'fs3', 'fs4', 'mb1', 'mb2', 'mb3', 'mb4', 'f_prototype',
            'sh1', 'sh2', 'sh3', 'sh4', 'emergency', 's_retreat1', 's_retreat2',
            's_retreat3', 's_retreat4', 'bunker', 'courageous', 'dh1', 'dh2', 'dh3', 'dh4',
            'disable', 'experimental', 'fire_team', 'silence_space', 'intercept',
            'mjets1', 'mjets2', 'mjets3', 'mjets4', 'salvage', 'upgrade', 'war_effort',
            'rally', 'harness', 'counterstroke', 'fsb', 'blitz', 'solar_flare',
            'war_machine1', 'war_machine2', 'war_machine3', 'war_machine4', 'refit',
            'plagiarize', 'master_plan', 'f_conscription',
        ]

    def snapshot(self):
        return list(self.deck), list(self.discard)

    @staticmethod
    def name_for(alias):
        return ACTION_CARD_DEFS.get(canonical_action_card(alias), {}).get('name', alias)

    def restore(self, state):
        self.deck[:], self.discard[:] = state
        self.pending = None
        self._master_plan_waiting = None

    def draw(self, player, count=1):
        drawn = []
        for _ in range(max(0, count)):
            if player.faction != 'yssaril' and len(player.action_cards) >= 7:
                break
            alias = self._draw_one()
            if alias is None:
                break
            player.action_cards.append(alias)
            drawn.append(alias)
        if player.faction == 'yssaril' and drawn:
            extra = self._draw_one()
            if extra is not None:
                player.action_cards.append(extra)
                drawn.append(extra)
                duplicate = next((alias for alias in player.action_cards
                                  if player.action_cards.count(alias) > 1), None)
                discarded = duplicate if duplicate is not None else player.action_cards[0]
                player.action_cards.remove(discarded)
                self.discard.append(discarded)
        return drawn

    def _draw_one(self):
        if not self.deck and self.discard:
            self.deck[:] = self.discard
            self.discard.clear()
            self.rng.shuffle(self.deck)
        return self.deck.pop() if self.deck else None

    def can_draw(self, player, count=1):
        capacity = max(0, count) if player.faction == 'yssaril' else max(0, 7 - len(player.action_cards))
        return min(capacity, max(0, count), len(self.deck) + len(self.discard)) > 0

    def resolve_stall_tactics(self, player, hand_index):
        if (player.faction != 'yssaril' or self.turn is None or
                self.turn.active_player is not player or not self.turn.can_take_action or
                self.movement.session is not None or self.turn.strategy_resolution is not None or
                self.turn.strategy_selection or self.turn.command_allocation):
            raise ValueError('Stall Tactics can only be used as an action during your turn.')
        if not 0 <= hand_index < len(player.action_cards):
            raise ValueError('Choose an action card from your hand to discard.')
        alias = player.action_cards.pop(hand_index)
        self.discard.append(alias)
        self.turn.mark_action_completed()
        return alias

    def has_reinforcement(self, player):
        return command_tokens_in_reinforcements(player, self.movement.board) > 0

    def _mechs_in_play(self, player):
        return sum(unit.owner == player.faction and unit.kind == 'mech'
                   for tile in self.movement.board.values() for unit in tile.units)

    @staticmethod
    def _combat_start(session):
        return (session.stage in ('space_combat', 'ground_combat') and
                not session.combat_needs_resolution and not session.combat_rolls)

    def eligible_docks(self, player, session):
        systems = (session.target, *self.movement.neighbors(session.target, player.faction))
        return [(tile, unit) for tile in systems for unit in tile.units
                if unit.kind == 'spacedock' and unit.owner == player.faction]

    def silence_origins(self, player, session):
        result = []
        for tile in self.movement.board.values():
            if player.faction in tile.command_tokens:
                continue
            for unit in tile.units:
                ship = (unit.kind != 'fighter' and UNIT_TYPES[unit.kind]['ship']) or \
                    (unit.kind == 'fighter' and 'ff2' in player.technologies)
                if unit.owner != player.faction or unit.location.region != Region.SPACE or not ship:
                    continue
                path = self.movement.route(tile, session.target, unit, player, ignore_enemy=True)
                if not path and 'gd' in player.technologies:
                    path = self.movement.route(tile, session.target, unit, player,
                                               move_bonus=1, ignore_enemy=True)
                if path:
                    result.append(tile)
                    break
        return result

    def plagiarize_targets(self, player):
        """Technology aliases held by players with units or planets in adjacent systems."""
        occupied = [tile for tile in self.movement.board.values()
                    if player.faction in tile.planet_owners.values() or
                    any(unit.owner == player.faction for unit in tile.units)]
        adjacent = {neighbor.position for tile in occupied
                    for neighbor in (tile, *self.movement.neighbors(tile, player.faction))}
        neighbors = {owner for tile in self.movement.board.values()
                     if tile.position in adjacent
                     for owner in (*tile.planet_owners.values(),
                                   *(unit.owner for unit in tile.units))
                     if owner != player.faction}
        catalog = technology_catalog()
        return sorted({alias for other in self.players if other.faction in neighbors
                       for alias in other.technologies
                       if alias not in player.technologies and alias in catalog and
                       not catalog[alias].get('faction')},
                      key=lambda alias: catalog[alias]['name'])

    def _plagiarize_influence(self, player, selected):
        return sum(self.movement.planet_influence(card.planet)
                   for card in player.planets if card.planet.planet_id in selected and
                   not card.exhausted)

    def fighter_conscription_systems(self, player):
        return [tile for tile in self.movement.board.values()
                if any(unit.owner == player.faction and
                       (unit.kind == 'spacedock' or
                        (unit.location.region == Region.SPACE and unit.capacity > 0))
                       for unit in tile.units) and
                not any(unit.owner != player.faction and
                        unit.location.region == Region.SPACE and UNIT_TYPES[unit.kind]['ship']
                        for unit in tile.units)]

    def can_resolve_fighter_conscription(self, player):
        systems = self.fighter_conscription_systems(player)
        if not systems:
            return False
        anchored = fighter_area_counts(player, self.movement.board)
        new_anchors = sum(tile.position not in anchored for tile in systems)
        return new_anchors <= fighter_areas_remaining(player, self.movement.board)

    def _next_fighter_conscription_overflow(self):
        pending = self.pending
        player = pending['player']
        for position in pending['positions']:
            tile = self.movement.board[position]
            if self.movement.capacity_overflow(tile, player.faction):
                pending['overflow_position'] = position
                pending['overflow_kind'] = 'capacity'
                return
            if (len(self.movement._fleet_ships_at(tile, player.faction)) >
                    self.movement.fleet_supply(player)):
                pending['overflow_position'] = position
                pending['overflow_kind'] = 'fleet'
                return
        self.pending = None
        self.refresh_leader_unlocks()
        self._offer_keleres_commander(player, pending['alias'])

    def refresh_leader_unlocks(self):
        from leaders import refresh_leader_unlocks
        for player in self.players:
            refresh_leader_unlocks(player, self.movement.board, self.players,
                                   self.movement.exploration)

    def can_play(self, faction, alias, session=None):
        effect = canonical_action_card(alias)
        if effect not in ACTION_CARD_DEFS:
            return False
        player = next((p for p in self.players if p.faction == faction), None)
        if player is None or self.pending:
            return False
        session = session or self.movement.session
        if not session:
            if effect not in ('war_effort', 'refit', 'plagiarize', 'master_plan',
                              'fighter_conscription') or self.turn is None or \
                    self.turn.active_player is not player:
                return False
            if (self.turn.strategy_selection or self.turn.command_allocation or
                    self.turn.strategy_resolution):
                return False
            if effect == 'master_plan':
                return (self.turn.action_used and self.turn.actions_used > 0 and
                        self._master_plan_waiting !=
                        (self.turn.turn_serial, self.turn.actions_used))
            if not self.turn.can_take_action:
                return False
            if effect == 'plagiarize':
                available = self._plagiarize_influence(
                    player, {card.planet.planet_id for card in player.planets})
                return bool(self.plagiarize_targets(player) and
                            available + player.trade_goods >= 5)
            if effect == 'fighter_conscription':
                return self.can_resolve_fighter_conscription(player)
            if effect == 'refit':
                return (self._mechs_in_play(player) < 4 and any(
                    unit.owner == faction and unit.kind == 'infantry'
                    for tile in self.movement.board.values() for unit in tile.units))
            return any(unit.owner == faction and unit.location.region == Region.SPACE and
                       UNIT_TYPES[unit.kind]['ship'] for tile in self.movement.board.values()
                       for unit in tile.units)

        combat_factions = set(session.combat_factions)
        if faction not in combat_factions and faction != session.player.faction:
            # Bunker can be played by a planet owner whose ground forces were
            # already removed before the invasion begins.
            if effect == 'bunker':
                if not any(owner == faction for owner in session.target.planet_owners.values()):
                    return False
            elif effect == 'direct_hit':
                if not session.pending_direct_hits.get(faction):
                    return False
            elif effect in ('forward_supply_base', 'counterstroke'):
                if effect == 'forward_supply_base' and not any(
                        unit.owner == faction for unit in session.target.units):
                    return False
                if effect == 'counterstroke' and faction not in session.target.command_tokens:
                    return False
            elif effect == 'experimental_battlestation':
                if not self.eligible_docks(player, session):
                    return False
            else:
                return False
        window = self.window_key(faction, effect, session)
        if window is None or window in session.played_action_windows:
            return False
        combat_start = self._combat_start(session)
        own_units = self.movement.combat_units(session, faction) if faction in combat_factions else []

        if effect == 'flank_speed':
            return (faction == session.player.faction and session.stage == 'movement' and
                    not session.strategic_production)
        if effect == 'rally':
            return (faction == session.player.faction and session.stage == 'movement' and
                    not session.strategic_production and self.has_reinforcement(player) and
                    any(unit.owner != faction and unit.location.region == Region.SPACE and
                        UNIT_TYPES[unit.kind]['ship'] for unit in session.target.units))
        if effect == 'harness':
            return (faction == session.player.faction and session.stage == 'movement' and
                    bool(session.target.anomalies) and player.commodities < player.commodity_limit)
        if effect == 'counterstroke':
            return (faction != session.player.faction and session.stage == 'movement' and
                    faction in session.target.command_tokens)
        if effect == 'forward_supply_base':
            return (faction != session.player.faction and session.stage == 'movement' and
                    any(unit.owner == faction for unit in session.target.units))
        if effect == 'solar_flare':
            return (faction == session.player.faction and session.stage == 'movement' and
                    not session.strategic_production)
        if effect == 'blitz':
            return (faction == session.player.faction and session.stage == 'invasion_start' and
                    any(unit.owner == faction and unit.location.region == Region.SPACE and
                        UNIT_TYPES[unit.kind]['ship'] and unit.kind != 'fighter' and
                        not unit_profile(unit).get('bombardHitsOn')
                        for unit in session.target.units))
        if effect == 'war_machine':
            return (faction == session.player.faction and session.stage == 'production' and
                    bool(session.production_sites))
        if effect == 'morale_boost':
            return combat_start
        if effect == 'fighter_prototype':
            return (combat_start and session.combat_type == 'space' and session.combat_round == 0 and
                    any(unit.kind == 'fighter' for unit in own_units))
        if effect == 'shields_holding':
            return (session.combat_type == 'space' and session.stage == 'space_combat' and
                    session.combat_needs_resolution and session.combat_hits.get(faction, 0) > 0 and
                    not any(session.combat_assignments.values()))
        if effect == 'emergency_repairs':
            damaged = any(unit.owner == faction and unit.damaged and unit_profile(unit).get('sustainDamage')
                          for unit in session.target.units)
            return bool(damaged and (combat_start or session.stage == 'combat_end'))
        if effect == 'skilled_retreat':
            destination_exists = bool(self.movement.retreat_options(
                session, faction, skilled=True))
            return (combat_start and session.combat_type == 'space' and bool(own_units) and
                    destination_exists)
        if effect == 'bunker':
            bombers = any(unit.owner == session.player.faction and unit.location.region == Region.SPACE and
                          unit_profile(unit).get('bombardHitsOn') for unit in session.target.units)
            enemy_forces = any(unit.owner != session.player.faction and unit.kind == 'infantry' and
                               unit.location.region == Region.PLANET for unit in session.target.units)
            return (session.stage == 'invasion_start' and faction != session.player.faction and bombers and
                    enemy_forces and any(owner == faction for owner in session.target.planet_owners.values()))
        if effect == 'disable':
            return (faction == session.player.faction and session.stage == 'invasion_start' and
                    any(unit.kind == 'pds' and unit.owner != faction for unit in session.target.units))
        if effect == 'courageous':
            return (any(trigger.get('stage') == session.stage
                        for trigger in session.pending_courageous.get(faction, ())) and
                    session.combat_type == 'space' and
                    (session.stage != 'space_combat' or
                     (session.combat_round == 0 and not session.combat_rolls)))
        if effect == 'direct_hit':
            pending_targets = set(session.pending_direct_hits.get(faction, ()))
            return (any(unit.unit_id in pending_targets and unit.owner != faction and unit.damaged
                        for unit in session.target.units) and session.combat_type == 'space' and
                    session.stage in ('combat_end', 'space_cannon_direct_hit'))
        if effect == 'experimental_battlestation':
            return (session.stage == 'space_cannon_action' and faction != session.player.faction and
                    bool(self.eligible_docks(player, session)))
        if effect == 'fire_team':
            return (session.stage == 'ground_combat' and session.combat_needs_resolution and
                    bool(session.combat_rolls.get(faction)))
        if effect == 'in_the_silence_of_space':
            return (faction == session.player.faction and session.stage == 'movement' and
                    not session.strategic_production and bool(self.silence_origins(player, session)))
        if effect == 'intercept':
            return (session.stage == 'space_combat' and session.combat_type == 'space' and
                    session.retreat_announced not in (None, faction) and
                    session.retreat_blocked_round != session.combat_round + 1)
        if effect == 'maneuvering_jets':
            return (session.stage == 'space_cannon_response' and faction == session.player.faction and
                    bool(session.space_cannon_events))
        if effect == 'salvage':
            return (session.stage == 'space_combat_won' and session.combat_winner == faction)
        if effect == 'upgrade':
            return (faction == session.player.faction and session.stage == 'movement' and
                    not session.strategic_production and any(
                        unit.owner == faction and unit.kind == 'cruiser' and
                        unit.location.region == Region.SPACE for unit in session.target.units))
        if effect == 'war_effort':
            return False
        return False

    @staticmethod
    def _pending_key(faction, effect):
        return (faction, effect, 'choice')

    def window_key(self, faction, effect, session):
        if effect in ('rally', 'harness', 'counterstroke', 'forward_supply_base',
                      'solar_flare'):
            return (faction, effect, session.target.position, 'activation')
        if effect == 'war_machine':
            return (faction, effect, session.target.position, 'production')
        if effect == 'blitz':
            return (faction, effect, session.target.position, 'invasion')
        if effect == 'flank_speed':
            return (faction, effect, session.target.position, 'activation')
        if effect == 'in_the_silence_of_space':
            return (faction, effect, session.target.position, 'activation')
        if effect == 'upgrade':
            return (faction, effect, session.target.position, 'activation')
        if effect in ('war_effort', 'refit', 'plagiarize', 'master_plan',
                      'fighter_conscription'):
            return (faction, effect, 'action')
        if effect in ('bunker', 'disable'):
            return (faction, effect, session.target.position, 'invasion')
        if effect == 'experimental_battlestation':
            return (faction, effect, session.target.position, 'space-cannon')
        if effect == 'maneuvering_jets':
            event = session.space_cannon_events[0] if session.space_cannon_events else {}
            if event.get('source') == 'planetary_defense':
                return (faction, effect, session.target.position, 'space-cannon-defense',
                        event.get('planet_id'))
            return (faction, effect, session.target.position, 'space-cannon')
        if effect == 'salvage':
            return (faction, effect, session.target.position, 'space-combat-won')
        if effect in ('courageous', 'direct_hit'):
            pending = (session.pending_courageous if effect == 'courageous' else session.pending_direct_hits)
            target = tuple((entry['threshold'], tuple(entry['opponents']), entry.get('stage'))
                           for entry in pending.get(faction, ())) if effect == 'courageous' else \
                tuple(pending.get(faction, ()))
            return (faction, effect, session.target.position, session.combat_round,
                    session.stage, target)
        if self._combat_start(session):
            return (faction, effect, session.target.position, session.combat_type,
                    session.combat_planet_id, session.combat_round, 'round-start')
        if effect == 'emergency_repairs' and session.stage == 'combat_end':
            return (faction, effect, session.target.position, session.combat_type,
                    session.combat_planet_id, session.combat_round, 'round-end')
        if effect == 'shields_holding' and session.combat_needs_resolution:
            return (faction, effect, session.target.position, session.combat_type,
                    session.combat_planet_id, session.combat_round, 'assign-hits')
        if effect == 'fire_team' and session.combat_needs_resolution:
            return (faction, effect, session.target.position, session.combat_planet_id,
                    session.combat_round, 'reroll')
        if effect == 'intercept' and session.retreat_announced:
            return (faction, effect, session.target.position, session.combat_round, 'retreat')
        return None

    def participants(self, session=None):
        session = session or self.movement.session
        if not session:
            player = self.turn.active_player if self.turn else None
            return [player] if player and any(self.can_play(player.faction, alias, None)
                                              for alias in player.action_cards) else []
        return [player for player in self.players if any(
            self.can_play(player.faction, alias, session) for alias in player.action_cards)]

    def playable(self, player, session=None):
        session = session or self.movement.session
        return [(index, alias) for index, alias in enumerate(player.action_cards)
                if self.can_play(player.faction, alias, session)]

    def _consume(self, player, hand_index, alias, window, session, *, refresh_leaders=True):
        player.action_cards.pop(hand_index)
        self.discard.append(alias)
        if window is not None and session is not None:
            session.played_action_windows.add(window)
        elif self.turn is not None:
            self.turn.mark_action_completed()
        if refresh_leaders:
            self.refresh_leader_unlocks()
            if self.pending is None:
                self._offer_keleres_commander(player, alias)

    def _offer_keleres_commander(self, player, alias):
        """Offer Suffi An's printed unlock cost after a component-action card resolves."""
        from leaders import leader_available
        if (ACTION_CARD_DEFS.get(canonical_action_card(alias), {}).get('window') != 'As an action' or
                not player.faction.startswith('keleres') or player.trade_goods < 1 or
                leader_available(player, 'kelerescommander')):
            return False
        self.pending = {'player': player, 'effect': 'keleres_unlock', 'alias': alias,
                        'window': None}
        return True

    def _pending_choices(self):
        pending = self.pending
        if not pending:
            return []
        session = self.movement.session
        player = pending['player']
        effect = pending['effect']
        if effect == 'keleres_unlock':
            choices = [(('spend',), 'Spend 1 trade good · unlock Suffi An'),
                       (('decline',), 'Keep the trade good')]
            return choices if player.trade_goods > 0 else choices[1:]
        if effect == 'plagiarize':
            if 'technology' not in pending:
                targets = self.plagiarize_targets(player)
                page = min(pending.get('page', 0), max(0, (len(targets) - 1) // 10))
                pending['page'] = page
                choices = [(('technology', alias), technology_catalog()[alias]['name'])
                           for alias in targets[page * 10:(page + 1) * 10]]
                if page:
                    choices.append((('page', -1), 'Previous technologies'))
                if (page + 1) * 10 < len(targets):
                    choices.append((('page', 1), 'More technologies'))
                return choices
            selected = pending.setdefault('planets', set())
            ready = [card for card in player.planets if not card.exhausted]
            page = min(pending.get('page', 0), max(0, (len(ready) - 1) // 10))
            pending['page'] = page
            visible = ready[page * 10:(page + 1) * 10]
            total = self._plagiarize_influence(player, selected) + pending.get('goods', 0)
            choices = []
            if total >= 5:
                choices.append((('confirm',), f'Gain technology · pay {total} influence'))
            choices.extend((('planet', card.planet.planet_id),
                            f'{card.planet.name} · {self.movement.planet_influence(card.planet)} influence')
                           for card in visible if card.planet.planet_id not in selected)
            if (page + 1) * 10 < len(ready):
                choices.append((('page', 1), 'More planets'))
            if pending.get('goods', 0) < player.trade_goods:
                choices.append((('goods', 1), f'Add trade good · {pending.get("goods", 0)}/{player.trade_goods}'))
            choices.extend((('planet', card.planet.planet_id),
                            f'Undo {card.planet.name} · selected')
                           for card in visible if card.planet.planet_id in selected)
            if page:
                choices.append((('page', -1), 'Previous planets'))
            if pending.get('goods', 0):
                choices.append((('goods', -1), 'Remove trade good'))
            return choices
        if effect == 'fighter_conscription':
            tile = self.movement.board[pending['overflow_position']]
            units = (self.movement.capacity_overflow_units(tile, player.faction)
                     if pending['overflow_kind'] == 'capacity' else
                     self.movement._fleet_ships_at(tile, player.faction))
            return [(unit.unit_id,
                     f'Tile {tile.system_id} · destroy {UNIT_TYPES[unit.kind]["name"]} {unit.unit_id[-6:]}')
                    for unit in units]
        if effect == 'refit':
            selected = set(pending.get('selected', ()))
            choices = [(unit.unit_id, f'Tile {tile.system_id} · Infantry {unit.unit_id[-6:]}')
                       for tile in self.movement.board.values() for unit in tile.units
                       if unit.owner == player.faction and unit.kind == 'infantry' and
                       unit.unit_id not in selected]
            page_size = 12
            page = min(pending.get('page', 0), max(0, (len(choices) - 1) // page_size))
            pending['page'] = page
            visible = choices[page * page_size:(page + 1) * page_size]
            if page:
                visible.append(('__previous_refit__', '← Previous infantry'))
            if (page + 1) * page_size < len(choices):
                visible.append(('__next_refit__', 'More infantry →'))
            return ([('__finish_refit__', 'Finish with one mech')] if selected else []) + visible
        if effect in ('war_effort',):
            if unit_supply_remaining(player, self.movement.board, 'cruiser') <= 0:
                return []
            return [(tile.position, f'Tile {tile.system_id} · {tile.name}')
                    for tile in self.movement.board.values() if any(
                        unit.owner == player.faction and unit.location.region == Region.SPACE and
                        UNIT_TYPES[unit.kind]['ship'] for unit in tile.units)]
        if effect == 'in_the_silence_of_space':
            return [(tile.position, f'Tile {tile.system_id} · {tile.name}')
                    for tile in self.silence_origins(player, session)]
        if effect == 'experimental_battlestation':
            return [(unit.unit_id, f'Tile {tile.system_id} · Space Dock')
                    for tile, unit in self.eligible_docks(player, session)]
        if effect == 'forward_supply_base':
            return [(other.faction, other.name) for other in self.players if other is not player]
        if effect == 'upgrade':
            if unit_supply_remaining(player, self.movement.board, 'dreadnought') <= 0:
                return []
            return [(unit.unit_id, f'{UNIT_TYPES[unit.kind]["name"]} · {unit.unit_id[-6:]}')
                    for unit in session.target.units if unit.owner == player.faction and
                    unit.kind == 'cruiser' and unit.location.region == Region.SPACE]
        if effect == 'direct_hit':
            ids = dict.fromkeys(session.pending_direct_hits.get(player.faction, []))
            return [(unit_id, f'{unit.owner.upper()} · {UNIT_TYPES[unit.kind]["name"]}')
                    for unit_id in ids for unit in session.target.units if unit.unit_id == unit_id]
        if effect == 'courageous' and pending.get('remaining', 0) > 0:
            factions = pending.get('opponents', ())
            return [(unit.unit_id, f'{unit.owner.upper()} · {UNIT_TYPES[unit.kind]["name"]}')
                    for unit in session.target.units if unit.owner in factions and
                    unit.location.region == Region.SPACE and UNIT_TYPES[unit.kind]['ship']]
        return []

    def pending_choices(self):
        return self._pending_choices()

    def cancel_pending(self):
        if self.pending and self.pending.get('mandatory'):
            return False
        self.pending = None
        return True

    def resolve_pending(self, choice):
        pending = self.pending
        if not pending:
            raise ValueError('No action-card choice is waiting.')
        player = pending['player']
        effect = pending['effect']
        session = self.movement.session
        if choice not in {value for value, _ in self._pending_choices()}:
            raise ValueError('This action-card choice is no longer available.')
        if effect == 'keleres_unlock':
            if choice[0] == 'spend':
                if player.trade_goods < 1:
                    raise ValueError('You no longer have a trade good to spend.')
                player.trade_goods -= 1
                from leaders import notify_leader_event
                notify_leader_event(player, 'keleres_action_card_trade_good')
            self.pending = None
            return
        if effect == 'plagiarize':
            if choice[0] == 'page':
                pending['page'] += choice[1]
                return
            if choice[0] == 'technology':
                pending['technology'] = choice[1]
                pending['page'] = 0
                return
            if choice[0] == 'planet':
                selected = pending.setdefault('planets', set())
                if choice[1] in selected:
                    selected.remove(choice[1])
                else:
                    selected.add(choice[1])
                return
            if choice[0] == 'goods':
                pending['goods'] = pending.get('goods', 0) + choice[1]
                return
            if choice[0] == 'confirm':
                if (pending['technology'] not in self.plagiarize_targets(player) or
                        pending.get('goods', 0) > player.trade_goods or
                        self._plagiarize_influence(player, pending['planets']) +
                        pending.get('goods', 0) < 5):
                    raise ValueError('The technology or influence payment is no longer available.')
                copy_technology(player, pending['technology'], self.movement.board)
                for card in player.planets:
                    if card.planet.planet_id in pending['planets']:
                        card.exhausted = True
                player.trade_goods -= pending.get('goods', 0)
                self._consume(player, player.action_cards.index(pending['alias']),
                              pending['alias'], pending['window'], session)
                self.pending = None
                self._offer_keleres_commander(player, pending['alias'])
                return
        if effect == 'fighter_conscription':
            tile = self.movement.board[pending['overflow_position']]
            unit = next(unit for unit in tile.units if unit.unit_id == choice)
            self.movement.destroy_unit(tile, unit)
            self._next_fighter_conscription_overflow()
            return
        if effect == 'war_effort':
            tile = self.movement.board[choice]
            self._place_ship(tile, player, 'cruiser', f'war-effort-{player.faction}')
            self._consume(player, player.action_cards.index(pending['alias']), pending['alias'],
                          pending['window'], session)
            self.pending = None
            self._offer_keleres_commander(player, pending['alias'])
            return
        if effect == 'refit':
            selected = pending.setdefault('selected', [])
            if choice in ('__previous_refit__', '__next_refit__'):
                pending['page'] = pending.get('page', 0) + (1 if choice == '__next_refit__' else -1)
                return
            if choice != '__finish_refit__':
                selected.append(choice)
            if (choice != '__finish_refit__' and len(selected) < 2 and
                    len(selected) < 4 - self._mechs_in_play(player)):
                return
            for unit_id in selected:
                tile, infantry = next((tile, unit) for tile in self.movement.board.values()
                                      for unit in tile.units if unit.unit_id == unit_id)
                tile.units.remove(infantry)
                tile.units.append(Unit(f'{player.faction}-refit-{uuid4().hex}', 'mech',
                                       player.faction, player.color_code, infantry.location))
            self._consume(player, player.action_cards.index(pending['alias']),
                          pending['alias'], pending['window'], session)
            self.pending = None
            self._offer_keleres_commander(player, pending['alias'])
            return
        if effect == 'forward_supply_base':
            beneficiary = next(other for other in self.players if other.faction == choice)
            player.trade_goods += 3
            beneficiary.trade_goods += 1
            self.movement.resolve_pillage(player)
            self.movement.resolve_pillage(beneficiary)
            self._consume(player, player.action_cards.index(pending['alias']), pending['alias'],
                          pending['window'], session)
            self.pending = None
            return
        if effect == 'in_the_silence_of_space':
            self.movement.apply_silence_space(session, choice)
            self._consume(player, player.action_cards.index(pending['alias']), pending['alias'],
                          pending['window'], session)
            self.pending = None
            return
        if effect == 'experimental_battlestation':
            rolls = [self.movement.roll_d10(session) for _ in range(3)]
            hits = sum(value >= 5 for value in rolls)
            candidates = [unit for unit in session.target.units if unit.owner == session.player.faction and
                          unit.location.region == Region.SPACE and UNIT_TYPES[unit.kind]['ship']]
            session.cannon_log.append(f'{player.faction.upper()} Space Dock: {rolls} → {hits} hit(s).')
            session.space_cannon_events.extend({'owner': player.faction,
                                                'candidates': [unit.unit_id for unit in candidates],
                                                'graviton': False}
                                               for _ in range(hits))
            self._consume(player, player.action_cards.index(pending['alias']), pending['alias'],
                          pending['window'], session)
            self.pending = None
            return
        if effect == 'upgrade':
            cruiser = next(unit for unit in session.target.units if unit.unit_id == choice)
            session.target.units.remove(cruiser)
            dreadnought = self._place_ship(session.target, player, 'dreadnought',
                                            f'upgrade-{player.faction}')
            for cargo in session.target.units:
                if (cargo.location.region == Region.TRANSPORT and
                        cargo.location.carrier_id == cruiser.unit_id):
                    cargo.location = UnitLocation(Region.TRANSPORT, carrier_id=dreadnought.unit_id)
            self._consume(player, player.action_cards.index(pending['alias']), pending['alias'],
                          pending['window'], session)
            self.pending = None
            return
        if effect == 'direct_hit':
            unit = next(unit for unit in session.target.units if unit.unit_id == choice)
            source_factions = ((player.faction,) if session.combat_type == 'space' and
                               session.stage == 'combat_end' else ())
            self.movement.destroy_unit(session.target, unit, session, source_factions)
            session.pending_direct_hits[player.faction].remove(choice)
            self._consume(player, player.action_cards.index(pending['alias']), pending['alias'],
                          pending['window'], session)
            self.pending = None
            return
        if effect == 'courageous':
            unit = next(unit for unit in session.target.units if unit.unit_id == choice)
            self.movement.destroy_unit(session.target, unit, session)
            pending['remaining'] -= 1
            if pending['remaining'] <= 0 or not self._pending_choices():
                self.pending = None
                self.refresh_leader_unlocks()
            return
        raise ValueError('This action-card choice is no longer available.')

    def _place_ship(self, tile, player, kind, prefix):
        remaining = unit_supply_remaining(player, self.movement.board, kind)
        if remaining is not None and remaining <= 0:
            raise ValueError(f'No {kind} units remain in your reinforcements.')
        if kind == 'fighter' and not can_place_fighter(
                player, self.movement.board, tile, UnitLocation(Region.SPACE)):
            raise ValueError('No plastic fighter anchor is available for another system.')
        unit = Unit(f'{prefix}-{random.randrange(1_000_000_000)}', kind, player.faction,
                    player.color_code, UnitLocation(Region.SPACE),
                    profile_id=(upgrade_profile(player, kind) or {}).get('id'))
        tile.units.append(unit)
        return unit

    def play(self, player, hand_index):
        session = self.movement.session
        if self.pending:
            raise ValueError('Finish the current action-card choice first.')
        if not 0 <= hand_index < len(player.action_cards):
            raise ValueError('That action card is no longer in your hand.')
        alias = player.action_cards[hand_index]
        effect = canonical_action_card(alias)
        if not self.can_play(player.faction, effect, session):
            raise ValueError('This action card cannot be played in the current timing window.')
        window = self.window_key(player.faction, effect, session) if session else ('action', effect)

        if effect in ('war_effort', 'refit', 'plagiarize', 'forward_supply_base', 'in_the_silence_of_space',
                      'experimental_battlestation',
                      'direct_hit', 'upgrade'):
            self.pending = {'player': player, 'effect': effect, 'alias': alias,
                            'window': window}
            return ACTION_CARD_DEFS[effect]
        if effect == 'master_plan':
            player.action_cards.pop(hand_index)
            self.discard.append(alias)
            self.turn.actions_used -= 1
            self._master_plan_waiting = (self.turn.turn_serial, self.turn.actions_used)
            return ACTION_CARD_DEFS[effect]
        if effect == 'fighter_conscription':
            systems = self.fighter_conscription_systems(player)
            if not self.can_resolve_fighter_conscription(player):
                raise ValueError('Fighter Conscription has more eligible systems than fighter anchors.')
            for tile in systems:
                self._place_ship(tile, player, 'fighter', f'fighter-conscription-{player.faction}')
            self._consume(player, hand_index, alias, window, session,
                          refresh_leaders=False)
            self.pending = {'player': player, 'effect': effect, 'alias': alias,
                            'mandatory': True,
                            'positions': [tile.position for tile in systems]}
            self._next_fighter_conscription_overflow()
            return ACTION_CARD_DEFS[effect]
        if effect == 'courageous':
            trigger = session.pending_courageous[player.faction].pop(0)
            threshold = trigger['threshold']
            rolls = [self.movement.roll_d10(session) for _ in range(2)]
            hits = sum(value >= threshold for value in rolls)
            self._consume(player, hand_index, alias, window, session,
                          refresh_leaders=False)
            self.pending = {'player': player, 'effect': effect, 'alias': alias,
                            'remaining': hits, 'rolls': rolls,
                            'opponents': trigger['opponents']}
            if not hits or not self._pending_choices():
                self.pending = None
            return {**ACTION_CARD_DEFS[effect], 'rolls': rolls, 'hits': hits}
        if effect == 'fire_team':
            self._consume(player, hand_index, alias, window, session)
            session.fire_team_pending = player.faction
            session.fire_team_selected.clear()
            return ACTION_CARD_DEFS[effect]

        if effect == 'flank_speed':
            self.movement.apply_flank_speed(session)
        elif effect == 'rally':
            player.command_pools['fleet'] += min(
                2, command_tokens_in_reinforcements(player, self.movement.board))
        elif effect == 'harness':
            player.commodities = player.commodity_limit
            on_replenished = getattr(self.movement, 'on_commodities_replenished', None)
            if on_replenished:
                on_replenished(player)
        elif effect == 'counterstroke':
            session.target.command_tokens.remove(player.faction)
            player.command_pools['tactical'] += 1
        elif effect == 'solar_flare':
            session.solar_flare = True
        elif effect == 'blitz':
            session.blitz_factions = getattr(session, 'blitz_factions', set()) | {player.faction}
        elif effect == 'war_machine':
            session.production_limit += 4
            session.war_machine_discount = getattr(session, 'war_machine_discount', 0) + 1
        elif effect == 'morale_boost':
            session.combat_modifiers[player.faction] = session.combat_modifiers.get(player.faction, 0) + 1
        elif effect == 'fighter_prototype':
            session.fighter_combat_modifiers[player.faction] = 2
        elif effect == 'shields_holding':
            session.combat_hits[player.faction] = max(0, session.combat_hits.get(player.faction, 0) - 2)
        elif effect == 'emergency_repairs':
            for unit in session.target.units:
                if unit.owner == player.faction and unit.damaged and unit_profile(unit).get('sustainDamage'):
                    unit.damaged = False
        elif effect == 'skilled_retreat':
            session.skilled_retreat = True
            session.retreat_announced = player.faction
            session.stage = 'retreat_selection'
        elif effect == 'bunker':
            session.bunker_factions.add(player.faction)
        elif effect == 'disable':
            session.disabled_pds.update(unit.unit_id for unit in session.target.units
                                         if unit.kind == 'pds' and unit.owner != player.faction)
        elif effect == 'intercept':
            blocked = session.retreat_announced
            session.retreat_announced = None
            session.retreat_blocked_round = session.combat_round + 1
            session.retreat_log = f'{blocked.upper()} retreat intercepted for this combat round.'
        elif effect == 'maneuvering_jets':
            session.space_cannon_events.pop(0)
            session.cannon_log.append(f'{player.faction.upper()} canceled 1 Space Cannon hit with Maneuvering Jets.')
        elif effect == 'salvage':
            for opponent in self.players:
                if opponent.faction != player.faction and opponent.faction in session.combat_factions:
                    player.commodities += opponent.commodities
                    opponent.commodities = 0
        self._consume(player, hand_index, alias, window, session)
        if effect == 'salvage':
            self.movement.continue_after_salvage(session)
        return ACTION_CARD_DEFS[effect]

    def toggle_fire_team_die(self, faction, index):
        session = self.movement.session
        if not session or session.fire_team_pending != faction:
            raise ValueError('Fire Team is not selecting dice.')
        rolls = session.combat_rolls.get(faction, [])
        if index < 0 or index >= len(rolls):
            raise ValueError('This die is no longer available.')
        selected = session.fire_team_selected
        if index in selected:
            selected.remove(index)
        else:
            selected.add(index)

    def resolve_fire_team(self, faction):
        session = self.movement.session
        if not session or session.fire_team_pending != faction:
            raise ValueError('Fire Team is not ready to resolve.')
        selected = sorted(session.fire_team_selected)
        if not selected:
            raise ValueError('Select at least one die to reroll.')
        for index in selected:
            roll = session.combat_rolls[faction][index]
            value = self.movement.roll_d10(session)
            modifier = roll.get('modifier', 0)
            result = value + modifier
            roll.update(value=result, natural=value,
                        hit=result >= self.movement.combat_threshold(
                            faction, unit_profile(next(unit for unit in session.target.units
                                                       if unit.unit_id == roll['unit_id']))),
                        rerolled=True)
        outgoing = {owner: sum(bool(roll['hit']) for roll in session.combat_rolls.get(owner, []))
                    for owner in session.combat_factions}
        session.combat_hits = {owner: sum(count for shooter, count in outgoing.items()
                                           if shooter != owner)
                               for owner in session.combat_factions}
        session.fire_team_pending = None
        session.fire_team_selected.clear()
        session.combat_assignments = {owner: [] for owner in session.combat_factions}
        return len(selected)
