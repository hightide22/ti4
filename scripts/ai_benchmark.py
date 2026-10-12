"""Reproducible headless matches through the game's real controllers.

Examples:
    .venv/Scripts/python scripts/ai_benchmark.py --players 3 4 --rounds 3
    .venv/Scripts/python scripts/ai_benchmark.py --factions saar sol jolnar --seed 7

All seats use the same GameAI. This measures completion, expansion, economy,
fleet composition, and decision latency, not a win rate against human players.
Presentation delays are bypassed; dice, cards, and game rules are unchanged.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack
import json
import os
from pathlib import Path
import random
import sys
import subprocess
from time import perf_counter
from types import SimpleNamespace
import traceback
from unittest.mock import patch
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from action_card_deck import ActionCardDeck
from action_cards import ActionCardController
from app import BoardWindow
from board import load_board
from game_ai import GameAI
from movement import MovementController
from player import create_players
from strategic_action import StrategyController
from technology_actions import TechnologyActions
from transactions import TransactionController
from turn_order import TurnOrder
from units import Region


def no_display(*args, **kwargs):
    """Camera and widget updates have no gameplay effects."""


class ObservedAI(GameAI):
    """Log decisions without changing their inputs or selection."""

    def report(self, player, message, duration=.7):
        self.window.trace.append(dict(step=self.window.steps,
                                      round=self.window.turn_order.round_number,
                                      faction=player.faction if player else None,
                                      message=message))
        super().report(player, message, duration)

    def start_activation(self, player, move):
        target = self.window.board[move[1]]
        self.window.activations.append(dict(
            step=self.window.steps, round=self.window.turn_order.round_number,
            faction=player.faction, tile=target.number, target=target.name,
            score=round(move[0], 3), ships=len(move.ships),
            passengers=len(move.passengers), odds=move.odds,
            production_only=not move.ships and not move.passengers,
            home=target.definition.is_home_system,
        ))
        super().start_activation(player, move)


class HeadlessGame:
    """Supply display ports to the actual BoardWindow orchestration methods.

    Reusing these methods is intentional: status-phase income, repair, cards,
    round drafts and action completion must behave exactly as in the GUI.
    No Arcade window, sprites, textures, or OpenGL context are constructed.
    """

    sync_turn_action = BoardWindow.sync_turn_action
    restore_infantry_on_cards = BoardWindow.restore_infantry_on_cards
    sync_strategy_actor = BoardWindow.sync_strategy_actor
    pass_turn = BoardWindow.pass_turn
    finish_status_phase = BoardWindow.finish_status_phase
    clear_round_tokens = BoardWindow.clear_round_tokens
    frame_player_systems = staticmethod(no_display)
    frame_action_route = staticmethod(no_display)

    def __init__(self, count=3, factions=None, seed=1):
        map_path = ROOT / 'maps' / ('three_player.json' if count == 3 else 'four_player.json')
        self.map_config, self.board = load_board(map_path, factions)
        players = create_players(self.board, self.map_config)
        self.player_panel = SimpleNamespace(players=players, active=0, card_offset=0,
                                            source_pool=None, hovered_planet=None)
        self.turn_order = TurnOrder(players, strategy_enabled=True)
        self.movement = MovementController(self.board, players)
        self.action_card_deck = ActionCardDeck(rng=random.Random(seed))
        self.action_cards = ActionCardController(
            players, self.movement, deck=self.action_card_deck.cards,
            turn=self.turn_order, rng=random.Random(seed + 1))
        self.strategy = StrategyController(self.board, self.turn_order,
                                            self.movement, self.action_cards)
        self.technology_actions = TechnologyActions(self.board, self.movement, self.turn_order)
        self.transaction = TransactionController(self.board, players, self.turn_order, self.movement)
        self.ai = ObservedAI(self, {p.faction for p in players})
        self.system_panel = SimpleNamespace(reset=no_display)
        self.movement_panel = SimpleNamespace(reset=no_display)
        self.smoke = self.main_menu_visible = self.strategy_view = self.focus_view = False
        self.inspector_visible = True
        self.turn_history_size = 0
        self.last_infantry_return_turn = None
        self.mitosis_player = self.movement_error = self.token_context = None
        self.selected_units = ()
        self.token_hits = []
        self.steps = 0
        self.trace = []
        self.activations = []

    def state(self):
        turn = self.turn_order
        movement, strategy = self.movement.session, self.strategy.session
        return dict(
            round=turn.round_number, player=turn.active_player.faction,
            turn=turn.turn_serial, draft=turn.strategy_selection,
            draft_cursor=turn.strategy_pick_cursor,
            allocation=turn.command_allocation, passed=sorted(turn.passed_indices),
            strategy=(dict(card=strategy.card, stage=strategy.stage,
                           player=strategy.player.faction) if strategy else None),
            movement=(dict(stage=movement.stage, player=movement.player.faction,
                           target=movement.target.number,
                           combat_round=movement.combat_round,
                           combat_factions=movement.combat_factions,
                           combat_hits=movement.combat_hits,
                           combat_assignments=movement.combat_assignments,
                           production_choices=movement.production_choices,
                           production_limit=movement.production_limit,
                           production_sites=movement.production_sites,
                           integrated_current=movement.integrated_current,
                           selected_units=sorted(movement.selected)) if movement else None),
            card_pending=(str(self.action_cards.pending) if self.action_cards.pending else None),
            error=self.movement_error,
        )

    def fingerprint(self):
        """Detect a no-op loop while allowing multi-click assignments to progress."""
        sessions = []
        for session in (self.movement.session, self.strategy.session):
            sessions.append(None if not session else {
                key: value for key, value in vars(session).items()
                if key not in {'snapshot', 'sources', 'pickup_sources', 'target',
                               'bombardment_checkpoint', 'invasion_checkpoint',
                               'production_checkpoint'}})
        players = [(p.faction, p.command_pools, p.pending_commands, p.trade_goods,
                    p.commodities, p.technologies, p.action_cards,
                    [(c.planet.planet_id, c.exhausted) for c in p.planets])
                   for p in self.turn_order.players]
        tiles = [(tile.position, tile.planet_owners, sorted(tile.command_tokens),
                  [(u.unit_id, u.location, u.damaged) for u in tile.units])
                 for tile in self.board.values()]
        return repr((self.state(), self.turn_order.actions_used, sessions, players, tiles))

    def metrics(self):
        result = {}
        for player in self.turn_order.players:
            units = [u for tile in self.board.values() for u in tile.units
                     if u.owner == player.faction]
            owned = [(tile, planet) for tile in self.board.values() for planet in tile.planets
                     if tile.planet_owners.get(planet.planet_id) == player.faction]
            result[player.faction] = dict(
                planets=len(owned), resources=sum(p.resources for _, p in owned),
                influence=sum(p.influence for _, p in owned),
                systems=len({tile.position for tile, _ in owned}),
                units=dict(sorted(Counter(u.kind for u in units).items())),
                infantry_in_space=sum(u.kind == 'infantry' and
                                      u.location.region == Region.TRANSPORT for u in units),
                technologies=sorted(player.technologies),
                trade_goods=player.trade_goods, commodities=player.commodities,
                pools=dict(player.command_pools), pending_commands=player.pending_commands,
                action_cards=len(player.action_cards),
            )
        return result


def run_match(count=3, rounds=3, seed=1, factions=None, max_steps=6000,
              max_seconds=180, stall_steps=20):
    """Return a JSON-serializable report, including a precise state on failure."""
    original_rng = random.getstate()
    random.seed(seed)
    identity_rng = random.Random(seed + 2)
    deterministic_uuid = lambda: UUID(int=identity_rng.getrandbits(128), version=4)
    started = perf_counter()
    report = dict(players=count, seed=seed, requested_rounds=rounds, status='running')
    game = None
    times = []
    slowest = []
    try:
        with ExitStack() as stack:
            # IDs only break ties, but OS-random UUIDs would still make equal
            # choices change between runs. Do not consume the dice RNG here.
            for module in ('movement', 'strategic_action', 'technology'):
                stack.enter_context(patch(f'{module}.uuid4', deterministic_uuid))
            game = HeadlessGame(count, factions, seed)
            report['factions'] = [p.faction for p in game.turn_order.players]
            report['initial'] = game.metrics()
            report['rounds'] = []
            unchanged, previous = 0, None
            for game.steps in range(1, max_steps + 1):
                before_round = game.turn_order.round_number
                game.sync_turn_action()
                decision_context = game.state()
                before_decision = perf_counter()
                try:
                    game.ai.step()
                finally:
                    elapsed = perf_counter() - before_decision
                    times.append(elapsed)
                    if len(slowest) < 10 or elapsed > slowest[-1]['seconds']:
                        slowest.append(dict(seconds=elapsed, step=game.steps,
                                            state=decision_context))
                        slowest.sort(key=lambda item: item['seconds'], reverse=True)
                        del slowest[10:]
                if game.movement_error:
                    raise RuntimeError(game.movement_error)
                if game.turn_order.round_number > before_round:
                    report['rounds'].append(dict(round=before_round, step=game.steps,
                                                 players=game.metrics()))
                    if game.turn_order.round_number >= 1 + rounds:
                        report['status'] = 'passed'
                        break
                signature = game.fingerprint()
                unchanged = unchanged + 1 if signature == previous else 0
                previous = signature
                if unchanged >= stall_steps:
                    raise RuntimeError(f'No game-state change for {unchanged} decisions')
                if perf_counter() - started > max_seconds:
                    raise TimeoutError(f'Match exceeded {max_seconds} seconds')
            else:
                raise TimeoutError(f'Match exceeded {max_steps} decisions')
    except Exception as error:
        report['status'] = 'failed'
        report['error'] = f'{type(error).__name__}: {error}'
        report['traceback'] = traceback.format_exc()
    finally:
        random.setstate(original_rng)
        ordered = sorted(times)
        report['decision_seconds'] = dict(total=round(sum(times), 3),
                                           max=round(max(times, default=0), 3),
                                           p95=round(ordered[int(.95 * (len(ordered) - 1))], 3)
                                           if ordered else 0, slowest=slowest)
        report['wall_seconds'] = round(perf_counter() - started, 3)
        if game:
            report['steps'] = game.steps
            report['state'] = game.state()
            report['final'] = game.metrics()
            report['activations'] = game.activations
            report['events'] = game.trace
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--players', nargs='+', type=int, choices=(3, 4), default=[3, 4])
    parser.add_argument('--factions', nargs='+', help='Override map seats (3 or 4 unique aliases)')
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--seed', nargs='+', type=int, default=[1])
    parser.add_argument('--max-steps', type=int, default=6000)
    parser.add_argument('--max-seconds', type=float, default=180)
    parser.add_argument('--output', type=Path, help='Write full report and event trace as JSON')
    args = parser.parse_args(argv)
    if args.rounds < 1 or args.max_steps < 1 or args.max_seconds <= 0:
        parser.error('Round and step limits must be positive')
    if args.factions and (len(args.factions) not in (3, 4) or
                         len(set(args.factions)) != len(args.factions)):
        parser.error('--factions requires 3 or 4 unique faction aliases')
    counts = [len(args.factions)] if args.factions else args.players
    reports = []
    for count in counts:
        for seed in args.seed:
            report = run_match(count, args.rounds, seed, args.factions,
                               args.max_steps, args.max_seconds)
            reports.append(report)
            print(f'{report["status"].upper()}: {count} players, seed {seed}, '
                  f'{len(report.get("rounds", []))}/{args.rounds} rounds, '
                  f'{report.get("steps", 0)} decisions, {report["wall_seconds"]:.2f}s', flush=True)
            timing = report['decision_seconds']
            print(f'  Decision latency: p95 {timing["p95"]:.3f}s, '
                  f'max {timing["max"]:.3f}s', flush=True)
            for faction, state in report.get('final', {}).items():
                before = report['initial'][faction]
                print(f'  {faction}: planets {before["planets"]}->{state["planets"]}, '
                      f'resources {state["resources"]}, goods {state["trade_goods"]}, '
                      f'units {state["units"]}', flush=True)
            if report['status'] != 'passed':
                print(f'  {report["error"]}\n  State: {report.get("state")}', flush=True)
            # Keep completed cases if a later, larger match is interrupted.
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(reports, indent=2), encoding='utf-8')
    return int(any(report['status'] != 'passed' for report in reports))


if __name__ == '__main__':
    # String-set iteration can otherwise change tied choices between Python
    # processes, even when dice and UUIDs are seeded. Preserve the caller's
    # environment and apply the hash seed only to this benchmark process.
    if os.environ.get('PYTHONHASHSEED') != '0':
        raise SystemExit(subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]],
            env={**os.environ, 'PYTHONHASHSEED': '0'}).returncode)
    raise SystemExit(main())
