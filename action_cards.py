"""Focused action-card deck and timing-window rules for combat and movement."""
from __future__ import annotations

import random

from player import command_tokens_in_reinforcements
from units import Region, UNIT_TYPES, unit_profile


ACTION_CARD_DEFS = {
    'flank_speed': {
        'name': 'Flank Speed', 'window': 'After you activate a system',
        'effect': '+1 movement for each ship during this tactical action.',
        'implemented': True,
    },
    'morale_boost': {
        'name': 'Morale Boost', 'window': 'At the start of a combat round',
        'effect': '+1 to your units’ combat rolls this round.',
        'implemented': True,
    },
    'fighter_prototype': {
        'name': 'Fighter Prototype', 'window': 'At the start of the first space-combat round',
        'effect': '+2 to your fighters’ combat rolls this round.',
        'implemented': True,
    },
    'shields_holding': {
        'name': 'Shields Holding', 'window': 'Before assigning hits to your ships in space combat',
        'effect': 'Cancel up to 2 hits against your ships.',
        'implemented': True,
    },
    'emergency_repairs': {
        'name': 'Emergency Repairs', 'window': 'At the start or end of a combat round',
        'effect': 'Repair your damaged units with Sustain Damage in the active system.',
        'implemented': True,
    },
    'skilled_retreat': {
        'name': 'Skilled Retreat', 'window': 'At the start of a space-combat round',
        'effect': 'Retreat all your ships to an eligible adjacent system; end combat in a draw.',
        'implemented': True,
    },
}

# A compact first deck made from the matching base-game cards. Additional cards
# are listed in docs/action_cards.md and can be added as their effects are built.
STARTER_DECK = (['flank_speed'] * 4 + ['morale_boost'] * 4 + ['fighter_prototype'] +
                ['shields_holding'] * 4 + ['emergency_repairs'] + ['skilled_retreat'] * 4)


class ActionCardController:
    def __init__(self, players, movement, deck=None, rng=None):
        self.players = list(players)
        self.movement = movement
        self.rng = rng or random.Random()
        self.deck = list(STARTER_DECK if deck is None else deck)
        self.rng.shuffle(self.deck)
        self.discard: list[str] = []
        self.movement.action_cards = self

    def snapshot(self):
        return list(self.deck), list(self.discard)

    @staticmethod
    def name_for(alias):
        return ACTION_CARD_DEFS.get(alias, {}).get('name', alias)

    def restore(self, state):
        self.deck[:], self.discard[:] = state

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

    def can_play(self, faction, alias, session=None):
        session = session or self.movement.session
        if not session or alias not in ACTION_CARD_DEFS:
            return False
        if faction not in {player.faction for player in self.players}:
            return False
        if faction not in session.combat_factions and faction != session.player.faction:
            return False
        window = self.window_key(faction, alias, session)
        if window is None or window in session.played_action_windows:
            return False
        combat_start = self._combat_start(session)
        if alias == 'flank_speed':
            return (faction == session.player.faction and session.stage == 'movement' and
                    not session.strategic_production and
                    any(source.ships for source in session.sources.values()))
        if alias == 'morale_boost':
            return combat_start
        if alias == 'fighter_prototype':
            return (combat_start and session.combat_type == 'space' and session.combat_round == 0 and
                    any(unit.kind == 'fighter' for unit in self.movement.combat_units(session, faction)))
        if alias == 'shields_holding':
            return (session.combat_type == 'space' and session.stage == 'space_combat' and
                    session.combat_needs_resolution and session.combat_hits.get(faction, 0) > 0 and
                    not any(session.combat_assignments.values()))
        if alias == 'emergency_repairs':
            damaged = any(unit.owner == faction and unit.damaged and unit_profile(unit).get('sustainDamage')
                          for unit in session.target.units)
            return bool(damaged and (combat_start or session.stage == 'combat_end'))
        if alias == 'skilled_retreat':
            destination_exists = any(
                not any(unit.owner != faction and unit.location.region == Region.SPACE and
                        UNIT_TYPES[unit.kind]['ship'] for unit in tile.units) and
                faction not in tile.command_tokens
                for tile in self.movement.neighbors(session.target))
            return (combat_start and session.combat_type == 'space' and destination_exists and
                    self.has_reinforcement(next(p for p in self.players if p.faction == faction)))
        return False

    def window_key(self, faction, alias, session):
        if alias == 'flank_speed':
            return (faction, alias, session.target.position, 'activation')
        if self._combat_start(session):
            return (faction, alias, session.target.position, session.combat_type,
                    session.combat_planet_id, session.combat_round, 'round-start')
        if alias == 'emergency_repairs' and session.stage == 'combat_end':
            return (faction, alias, session.target.position, session.combat_type,
                    session.combat_planet_id, session.combat_round, 'round-end')
        if alias == 'shields_holding' and session.combat_needs_resolution:
            return (faction, alias, session.target.position, session.combat_type,
                    session.combat_planet_id, session.combat_round, 'assign-hits')
        return None

    def playable(self, player, session=None):
        session = session or self.movement.session
        if not session:
            return []
        return [(index, alias) for index, alias in enumerate(player.action_cards)
                if self.can_play(player.faction, alias, session)]

    def play(self, player, hand_index):
        session = self.movement.session
        if not session or not 0 <= hand_index < len(player.action_cards):
            raise ValueError('That action card is no longer in your hand.')
        alias = player.action_cards[hand_index]
        if not self.can_play(player.faction, alias, session):
            raise ValueError('This action card cannot be played in the current timing window.')
        window = self.window_key(player.faction, alias, session)

        if alias == 'flank_speed':
            self.movement.apply_flank_speed(session)
        elif alias == 'morale_boost':
            session.combat_modifiers[player.faction] = session.combat_modifiers.get(player.faction, 0) + 1
        elif alias == 'fighter_prototype':
            session.fighter_combat_modifiers[player.faction] = 2
        elif alias == 'shields_holding':
            session.combat_hits[player.faction] = max(0, session.combat_hits.get(player.faction, 0) - 2)
        elif alias == 'emergency_repairs':
            for unit in session.target.units:
                if unit.owner == player.faction and unit.damaged and unit_profile(unit).get('sustainDamage'):
                    unit.damaged = False
        elif alias == 'skilled_retreat':
            session.skilled_retreat = True
            session.retreat_announced = player.faction
            session.stage = 'retreat_selection'

        session.played_action_windows.add(window)
        player.action_cards.pop(hand_index)
        self.discard.append(alias)
        return ACTION_CARD_DEFS[alias]
