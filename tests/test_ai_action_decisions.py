"""Bot card timing, damage priorities, bombardment and strategic targets."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from action_cards import ActionCardController
from ai_actions import plan_bombardment, resolve_card_choice, war_effort_plan
from ai_strategy import construction_choice, diplomacy_value, warfare_value
from board import ROOT, load_board
from game_ai import GameAI
from leaders import leader_available
from movement import MovementController, Session, Snapshot
from player import create_players
from strategic_action import StrategyController
from turn_order import TurnOrder
from units import Region, Unit, UnitLocation


class BotActionDecisions(TestCase):
    def setUp(self):
        config, self.board = load_board()
        self.players = create_players(self.board, config)
        self.sol = next(p for p in self.players if p.faction == 'sol')
        self.hacan = next(p for p in self.players if p.faction == 'hacan')
        self.turn = TurnOrder(self.players)
        self.movement = MovementController(self.board, self.players)
        self.cards = ActionCardController(self.players, self.movement, turn=self.turn)
        self.window = SimpleNamespace(board=self.board, movement=self.movement,
                                      turn_order=self.turn, action_cards=self.cards)
        self.ai = GameAI(self.window, {'sol'})
        self.target = self.board[(-3, 2)]
        self.target.units.clear()

    def unit(self, kind, player, name, location=None, damaged=False):
        unit = Unit(name, kind, player.faction, player.color_code,
                    location or UnitLocation(Region.SPACE), damaged=damaged)
        self.target.units.append(unit)
        return unit

    def session(self, stage='space_combat', **kwargs):
        session = Session(self.sol, self.target, {},
                          Snapshot.capture(self.board, self.sol, self.players),
                          stage=stage, combat_factions=('sol', 'hacan'), **kwargs)
        self.movement.session = session
        return session

    def test_direct_hit_bot_finishes_target_prompt_during_human_turn(self):
        own = self.unit('destroyer', self.sol, 'own')
        enemy = self.unit('dreadnought', self.hacan, 'enemy', damaged=True)
        session = self.session('combat_end')
        session.player = self.hacan
        session.pending_direct_hits = {'sol': [enemy.unit_id]}
        self.sol.action_cards[:] = ['dh1']
        self.assertTrue(self.ai.play_combat_card(session))
        self.assertNotIn(enemy, self.target.units)
        self.assertIn(own, self.target.units)
        self.assertIsNone(self.cards.pending)
        self.assertEqual(self.sol.action_cards, [])

    def test_human_card_target_is_never_resolved_by_bot(self):
        self.unit('dreadnought', self.sol, 'damaged-bot', damaged=True)
        session = self.session('combat_end')
        session.pending_direct_hits = {'hacan': ['damaged-bot']}
        self.hacan.action_cards[:] = ['dh1']
        self.cards.play(self.hacan, 0)
        self.assertFalse(resolve_card_choice(self.ai))
        self.assertIsNotNone(self.cards.pending)
        self.assertEqual(len(self.target.units), 1)

    def test_bot_titans_use_tellurian_before_assigning_incoming_combat_hit(self):
        config, board = load_board(ROOT / 'maps/four_player.json',
                                   ('titans', 'sol', 'jolnar', 'hacan'))
        players = create_players(board, config)
        by_faction = {player.faction: player for player in players}
        titans, sol = by_faction['titans'], by_faction['sol']
        movement = MovementController(board, players)
        target = next(iter(board.values()))
        target.units[:] = [
            Unit(f'ai-tellurian-dread-{index}', 'dreadnought', 'titans', titans.color_code,
                 UnitLocation(Region.SPACE)) for index in range(2)]
        session = Session(titans, target, {}, Snapshot.capture(board, titans, players),
                          stage='space_combat', combat_factions=('titans', 'sol'),
                          combat_hits={'titans': 1, 'sol': 0},
                          combat_assignments={'titans': [], 'sol': []},
                          combat_needs_resolution=True)
        movement.session = session
        turn = TurnOrder(players)
        cards = ActionCardController(players, movement, turn=turn)
        window = SimpleNamespace(board=board, movement=movement, turn_order=turn,
                                 action_cards=cards)
        ai = GameAI(window, {'titans'})

        ai.resolve_movement()

        self.assertEqual(session.combat_hits['titans'], 0)
        self.assertTrue(movement.combat_assignments_complete(session))
        self.assertFalse(leader_available(titans, 'titansagent'))

    def test_bot_nomad_uses_thundarian_only_after_an_unfavorable_combat_roll(self):
        config, board = load_board(ROOT / 'maps/four_player.json',
                                   ('nomad', 'sol', 'jolnar', 'hacan'))
        players = create_players(board, config)
        by_faction = {player.faction: player for player in players}
        nomad, sol = by_faction['nomad'], by_faction['sol']
        movement = MovementController(board, players)
        target = next(iter(board.values()))
        target.units[:] = [
            Unit('ai-thundarian-nomad', 'carrier', 'nomad', nomad.color_code,
                 UnitLocation(Region.SPACE)),
            Unit('ai-thundarian-sol', 'carrier', 'sol', sol.color_code,
                 UnitLocation(Region.SPACE)),
        ]
        session = Session(nomad, target, {}, Snapshot.capture(board, nomad, players),
                          stage='space_combat', combat_factions=('nomad', 'sol'),
                          combat_rolls={
                              'nomad': [{'unit_id': 'ai-thundarian-nomad', 'kind': 'carrier',
                                         'threshold': 9, 'modifier': 0, 'hit': False}],
                              'sol': [{'unit_id': 'ai-thundarian-sol', 'kind': 'carrier',
                                       'threshold': 9, 'modifier': 0, 'hit': True}],
                          },
                          combat_hits={'nomad': 1, 'sol': 0},
                          combat_assignments={'nomad': [], 'sol': []},
                          combat_needs_resolution=True, thundarian_window_open=True)
        movement.session = session
        turn = TurnOrder(players)
        cards = ActionCardController(players, movement, turn=turn)
        window = SimpleNamespace(board=board, movement=movement, turn_order=turn,
                                 action_cards=cards)
        ai = GameAI(window, {'nomad', 'sol', 'jolnar', 'hacan'})

        ai.resolve_movement()

        self.assertTrue(session.repeat_combat_rolls)
        self.assertFalse(session.thundarian_window_open)
        self.assertFalse(leader_available(nomad, 'nomadagentthundarian'))

    def test_fire_team_rerolls_misses_without_touching_successful_dice(self):
        planet = self.target.planets[0].planet_id
        units = [self.unit('infantry', self.sol, f'infantry-{i}',
                           UnitLocation(Region.PLANET, planet)) for i in range(2)]
        self.unit('infantry', self.hacan, 'defender', UnitLocation(Region.PLANET, planet))
        session = self.session('ground_combat', combat_type='ground',
                               combat_planet_id=planet, combat_needs_resolution=True,
                               combat_rolls={'sol': [dict(unit_id=u.unit_id, kind='infantry',
                                   value=v, natural=v, modifier=0, hit=(v == 10), rerolled=False)
                                   for u, v in zip(units, (1, 10))], 'hacan': []})
        self.sol.action_cards[:] = ['fire_team']
        self.assertTrue(self.ai.play_combat_card(session))
        with patch('movement.random.randint', return_value=10) as roll:
            self.assertTrue(self.ai.play_combat_card(session))
        self.assertEqual(roll.call_count, 1)
        self.assertEqual(session.combat_hits['hacan'], 2)
        self.assertIsNone(session.fire_team_pending)

    def test_saves_morale_boost_when_battle_is_already_overwhelming(self):
        for i in range(3):
            self.unit('dreadnought', self.sol, f'dread-{i}')
        self.unit('carrier', self.hacan, 'carrier')
        session = self.session()
        self.sol.action_cards[:] = ['morale_boost']
        self.assertFalse(self.ai.play_combat_card(session))
        self.assertEqual(self.sol.action_cards, ['morale_boost'])

    def test_waits_for_human_fire_team_even_without_incoming_hits(self):
        planet = self.target.planets[0].planet_id
        for player in (self.sol, self.hacan):
            self.unit('infantry', player, player.faction, UnitLocation(Region.PLANET, planet))
        session = self.session('ground_combat', combat_type='ground',
                               combat_planet_id=planet, combat_needs_resolution=True,
                               combat_hits={'sol': 0, 'hacan': 0},
                               fire_team_pending='hacan')
        self.ai.resolve_movement()
        self.assertEqual(session.fire_team_pending, 'hacan')
        self.assertTrue(session.combat_needs_resolution)
        self.assertEqual(session.stage, 'ground_combat')

    def test_damage_preserves_loaded_carrier_over_empty_cruiser(self):
        carrier = self.unit('carrier', self.sol, 'loaded')
        self.unit('infantry', self.sol, 'cargo',
                  UnitLocation(Region.TRANSPORT, carrier_id=carrier.unit_id))
        cruiser = self.unit('cruiser', self.sol, 'empty-cruiser')
        session = self.session(combat_needs_resolution=True,
                               combat_hits={'sol': 1}, combat_assignments={'sol': []})
        self.ai._bot_hit('sol', session)
        self.assertEqual(session.combat_assignments['sol'], [cruiser.unit_id])

    def test_damage_selects_empty_carrier_even_when_loaded_one_sorts_first(self):
        carrier = self.unit('carrier', self.sol, 'a-loaded')
        self.unit('infantry', self.sol, 'cargo',
                  UnitLocation(Region.TRANSPORT, carrier_id=carrier.unit_id))
        empty = self.unit('carrier', self.sol, 'z-empty')
        session = self.session(combat_needs_resolution=True,
                               combat_hits={'sol': 1}, combat_assignments={'sol': []})
        self.ai._bot_hit('sol', session)
        self.assertEqual(session.combat_assignments['sol'], [empty.unit_id])
        with self.assertRaises(ValueError):
            self.movement.assign_combat_hit('sol', 'carrier', carrier.unit_id)

    def test_bombardment_spreads_fire_and_never_targets_empty_planet(self):
        for planet in self.target.planets:
            self.target.planet_owners[planet.planet_id] = self.hacan.faction
            self.unit('infantry', self.hacan, planet.planet_id,
                      UnitLocation(Region.PLANET, planet.planet_id))
        bombers = [self.unit('dreadnought', self.sol, f'bomber-{i}') for i in range(2)]
        session = self.session('bombardment', bombard_targets={u.unit_id: None for u in bombers})
        plan_bombardment(session)
        self.assertEqual(set(session.bombard_targets.values()),
                         {planet.planet_id for planet in self.target.planets})
        empty = self.target.planets[0].planet_id
        self.target.units[:] = [u for u in self.target.units if u.unit_id != empty]
        plan_bombardment(session)
        self.assertNotIn(empty, session.bombard_targets.values())

    def test_blitz_ships_without_profile_bombardment_are_planned_at_six_plus(self):
        planet = self.target.planets[0]
        self.target.planet_owners[planet.planet_id] = self.hacan.faction
        self.unit('infantry', self.hacan, 'blitz-defender',
                  UnitLocation(Region.PLANET, planet.planet_id))
        cruiser = self.unit('cruiser', self.sol, 'blitz-cruiser')
        session = self.session('bombardment', bombard_targets={cruiser.unit_id: None})
        session.blitz_factions = {'sol'}

        plan_bombardment(session)

        self.assertEqual(session.bombard_targets[cruiser.unit_id], planet.planet_id)

    def test_war_effort_respects_fleet_limit_and_uses_own_turn(self):
        self.sol.action_cards[:] = ['war_effort']
        for tile in self.board.values():
            tile.units[:] = [u for u in tile.units if u.owner != 'sol']
        self.unit('carrier', self.sol, 'carrier')
        self.sol.command_pools['fleet'] = 1
        self.assertIsNone(war_effort_plan(self.ai, self.sol))
        self.sol.command_pools['fleet'] = 2
        plan = war_effort_plan(self.ai, self.sol)
        self.assertEqual(plan[2], self.target.position)
        self.cards.play(self.sol, plan[1])
        resolve_card_choice(self.ai)
        self.assertEqual(sum(u.kind == 'cruiser' for u in self.target.units), 1)
        self.assertTrue(self.turn.action_used)

    def test_warfare_unlocks_a_useful_fleet_instead_of_an_empty_rich_system(self):
        rich = self.board[(-3, 3)]
        rich.units.clear()
        self.unit('carrier', self.sol, 'locked-carrier')
        self.unit('infantry', self.sol, 'troop',
                  UnitLocation(Region.PLANET, self.target.planets[0].planet_id))
        self.assertGreater(warfare_value(self.ai, self.target, self.sol),
                           warfare_value(self.ai, rich, self.sol))

    def test_construction_secondary_can_rebuild_lost_dock(self):
        for tile in self.board.values():
            tile.units[:] = [u for u in tile.units if u.owner != 'sol']
        ctl = StrategyController(self.board, self.turn, self.movement)
        session = SimpleNamespace(player=self.sol, stage='construction', primary=False,
                                  builds_left=1, structure='pds', selected_system=None)
        ctl.session = session
        choice = construction_choice(self.ai, ctl, session)
        self.assertEqual(choice[1], 'spacedock')

    def test_war_effort_preserves_card_when_cruiser_models_are_exhausted(self):
        self.sol.action_cards[:] = ['war_effort']
        self.sol.command_pools['fleet'] = 12
        for tile in self.board.values():
            tile.units[:] = [u for u in tile.units if u.owner != 'sol']
        for i in range(8):
            self.unit('cruiser', self.sol, f'cruiser-{i}')
        self.assertIsNone(war_effort_plan(self.ai, self.sol))
        self.cards.play(self.sol, 0)
        self.assertTrue(resolve_card_choice(self.ai))
        self.assertIsNone(self.cards.pending)
        self.assertEqual(self.sol.action_cards, ['war_effort'])
        self.assertEqual(len(self.target.units), 8)

    def test_upgrade_waits_for_an_available_dreadnought_model(self):
        self.sol.action_cards[:] = ['upgrade']
        for tile in self.board.values():
            tile.units[:] = [u for u in tile.units if u.owner != 'sol']
        self.unit('cruiser', self.sol, 'upgrade-target')
        for i in range(5):
            self.unit('dreadnought', self.sol, f'dread-{i}')
        session = self.session('movement')
        self.assertFalse(self.ai.play_combat_card(session))
        self.cards.play(self.sol, 0)
        self.assertTrue(resolve_card_choice(self.ai))
        self.assertIsNone(self.cards.pending)
        self.assertEqual(self.sol.action_cards, ['upgrade'])
        self.assertTrue(any(u.unit_id == 'upgrade-target' for u in self.target.units))

    def test_maneuvering_jets_does_not_cancel_own_offensive_pds_hit(self):
        self.sol.action_cards[:] = ['mjets1']
        event = {'target': self.hacan.faction, 'owner': self.sol.faction}
        session = self.session('space_cannon_response', space_cannon_events=[event])
        self.assertFalse(self.ai.play_combat_card(session))
        self.assertEqual(session.space_cannon_events, [event])
        self.assertEqual(self.sol.action_cards, ['mjets1'])
        session.space_cannon_events[0] = {'target': self.sol.faction, 'owner': self.hacan.faction}
        self.assertTrue(self.ai.play_combat_card(session))
        self.assertEqual(session.space_cannon_events, [])
