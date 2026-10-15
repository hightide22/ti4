"""Codex I action cards that resolve outside a tactical action."""
import unittest
from types import SimpleNamespace

from action_card_deck import ActionCardDeck
from action_cards import ActionCardController
from app import BoardWindow
from board import load_board
from movement import MovementController
from player import create_players
from technology import technology_catalog
from turn_order import TurnOrder
from units import Region, Unit, UnitLocation


class CodexActionCardsTests(unittest.TestCase):
    def setUp(self):
        config, self.board = load_board()
        self.players = create_players(self.board, config)
        self.sol = next(player for player in self.players if player.faction == 'sol')
        self.opponent = next(player for player in self.players if player is not self.sol)
        self.movement = MovementController(self.board, self.players)
        self.turn = TurnOrder([self.sol], strategy_enabled=False)
        self.cards = ActionCardController(self.players, self.movement, deck=[], turn=self.turn)

    def _make_neighbors(self):
        tile = next(tile for tile in self.board.values()
                    if self.sol.faction in tile.planet_owners.values())
        tile.units.append(Unit('codex-neighbor', 'cruiser', self.opponent.faction,
                               self.opponent.color_code, UnitLocation(Region.SPACE)))
        return tile

    def test_curated_deck_contains_the_playable_codex_cards(self):
        deck = ActionCardDeck()
        self.assertIn('plagiarize', deck.cards)
        self.assertIn('master_plan', deck.cards)
        self.assertIn('f_conscription', deck.cards)
        self.assertNotIn('reflective', deck.cards)
        self.assertNotIn('scramble', deck.cards)

    def test_plagiarize_requires_a_neighbor_and_non_faction_technology(self):
        self.sol.action_cards[:] = ['plagiarize']
        self.sol.trade_goods = 5
        self.opponent.technologies = self.opponent.technologies | {'gd'}
        self.assertFalse(self.cards.can_play(self.sol.faction, 'plagiarize'))

        self._make_neighbors()
        self.assertTrue(self.cards.can_play(self.sol.faction, 'plagiarize'))
        self.opponent.technologies = frozenset({'l4'})
        self.assertFalse(self.cards.can_play(self.sol.faction, 'plagiarize'))

    def test_plagiarize_selects_technology_and_explicit_payment(self):
        self._make_neighbors()
        self.opponent.technologies = self.opponent.technologies | {'gd'}
        self.sol.action_cards[:] = ['plagiarize']
        self.sol.trade_goods = 5

        self.cards.play(self.sol, 0)
        self.assertEqual(self.sol.trade_goods, 5)
        self.cards.resolve_pending(('technology', 'gd'))
        self.assertNotIn(('confirm',), dict(self.cards.pending_choices()))
        for _ in range(5):
            self.cards.resolve_pending(('goods', 1))
        self.cards.resolve_pending(('confirm',))

        self.assertIn('gd', self.sol.technologies)
        self.assertEqual(self.sol.trade_goods, 0)
        self.assertTrue(self.turn.action_used)
        self.assertEqual(self.sol.action_cards, [])

    def test_plagiarize_can_mix_planets_and_trade_goods(self):
        self._make_neighbors()
        self.opponent.technologies = self.opponent.technologies | {'gd'}
        self.sol.action_cards[:] = ['plagiarize']
        self.sol.trade_goods = 5
        card = next(card for card in self.sol.planets if
                    self.movement.planet_influence(card.planet) > 0)
        card_influence = self.movement.planet_influence(card.planet)

        self.cards.play(self.sol, 0)
        self.cards.resolve_pending(('technology', 'gd'))
        self.cards.resolve_pending(('planet', card.planet.planet_id))
        for _ in range(max(0, 5 - card_influence)):
            self.cards.resolve_pending(('goods', 1))
        self.cards.resolve_pending(('confirm',))

        self.assertTrue(card.exhausted)
        self.assertEqual(self.sol.trade_goods, 5 - max(0, 5 - card_influence))

    def test_plagiarize_counts_hero_planet_bonus_once(self):
        card = next(card for card in self.sol.planets if card.planet.influence > 0)
        planet_id = card.planet.planet_id
        self.sol.hero_attachments.add(planet_id)
        self.assertEqual(card.planet.influence + 3,
                         self.cards._plagiarize_influence(self.sol, {planet_id}))

    def test_master_plan_opens_one_additional_action_after_an_action(self):
        self.sol.action_cards[:] = ['master_plan', 'master_plan']
        self.assertFalse(self.cards.can_play(self.sol.faction, 'master_plan'))
        self.turn.mark_action_completed()
        self.assertTrue(self.cards.can_play(self.sol.faction, 'master_plan'))

        self.cards.play(self.sol, 0)
        self.assertEqual(self.turn.actions_used, 0)
        self.assertTrue(self.turn.action_used)
        self.assertFalse(self.cards.can_play(self.sol.faction, 'master_plan'))
        self.turn.mark_action_completed()
        self.assertTrue(self.cards.can_play(self.sol.faction, 'master_plan'))

    def test_master_plan_app_flow_second_action_undo_and_end_turn(self):
        self.sol.action_cards[:] = ['master_plan']
        fake_window = SimpleNamespace(
            strategy=SimpleNamespace(session=None), smoke=False,
            movement=self.movement, action_cards=self.cards, turn_order=self.turn,
            turn_history_size=0, turn_history_action_count=0,
            restore_infantry_on_cards=lambda: None)
        self.movement.history.append(SimpleNamespace(counts_as_action=True))
        BoardWindow.sync_turn_action(fake_window)
        self.assertEqual(1, self.turn.actions_used)

        self.cards.play(self.sol, 0)
        self.assertEqual(0, self.turn.actions_used)
        self.movement.history.append(SimpleNamespace(counts_as_action=True))
        BoardWindow.sync_turn_action(fake_window)
        self.assertEqual(1, self.turn.actions_used)

        self.movement.history.pop()
        BoardWindow.sync_turn_action(fake_window)
        self.assertEqual(0, self.turn.actions_used)
        self.assertTrue(self.turn.action_used)
        self.movement.history.append(SimpleNamespace(counts_as_action=True))
        BoardWindow.sync_turn_action(fake_window)
        self.assertEqual(1, self.turn.actions_used)
        self.assertTrue(self.turn.action_used)

        self.turn.end_turn()
        self.assertEqual(0, self.turn.actions_used)
        self.assertFalse(self.turn.action_used)

    def test_fighter_conscription_places_one_per_eligible_system_then_resolves_capacity(self):
        for tile in self.board.values():
            tile.units[:] = [unit for unit in tile.units if unit.owner != self.sol.faction]
        tile = next(tile for tile in self.board.values()
                    if self.sol.faction in tile.planet_owners.values())
        planet_id = tile.planets[0].planet_id
        tile.units.append(Unit('codex-dock', 'spacedock', self.sol.faction,
                               self.sol.color_code, UnitLocation(Region.PLANET, planet_id)))
        tile.units.extend(Unit(f'codex-fighter-{index}', 'fighter', self.sol.faction,
                               self.sol.color_code, UnitLocation(Region.SPACE))
                          for index in range(3))
        self.sol.action_cards[:] = ['f_conscription']

        self.cards.play(self.sol, 0)
        self.assertIsNotNone(self.cards.pending)
        self.assertEqual(self.cards.pending['overflow_kind'], 'capacity')
        self.assertFalse(self.cards.cancel_pending())
        self.cards.resolve_pending(self.cards.pending_choices()[0][0])

        self.assertIsNone(self.cards.pending)
        self.assertEqual(sum(unit.kind == 'fighter' and unit.owner == self.sol.faction
                             for unit in tile.units), 3)
        self.assertTrue(self.turn.action_used)

    def test_fighter_conscription_fighter_ii_can_trigger_fleet_supply_choice(self):
        for tile in self.board.values():
            tile.units[:] = [unit for unit in tile.units if unit.owner != self.sol.faction]
        tile = next(tile for tile in self.board.values()
                    if not any(unit.owner != self.sol.faction for unit in tile.units))
        carrier = Unit('codex-carrier', 'carrier', self.sol.faction,
                       self.sol.color_code, UnitLocation(Region.SPACE))
        tile.units.append(carrier)
        tile.units.extend(Unit(f'codex-infantry-{index}', 'infantry', self.sol.faction,
                               self.sol.color_code,
                               UnitLocation(Region.TRANSPORT, carrier_id=carrier.unit_id))
                          for index in range(carrier.capacity))
        self.sol.command_pools['fleet'] = 1
        self.sol.technologies = self.sol.technologies | {'ff2'}
        self.sol.action_cards[:] = ['f_conscription']

        self.cards.play(self.sol, 0)
        self.assertEqual(self.cards.pending['overflow_kind'], 'fleet')
        fighter_id = next(unit.unit_id for unit in tile.units if unit.kind == 'fighter')
        self.cards.resolve_pending(fighter_id)

        self.assertIsNone(self.cards.pending)
        self.assertEqual(sum(unit.kind == 'fighter' for unit in tile.units), 0)
        self.assertIn(carrier, tile.units)


if __name__ == '__main__':
    unittest.main()
