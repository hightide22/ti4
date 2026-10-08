"""Exercise strategy dialogs through their real hit targets and save GUI previews."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import arcade
from app import BoardWindow
from player import PlanetCard


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
        # The large strategy-card face can be moved by itself while planet selection stays open.
        construction_owner = w.turn_order.active_player
        if 4 not in w.turn_order.strategy_assignments[construction_owner.faction]:
            w.turn_order.strategy_assignments[construction_owner.faction].append(4)
        w.turn_order.strategy_used[construction_owner.faction].discard(4)
        w.turn_order.action_used = False
        w.turn_order.actions_used = 0
        w.strategy.start(4)
        w.sync_strategy_actor()
        render('construction-card-before-drag')
        panel_before = w.strategy_panel.bounds
        card_before = w.strategy_panel.card_bounds
        card_x, card_y = (card_before[0] + card_before[1]) / 2, (card_before[2] + card_before[3]) / 2
        w.on_mouse_press(card_x, card_y, arcade.MOUSE_BUTTON_LEFT, 0)
        assert w.dragging_modal == 'strategy_card'
        w.on_mouse_drag(card_x - 500, card_y, -500, 0, arcade.MOUSE_BUTTON_LEFT, 0)
        w.on_mouse_release(card_x - 500, card_y, arcade.MOUSE_BUTTON_LEFT, 0)
        render('construction-card-moved')
        assert w.strategy_panel.bounds == panel_before
        assert w.strategy_panel.card_bounds[1] < panel_before[0]
        selectable = []
        for position in w.strategy.selectable_systems():
            sx, sy = w.screen(position)
            in_viewport = (w.roster.WIDTH <= sx < w.width - w.sidebar and
                           w.player_panel.HEIGHT <= sy < w.height - 80)
            in_modal = (panel_before[0] <= sx <= panel_before[1] and
                        panel_before[2] <= sy <= panel_before[3])
            in_card = (w.strategy_panel.card_bounds[0] <= sx <= w.strategy_panel.card_bounds[1] and
                       w.strategy_panel.card_bounds[2] <= sy <= w.strategy_panel.card_bounds[3])
            if in_viewport and not in_modal and not in_card:
                selectable.append((position, sx, sy))
        assert selectable, 'No planet system remained clickable after moving the strategy card.'
        position, sx, sy = selectable[0]
        w.on_mouse_press(sx, sy, arcade.MOUSE_BUTTON_LEFT, 0)
        assert w.strategy.session.selected_system == position
        w.strategy.session = w.turn_order.strategy_resolution = None
        w.strategy_view = False

        # Base-game Diplomacy readies only the owner's planets in the chosen system.
        diplomacy_owner = w.turn_order.active_player
        for assignments in w.turn_order.strategy_assignments.values():
            if 2 in assignments:
                assignments.remove(2)
        w.turn_order.strategy_assignments[diplomacy_owner.faction].append(2)
        w.turn_order.strategy_used[diplomacy_owner.faction].discard(2)
        w.turn_order.action_used = False
        selected_system = next(tile for tile in w.board.values()
                               if diplomacy_owner.faction in tile.planet_owners.values() and tile.number != 18)
        remote_system = next(tile for tile in w.board.values()
                             if tile.position != selected_system.position and tile.planets and
                             not tile.planet_owners)
        remote_planet = remote_system.planets[0]
        remote_system.planet_owners[remote_planet.planet_id] = diplomacy_owner.faction
        remote_card = PlanetCard(remote_planet, exhausted=True)
        diplomacy_owner.planets.append(remote_card)
        for planet_card in diplomacy_owner.planets:
            planet_card.exhausted = True
        w.strategy.start(2)
        w.sync_strategy_actor()
        render('diplomacy-map-selection')
        before_pan = tuple(w.map_center)
        w.on_mouse_drag(w.roster.WIDTH + 160, 420, 28, -16, arcade.MOUSE_BUTTON_MIDDLE, 0)
        assert tuple(w.map_center) != before_pan, 'The map should pan while the Diplomacy window is open.'
        sx, sy = w.screen(selected_system.position)
        bounds = w.strategy_panel.bounds
        assert not (bounds[0] <= sx <= bounds[1] and bounds[2] <= sy <= bounds[3]), \
            'The selected controlled system should remain clickable beside the modal.'
        assert w.pick(sx, sy) == selected_system.position
        w.on_mouse_press(sx, sy, arcade.MOUSE_BUTTON_LEFT, 0)
        assert w.strategy.session.stage == 'offer'
        assert all(player.faction in selected_system.command_tokens
                   for player in w.player_panel.players if player is not diplomacy_owner)
        assert all(not card.exhausted for card in diplomacy_owner.planets
                   if selected_system.planet_owners.get(card.planet.planet_id) == diplomacy_owner.faction)
        assert remote_card.exhausted
        responder = w.strategy.player
        for card in responder.planets:
            card.exhausted = True
        w.strategy.accept_secondary()
        w.sync_strategy_actor()
        render('diplomacy-ready-planets')
        responder_planet = responder.planets[0].planet
        responder_system = w.strategy.planet_system(responder_planet.planet_id)
        remote_action = ('ready_planet', responder_planet.planet_id)
        displayed_planets = {hit[0][1] for hit in w.strategy_panel.hits if hit[0][0] == 'ready_planet'}
        controlled_exhausted = {card.planet.planet_id for card in responder.planets
                                if card.exhausted}
        assert displayed_planets == controlled_exhausted
        remote_hit = next(hit for hit in w.strategy_panel.hits if hit[0] == remote_action)
        _, left, right, card_bottom, card_top = remote_hit
        w.on_mouse_motion((left + right) / 2, (card_bottom + card_top) / 2, 0, 0)
        assert w.strategy_hover_system == responder_system.position
        render('diplomacy-remote-planet-hover')
        click(remote_action)
        assert responder_planet.planet_id in w.strategy.session.ready_planets
        w.strategy.session = w.turn_order.strategy_resolution = None
        w.strategy_view = False
        # Repeat the draft layout at the minimum supported window size.
        w.turn_order.begin_strategy_phase()
        w.sync_strategy_actor()
        w.set_size(1120, 720)
        w.dispatch_events()
        render('draft-compact')
        assert len(w.strategy_panel.hits) == 8
        print('PASS: strategy draft, modal and card dragging, Diplomacy planet cards and map hover, payment, allocation, Trade, Warfare and compact layout', flush=True)
    finally:
        w.unit_renderer.layout_executor.shutdown(wait=True, cancel_futures=True)
        w.close()


if __name__ == '__main__':
    main()
