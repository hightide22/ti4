"""Exercise strategy dialogs through their real hit targets and save GUI previews."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import arcade
from app import BoardWindow


def main():
    previews = Path(__file__).resolve().parents[1] / 'previews'
    previews.mkdir(exist_ok=True)
    w = BoardWindow()
    try:
        w.set_size(1440, 900)
        w.dispatch_events()
        w.switch_to()

        def render(name=None):
            w.on_draw()
            if name:
                arcade.get_image().save(previews / f'strategy-{name}.png')

        def click(action, panel=None):
            render()
            target_panel = panel or w.strategy_panel
            targets = target_panel.hits + getattr(target_panel, 'buttons', [])
            target = next(hit for hit in targets if hit[0] == action)
            _, left, right, bottom, top = target
            w.on_mouse_press((left + right) / 2, (bottom + top) / 2, arcade.MOUSE_BUTTON_LEFT, 0)
            render()
            assert not w.movement_error, w.movement_error

        render('draft')
        assert w.strategy_panel.bounds[0] < w.width / 2 < w.strategy_panel.bounds[1]
        for card in (1, 5, 6, 4, 2, 3):
            click(('choose_strategy', card))
        assert not w.turn_order.strategy_selection
        owner = w.turn_order.active_player
        w.strategy_view = True
        click(('start', 1))
        assert w.turn_button_hit is None
        click(('buy', 1))
        owner.trade_goods = 3
        render()
        planet_control = next(c for c in w.player_panel.controls if c.action == ('planet', 0))
        w.on_mouse_press(planet_control.left + 12, planet_control.bottom + 12, arcade.MOUSE_BUTTON_LEFT, 0)
        render()
        assert owner.planets[0].exhausted
        needed = max(0, 3 - owner.planets[0].planet.influence)
        for _ in range(needed):
            click(('goods', 1))
        render('payment')
        click(('pay',))
        assert w.strategy.session.stage == 'allocate'
        render('allocation')
        while owner.pending_commands:
            click(('allocate', 'tactical'))
        assert w.strategy.session.stage == 'offer'
        assert w.turn_order.active_player is owner
        assert w.player_panel.player is w.strategy.player
        render('secondary-offer')
        responder = w.strategy.player
        responder.trade_goods = 3
        responder.command_pools['strategic'] = 0
        click(('accept',))
        click(('buy', 1))
        for _ in range(3):
            click(('goods', 1))
        click(('pay',))
        click(('allocate', 'strategic'))
        click(('decline',))
        assert w.strategy.session is None
        assert w.turn_order.action_used and w.turn_order.active_player is owner
        assert w.turn_button_hit is not None
        assert 1 in w.turn_order.strategy_used[owner.faction]
        # Show read-only information and front/back cards in the roster.
        w.on_mouse_motion(30, w.height - 180, 0, 0)
        render('roster-details')
        assert w.roster.hovered == 0
        w.on_mouse_motion(500, 200, 0, 0)
        w.pass_turn()
        w.strategy_view = True
        click(('start', 5))
        first = w.turn_order.clockwise_players_after(w.turn_order.active_player)[0]
        first.command_pools['strategic'] = 0
        click(('trade_toggle', first.faction))
        click(('trade_confirm',))
        assert w.strategy.player is first
        click(('accept',))
        assert first.commodities == first.commodity_limit
        assert first.command_pools['strategic'] == 0
        click(('decline',))
        w.pass_turn()
        w.strategy_view = True
        click(('start', 6))
        click(('continue',))  # No own command token is on the board in this fixture.
        click(('continue',))
        click(('accept',))
        dock_id = w.strategy.home_docks()[0][1].unit_id
        click(('produce_at', dock_id))
        assert w.movement.session.strategic_production
        assert not w.strategy_modal
        render('warfare-production')
        click(('skip_production',), w.movement_panel)
        assert w.strategy.session.stage == 'offer'
        click(('decline',))
        # Repeat the draft layout at the minimum supported window size.
        w.turn_order.begin_strategy_phase()
        w.sync_strategy_actor()
        w.set_size(1120, 720)
        w.dispatch_events()
        render('draft-compact')
        assert len(w.strategy_panel.hits) == 8
        print('PASS: strategy draft, manual payment, allocation, ordered secondaries, Trade, Warfare, roster and compact layout', flush=True)
    finally:
        w.unit_renderer.layout_executor.shutdown(wait=True, cancel_futures=True)
        w.close()


if __name__ == '__main__':
    main()
