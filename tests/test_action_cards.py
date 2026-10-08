import unittest
from unittest.mock import patch

from action_cards import ActionCardController
from action_card_panel import CARD_ART, CARD_IMAGES
from board import load_board
from movement import MovementController, Session, Snapshot, capital_ship
from player import create_players
from strategic_action import StrategyController
from turn_order import TurnOrder
from units import Region, Unit, UnitLocation


class ActionCardTests(unittest.TestCase):
    def setUp(self):
        self.config, self.board = load_board()
        self.players = create_players(self.board, self.config)
        self.movement = MovementController(self.board, self.players)
        self.cards = ActionCardController(self.players, self.movement, deck=[])
        self.sol = next(player for player in self.players if player.faction == 'sol')

    def test_flank_speed_unlocks_a_route_and_is_spent_on_play(self):
        fleet = [(origin, unit) for origin in self.board.values() for unit in origin.units
                 if unit.owner == self.sol.faction and capital_ship(unit)]
        target = next(tile for tile in self.board.values() if not tile.command_tokens and
                      not any(self.movement.route(origin, tile, unit, self.sol)
                              for origin, unit in fleet) and
                      any(self.movement.route(origin, tile, unit, self.sol, move_bonus=1)
                          for origin, unit in fleet))
        origin, ship = next((origin, unit) for origin, unit in fleet
                            if self.movement.route(origin, target, unit, self.sol, move_bonus=1))
        session = self.movement.activate(self.sol, target.position)
        self.sol.action_cards.append('flank_speed')
        self.assertFalse(session.sources)
        self.assertTrue(self.cards.can_play(self.sol.faction, 'flank_speed', session))

        self.cards.play(self.sol, 0)

        self.assertIn(ship.unit_id, session.sources[origin.position].routes)
        self.assertEqual(session.movement_bonus, 1)
        self.assertEqual(self.sol.action_cards, [])
        self.assertEqual(self.cards.discard, ['flank_speed'])

    def test_undo_activation_restores_played_card_and_discard(self):
        origin = next(tile for tile in self.board.values()
                      if any(unit.owner == self.sol.faction for unit in tile.units))
        target = next(tile for tile in self.movement.neighbors(origin)
                      if not tile.command_tokens and tile.planets)
        self.sol.action_cards.append('flank_speed')
        tactical = self.sol.command_pools['tactical']
        self.movement.activate(self.sol, target.position)
        self.cards.play(self.sol, 0)

        self.assertTrue(self.movement.undo())

        self.assertEqual(self.sol.action_cards, ['flank_speed'])
        self.assertEqual(self.cards.discard, [])
        self.assertEqual(self.sol.command_pools['tactical'], tactical)
        self.assertNotIn(self.sol.faction, target.command_tokens)

    def test_combat_cards_only_highlight_in_their_timing_windows(self):
        target = self.board[next(iter(self.board))]
        sol_ship = Unit('action-sol-dread', 'dreadnought', 'sol', self.sol.color_code,
                        UnitLocation(Region.SPACE))
        fighter = Unit('action-sol-fighter', 'fighter', 'sol', self.sol.color_code,
                       UnitLocation(Region.SPACE))
        hacan = next(player for player in self.players if player.faction == 'hacan')
        enemy_ship = Unit('action-hacan-carrier', 'carrier', 'hacan', hacan.color_code,
                          UnitLocation(Region.SPACE))
        target.units.extend((sol_ship, fighter, enemy_ship))
        session = Session(self.sol, target, {}, Snapshot.capture(self.board, self.sol, self.players),
                          stage='space_combat', combat_factions=('sol', 'hacan'))
        self.movement.session = session
        self.sol.action_cards[:] = ['morale_boost', 'fighter_prototype', 'shields_holding']

        self.assertTrue(self.cards.can_play('sol', 'morale_boost', session))
        self.assertTrue(self.cards.can_play('sol', 'fighter_prototype', session))
        self.assertFalse(self.cards.can_play('sol', 'shields_holding', session))
        self.cards.play(self.sol, 0)
        self.cards.play(self.sol, 0)
        self.assertFalse(self.cards.can_play('sol', 'morale_boost', session))
        self.assertFalse(self.cards.can_play('sol', 'fighter_prototype', session))

        session.combat_hits['sol'] = 3
        session.combat_needs_resolution = True
        self.assertTrue(self.cards.can_play('sol', 'shields_holding', session))
        self.cards.play(self.sol, 0)
        self.assertEqual(session.combat_hits['sol'], 1)

        session.combat_needs_resolution = False
        session.combat_rolls.clear()
        with patch('movement.random.randint', return_value=9):
            self.movement.advance_combat()
        fighter_roll = next(roll for roll in session.combat_rolls['sol']
                            if roll['kind'] == 'fighter')
        dread_roll = next(roll for roll in session.combat_rolls['sol']
                          if roll['kind'] == 'dreadnought')
        self.assertEqual(fighter_roll['modifier'], 3)
        self.assertEqual(dread_roll['modifier'], 1)

    def test_emergency_repairs_is_playable_after_hits_resolve(self):
        target = next(iter(self.board.values()))
        dread = Unit('action-sol-damaged-dread', 'dreadnought', 'sol', self.sol.color_code,
                     UnitLocation(Region.SPACE))
        enemy = next(player for player in self.players if player.faction == 'hacan')
        opponent = Unit('action-hacan-carrier', 'carrier', enemy.faction, enemy.color_code,
                        UnitLocation(Region.SPACE))
        target.units.extend((dread, opponent))
        session = Session(self.sol, target, {}, Snapshot.capture(self.board, self.sol, self.players),
                          stage='space_combat', combat_factions=('sol', 'hacan'),
                          combat_hits={'sol': 1, 'hacan': 0},
                          combat_assignments={'sol': ['action-sol-damaged-dread'], 'hacan': []},
                          combat_needs_resolution=True)
        self.movement.session = session
        self.sol.action_cards.append('emergency_repairs')

        self.movement.advance_combat()

        self.assertTrue(dread.damaged)
        self.assertEqual(session.stage, 'combat_end')
        self.assertTrue(self.cards.can_play('sol', 'emergency_repairs', session))
        self.cards.play(self.sol, 0)
        self.assertFalse(dread.damaged)
        self.movement.advance_combat()
        self.assertEqual(session.stage, 'space_combat')

    def test_skilled_retreat_moves_fleet_and_places_a_reinforcement_token(self):
        target = next(tile for tile in self.board.values()
                      if not tile.planets and len(self.movement.neighbors(tile)) >= 2)
        enemy = next(player for player in self.players if player.faction == 'hacan')
        ship = Unit('action-sol-cruiser', 'cruiser', 'sol', self.sol.color_code,
                    UnitLocation(Region.SPACE))
        opponent = Unit('action-hacan-destroyer', 'destroyer', enemy.faction, enemy.color_code,
                        UnitLocation(Region.SPACE))
        target.units.extend((ship, opponent))
        session = Session(self.sol, target, {}, Snapshot.capture(self.board, self.sol, self.players),
                          stage='space_combat', combat_factions=('sol', 'hacan'))
        self.movement.session = session
        self.sol.action_cards.append('skilled_retreat')
        destination = next(tile for tile in self.movement.neighbors(target)
                           if not any(unit.owner != 'sol' and unit.location.region == Region.SPACE
                                      for unit in tile.units) and 'sol' not in tile.command_tokens)

        self.assertTrue(self.cards.can_play('sol', 'skilled_retreat', session))
        self.cards.play(self.sol, 0)
        self.assertEqual(session.stage, 'retreat_selection')
        self.movement.resolve_retreat(destination.position)

        self.assertIn(ship, destination.units)
        self.assertIn('sol', destination.command_tokens)
        self.assertNotIn(ship, target.units)
        self.assertTrue(session.space_combat_resolved)

    def test_politics_draws_two_cards_and_status_draw_is_limited_to_seven(self):
        self.cards.deck[:] = ['flank_speed', 'morale_boost', 'shields_holding']
        turns = TurnOrder(self.players)
        player = turns.active_player
        turns.strategy_assignments[player.faction].append(3)
        strategy = StrategyController(self.board, turns, self.movement, self.cards)

        strategy.start(3)

        self.assertEqual(len(strategy.session.drawn_cards), 2)
        self.assertEqual(len(player.action_cards), 2)
        strategy.choose_speaker('hacan')
        self.cards.deck.extend(('flank_speed', 'morale_boost'))
        responder = strategy.player
        tokens_before = responder.command_pools['strategic']
        self.assertEqual(responder.faction, 'jolnar')
        self.assertFalse(strategy.secondary_unavailable())
        strategy.accept_secondary()
        self.assertEqual(len(responder.action_cards), 2)
        self.assertEqual(responder.command_pools['strategic'], tokens_before - 1)
        self.assertEqual(strategy.player.faction, 'hacan')
        player.action_cards[:] = ['flank_speed'] * 6
        self.assertEqual(len(self.cards.draw(player, 2)), 1)
        self.assertEqual(len(player.action_cards), 7)


class ActionCardArtworkTests(unittest.TestCase):
    def test_all_shortlisted_cards_have_a_face_image(self):
        self.assertEqual(len(CARD_IMAGES), 30)
        self.assertTrue(all((CARD_ART / filename).is_file() for filename in CARD_IMAGES.values()))


if __name__ == '__main__':
    unittest.main()
