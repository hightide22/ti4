"""GUI-backed smoke test for setup and one fully automated round."""
from pathlib import Path
import argparse
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import arcade

from app import BoardWindow
from units import Unit, UnitLocation, Region


def click(window, action):
    window.on_draw()
    hit = next(hit for hit in window.main_menu.hits if hit[0] == action)
    window.on_mouse_press((hit[1] + hit[2]) / 2, (hit[3] + hit[4]) / 2,
                          arcade.MOUSE_BUTTON_LEFT, 0)


def main(player_count=3):
    window = BoardWindow(smoke=False, show_menu=True)
    try:
        window.switch_to()
        if player_count == 4:
            click(window, ('map', 4))
        click(window, ('toggle_ai',))
        click(window, ('human_slot', player_count - 1))
        assert window.main_menu.vs_ai and window.main_menu.human_slot == player_count - 1
        window.on_draw()
        preview = Path(__file__).resolve().parents[1] / 'previews' / f'ai-menu-{player_count}.png'
        preview.parent.mkdir(exist_ok=True)
        arcade.get_image().save(preview)
        click(window, ('start',))
        human = window.main_menu.active_factions[player_count - 1]
        assert window.ai.bot_factions == set(window.main_menu.active_factions) - {human}
        window.on_draw()
        assert window.bot_actor and not window.strategy_modal
        assert window.turn_button_hit is None and window.action_card_button_hit is None
        assert not window.strategy_panel.hits
        assert not any(control.action[0] in ('currency', 'pool')
                       for control in window.player_panel.controls)
        # The renderer must keep a crowded two-planet home system drawable.
        crowded = next(tile for tile in window.board.values() if tile.number == 12)
        original_units = crowded.units[:]
        jolnar = next(player for player in window.turn_order.players if player.faction == 'jolnar')
        crowded.units.extend(Unit(f'ai-smoke-{kind}-{index}', kind, 'jolnar',
                                   jolnar.color_code, UnitLocation(Region.SPACE))
                             for kind in ('carrier', 'dreadnought', 'cruiser', 'destroyer', 'fighter')
                             for index in range(3))
        window.unit_renderer.blocking_layouts = True
        window.on_draw()
        crowded.units[:] = original_units
        window.unit_renderer.blocking_layouts = False
        assert window.ai.speed == 1.0
        pause = window.ai_pause_hit
        window.on_mouse_press((pause[0] + pause[1]) / 2, (pause[2] + pause[3]) / 2,
                              arcade.MOUSE_BUTTON_LEFT, 0)
        assert window.ai.paused
        picks = window.turn_order.strategy_pick_cursor
        window.on_update(5.0)
        assert window.turn_order.strategy_pick_cursor == picks
        window.on_draw()
        speed = window.ai_speed_hit
        window.on_mouse_press((speed[0] + speed[1]) / 2, (speed[2] + speed[3]) / 2,
                              arcade.MOUSE_BUTTON_LEFT, 0)
        assert window.ai.speed == 2.0
        window.on_mouse_press((pause[0] + pause[1]) / 2, (pause[2] + pause[3]) / 2,
                              arcade.MOUSE_BUTTON_LEFT, 0)
        assert not window.ai.paused
        for _ in range(100):
            if window.turn_order.active_player.faction == human:
                break
            window.on_update(.25)
        else:
            raise AssertionError('AI did not finish opening draft picks')
        assert window.turn_order.strategy_selection
        assert window.turn_order.active_player.faction == human
        window.on_update(.25)
        assert window.turn_order.active_player.faction == human
        # Use the same scheduler for all seats to exercise a complete round
        # without synthetic mouse input for the human seat.
        window.ai.bot_factions = {p.faction for p in window.turn_order.players}
        window.ai.speed = 4.0
        initial_units = sum(len(tile.units) for tile in window.board.values())
        initial_planets = {p.faction: len(p.planets) for p in window.turn_order.players}
        initial_round = window.turn_order.round_number
        conversions = 0
        movement_preview = False
        for step in range(1800):
            before_round = window.turn_order.round_number
            before_wallets = {p.faction: (p.trade_goods, p.commodities)
                              for p in window.turn_order.players}
            window.on_update(.25)
            if window.turn_order.round_number > before_round:
                conversions += 1
                for player in window.turn_order.players:
                    goods, commodities = before_wallets[player.faction]
                    assert player.commodities == 0
                    assert player.trade_goods == goods + commodities
            if window.ai.last_error:
                raise AssertionError(f'{window.ai.last_error}; step={step}; '
                                     f'strategy={window.strategy.session and window.strategy.session.stage}; '
                                     f'movement={window.movement.session and window.movement.session.stage}')
            if step % 24 == 0:
                window.on_draw()
            if (not movement_preview and window.movement.session and
                    window.movement.session.stage == 'movement'):
                window.on_draw()
                assert window.bot_actor and not window.movement_panel.hits
                assert not window.movement_panel.buttons
                if not window.human_card_participants():
                    assert window.action_card_button_hit is None
                arcade.get_image().save(preview.parent / f'ai-movement-{player_count}.png')
                movement_preview = True
            if window.turn_order.round_number > initial_round + 1:
                break
        else:
            raise AssertionError(f'Automated round did not finish: round={window.turn_order.round_number}, '
                                 f'player={window.turn_order.active_player.faction}, '
                                 f'pick={window.turn_order.strategy_selection}, '
                                 f'allocation={window.turn_order.command_allocation}, '
                                 f'strategy={window.strategy.session and (window.strategy.session.stage, window.strategy.player.faction)}, '
                                 f'movement={window.movement.session and (window.movement.session.stage, window.movement.session.player.faction)}, '
                                 f'cards={window.action_cards.pending}, '
                                 f'passed={window.turn_order.passed_indices}')
        final_units = sum(len(tile.units) for tile in window.board.values())
        final_planets = {p.faction: len(p.planets) for p in window.turn_order.players}
        assert final_units >= initial_units
        assert any(final_planets[f] > initial_planets[f] for f in initial_planets)
        assert conversions == 2
        assert movement_preview
        print(f'PASS: AI setup, draft, strategy, tactical actions and two rounds complete '
              f'in {step + 1} scheduler steps; units {initial_units}->{final_units}; '
              f'planets {initial_planets}->{final_planets}')
    finally:
        window.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--players', type=int, choices=(3, 4), default=3)
    main(parser.parse_args().players)
