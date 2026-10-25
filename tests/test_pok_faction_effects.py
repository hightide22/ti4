"""Executable PoK leader and faction abilities."""

import random
import unittest
from dataclasses import replace

from board import ROOT, load_board
from leader_effects import (use_arborec_agent, use_cabal_agent, use_cabal_hero, use_empyrean_agent,
                            use_empyrean_hero, use_nomad_artuno, use_nomad_hero,
                            use_ghost_agent, use_l1z1x_agent, use_saar_agent,
                            l1z1x_agent_mech_available,
                            use_xxcha_agent,
                            clear_round_hero_effects, use_mahact_hero, use_titans_agent,
                            use_titans_hero)
from leaders import leader_available, ready_leaders, unlock_leader
from player import create_players
from pok_factions import (cabal_capture_destroyed, cabal_return_captured,
                          keleres_council_patronage, mahact_gain_fleet_token,
                          mahact_fleet_limit, naaz_explore_planet, titans_awaken,
                          titans_hero_space_cannon, titans_place_sleeper)
from units import Region, Unit, UnitLocation


class FixedDie:
    def randint(self, low, high):
        return 2


class PokEffectsTests(unittest.TestCase):
    def game(self, faction):
        config, board = load_board(ROOT / 'maps/four_player.json',
                                   (faction, 'sol', 'jolnar', 'hacan'))
        players = create_players(board, config)
        return board, players[0], players[1]

    def test_cabal_agent_and_devour_capture_without_touching_structures(self):
        board, cabal, sol = self.game('cabal')
        sol.commodities = sol.commodity_limit
        use_cabal_agent(cabal, sol, 'carrier')
        self.assertEqual(sol.commodities, 0)
        self.assertEqual(sol.trade_goods, sol.commodity_limit)
        self.assertEqual(cabal.captured_units['carrier'], 1)
        self.assertFalse(leader_available(cabal, 'cabalagent'))
        ready_leaders(cabal)
        carrier = Unit('enemy-carrier', 'carrier', 'sol', sol.color_code,
                       UnitLocation(Region.SPACE))
        self.assertTrue(cabal_capture_destroyed(cabal, carrier, during_combat=True))
        self.assertFalse(cabal_capture_destroyed(cabal, carrier, during_combat=False))
        self.assertEqual(cabal.captured_units['carrier'], 2)
        self.assertTrue(cabal_return_captured(cabal, 'carrier'))
        self.assertEqual(cabal.captured_units['carrier'], 1)

    def test_cabal_hero_rolls_for_enemy_ship_and_captures_its_cargo(self):
        board, cabal, sol = self.game('cabal')
        home = next(tile for tile in board.values() if tile.system_id == '54')
        carrier = Unit('carrier-to-capture', 'carrier', 'sol', sol.color_code,
                       UnitLocation(Region.SPACE))
        infantry = Unit('infantry-to-capture', 'infantry', 'sol', sol.color_code,
                        UnitLocation(Region.TRANSPORT, carrier_id=carrier.unit_id))
        home.units.extend((carrier, infantry))
        self.assertTrue(unlock_leader(cabal, 'cabalhero'))
        self.assertIn((carrier.unit_id, 2), use_cabal_hero(cabal, board, FixedDie()))
        self.assertNotIn(carrier, home.units)
        self.assertNotIn(infantry, home.units)
        self.assertEqual(cabal.captured_units['carrier'], 1)
        self.assertEqual(cabal.captured_units['infantry'], 1)
        self.assertFalse(leader_available(cabal, 'cabalhero'))

    def test_empyrean_agent_grants_token_only_for_planetless_destination(self):
        board, empyrean, sol = self.game('empyrean')
        empty = next(tile for tile in board.values() if not tile.planets)
        ship = Unit('sol-moved', 'carrier', 'sol', sol.color_code,
                    UnitLocation(Region.SPACE))
        empty.units.append(ship)
        before = sol.command_pools['tactical']
        use_empyrean_agent(empyrean, sol, board, empty, 'tactical')
        self.assertEqual(sol.command_pools['tactical'], before + 1)
        self.assertFalse(leader_available(empyrean, 'empyreanagent'))

    def test_l1z1x_agent_replaces_active_system_infantry_with_mech(self):
        board, l1z1x, sol = self.game('l1z1x')
        tile = next(tile for tile in board.values()
                    if any(unit.owner == 'l1z1x' and unit.kind == 'infantry'
                           for unit in tile.units))
        infantry = next(unit for unit in tile.units
                        if unit.owner == 'l1z1x' and unit.kind == 'infantry')
        mech = use_l1z1x_agent(l1z1x, l1z1x, tile, infantry.unit_id, board)
        self.assertNotIn(infantry, tile.units)
        self.assertIn(mech, tile.units)
        self.assertEqual('mech', mech.kind)
        self.assertEqual(infantry.location, mech.location)
        self.assertFalse(leader_available(l1z1x, 'l1z1xagent'))

    def test_saar_agent_matches_the_highest_ship_move_value(self):
        board, saar, sol = self.game('saar')
        tile = next(tile for tile in board.values() if not tile.units)
        carrier = Unit('sol-agent-carrier', 'carrier', 'sol', sol.color_code,
                       UnitLocation(Region.SPACE))
        hacan_color = next(unit.color_code for system in board.values()
                           for unit in system.units if unit.owner == 'hacan')
        fast_cruiser = Unit('hacan-agent-cruiser', 'cruiser', 'hacan',
                            hacan_color,
                            UnitLocation(Region.SPACE))
        tile.units.extend((carrier, fast_cruiser))

        granted_move = use_saar_agent(saar, sol, board, carrier.unit_id)

        self.assertEqual(fast_cruiser.move_value, granted_move)
        self.assertFalse(leader_available(saar, 'saaragent'))

    def test_ghost_agent_requires_non_delta_wormhole_and_exhausts_on_use(self):
        board, ghost, _ = self.game('ghost')
        tile = next(tile for tile in board.values())
        tile.definition = replace(tile.definition, wormholes=('DELTA',))
        with self.assertRaises(ValueError):
            use_ghost_agent(ghost, tile, board)
        self.assertTrue(leader_available(ghost, 'ghostagent'))

        tile.definition = replace(tile.definition, wormholes=('GAMMA',))
        use_ghost_agent(ghost, tile, board)
        self.assertFalse(leader_available(ghost, 'ghostagent'))

    def test_l1z1x_agent_does_not_exhaust_without_mech_in_reinforcements(self):
        board, l1z1x, _ = self.game('l1z1x')
        tile = next(tile for tile in board.values()
                    if any(unit.owner == 'l1z1x' and unit.kind == 'infantry'
                           for unit in tile.units))
        infantry = next(unit for unit in tile.units
                        if unit.owner == 'l1z1x' and unit.kind == 'infantry')
        other = next(candidate for candidate in board.values()
                     if candidate is not tile and candidate.planets)
        other.command_tokens.add('l1z1x')
        for index in range(4):
            other.units.append(Unit(f'l1z1x-reserve-mech-{index}', 'mech', 'l1z1x',
                                    l1z1x.color_code,
                                    UnitLocation(Region.PLANET, other.planets[0].planet_id)))
        with self.assertRaises(ValueError):
            use_l1z1x_agent(l1z1x, l1z1x, tile, infantry.unit_id, board)
        self.assertTrue(leader_available(l1z1x, 'l1z1xagent'))

    def test_l1z1x_agent_redeploys_mech_from_unlocked_system_when_supply_is_empty(self):
        board, l1z1x, _ = self.game('l1z1x')
        tile = next(tile for tile in board.values()
                    if any(unit.owner == 'l1z1x' and unit.kind == 'infantry'
                           for unit in tile.units))
        infantry = next(unit for unit in tile.units
                        if unit.owner == 'l1z1x' and unit.kind == 'infantry')
        other = next(candidate for candidate in board.values()
                     if candidate is not tile and candidate.planets)
        mechs = []
        for index in range(4):
            mech = Unit(f'l1z1x-deploy-mech-{index}', 'mech', 'l1z1x',
                        l1z1x.color_code,
                        UnitLocation(Region.PLANET, other.planets[0].planet_id))
            other.units.append(mech)
            mechs.append(mech)

        self.assertTrue(l1z1x_agent_mech_available(l1z1x, tile, board))
        replacement = use_l1z1x_agent(l1z1x, l1z1x, tile, infantry.unit_id, board)

        self.assertIn(replacement, tile.units)
        self.assertNotIn(infantry, tile.units)
        self.assertNotIn(mechs[0], other.units)
        self.assertEqual(4, sum(unit.owner == 'l1z1x' and unit.kind == 'mech'
                                 for system in board.values() for unit in system.units))

    def test_arborec_agent_replaces_ship_and_keeps_legal_cargo_attached(self):
        board, arborec, sol = self.game('arborec')
        tile = next(tile for tile in board.values() if not tile.units)
        carrier = Unit('sol-agent-carrier', 'carrier', 'sol', sol.color_code,
                       UnitLocation(Region.SPACE))
        infantry = Unit('sol-agent-infantry', 'infantry', 'sol', sol.color_code,
                        UnitLocation(Region.TRANSPORT, carrier_id=carrier.unit_id))
        tile.units.extend((carrier, infantry))
        replacement = use_arborec_agent(arborec, sol, board, carrier.unit_id,
                                        'dreadnought')
        self.assertNotIn(carrier, tile.units)
        self.assertIn(replacement, tile.units)
        self.assertEqual('dreadnought', replacement.kind)
        self.assertEqual(replacement.unit_id, infantry.location.carrier_id)
        self.assertFalse(leader_available(arborec, 'arborecagent'))

    def test_xxcha_agent_readies_planet_and_optionally_returns_infantry(self):
        board, xxcha, _ = self.game('xxcha')
        card = xxcha.planets[0]
        home = next(tile for tile in board.values()
                    if card.planet.planet_id in tile.planet_owners)
        neighbor = next(tile for tile in board.neighbors(home.position) if tile.planets)
        neighbor.planet_owners[neighbor.planets[0].planet_id] = 'xxcha'
        card.exhausted = True
        infantry = Unit('xxcha-agent-infantry', 'infantry', 'xxcha', xxcha.color_code,
                        UnitLocation(Region.PLANET, card.planet.planet_id))
        home.units.append(infantry)
        self.assertIs(use_xxcha_agent(xxcha, board, card.planet.planet_id,
                                      remove_infantry_id=infantry.unit_id), infantry)
        self.assertFalse(card.exhausted)
        self.assertNotIn(infantry, home.units)
        self.assertFalse(leader_available(xxcha, 'xxchaagent'))

    def test_empyrean_hero_restores_frontiers_and_returns_systems_to_explore(self):
        from exploration import ExplorationController
        board, empyrean, _ = self.game('empyrean')
        empty = next(tile for tile in board.values() if not tile.planets)
        empty.units.append(Unit('empyrean-explorer', 'carrier', 'empyrean',
                                empyrean.color_code, UnitLocation(Region.SPACE)))
        exploration = ExplorationController(board, [empyrean],
                                            rng=random.Random(1), relic_deck=[])
        exploration.space_tokens[empty.position].clear()
        unlock_leader(empyrean, 'empyreanhero')
        eligible = use_empyrean_hero(empyrean, exploration, board)
        self.assertIn(empty, eligible)
        self.assertIn('frontier', exploration.space_tokens[empty.position])
        self.assertFalse(leader_available(empyrean, 'empyreanhero'))

    def test_naaz_mech_offers_two_exploration_cards(self):
        from exploration import ExplorationController
        board, naaz, _ = self.game('naaz')
        tile = next(tile for tile in board.values() if tile.number == 30)
        planet = tile.planets[0]
        tile.planet_owners[planet.planet_id] = naaz.faction
        tile.units.append(Unit('naaz-explorer-mech', 'mech', 'naaz', naaz.color_code,
                               UnitLocation(Region.PLANET, planet.planet_id)))
        exploration = ExplorationController(board, [naaz],
                                            rng=random.Random(1), relic_deck=[])
        outcome = naaz_explore_planet(naaz, exploration, tile, planet.planet_id)
        self.assertEqual(len(outcome.offered_cards), 2)

    def test_nomad_artuno_collects_stored_trade_goods_when_readied(self):
        board, nomad, _ = self.game('nomad')
        use_nomad_artuno(nomad, 3)
        self.assertEqual(nomad.trade_goods, 0)
        ready_leaders(nomad)
        self.assertEqual(nomad.trade_goods, 3)

    def test_nomad_hero_stays_on_board_until_end_of_round(self):
        _, nomad, _ = self.game('nomad')
        unlock_leader(nomad, 'nomadhero')
        use_nomad_hero(nomad)
        self.assertIn('nomad_command_token_exit', nomad.hero_round_flags)
        self.assertFalse(nomad.leaders['nomadhero'].purged)
        self.assertFalse(leader_available(nomad, 'nomadhero'))
        clear_round_hero_effects(nomad)
        self.assertTrue(nomad.leaders['nomadhero'].purged)
        self.assertFalse(nomad.hero_round_flags)

    def test_titans_sleeper_awaken_and_hero_attachment(self):
        board, titans, _ = self.game('titans')
        home = next(tile for tile in board.values() if tile.system_id == '55')
        planet = home.planets[0]
        titans_place_sleeper(titans, board, home, planet.planet_id)
        self.assertIn(planet.planet_id, home.sleeper_tokens)
        placed = titans_awaken(titans, board, home, planet_ids=(planet.planet_id,))
        self.assertEqual(len(placed), 1)
        self.assertEqual(placed[0].kind, 'pds')
        self.assertNotIn(planet.planet_id, home.sleeper_tokens)
        self.assertEqual(use_titans_agent(titans, 3), 2)
        self.assertTrue(unlock_leader(titans, 'titanshero'))
        titans.planets[0].exhausted = True
        use_titans_hero(titans)
        self.assertFalse(titans.planets[0].exhausted)
        self.assertIn('elysium', titans.hero_attachments)
        self.assertEqual(titans_hero_space_cannon(titans, home), (5, 3))

    def test_titans_pds_limit_is_per_player_and_cannot_be_exceeded(self):
        config, board = load_board(ROOT / 'maps/four_player.json',
                                   ('titans', 'sol', 'jolnar', 'hacan'))
        players = create_players(board, config)
        by_faction = {player.faction: player for player in players}
        titans, sol, jolnar = (by_faction[faction] for faction in
                               ('titans', 'sol', 'jolnar'))
        target = next(tile for tile in board.values()
                      if any(planet.faction_homeworld == 'titans'
                             for planet in tile.planets))
        planet = target.planets[0]
        titans_place_sleeper(titans, board, target, planet.planet_id)
        target.units.extend((
            Unit('sol-pds-on-titans-planet', 'pds', sol.faction, sol.color_code,
                 UnitLocation(Region.PLANET, planet.planet_id)),
            Unit('jolnar-pds-on-titans-planet', 'pds', jolnar.faction, jolnar.color_code,
                 UnitLocation(Region.PLANET, planet.planet_id)),
        ))

        placed = titans_awaken(titans, board, target, planet_ids=(planet.planet_id,))

        self.assertEqual(1, len(placed), 'foreign PDS do not count against Titans limit')
        self.assertNotIn(planet.planet_id, target.sleeper_tokens)
        titans_place_sleeper(titans, board, target, planet.planet_id)
        target.units.append(Unit('titans-second-pds', 'pds', titans.faction,
                                 titans.color_code,
                                 UnitLocation(Region.PLANET, planet.planet_id)))
        placed = titans_awaken(titans, board, target, planet_ids=(planet.planet_id,))
        self.assertEqual((), placed)
        self.assertIn(planet.planet_id, target.sleeper_tokens)
        self.assertEqual(2, sum(unit.kind == 'pds' and unit.owner == titans.faction and
                                unit.location.region == Region.PLANET and
                                unit.location.planet_id == planet.planet_id
                                for unit in target.units))

    def test_mahact_foreign_token_changes_fleet_supply_and_enemy_reserve(self):
        board, mahact, sol = self.game('mahact')
        before = mahact_fleet_limit(mahact)
        self.assertTrue(mahact_gain_fleet_token(mahact, sol, board))
        self.assertEqual(mahact_fleet_limit(mahact), before + 1)
        self.assertIn('mahact', sol.foreign_command_tokens_out)
        self.assertFalse(mahact_gain_fleet_token(mahact, sol, board))

    def test_mahact_hero_moves_space_fleet_and_carried_units_into_combat(self):
        board, mahact, sol = self.game('mahact')
        source = next(tile for tile in board.values() if tile.system_id == '52')
        target = board.neighbors(source.position)[0]
        enemy = Unit('mahact-hero-enemy', 'carrier', 'sol', sol.color_code,
                     UnitLocation(Region.SPACE))
        target.units.append(enemy)
        carrier = next(unit for unit in source.units if unit.kind == 'carrier')
        cargo = Unit('mahact-hero-cargo', 'infantry', 'mahact', mahact.color_code,
                     UnitLocation(Region.TRANSPORT, carrier_id=carrier.unit_id))
        source.units.append(cargo)
        moving = tuple(unit for unit in source.units if unit.location.region != Region.PLANET)
        unlock_leader(mahact, 'mahacthero')
        self.assertEqual(use_mahact_hero(mahact, board, source, target), ('mahact', 'sol'))
        self.assertTrue(all(unit in target.units for unit in moving))
        self.assertTrue(all(unit not in source.units for unit in moving))
        self.assertFalse(leader_available(mahact, 'mahacthero'))

    def test_keleres_patronage_replenishes_and_gains_trade_good(self):
        board, keleres, _ = self.game('keleresm')
        keleres.commodities = 0
        keleres_council_patronage(keleres)
        self.assertEqual(keleres.commodities, keleres.commodity_limit)
        self.assertEqual(keleres.trade_goods, 1)


if __name__ == '__main__':
    unittest.main()
