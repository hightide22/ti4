import unittest

from board import ROOT, load_board
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

    @staticmethod
    def sync_turn_action(turn, movement, previous_count):
        count = sum(entry.counts_as_action for entry in movement.history)
        if count > previous_count and turn.can_take_action:
            turn.mark_action_completed()
        elif count < previous_count and turn.action_used and not movement.session:
            turn.actions_used = max(0, turn.actions_used - 1)
            turn.action_used = bool(turn.actions_used)
        return count

    def cabal_game(self):
        config, board = load_board(ROOT / 'maps/four_player.json',
                                   ('cabal', 'sol', 'hacan', 'letnev'))
        players = create_players(board, config)
        turn = TurnOrder(players)
        movement = MovementController(board, players)
        actions = TechnologyActions(board, movement, turn)
        cabal = next(player for player in players if player.faction == 'cabal')
        sol = next(player for player in players if player.faction == 'sol')
        turn.active_index = players.index(cabal)
        cabal.technologies |= {'vtx'}
        cabal.technologies |= {'fl'}
        cabal.command_pools['strategic'] = 0
        dock_tile = next(tile for tile in board.values()
                         if any(unit.owner == 'cabal' and unit.kind == 'spacedock'
                                for unit in tile.units))
        target_tile = movement.neighbors(dock_tile, 'cabal')[0]
        target = Unit('vortex-target-cruiser', 'cruiser', sol.faction,
                      sol.color_code, UnitLocation(Region.SPACE))
        target_tile.units.append(target)
        return board, players, turn, movement, actions, cabal, sol, dock_tile, target_tile, target

    def winnu_game(self):
        config, board = load_board(ROOT / 'maps/four_player.json',
                                   ('winnu', 'sol', 'hacan', 'letnev'))
        players = create_players(board, config)
        turn = TurnOrder(players)
        movement = MovementController(board, players)
        actions = TechnologyActions(board, movement, turn)
        winnu = next(player for player in players if player.faction == 'winnu')
        turn.active_index = players.index(winnu)
        winnu.technologies |= {'lgf'}
        mecatol = next(tile for tile in board.values() if tile.number == 18)
        planet = next(planet for planet in mecatol.planets if planet.planet_id == 'mr')
        return board, players, turn, movement, actions, winnu, mecatol, planet

    def test_lazax_gate_folding_places_infantry_on_controlled_mecatol(self):
        board, players, turn, movement, actions, winnu, mecatol, planet = self.winnu_game()
        winnu.technologies |= {'fl'}
        mecatol.planet_owners['mr'] = 'winnu'
        winnu.planets.append(PlanetCard(planet))

        self.assertEqual(actions.targets(winnu, 'lgf'),
                         [('mr', 'Mecatol Rex · place 1 infantry')])
        actions.use(winnu, 'lgf', 'mr')
        self.sync_turn_action(turn, movement, 0)

        self.assertTrue(any(unit.owner == 'winnu' and unit.kind == 'infantry' and
                            unit.location.planet_id == 'mr' for unit in mecatol.units))
        self.assertIn('lgf', winnu.exhausted_technologies)
        self.assertEqual(turn.actions_used, 1)
        self.assertEqual(actions.targets(winnu, 'lgf'), [])

    def test_lazax_gate_folding_can_be_exhausted_without_mecatol_control(self):
        board, players, turn, movement, actions, winnu, mecatol, _planet = self.winnu_game()
        self.assertEqual(actions.targets(winnu, 'lgf'), [('no-effect', 'Exhaust · no effect')])
        actions.use(winnu, 'lgf', 'no-effect')
        self.sync_turn_action(turn, movement, 0)
        self.assertIn('lgf', winnu.exhausted_technologies)
        self.assertEqual(turn.actions_used, 1)
        self.assertFalse(any(unit.owner == 'winnu' and unit.kind == 'infantry' and
                             unit.location.planet_id == 'mr' for unit in mecatol.units))
        self.assertTrue(movement.undo())
        self.sync_turn_action(turn, movement, 1)
        self.assertNotIn('lgf', winnu.exhausted_technologies)
        self.assertEqual(turn.actions_used, 0)

    def test_lazax_gate_folding_connects_mecatol_to_alpha_beta_for_movement(self):
        board, players, turn, movement, actions, winnu, mecatol, _planet = self.winnu_game()
        winnu.technologies -= {'lgf'}
        origin = next(tile for tile in board.values()
                      if set(tile.wormholes) & {'ALPHA', 'BETA'} and
                      tile not in board.neighbors(mecatol.position))
        ship = Unit('winnu-gate-carrier', 'carrier', 'winnu', winnu.color_code,
                    UnitLocation(Region.SPACE))
        origin.units.append(ship)

        self.assertIsNone(movement.route(origin, mecatol, ship, winnu))
        winnu.technologies |= {'lgf'}
        winnu.exhausted_technologies.add('lgf')
        self.assertEqual(movement.route(origin, mecatol, ship, winnu),
                         (origin.position, mecatol.position))

        from movement import Session, Snapshot
        movement.session = Session(winnu, mecatol, {},
                                   Snapshot.capture(board, winnu, players),
                                   tactical_activation=True)
        self.assertIn(origin, movement.neighbors(mecatol, 'sol'))
        movement.session = None
        self.assertNotIn(origin, movement.neighbors(mecatol, 'sol'))

        mecatol.planet_owners['mr'] = 'winnu'
        self.assertIsNone(movement.route(origin, mecatol, ship, winnu))

    def test_mageon_implants_lets_player_choose_and_take_an_opponents_card(self):
        self.sol.technologies |= {'mi'}
        hacan = next(player for player in self.players if player.faction == 'hacan')
        hacan.action_cards[:] = ['flank_speed', 'war_effort']
        before = list(hacan.action_cards)

        choices = self.actions.targets(self.sol, 'mi')
        owner_choice = next(key for key, _ in choices if key == 'owner:hacan')
        self.assertFalse(any('War Effort' in label for _, label in choices))
        self.assertIs(self.actions.use(self.sol, 'mi', owner_choice), False)
        choices = self.actions.targets(self.sol, 'mi')
        target_id = next(key for key, label in choices if label == 'War Effort')
        self.assertEqual({label for _, label in choices},
                         {'Choose another player', 'Flank Speed', 'War Effort'})
        self.actions.use(self.sol, 'mi', target_id)
        self.sync_turn_action(self.turn, self.movement, 0)

        self.assertEqual(hacan.action_cards, ['flank_speed'])
        self.assertEqual(self.sol.action_cards[-1], 'war_effort')
        self.assertEqual(len(hacan.action_cards) + len(self.sol.action_cards), len(before))
        self.assertIn('mi', self.sol.exhausted_technologies)
        self.assertEqual(self.turn.actions_used, 1)

    def test_cabal_vortex_captures_reinforcement_unit_without_spending_a_token(self):
        board, players, turn, movement, actions, cabal, sol, _dock, target_tile, target = self.cabal_game()

        self.assertEqual(cabal.technologies, cabal.technologies | {'vtx', 'fl'})
        self.assertIn((target.unit_id, f'SOL · Tile {target_tile.system_id} · Cruiser'),
                      actions.targets(cabal, 'vtx'))
        actions.use(cabal, 'vtx', target.unit_id)
        self.sync_turn_action(turn, movement, 0)

        self.assertIn(target, target_tile.units)
        self.assertEqual(cabal.captured_units, {'cruiser': 1})
        self.assertEqual(cabal.captured_unit_sources[('cruiser', 'sol')], 1)
        self.assertEqual(cabal.command_pools['strategic'], 0)
        self.assertIn('vtx', cabal.exhausted_technologies)
        self.assertEqual(turn.actions_used, 1)

    def test_cabal_vortex_cannot_target_a_blockading_player_or_empty_reinforcements(self):
        board, players, turn, movement, actions, cabal, sol, dock_tile, target_tile, target = self.cabal_game()
        # A Sol ship blockades the Tear. It prevents Vortex from targeting Sol units.
        dock_tile.units[:] = [unit for unit in dock_tile.units
                              if not (unit.owner == 'cabal' and
                                      unit.location.region == Region.SPACE and
                                      unit.kind in ('carrier', 'cruiser', 'destroyer',
                                                    'dreadnought', 'flagship', 'warsun',
                                                    'fighter'))]
        dock_tile.units.append(Unit('vortex-blockader', 'fighter', 'sol',
                                    sol.color_code, UnitLocation(Region.SPACE)))
        self.assertNotIn(target.unit_id, {key for key, _ in actions.targets(cabal, 'vtx')})

        dock_tile.units.remove(next(unit for unit in dock_tile.units
                                    if unit.unit_id == 'vortex-blockader'))
        for index in range(8):
            board[next(iter(board))].units.append(
                Unit(f'vortex-sol-cruiser-{index}', 'cruiser', 'sol', sol.color_code,
                     UnitLocation(Region.SPACE)))
        self.assertNotIn(target.unit_id, {key for key, _ in actions.targets(cabal, 'vtx')})

    def test_cabal_vortex_cannot_target_a_unit_in_the_dock_system_itself(self):
        board, players, turn, movement, actions, cabal, sol, dock_tile, target_tile, target = self.cabal_game()
        target_tile.units.remove(target)
        dock_tile.units.append(target)

        self.assertNotIn(target.unit_id, {key for key, _ in actions.targets(cabal, 'vtx')})

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
