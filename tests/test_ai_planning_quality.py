"""Decision-level regressions for economical invasions and credible threats."""
import unittest
from types import SimpleNamespace

from ai_combat import _spec, _take_hits, win_probability
from ai_planner import best_activation, plan_landings
from board import Board, TILES
from movement import MovementController
from player import PlayerState
from turn_order import TurnOrder
from units import Region, Unit, UnitLocation


class TacticalPlanningQualityTests(unittest.TestCase):
    def setUp(self):
        self.player = PlayerState('sol', 'Sol', 'blu', 4)
        self.enemy = PlayerState('hacan', 'Hacan', 'ylw', 6)
        self.players = [self.player, self.enemy]
        self.home = TILES.create(1, (0, 0))
        for planet in self.home.planets:
            self.home.planet_owners[planet.planet_id] = 'sol'
        self.target = TILES.create(19, (1, 0))
        self.board = Board([self.home, self.target])
        self.turn = TurnOrder(self.players)
        self.movement = MovementController(self.board, self.players)
        self.ai = SimpleNamespace(window=SimpleNamespace(
            board=self.board, turn_order=self.turn, movement=self.movement),
            human_faction=lambda: 'hacan')

    def unit(self, tile, kind, owner='sol', planet=None, **kwargs):
        player = next(player for player in self.players if player.faction == owner)
        location = UnitLocation(Region.PLANET, planet) if planet else UnitLocation(Region.SPACE)
        unit = Unit(f'{owner}-{kind}-{len(tile.units)}', kind, owner,
                    player.color_code, location, **kwargs)
        tile.units.append(unit)
        return unit

    def infantry(self, tile, count, owner='sol', planet=None):
        return [self.unit(tile, 'infantry', owner, planet or tile.planets[0].planet_id)
                for _ in range(count)]

    def test_free_planet_uses_one_carrier_and_one_infantry(self):
        carriers = [self.unit(self.home, 'carrier') for _ in range(2)]
        infantry = self.infantry(self.home, 6)
        plan = best_activation(self.ai, self.player)
        self.assertEqual(plan.target, self.target.position)
        self.assertEqual(len(set(plan.ships) & {unit.unit_id for unit in carriers}), 1)
        self.assertEqual(len(set(plan.passengers) & {unit.unit_id for unit in infantry}), 1)
        # Evaluate again: planning must neither spend tokens nor mutate pieces.
        self.assertEqual(plan, best_activation(self.ai, self.player))
        self.assertEqual(self.player.command_pools['tactical'], 3)
        self.assertEqual(len(self.home.units), 8)

    def test_landing_can_capture_two_defended_planets(self):
        target = TILES.create(30, (1, 0))
        for planet in target.planets:
            target.planet_owners[planet.planet_id] = 'hacan'
            self.infantry(target, 1, 'hacan', planet.planet_id)
        ground = self.infantry(self.home, 8)
        assignments = plan_landings(self.ai, target, self.player, ground)
        self.assertEqual(set(assignments.values()), {planet.planet_id for planet in target.planets})
        self.assertLessEqual(len(assignments), 8)
        for planet in target.planets:
            attackers = [unit for unit in ground if assignments.get(unit.unit_id) == planet.planet_id]
            defenders = [unit for unit in target.units if unit.location.planet_id == planet.planet_id]
            self.assertGreaterEqual(win_probability(attackers, defenders, self.players, space=False), .8)

    def test_mech_takes_defended_planet_while_infantry_claims_empty_one(self):
        target = TILES.create(30, (1, 0))
        defended, empty = target.planets
        target.planet_owners[defended.planet_id] = self.enemy.faction
        self.infantry(target, 1, self.enemy.faction, defended.planet_id)
        infantry = self.infantry(self.home, 1)[0]
        mech = self.unit(self.home, 'mech', planet=self.home.planets[0].planet_id)
        assignments = plan_landings(self.ai, target, self.player, [infantry, mech])
        self.assertEqual(assignments[mech.unit_id], defended.planet_id)
        self.assertEqual(assignments[infantry.unit_id], empty.planet_id)

    def test_codex_magen_ground_hit_is_not_first_round_suppression(self):
        self.enemy.technologies |= {'md'}
        attacker = self.infantry(self.home, 1)[0]
        self.assertEqual(win_probability([attacker], [], self.players,
                                         space=False), 1)
        self.assertEqual(win_probability([attacker], [], self.players,
                                         space=False, defender_faction='hacan',
                                         magen_structures=True), 0)

    def test_home_is_not_abandoned_to_a_legal_neighboring_invasion(self):
        self.unit(self.home, 'carrier')
        self.unit(self.home, 'fighter')
        self.unit(self.home, 'fighter')
        self.infantry(self.home, 3)
        enemy_origin = TILES.create(20, (-1, 0))
        self.board.add(enemy_origin)
        for planet in enemy_origin.planets:
            enemy_origin.planet_owners[planet.planet_id] = 'hacan'
        self.unit(enemy_origin, 'carrier', 'hacan')
        self.unit(enemy_origin, 'fighter', 'hacan')
        self.infantry(enemy_origin, 4, 'hacan')
        threatened = best_activation(self.ai, self.player)
        self.assertTrue(threatened is None or threatened.target != self.target.position)
        # Already-activated enemy fleets cannot make that counterattack.
        enemy_origin.command_tokens.add('hacan')
        safe = best_activation(self.ai, self.player)
        self.assertIsNotNone(safe)
        self.assertEqual(safe.target, self.target.position)

    def test_fleet_can_break_blockade_without_carrying_infantry(self):
        self.target.planet_owners[self.target.planets[0].planet_id] = 'sol'
        for _ in range(2):
            self.unit(self.home, 'dreadnought')
        self.unit(self.target, 'cruiser', 'hacan')
        plan = best_activation(self.ai, self.player)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.target, self.target.position)
        self.assertFalse(plan.passengers)
        self.assertGreaterEqual(plan.odds[0], .8)

    def test_bot_attacks_a_credible_but_not_overwhelming_human_fleet(self):
        self.target.planet_owners[self.target.planets[0].planet_id] = 'hacan'
        self.unit(self.home, 'carrier')
        self.unit(self.home, 'cruiser')
        self.infantry(self.home, 2)
        self.unit(self.target, 'cruiser', 'hacan')
        plan = best_activation(self.ai, self.player)
        self.assertIsNotNone(plan)
        self.assertEqual(self.target.position, plan.target)
        self.assertGreaterEqual(plan.odds[0], .72)
        self.assertLess(plan.odds[0], .8)

    def test_bot_still_rejects_a_low_probability_invasion(self):
        self.target.planet_owners[self.target.planets[0].planet_id] = 'hacan'
        self.unit(self.home, 'carrier')
        self.infantry(self.home, 2)
        self.unit(self.target, 'cruiser', 'hacan')
        self.assertIsNone(best_activation(self.ai, self.player))

    def test_stages_towards_real_objective_when_it_is_out_of_range(self):
        self.target.planet_owners[self.target.planets[0].planet_id] = 'sol'
        goal = TILES.create(30, (2, 0))
        self.board.add(goal)
        self.unit(self.home, 'carrier')
        self.infantry(self.home, 4)
        plan = best_activation(self.ai, self.player)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.target, self.target.position)
        self.assertEqual(plan.source, self.home.position)
        self.assertTrue(plan.passengers)

    def test_assault_cannon_forecast_requires_a_surviving_loaded_carrier(self):
        carrier = self.unit(self.home, 'carrier')
        attackers = [carrier] + [self.unit(self.home, 'fighter') for _ in range(8)]
        defenders = [self.unit(self.target, 'destroyer', 'hacan') for _ in range(3)]
        self.enemy.technologies = frozenset({'asc'})
        chance = win_probability(attackers, defenders, self.players,
                                 require_transport=True, cargo_carriers={carrier.unit_id})
        self.assertEqual(chance, 0)

    def test_saar_factory_can_be_the_transport_for_peaceful_expansion(self):
        self.player.faction = 'saar'
        self.home.planet_owners = {planet.planet_id: 'saar' for planet in self.home.planets}
        factory = self.unit(self.home, 'spacedock', 'saar')
        troops = self.infantry(self.home, 3, 'saar')
        plan = best_activation(self.ai, self.player)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.target, self.target.position)
        self.assertIn(factory.unit_id, plan.ships)
        self.assertTrue(set(plan.passengers) & {unit.unit_id for unit in troops})

    def test_l4_disruptors_are_included_in_ground_assault_forecast(self):
        ground = self.infantry(self.home, 1)
        cannon = self.unit(self.target, 'pds', 'hacan', self.target.planets[0].planet_id)
        self.assertLess(win_probability(ground, [], self.players, space=False, cannons=[cannon]), .8)
        self.player.technologies = frozenset({'l4'})
        self.assertEqual(win_probability(ground, [], self.players, space=False, cannons=[cannon]), 1)

    def test_duranium_repairs_units_already_damaged_before_the_round(self):
        fleet = [self.unit(self.home, 'dreadnought', damaged=True) for _ in range(2)]
        enemy = [self.unit(self.target, 'dreadnought', 'hacan')]
        without_repair = win_probability(fleet, enemy, self.players)
        self.player.technologies = frozenset({'da'})
        with_repair = win_probability(fleet, enemy, self.players)
        self.assertGreater(with_repair, without_repair)

    def test_non_euclidean_shielding_does_not_add_permanent_hit_point(self):
        dreadnought = self.unit(self.home, 'dreadnought')
        fleet = [list(_spec(dreadnought, {'nes'}))]
        _take_hits(fleet, 1)
        self.assertEqual(len(fleet), 1)
        _take_hits(fleet, 1)  # A separate event must destroy the damaged ship.
        self.assertFalse(fleet)
        fleet = [list(_spec(dreadnought, {'nes'}))]
        _take_hits(fleet, 2)  # Two hits in one event are canceled by sustain.
        self.assertEqual(len(fleet), 1)


if __name__ == '__main__':
    unittest.main()
