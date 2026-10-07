"""Exercise dense, scrollable UI states at the minimum supported window size."""
from pathlib import Path
from copy import copy
import math
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import arcade
from app import BoardWindow, world
from movement import Session, Snapshot
from units import Unit, UnitLocation, Region, unit_profile
from ui_theme import VARIANT


def main():
    output = Path(__file__).resolve().parents[1] / 'previews' / VARIANT.lower()
    output.mkdir(parents=True, exist_ok=True)
    w = BoardWindow(smoke=True)
    try:
        w.set_size(1120, 720)
        w.dispatch_events()
        w.switch_to()

        def render(name=None):
            w.on_draw()
            if name:
                arcade.get_image().save(output / f'{name}.png')

        # The compact map toggle sits directly beside the turn button and has no
        # detached hover caption. Turn handoffs reorder and frame the next player.
        render('turn-order-first-player')
        toggle_bounds = w.control_toggle_hit
        turn_bounds = w.turn_button_hit
        assert toggle_bounds[1] - toggle_bounds[0] <= 40
        assert 0 <= toggle_bounds[0] - turn_bounds[1] <= 10
        w.mouse_position = ((toggle_bounds[0] + toggle_bounds[1]) / 2,
                            (toggle_bounds[2] + toggle_bounds[3]) / 2)
        render()
        assert 'control_toggle_hint' not in w.labels
        order = w.turn_order
        original_initiative = order.strategy_initiative[:]
        order.strategy_initiative = [1, 2, 0]
        order.active_index = 1
        assert w.roster.player_indices(w) == [1, 2, 0]
        order.strategy_initiative = original_initiative
        order.active_index = 0
        first_index = order.active_index
        next_index = (first_index + 1) % len(order.players)
        next_player = order.players[next_index]
        order.mark_action_completed()
        w.pass_turn()
        assert order.active_player is next_player
        assert w.roster.player_indices(w)[0] == next_index
        previous_center = tuple(w.map_center)
        next_positions = w.player_system_positions(next_player)
        next_coords = [world(position) for position in next_positions]
        assert next_coords
        assert w.target_map_center == [
            (min(x for x, _ in next_coords) + max(x for x, _ in next_coords)) / 2,
            (min(y for _, y in next_coords) + max(y for _, y in next_coords)) / 2,
        ]
        previous_distance = math.dist(previous_center, w.target_map_center)
        w.on_update(.016)
        assert math.dist(w.map_center, w.target_map_center) < previous_distance
        w.on_update(1.0)
        assert w.map_center == w.target_map_center
        assert w.target_zoom <= 1.6
        center_x, center_y = w.viewport_center
        scale = w.fit_scale * w.target_zoom
        radius = scale
        for position in next_positions:
            x, y = world(position)
            screen_x = center_x + (x - w.target_map_center[0]) * scale
            screen_y = center_y + (y - w.target_map_center[1]) * scale
            assert w.roster.WIDTH <= screen_x - radius
            assert screen_x + radius <= w.width - w.sidebar
            assert w.player_panel.HEIGHT <= screen_y - radius
            assert screen_y + radius <= w.height - 80
        render('turn-order-next-player')
        order.active_index = first_index
        order.action_used = False
        w.player_panel.active = first_index
        w.frame_player_systems(order.active_player)
        w.on_update(1.0)
        w.mouse_position = (-1, -1)

        player = w.player_panel.players[0]
        home = next(t for t in w.board.values() if any(u.owner == player.faction for u in t.units))
        target = next(t for t in w.board.neighbors(home.position) if t.planets and
                      any(w.movement.route(home, t, u, player) for u in home.units))
        source = next(t for t in w.board.neighbors(target.position) if t is not home and not t.anomalies)
        source.units.append(Unit('preview-carrier', 'carrier', player.faction, player.color_code,
                                 UnitLocation(Region.SPACE)))
        session = w.movement.activate(player, target.position)
        w.selected = target.position
        render('movement-compact')
        assert len(session.sources) >= 2
        toggle = next(h for h in w.movement_panel.buttons if h[0] == ('timeline',))
        w.on_mouse_press((toggle[1] + toggle[2]) / 2, (toggle[3] + toggle[4]) / 2,
                         arcade.MOUSE_BUTTON_LEFT, 0)
        render()
        assert w.movement_panel.timeline_expanded
        toggle = next(h for h in w.movement_panel.buttons if h[0] == ('timeline',))
        w.on_mouse_press((toggle[1] + toggle[2]) / 2, (toggle[3] + toggle[4]) / 2,
                         arcade.MOUSE_BUTTON_LEFT, 0)
        assert not w.movement_panel.timeline_expanded
        w.movement_panel.scroll_by(10000)
        render()
        hit = next(h for h in w.movement_panel.route_hits if h[1] == 'preview-carrier')
        w.on_mouse_motion((hit[2] + hit[3]) / 2, (hit[4] + hit[5]) / 2, 0, 0)
        assert w.movement_panel.hovered_source_position == source.position
        render('movement-source-hover')
        w.movement.cancel()
        w.movement_panel.reset()

        # Fixed dice values make wrapping and hit assignment repeatable.
        target = copy(target)
        target.units = []
        opponents = w.player_panel.players[:2]
        for actor in opponents:
            for kind in ('carrier', 'cruiser', 'destroyer', 'dreadnought', 'fighter', 'flagship', 'warsun'):
                for index in range(30 if kind == 'fighter' else 4):
                    target.units.append(Unit(f'{actor.faction}-{kind}-{index}', kind, actor.faction,
                                             actor.color_code, UnitLocation(Region.SPACE)))
        session = Session(player, target, {}, Snapshot.capture(w.board, player, w.player_panel.players))
        session.stage, session.combat_round = 'space_combat', 1
        session.combat_factions = tuple(p.faction for p in opponents)
        session.combat_needs_resolution = True
        for actor in opponents:
            session.combat_hits[actor.faction] = 3
            session.combat_rolls[actor.faction] = [
                {'unit_id': u.unit_id, 'kind': u.kind, 'value': index % 10 + 1,
                 'hit': index % 10 + 1 >= int(unit_profile(u).get('combatHitsOn') or 10)}
                for index, u in enumerate(target.units) if u.owner == actor.faction]
        w.movement.session = session
        render('combat-dense')
        assert all(limit > 0 for limit in w.combat_panel.scroll_max)
        before_zoom = w.target_zoom
        left, right, bottom, top = w.combat_panel.bounds
        w.on_mouse_scroll(left + 40, (bottom + top) / 2, 0, -5)
        render('combat-scrolled')
        assert w.combat_panel.scroll[0] > 0 and w.combat_panel.scroll[1] == 0
        assert w.target_zoom == before_zoom
        hit = next(h for h in w.combat_panel.assignment_hits if h[0][1] == player.faction)
        assert hit[3] >= w.combat_panel.body_bottom
        w.on_mouse_press(hit[1] + 12, (hit[3] + hit[4]) / 2,
                         arcade.MOUSE_BUTTON_LEFT, 0)
        assert len(session.combat_assignments[player.faction]) == 1
        render('combat-assignment')
        label_count = len(w.labels)
        w.combat_panel.move(12, 8)
        render()
        assert len(w.labels) == label_count, 'Dragging must reuse dice labels'
        print('PASS: compact source scrolling, hover routes, timeline expansion, dense dice and scrolled hit assignment', flush=True)
    finally:
        w.unit_renderer.layout_executor.shutdown(wait=True, cancel_futures=True)
        w.close()


if __name__ == '__main__':
    main()
