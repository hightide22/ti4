import unittest

from board import load_board
from movement import MovementController, Session, Snapshot
from player import PlanetCard, create_players
from technology_actions import TechnologyActions
from turn_order import TurnOrder
from units import Region, Unit, UnitLocation


class TechnologyActionTests(unittest.TestCase):
    def setUp(self):
        config, self.board = load_board()
        self.players = create_players(self.board, config)
        self.turn = TurnOrder(self.players)
        self.movement = MovementController(self.board, self.players)
        self.actions = TechnologyActions(self.board, self.movement, self.turn)
        self.sol = next(p for p in self.players if p.faction == 'sol')
        self.home = next(tile for tile in self.board.values()
                         if any(unit.owner == 'sol' and unit.kind == 'spacedock'
                                for unit in tile.units))

    def test_codex_x89_is_not_a_separate_action(self):
        self.sol.technologies |= {'x89c4'}
        self.assertEqual(self.actions.targets(self.sol, 'x89c4'), [])
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.actions.use(self.sol, 'x89c4', 'jord')
        self.assertNotIn('x89c4', self.sol.exhausted_technologies)

    def test_production_biomes_pays_strategy_token_and_gives_goods(self):
        hacan = next(p for p in self.players if p.faction == 'hacan')
        self.turn.active_index = self.players.index(hacan)
        hacan.technologies |= {'pm'}
        before = hacan.command_pools['strategic']
        self.actions.use(hacan, 'pm', 'sol')
        self.assertEqual(hacan.command_pools['strategic'], before - 1)
        self.assertEqual(hacan.trade_goods, 4)
        self.assertEqual(self.sol.trade_goods, 2)
        self.assertIn('pm', hacan.exhausted_technologies)

    def test_transit_diodes_moves_ground_force_to_controlled_planet(self):
        self.sol.technologies |= {'td'}
        destination = next(tile for tile in self.board.values()
                           if tile is not self.home and tile.planets)
        planet = destination.planets[0]
        destination.planet_owners[planet.planet_id] = 'sol'
        self.sol.planets.append(PlanetCard(planet))
        infantry = next(unit for unit in self.home.units
                        if unit.owner == 'sol' and unit.kind == 'infantry')
        self.actions.select_transit_unit(self.sol, infantry.unit_id)
        self.actions.place_transit_unit(self.sol, planet.planet_id)
        self.actions.finish_transit(self.sol)
        self.assertIn(infantry, destination.units)
        self.assertEqual(infantry.location.planet_id, planet.planet_id)
        self.assertIn('td', self.sol.exhausted_technologies)
        self.movement.undo()
        self.assertIn(infantry, self.home.units)

    def test_transit_diodes_can_move_a_mech(self):
        self.sol.technologies |= {'td'}
        destination = next(tile for tile in self.board.values()
                           if tile is not self.home and tile.planets)
        planet = destination.planets[0]
        destination.planet_owners[planet.planet_id] = 'sol'
        self.sol.planets.append(PlanetCard(planet))
        mech = Unit('test-transit-mech', 'mech', 'sol', self.sol.color_code,
                    UnitLocation(Region.PLANET, planet_id='jord'))
        self.home.units.append(mech)
        self.assertIn(mech.unit_id, {key for key, _ in self.actions.transit_units(self.sol)})
        self.actions.select_transit_unit(self.sol, mech.unit_id)
        self.actions.place_transit_unit(self.sol, planet.planet_id)
        self.actions.finish_transit(self.sol)
        self.assertIn(mech, destination.units)
        self.assertEqual(mech.location.planet_id, planet.planet_id)
        self.movement.undo()
        self.assertIn(mech, self.home.units)
        self.assertEqual(mech.location.planet_id, 'jord')

    def test_integrated_economy_production_is_capped_by_captured_planet_resources(self):
        self.sol.technologies |= {'ie'}
        target = next(tile for tile in self.board.values()
                      if tile is not self.home and any(p.resources >= 2 for p in tile.planets))
        planet = next(p for p in target.planets if p.resources >= 2)
        target.planet_owners[planet.planet_id] = 'sol'
        self.sol.planets.append(PlanetCard(planet, exhausted=True))
        session = Session(self.sol, target, {}, Snapshot.capture(self.board, self.sol, self.players))
        session.landed_planets = [planet.planet_id]
        session.captured_planets = [planet.planet_id]
        self.movement.session = session
        self.movement.finish_invasion(session)
        self.assertEqual(session.integrated_current, planet.planet_id)
        self.movement.adjust_production('cruiser', 1)
        self.movement.toggle_production_planet('jord')
        self.movement.produce()
        self.assertTrue(any(unit.owner == 'sol' and unit.kind == 'cruiser'
                            for unit in target.units))
        self.assertIsNone(self.movement.session)

    def test_quantum_datahub_node_exchanges_strategy_cards_after_draft(self):
        hacan = next(p for p in self.players if p.faction == 'hacan')
        hacan.technologies |= {'qdn'}
        hacan.trade_goods = 3
        turn = TurnOrder(self.players, strategy_enabled=True)
        for card in range(1, 7):
            turn.choose_strategy_card(card)
        self.assertTrue(turn.qdn_pending)
        own = turn.strategy_assignments['hacan'][0]
        theirs = turn.strategy_assignments['sol'][0]
        turn.qdn_exchange('sol', own, theirs)
        self.assertIn(theirs, turn.strategy_assignments['hacan'])
        self.assertIn(own, turn.strategy_assignments['sol'])
        self.assertEqual(hacan.trade_goods, 0)
        self.assertEqual(self.sol.trade_goods, 3)
        self.assertFalse(turn.qdn_pending)


if __name__ == '__main__':
    unittest.main()
