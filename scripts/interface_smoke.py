"""Exercise dense, scrollable UI states at the minimum supported window size."""
from pathlib import Path
from copy import copy
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import arcade
from app import BoardWindow
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
