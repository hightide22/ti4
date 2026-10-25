"""Infantry cannot exceed the twelve physical faction pieces."""

import unittest

from board import ROOT, load_board
from exploration import ExplorationController
from movement import MovementController, MovementError, Session, Snapshot
from player import create_players
from units import Region, Unit, UnitLocation
from unit_supply import infantry_count, infantry_remaining


class InfantrySupplyTests(unittest.TestCase):
    def game(self, factions=('sol', 'jolnar', 'hacan', 'letnev')):
        config, board = load_board(ROOT / 'maps/four_player.json', factions)
        players = create_players(board, config)
        return board, players, MovementController(board, players)

    @staticmethod
    def set_infantry_count(board, player, count):
        for tile in board.values():
            tile.units[:] = [unit for unit in tile.units
                             if not (unit.owner == player.faction and unit.kind == 'infantry')]
        home = next(tile for tile in board.values()
                    if any(planet.faction_homeworld == player.faction for planet in tile.planets))
        planet = next(planet for planet in home.planets
                      if home.planet_owners.get(planet.planet_id) == player.faction)
        home.units.extend(Unit(f'{player.faction}-supply-{index}', 'infantry',
                               player.faction, player.color_code,
                               UnitLocation(Region.PLANET, planet.planet_id))
                          for index in range(count))
        return home, planet

    def test_normal_production_caps_infantry_and_rejects_invalid_queue(self):
        board, players, movement = self.game()
        sol = next(player for player in players if player.faction == 'sol')
        home, _planet = self.set_infantry_count(board, sol, 11)
        session = Session(sol, home, {}, Snapshot.capture(board, sol, players))
        session.stage = 'production'
        session.production_limit = 4
        session.production_sites = [(home.planets[0].planet_id, 4)]
        movement.session = session

        movement.adjust_production('infantry', 1)
        self.assertEqual(session.production_choices['infantry'], 1)
        self.assertEqual(infantry_count(sol, board) + 1, 12)
        with self.assertRaisesRegex(MovementError, 'No infantry remain'):
            movement.adjust_production('infantry', 1)

        session.production_choices['infantry'] = 2
        with self.assertRaisesRegex(MovementError, '12 infantry supply'):
            movement.produce()

    def test_infantry_stored_on_spec_ops_card_counts_against_supply(self):
        board, players, _movement = self.game()
        sol = next(player for player in players if player.faction == 'sol')
        self.set_infantry_count(board, sol, 11)
        sol.infantry_on_cards['so2'] = 1
        self.assertEqual(infantry_count(sol, board), 12)
        self.assertEqual(infantry_remaining(sol, board), 0)

    def test_special_placement_abilities_cannot_exceed_infantry_supply(self):
        board, players, movement = self.game(('sol', 'arborec', 'jolnar', 'hacan'))
        sol = next(player for player in players if player.faction == 'sol')
        arborec = next(player for player in players if player.faction == 'arborec')
        sol_home, sol_planet = self.set_infantry_count(board, sol, 11)
        with self.assertRaisesRegex(MovementError, 'needs 2 infantry'):
            movement.orbital_drop(sol, sol_planet.planet_id)

        self.set_infantry_count(board, arborec, 12)
        arborec_planet = next(card.planet for card in arborec.planets)
        with self.assertRaisesRegex(MovementError, 'No infantry remain'):
            movement.mitosis(arborec, arborec_planet.planet_id)

    def test_exploration_and_freelancers_respect_infantry_supply(self):
        board, players, movement = self.game()
        sol = next(player for player in players if player.faction == 'sol')
        home, planet = self.set_infantry_count(board, sol, 12)
        exploration = ExplorationController(board, players)

        self.assertFalse(exploration._place_unit(sol, home, planet.planet_id, 'infantry'))
        exploration.freelancer_offer = (sol.faction, home.position, planet.planet_id)
        with self.assertRaisesRegex(ValueError, 'No infantry remain'):
            exploration.produce_freelancers(sol, movement, 'infantry',
                                            planet_id=planet.planet_id)


if __name__ == '__main__':
    unittest.main()
