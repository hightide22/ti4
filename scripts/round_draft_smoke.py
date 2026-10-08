"""Exercise the base-game round transition and repeat strategy draft in the GUI."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import arcade

from app import BoardWindow
from units import Region, Unit, UnitLocation


def click_rect(window, rect):
    window.on_mouse_press((rect[0] + rect[1]) / 2,
                          (rect[2] + rect[3]) / 2,
                          arcade.MOUSE_BUTTON_LEFT, 0)


def main():
    window = BoardWindow()
    try:
        window.set_size(1120, 720)
        window.dispatch_events()
        window.switch_to()
        turn = window.turn_order
        for card in (1, 2, 3, 4, 5, 6):
            turn.choose_strategy_card(card)
        turn.set_speaker(next(player for player in turn.players if player.faction == 'jolnar'))
        for player in turn.players:
            turn.strategy_used[player.faction].update(turn.strategy_assignments[player.faction])
        window.strategy_view = False
        damaged = []
        for player in turn.players:
            tile = next(tile for tile in window.board.values() if any(
                planet.faction_homeworld == player.faction for planet in tile.planets))
            unit = Unit(f'round-repair-{player.faction}', 'dreadnought', player.faction,
                        player.color_code, UnitLocation(Region.SPACE))
            unit.damaged = True
            tile.units.append(unit)
            damaged.append(unit)
        turn.mark_action_completed()
        window.on_draw()
        click_rect(window, window.turn_button_hit)
        assert turn.round_number == 1 and all(unit.damaged for unit in damaged)
        for _ in turn.players:
            window.player_panel.active = turn.active_index
            window.on_draw()
            assert window.turn_button_hit is not None
            click_rect(window, window.turn_button_hit)
        assert turn.round_number == 2 and turn.command_allocation
        assert all(not unit.damaged for unit in damaged)
        assert turn.available_strategy_cards == tuple(range(1, 9))
        assert all(not cards for cards in turn.strategy_assignments.values())
        while turn.command_allocation:
            player = turn.active_player
            window.on_draw()
            pending = next(control for control in window.player_panel.controls
                           if control.action == ('pending',))
            click_rect(window, (pending.left, pending.left + pending.width,
                                pending.bottom, pending.bottom + pending.height))
            while player.pending_commands:
                window.on_draw()
                pool = next(control for control in window.player_panel.controls
                            if control.action == ('pool', 'tactical'))
                click_rect(window, (pool.left, pool.left + pool.width,
                                    pool.bottom, pool.bottom + pool.height))
        assert turn.strategy_selection and turn.active_player.faction == 'jolnar'
        window.on_draw()
        assert len([hit for hit in window.strategy_panel.hits
                    if hit[0][0] == 'choose_strategy']) == 8
        output = Path(__file__).resolve().parents[1] / 'previews' / 'round-two-draft.png'
        output.parent.mkdir(exist_ok=True)
        arcade.get_image().save(output)
        first = next(hit for hit in window.strategy_panel.hits
                     if hit[0] == ('choose_strategy', 1))
        click_rect(window, first[1:])
        assert turn.strategy_assignments['jolnar'] == [1]
        assert turn.active_player is turn.players[(turn.speaker_index + 1) % len(turn.players)]
        print('PASS: all players passed, commands allocated, and Speaker started a fresh draft')
    finally:
        window.unit_renderer.layout_executor.shutdown(wait=True, cancel_futures=True)
        window.close()


if __name__ == '__main__':
    main()
