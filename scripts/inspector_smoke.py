"""Verify the main branch's inspector can collapse and reopen without moving the map."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import arcade
from app import BoardWindow, COLLAPSED_SIDEBAR_WIDTH


def click_toggle(window):
    left, right, bottom, top = window.inspector_toggle_hit
    window.on_mouse_press((left + right) / 2, (bottom + top) / 2,
                          arcade.MOUSE_BUTTON_LEFT, 0)


def main():
    window = BoardWindow(smoke=True)
    try:
        window.set_size(1440, 900)
        window.dispatch_events()
        window.switch_to()
        window.on_draw()
        previews = Path(__file__).resolve().parents[1] / 'previews'
        previews.mkdir(exist_ok=True)
        arcade.get_image().save(previews / 'inspector-expanded.png')

        map_center = tuple(window.map_center)
        zoom = window.zoom
        target_zoom = window.target_zoom
        assert window.inspector_visible
        assert window.sidebar > COLLAPSED_SIDEBAR_WIDTH
        click_toggle(window)
        assert not window.inspector_visible
        assert window.sidebar == COLLAPSED_SIDEBAR_WIDTH
        assert tuple(window.map_center) == map_center
        assert window.zoom == zoom
        assert window.target_zoom == target_zoom

        window.on_draw()
        arcade.get_image().save(previews / 'inspector-collapsed.png')
        assert window.system_panel.strategy_tab_hit is None
        assert not window.system_panel.hits
        position = window.selected
        assert window.pick(*window.screen(position)) == position
        click_toggle(window)
        assert window.inspector_visible
        assert tuple(window.map_center) == map_center
        assert window.zoom == zoom
        assert window.target_zoom == target_zoom

        window.on_draw()
        assert window.system_panel.strategy_tab_hit is not None
        print('PASS: inspector collapses and reopens, clearing hidden panel hits while preserving map view', flush=True)
    finally:
        window.unit_renderer.layout_executor.shutdown(wait=True, cancel_futures=True)
        window.close()


if __name__ == '__main__':
    main()
