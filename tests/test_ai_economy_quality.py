from collections import Counter
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ai_economy import build_plan, command_targets, technology_values
from ai_strategy import warfare_value
from board import load_board
from game_ai import GameAI
from movement import MovementController, Session, Snapshot
from player import create_players
from technology import research_technology, technology_catalog
from turn_order import TurnOrder
from units import Region, Unit, UnitLocation


class EconomicPlanningTests(unittest.TestCase):
    def game(self, faction='sol'):
        config, board = load_board(factions=(faction, 'hacan', 'jolnar'))
        players = create_players(board, config)
        player = players[0]
        tile = next(t for t in board.values() if any(
            u.owner == faction and u.kind == 'spacedock' for u in t.units))
        movement = MovementController(board, players)
        window = SimpleNamespace(board=board, movement=movement,
                                 turn_order=TurnOrder(players))
        return GameAI(window, {faction}), player, tile

    def unit(self, player, kind, index=0, planet=None, profile=None):
        location = UnitLocation(Region.PLANET, planet) if planet else UnitLocation(Region.SPACE)
        return Unit(f'economy-{player.faction}-{kind}-{index}', kind, player.faction,
                    player.color_code, location, profile_id=profile)

    def production(self, ai, player, tile, limit=6, integrated=None):
        session = Session(player, tile, {}, Snapshot.capture(
            ai.window.board, player, ai.window.movement.players))
        session.stage = 'production'
        session.production_sites = ai.window.movement.production_sites(session)
        session.production_limit = limit
        session.integrated_current = integrated
        ai.window.movement.session = session
        return session

    def test_expansion_builds_combined_transport_force_and_keeps_savings(self):
        ai, player, tile = self.game()
        tile.units[:] = [u for u in tile.units if u.kind == 'spacedock']
        player.trade_goods = 12
        session = self.production(ai, player, tile)
        plan = build_plan(ai.window.movement, session, 1)
        self.assertEqual(plan['carrier'], 1)
        self.assertGreaterEqual(plan['infantry'], 2)
        self.assertGreaterEqual(plan['destroyer'] + plan['cruiser'] + plan['dreadnought'], 1)
        self.assertEqual(plan['flagship'] + plan['warsun'], 0)
        ai.resolve_production()
        self.assertGreater(player.trade_goods, 0)

    def test_ground_garrisons_do_not_use_ship_capacity_for_fighters(self):
        ai, player, tile = self.game()
        planet = tile.planets[0].planet_id
        tile.units[:] = [u for u in tile.units if u.kind == 'spacedock']
        tile.units.append(self.unit(player, 'carrier', profile='sol_carrier2'))
        tile.units.extend(self.unit(player, 'infantry', i, planet) for i in range(8))
        player.technologies |= {'ac2'}
        player.command_pools['fleet'] = 1
        player.trade_goods = 6
        session = self.production(ai, player, tile)
        plan = build_plan(ai.window.movement, session, 2)
        self.assertGreater(plan['fighter'], 0)
        self.assertEqual(plan['carrier'] + plan['cruiser'] + plan['dreadnought'], 0)
        ai.resolve_production()
        self.assertEqual(ai.window.movement.capacity_overflow(tile, player.faction), 0)
        self.assertLessEqual(len(ai.window.movement._fleet_ships_at(tile, player.faction)), 1)

    def test_saar_infantry_and_fighters_fit_floating_factory(self):
        ai, player, tile = self.game('saar')
        tile.units[:] = [u for u in tile.units if u.kind == 'spacedock']
        tile.planet_owners.clear()
        player.trade_goods = 5
        player.command_pools['fleet'] = 0
        self.production(ai, player, tile, 5)
        ai.resolve_production()
        infantry = [u for u in tile.units if u.kind == 'infantry']
        self.assertTrue(infantry)
        self.assertTrue(all(u.location.region == Region.TRANSPORT for u in infantry))
        self.assertEqual(ai.window.movement.capacity_overflow(tile, 'saar'), 0)

    def test_integrated_economy_obeys_planet_ceiling_without_sarween_discount(self):
        ai, player, tile = self.game()
        planet = next(p for p in ai.window.board.values() for p in p.planets if p.resources == 1)
        target = next(t for t in ai.window.board.values() if planet in t.planets)
        target.planet_owners[planet.planet_id] = player.faction
        target.units.clear()
        player.technologies |= {'st', 'ie'}
        player.trade_goods = 15
        session = self.production(ai, player, target, 9999, planet.planet_id)
        session.production_sites = [(planet.planet_id, 9999)]
        plan = build_plan(ai.window.movement, session, 6)
        raw_cost = sum(ai.window.movement.unit_cost(k, player) * n for k, n in plan.items())
        self.assertLessEqual(raw_cost, 1)
        ai.resolve_production()
        self.assertNotEqual(session.stage, 'production')

    def test_no_duplicate_flagship_after_all_models_are_in_play(self):
        ai, player, tile = self.game()
        other = next(t for t in ai.window.board.values() if t is not tile)
        other.units.append(self.unit(player, 'flagship'))
        for i in range(4):
            other.units.append(self.unit(player, 'carrier', i + 10))
        player.trade_goods = 20
        session = self.production(ai, player, tile, 8)
        plan = build_plan(ai.window.movement, session, 5)
        self.assertEqual(plan['carrier'], 0)
        self.assertEqual(plan['flagship'], 0)

    def test_destroyer_research_responds_to_enemy_fighter_fleet(self):
        ai, player, tile = self.game()
        enemy = ai.window.turn_order.players[1]
        tile.units[:] = [self.unit(player, 'destroyer', i) for i in range(4)]
        other = next(t for t in ai.window.board.values() if t is not tile)
        other.units.extend(self.unit(enemy, 'fighter', i + 20) for i in range(12))
        player.technologies = frozenset({'amd', 'ps', 'md'})
        player.trade_goods = 4
        with patch('game_ai.available_technologies', return_value=[
                technology_catalog()['dd2'], technology_catalog()['gd']]):
            self.assertEqual(ai.choose_technology(player, 4)[0], 'dd2')

    def test_shipbuilding_counters_neighbor_fighter_swarm(self):
        ai, player, tile = self.game()
        player.trade_goods = 12
        player.command_pools['fleet'] = 5
        session = self.production(ai, player, tile)
        peaceful = build_plan(ai.window.movement, session, 2)
        neighbor = next(iter(ai.window.movement.neighbors(tile)))
        enemy = ai.window.turn_order.players[1]
        neighbor.units.extend(self.unit(enemy, 'fighter', i + 90) for i in range(10))
        research_technology(player, 'dd2', ai.window.board)
        defensive = build_plan(ai.window.movement, session, 2)
        self.assertGreater(defensive['destroyer'], peaceful['destroyer'])
        self.assertGreater(defensive['fighter'], 0)

    def test_economic_preview_is_deterministic_and_does_not_spend_resources(self):
        ai, player, tile = self.game()
        player.trade_goods = 12
        session = self.production(ai, player, tile)
        units_before = tuple(u.unit_id for u in tile.units)
        first = build_plan(ai.window.movement, session, 2)
        self.assertEqual(first, build_plan(ai.window.movement, session, 2))
        self.assertEqual(tuple(u.unit_id for u in tile.units), units_before)
        self.assertEqual(player.trade_goods, 12)
        self.assertFalse(any(card.exhausted for card in player.planets))
        self.assertFalse(session.production_choices)

    def test_prerequisite_research_follows_useful_destroyer_upgrade(self):
        ai, player, tile = self.game()
        enemy = ai.window.turn_order.players[1]
        tile.units[:] = [self.unit(player, 'destroyer', i) for i in range(4)]
        other = next(t for t in ai.window.board.values() if t is not tile)
        other.units.extend(self.unit(enemy, 'fighter', i + 20) for i in range(12))
        player.technologies = frozenset({'ps'})
        ai.window.turn_order.round_number = 3
        values = technology_values(player, ai.window.board, 3)
        self.assertGreater(values['md'], values['nm'])
        with patch('game_ai.available_technologies', return_value=[
                technology_catalog()['md'], technology_catalog()['nm']]):
            self.assertEqual(ai.choose_technology(player, 0)[0], 'md')

    def test_command_allocation_matches_full_fleet_and_letnev_bonus(self):
        ai, player, tile = self.game()
        tile.units[:] = [self.unit(player, 'cruiser', i) for i in range(3)]
        player.trade_goods = 8
        player.command_pools = {'tactical': 3, 'fleet': 3, 'strategic': 2}
        self.assertEqual(ai.command_pool(player), 'fleet')
        player.faction = 'letnev'
        for u in tile.units:
            u.owner = 'letnev'
        self.assertEqual(command_targets(player, ai.window.board, 3)['fleet'], 2)

    def test_diplomacy_values_exhausted_income_not_planet_count(self):
        ai, player, tile = self.game()
        player.planets[0].exhausted = True
        ctl = SimpleNamespace(readyable_planets=lambda p: p.planets)
        session = SimpleNamespace(player=player, card=2)
        player.command_pools['strategic'] = 1
        self.assertTrue(ai._secondary_worthwhile(ctl, session))
        cheap = SimpleNamespace(planet=SimpleNamespace(resources=0, influence=1), exhausted=True)
        ctl.readyable_planets = lambda p: [cheap, cheap]
        self.assertFalse(ai._secondary_worthwhile(ctl, session))

    def test_saar_warfare_values_mobile_factory_and_its_ground_forces(self):
        ai, player, tile = self.game('saar')
        tile.units[:] = [u for u in tile.units if u.kind in ('spacedock', 'infantry')]
        other = next(t for t in ai.window.board.values() if t is not tile and t.planets)
        other.units.clear()
        self.assertGreater(warfare_value(ai, tile, player)[0], warfare_value(ai, other, player)[0])

    def test_strategy_scoring_only_evaluates_technology_for_technology_card(self):
        ai, player, _ = self.game()
        with patch('game_ai.technology_values', wraps=technology_values) as evaluate:
            for card in (1, 2, 3, 4, 5, 6, 8):
                ai.strategy_value(player, card)
            self.assertEqual(evaluate.call_count, 0)
            ai.strategy_value(player, 7)
            self.assertEqual(evaluate.call_count, 1)

    def test_rear_dock_supplies_frontier_beyond_owned_neighboring_systems(self):
        ai, player, tile = self.game()
        tile.units[:] = [u for u in tile.units if u.kind == 'spacedock']
        for neighbor in ai.window.movement.neighbors(tile):
            for planet in neighbor.planets:
                neighbor.planet_owners[planet.planet_id] = player.faction
        player.trade_goods = 12
        session = self.production(ai, player, tile)
        plan = build_plan(ai.window.movement, session, 4)
        self.assertGreater(plan['carrier'], 0)
        self.assertGreaterEqual(plan['infantry'], 2)


if __name__ == '__main__':
    unittest.main()
