from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from game_ai import GameAI, TECH_PRIORITY, payment_plan, resource_opportunity
from ai_combat import win_probability
from player import PlayerState
from turn_order import TurnOrder
from board import load_board
from player import create_players
from movement import MovementController, Session, Snapshot
from units import Region, Unit, UnitLocation
from technology import technology_catalog


@dataclass
class Card:
    resources: int
    influence: int
    planet_id: str
    exhausted: bool = False

    @property
    def planet(self):
        return self


class GameAITests(unittest.TestCase):
    def test_technology_priorities_reference_real_base_cards(self):
        self.assertFalse(set(TECH_PRIORITY) - set(technology_catalog()))
        self.assertTrue({'dd2', 'cr2', 'inf2', 'ac2', 'so2'} <= set(TECH_PRIORITY))

    def test_technology_can_use_three_specialty_planets_for_prerequisites(self):
        player = PlayerState('sol', 'Sol', 'blue', 4)
        player.planets = [SimpleNamespace(
            planet=SimpleNamespace(planet_id=f'red-{index}', resources=0,
                                   influence=0, tech_specialties=('WARFARE',)),
            exhausted=False) for index in range(3)]
        player.trade_goods = 4
        window = SimpleNamespace(board={}, turn_order=SimpleNamespace(round_number=2))
        ai = GameAI(window, {'sol'})
        with patch('game_ai.available_technologies',
                   return_value=[technology_catalog()['asc']]):
            choice = ai.choose_technology(player, 4)
        self.assertIsNotNone(choice)
        self.assertEqual(choice[0], 'asc')
        self.assertEqual(len(choice[1]), 3)

    def test_payment_uses_exact_planets_and_only_needed_goods(self):
        player = SimpleNamespace(planets=[Card(4, 0, 'four'), Card(3, 0, 'three'),
                                          Card(1, 0, 'one')], trade_goods=2)
        self.assertEqual(payment_plan(player, 5, lambda c: c.resources),
                         (('four', 'one'), 0))
        player.planets[-1].exhausted = True
        self.assertEqual(payment_plan(player, 5, lambda c: c.resources),
                         (('four',), 1))
        player.trade_goods = 0
        self.assertEqual(payment_plan(player, 5, lambda c: c.resources),
                         (('four', 'three'), 0))

    def test_resource_payment_preserves_influence_when_costs_are_equal(self):
        player = SimpleNamespace(planets=[Card(4, 4, 'cultural'),
                                          Card(2, 0, 'industrial-a'),
                                          Card(2, 0, 'industrial-b')], trade_goods=0)
        planets, goods = payment_plan(player, 4, lambda c: c.planet.resources,
                                      resource_opportunity)
        self.assertEqual(set(planets), {'industrial-a', 'industrial-b'})
        self.assertEqual(goods, 0)

    def test_resource_payment_uses_good_instead_of_exhausting_high_influence_planet(self):
        player = SimpleNamespace(planets=[Card(1, 6, 'mecatol')], trade_goods=1)
        self.assertEqual(payment_plan(player, 1, lambda c: c.planet.resources,
                                      resource_opportunity), ((), 1))

    def test_strategy_draft_adapts_to_command_pool_and_faction(self):
        players = [PlayerState('sol', 'Sol', 'blue', 4),
                   PlayerState('jolnar', 'Jol-Nar', 'cyan', 4),
                   PlayerState('hacan', 'Hacan', 'yellow', 6)]
        turns = TurnOrder(players, strategy_enabled=True)
        window = SimpleNamespace(turn_order=turns, board={})
        ai = GameAI(window, {'sol', 'jolnar', 'hacan'})
        players[0].command_pools['tactical'] = 0
        self.assertEqual(ai.choose_strategy_card(players[0]), 1)
        turns.choose_strategy_card(1)
        self.assertEqual(ai.choose_strategy_card(players[1]), 7)
        turns.choose_strategy_card(7)
        self.assertEqual(ai.choose_strategy_card(players[2]), 5)

    def test_frontline_target_prefers_human_over_other_bot(self):
        config, board = load_board(Path(__file__).resolve().parents[1] / 'maps/three_player.json')
        players = create_players(board, config)
        sol = next(player for player in players if player.faction == 'sol')
        home = next(tile for tile in board.values() if any(
            unit.owner == 'sol' and unit.kind == 'carrier' for unit in tile.units))
        human_tile = next(tile for tile in board.neighbors(home.position) if tile.name == 'Tequran/Torkan')
        bot_tile = next(tile for tile in board.neighbors(home.position) if tile.name == 'Mellon/Zohbat')
        for planet in human_tile.planets:
            human_tile.planet_owners[planet.planet_id] = 'hacan'
        for planet in bot_tile.planets:
            bot_tile.planet_owners[planet.planet_id] = 'jolnar'
        turn = TurnOrder(players)
        window = SimpleNamespace(turn_order=turn, board=board,
                                 movement=MovementController(board, players))
        ai = GameAI(window, {'sol', 'jolnar'})
        self.assertEqual(ai.best_activation(sol)[1], human_tile.position)

    def test_first_bot_action_expands_instead_of_locking_home_dock(self):
        config, board = load_board(Path(__file__).resolve().parents[1] / 'maps/three_player.json')
        players = create_players(board, config)
        turn = TurnOrder(players, strategy_enabled=True)
        turn.strategy_selection = False
        turn.strategy_assignments['sol'] = [4, 7]
        movement = MovementController(board, players)
        window = SimpleNamespace(turn_order=turn, board=board, movement=movement,
                                 strategy=SimpleNamespace(session=None, start=Mock()),
                                 action_cards=SimpleNamespace(pending=None),
                                 transaction=SimpleNamespace(session=None))
        ai = GameAI(window, {'sol'})
        ai.start_activation = Mock()
        ai.step()
        ai.start_activation.assert_called_once()
        selected = ai.start_activation.call_args.args[1]
        self.assertNotEqual(selected[1], selected[2])
        self.assertFalse(window.strategy.start.called)

    def test_fleet_logistics_bot_uses_its_second_action(self):
        player = PlayerState('sol', 'Sol', 'blue', 4)
        player.technologies = frozenset({'fl'})
        turn = TurnOrder([player])
        turn.mark_action_completed()
        self.assertTrue(turn.can_take_action)
        window = SimpleNamespace(
            turn_order=turn, board={}, movement=SimpleNamespace(session=None),
            strategy=SimpleNamespace(session=None),
            action_cards=SimpleNamespace(pending=None),
            transaction=SimpleNamespace(session=None), pass_turn=Mock())
        ai = GameAI(window, {'sol'})
        ai.best_activation = Mock(return_value=(18, (0, 0), (0, 0)))
        ai.start_activation = Mock()

        ai.step()

        ai.start_activation.assert_called_once()
        window.pass_turn.assert_not_called()

    def test_combat_odds_refuse_suicidal_attack_and_allow_superior_fleet(self):
        config, board = load_board(Path(__file__).resolve().parents[1] / 'maps/three_player.json')
        players = create_players(board, config)
        sol = next(player for player in players if player.faction == 'sol')
        hacan = next(player for player in players if player.faction == 'hacan')
        home = next(tile for tile in board.values() if any(
            unit.owner == 'sol' and unit.kind == 'carrier' for unit in tile.units))
        target = next(tile for tile in board.neighbors(home.position)
                      if tile.name == 'Tequran/Torkan')
        for planet in target.planets:
            target.planet_owners[planet.planet_id] = 'hacan'
        for tile in board.values():
            if tile not in (home, target):
                tile.command_tokens.add('sol')
        turn = TurnOrder(players)
        movement = MovementController(board, players)
        ai = GameAI(SimpleNamespace(turn_order=turn, board=board, movement=movement), {'sol'})
        for index in range(2):
            target.units.append(Unit(f'guard-{index}', 'dreadnought', 'hacan',
                                     hacan.color_code, UnitLocation(Region.SPACE)))
        self.assertNotEqual((ai.best_activation(sol) or (None, None))[1], target.position)
        self.assertLess(win_probability([unit for unit in home.units if unit.owner == 'sol' and
                                         unit.location.region == Region.SPACE],
                                        target.units, players), .80)
        target.units[:] = [Unit('guard-cruiser', 'cruiser', 'hacan', hacan.color_code,
                                UnitLocation(Region.SPACE))]
        for index in range(2):
            home.units.append(Unit(f'escort-{index}', 'dreadnought', 'sol',
                                   sol.color_code, UnitLocation(Region.SPACE)))
        self.assertGreaterEqual(win_probability([unit for unit in home.units if
                                                 unit.owner == 'sol' and
                                                 unit.location.region == Region.SPACE],
                                                target.units, players), .80)
        self.assertEqual(ai.best_activation(sol)[1], target.position)

    def test_invasion_odds_require_a_surviving_transport_and_count_pds(self):
        config, board = load_board(Path(__file__).resolve().parents[1] / 'maps/three_player.json')
        players = create_players(board, config)
        sol = next(player for player in players if player.faction == 'sol')
        hacan = next(player for player in players if player.faction == 'hacan')
        carrier = Unit('preview-carrier', 'carrier', 'sol', sol.color_code,
                       UnitLocation(Region.SPACE))
        cruiser = Unit('preview-cruiser', 'cruiser', 'sol', sol.color_code,
                       UnitLocation(Region.SPACE))
        pds = Unit('preview-pds', 'pds', 'hacan', hacan.color_code,
                   UnitLocation(Region.PLANET, planet_id='test-planet'))
        self.assertEqual(win_probability([cruiser], [], players,
                                         require_transport=True), 0)
        self.assertEqual(win_probability([carrier], [], players,
                                         require_transport=True), 1)
        self.assertLess(win_probability([carrier], [], players, cannons=[pds],
                                        require_transport=True), 1)

    def test_invasion_odds_track_the_carrier_with_ground_forces(self):
        config, board = load_board(Path(__file__).resolve().parents[1] / 'maps/three_player.json')
        players = create_players(board, config)
        sol = next(player for player in players if player.faction == 'sol')
        hacan = next(player for player in players if player.faction == 'hacan')
        loaded = Unit('loaded-carrier', 'carrier', sol.faction, sol.color_code,
                      UnitLocation(Region.SPACE))
        empty = Unit('empty-carrier', 'carrier', sol.faction, sol.color_code,
                     UnitLocation(Region.SPACE))
        pds = Unit('cannon', 'pds', hacan.faction, hacan.color_code,
                   UnitLocation(Region.PLANET, 'test-planet'))
        self.assertEqual(win_probability([loaded, empty], [], players,
                                         cannons=[pds], require_transport=True), 1)
        chance = win_probability([loaded, empty], [], players, cannons=[pds],
                                 require_transport=True,
                                 cargo_carriers={loaded.unit_id})
        self.assertGreater(chance, 0)
        self.assertLess(chance, 1)

    def test_bot_assignment_sustains_ship_before_losing_fighter(self):
        # Combat's existing assigner decides how damage is applied; the bot
        # should choose a free sustain hit before removing a plastic unit.
        class FakeUnit:
            def __init__(self, kind, damaged=False):
                self.kind, self.damaged, self.unit_id = kind, damaged, kind

        fighter, dreadnought = FakeUnit('fighter'), FakeUnit('dreadnought')
        session = SimpleNamespace(combat_hits={'sol': 1}, combat_assignments={'sol': []})
        chosen = []
        movement = SimpleNamespace(
            combat_hit_capacity=lambda s, f: 3,
            combat_units=lambda s, f: [fighter, dreadnought],
            combat_assignment_target=lambda s, f, k: fighter if k == 'fighter' else dreadnought,
            assign_combat_hit=lambda f, k: chosen.append(k),
        )
        window = SimpleNamespace(movement=movement)
        ai = GameAI(window, {'sol'})
        # The real profile reader needs a genuine Unit, so patch at the module
        # boundary while keeping the hit-choice logic under test.
        from unittest.mock import patch
        with patch('game_ai.unit_profile', side_effect=lambda u: {
                'sustainDamage': u.kind == 'dreadnought'}):
            self.assertTrue(ai._bot_hit('sol', session))
        self.assertEqual(chosen, ['dreadnought'])

    def test_bot_assigns_space_cannon_hit_to_fighter_before_loaded_carrier(self):
        config, board = load_board(Path(__file__).resolve().parents[1] / 'maps/three_player.json')
        players = create_players(board, config)
        sol = next(player for player in players if player.faction == 'sol')
        target = next(tile for tile in board.values() if tile.planets)
        carrier = Unit('ai-cannon-loaded-carrier', 'carrier', sol.faction, sol.color_code,
                       UnitLocation(Region.SPACE))
        fighter = Unit('ai-cannon-fighter', 'fighter', sol.faction, sol.color_code,
                       UnitLocation(Region.SPACE))
        infantry = Unit('ai-cannon-infantry', 'infantry', sol.faction, sol.color_code,
                        UnitLocation(Region.TRANSPORT, carrier_id=carrier.unit_id))
        target.units.extend((carrier, fighter, infantry))
        movement = MovementController(board, players)
        session = Session(sol, target, {}, Snapshot.capture(board, sol, players),
                          stage='space_cannon_assign', space_cannon_next_stage='invasion',
                          space_cannon_events=[{'owner': 'hacan',
                                                'candidates': [carrier.unit_id, fighter.unit_id],
                                                'graviton': False}])
        movement.session = session
        ai = GameAI(SimpleNamespace(movement=movement), {sol.faction})
        ai.play_combat_card = Mock(return_value=False)
        ai.report = Mock()

        ai.resolve_movement()

        self.assertNotIn(fighter, target.units)
        self.assertIn(carrier, target.units)
        self.assertIn(infantry, target.units)

    def test_two_bots_resolve_space_combat_without_manual_clicks(self):
        from unittest.mock import patch
        config, board = load_board(Path(__file__).resolve().parents[1] / 'maps/three_player.json')
        players = create_players(board, config)
        sol = next(player for player in players if player.faction == 'sol')
        hacan = next(player for player in players if player.faction == 'hacan')
        origin = next(tile for tile in board.values() if any(
            unit.owner == 'sol' and unit.kind == 'carrier' for unit in tile.units))
        target = next(tile for tile in board.neighbors(origin.position)
                      if not tile.anomalies and tile.planets)
        target.units.append(Unit('ai-test-enemy-cruiser', 'cruiser', hacan.faction,
                                 hacan.color_code, UnitLocation(Region.SPACE)))
        movement = MovementController(board, players)
        session = movement.activate(sol, target.position)
        source = session.sources[origin.position]
        for ship in source.ships:
            session.toggle(ship.unit_id)
        movement.confirm()
        self.assertEqual(session.stage, 'space_combat')
        window = SimpleNamespace(movement=movement, board=board,
                                 action_cards=SimpleNamespace(participants=lambda s: []))
        ai = GameAI(window, {'sol', 'hacan'})
        with patch('movement.random.randint', return_value=10):
            for _ in range(30):
                if movement.session is None or movement.session.stage not in (
                        'space_combat', 'combat_end', 'space_combat_won', 'assault_choice',
                        'retreat_selection'):
                    break
                ai.resolve_movement()
            else:
                self.fail('Bot combat remained unresolved')
        self.assertFalse(any(unit.unit_id == 'ai-test-enemy-cruiser' for unit in target.units))

    def test_bot_defender_resolves_assault_cannon_during_human_attack(self):
        from movement import Session, Snapshot
        config, board = load_board(Path(__file__).resolve().parents[1] / 'maps/three_player.json')
        players = create_players(board, config)
        sol = next(player for player in players if player.faction == 'sol')
        hacan = next(player for player in players if player.faction == 'hacan')
        hacan.technologies |= {'asc'}
        target = next(tile for tile in board.values() if not tile.units)
        target.units.append(Unit('assault-test-sol-carrier', 'carrier', 'sol', sol.color_code,
                                 UnitLocation(Region.SPACE)))
        target.units.extend(Unit(f'assault-test-hacan-{index}', kind, 'hacan', hacan.color_code,
                                 UnitLocation(Region.SPACE))
                            for index, kind in enumerate(('carrier', 'cruiser', 'destroyer')))
        movement = MovementController(board, players)
        session = Session(sol, target, {}, Snapshot.capture(board, sol, players))
        movement.session = session
        movement.start_combat(session)
        self.assertEqual(session.stage, 'assault_choice')
        self.assertEqual(session.assault_queue, ['hacan'])

        cards = SimpleNamespace(participants=lambda _session: [], playable=lambda *_args: [])
        window = SimpleNamespace(movement=movement, board=board, action_cards=cards,
                                 turn_order=TurnOrder(players))
        ai = GameAI(window, {'hacan'})
        ai.resolve_movement()

        self.assertNotIn('assault-test-sol-carrier', {unit.unit_id for unit in target.units})
        self.assertNotEqual(session.stage, 'assault_choice')
        self.assertTrue(any('Assault Cannon destroyed' in line for line in session.assault_log))

    def test_bot_production_buys_transport_escort_and_ground_forces(self):
        config, board = load_board(Path(__file__).resolve().parents[1] / 'maps/three_player.json')
        players = create_players(board, config)
        sol = next(player for player in players if player.faction == 'sol')
        tile = next(tile for tile in board.values() if any(
            unit.owner == 'sol' and unit.kind == 'spacedock' for unit in tile.units))
        dock = next(unit for unit in tile.units if unit.owner == 'sol' and unit.kind == 'spacedock')
        tile.units[:] = [dock]
        sol.trade_goods = 8
        movement = MovementController(board, players)
        movement.start_strategy_production(sol, tile, dock)
        turn = TurnOrder(players)
        window = SimpleNamespace(movement=movement, turn_order=turn)
        ai = GameAI(window, {'sol'})
        ai.resolve_production()
        produced = [unit.kind for unit in tile.units if unit is not dock]
        self.assertIn('carrier', produced)
        self.assertTrue(any(kind in produced for kind in ('cruiser', 'destroyer', 'dreadnought')))
        self.assertIn('infantry', produced)
        self.assertLess(sol.trade_goods, 8)


if __name__ == '__main__':
    unittest.main()
