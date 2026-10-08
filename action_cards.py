"""Base-game action cards and their timing windows."""
from __future__ import annotations

import random

from player import command_tokens_in_reinforcements
from units import Region, UNIT_TYPES, Unit, UnitLocation, unit_profile
from technology import upgrade_profile


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
        self.movement.action_cards = self

    @staticmethod
    def _default_deck():
        return [
            'fs1', 'fs2', 'fs3', 'fs4', 'mb1', 'mb2', 'mb3', 'mb4', 'f_prototype',
            'sh1', 'sh2', 'sh3', 'sh4', 'emergency', 's_retreat1', 's_retreat2',
            's_retreat3', 's_retreat4', 'bunker', 'courageous', 'dh1', 'dh2', 'dh3', 'dh4',
            'disable', 'experimental', 'fire_team', 'silence_space', 'intercept',
            'mjets1', 'mjets2', 'mjets3', 'mjets4', 'salvage', 'upgrade', 'war_effort',
        ]

    def snapshot(self):
        return list(self.deck), list(self.discard)

    @staticmethod
    def name_for(alias):
        return ACTION_CARD_DEFS.get(canonical_action_card(alias), {}).get('name', alias)

    def restore(self, state):
        self.deck[:], self.discard[:] = state
        self.pending = None

    def draw(self, player, count=1):
        drawn = []
        for _ in range(max(0, count)):
            if len(player.action_cards) >= 7:
                break
            if not self.deck and self.discard:
                self.deck[:] = self.discard
                self.discard.clear()
                self.rng.shuffle(self.deck)
            if not self.deck:
                break
            alias = self.deck.pop()
            player.action_cards.append(alias)
            drawn.append(alias)
        return drawn

    def can_draw(self, player, count=1):
        return min(max(0, 7 - len(player.action_cards)), max(0, count),
                   len(self.deck) + len(self.discard)) > 0

    def has_reinforcement(self, player):
        return command_tokens_in_reinforcements(player, self.movement.board) > 0

    @staticmethod
    def _combat_start(session):
        return (session.stage in ('space_combat', 'ground_combat') and
                not session.combat_needs_resolution and not session.combat_rolls)

    def eligible_docks(self, player, session):
        systems = (session.target, *self.movement.neighbors(session.target))
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

    def can_play(self, faction, alias, session=None):
        effect = canonical_action_card(alias)
        if effect not in ACTION_CARD_DEFS:
            return False
        player = next((p for p in self.players if p.faction == faction), None)
        if player is None or self.pending:
            return False
        session = session or self.movement.session
        if not session:
            if effect != 'war_effort' or self.turn is None or self.turn.active_player is not player:
                return False
            if (self.turn.strategy_selection or self.turn.command_allocation or
                    self.turn.strategy_resolution or not self.turn.can_take_action):
                return False
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
            destination_exists = any(
                not any(unit.owner != faction and unit.location.region == Region.SPACE and
                        UNIT_TYPES[unit.kind]['ship'] for unit in tile.units) and
                faction not in tile.command_tokens
                for tile in self.movement.neighbors(session.target))
            return (combat_start and session.combat_type == 'space' and bool(own_units) and
                    destination_exists and
                    self.has_reinforcement(player))
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
        if effect == 'flank_speed':
            return (faction, effect, session.target.position, 'activation')
        if effect == 'in_the_silence_of_space':
            return (faction, effect, session.target.position, 'activation')
        if effect == 'upgrade':
            return (faction, effect, session.target.position, 'activation')
        if effect in ('war_effort',):
            return (faction, effect, 'action')
        if effect in ('bunker', 'disable'):
            return (faction, effect, session.target.position, 'invasion')
        if effect == 'experimental_battlestation':
            return (faction, effect, session.target.position, 'space-cannon')
        if effect == 'maneuvering_jets':
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

    def _consume(self, player, hand_index, alias, window, session):
        player.action_cards.pop(hand_index)
        self.discard.append(alias)
        if window is not None and session is not None:
            session.played_action_windows.add(window)
        elif self.turn is not None:
            self.turn.mark_action_completed()

    def _pending_choices(self):
        pending = self.pending
        if not pending:
            return []
        session = self.movement.session
        player = pending['player']
        effect = pending['effect']
        if effect in ('war_effort',):
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
        if effect == 'upgrade':
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
        self.pending = None

    def resolve_pending(self, choice):
        pending = self.pending
        if not pending:
            raise ValueError('No action-card choice is waiting.')
        player = pending['player']
        effect = pending['effect']
        session = self.movement.session
        if choice not in {value for value, _ in self._pending_choices()}:
            raise ValueError('This action-card choice is no longer available.')
        if effect == 'war_effort':
            tile = self.movement.board[choice]
            self._place_ship(tile, player, 'cruiser', f'war-effort-{player.faction}')
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
            return
        raise ValueError('This action-card choice is no longer available.')

    def _place_ship(self, tile, player, kind, prefix):
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

        if effect in ('war_effort', 'in_the_silence_of_space', 'experimental_battlestation',
                      'direct_hit', 'upgrade'):
            self.pending = {'player': player, 'effect': effect, 'alias': alias,
                            'window': window}
            return ACTION_CARD_DEFS[effect]
        if effect == 'courageous':
            trigger = session.pending_courageous[player.faction].pop(0)
            threshold = trigger['threshold']
            rolls = [self.movement.roll_d10(session) for _ in range(2)]
            hits = sum(value >= threshold for value in rolls)
            self._consume(player, hand_index, alias, window, session)
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
