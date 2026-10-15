"""Render and interact with each action-relic modal without launching a game."""
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import arcade

from board import Board, TILES
from exploration import ExplorationController
from player import PlanetCard, PlayerState
from relic_action_panel import RelicActionPanel
from ui_theme import FONT
from units import Region, Unit, UnitLocation


class Harness(arcade.Window):
    def __init__(self):
        super().__init__(width=1200, height=800, visible=False,
                         title='Action relic smoke')
        self.player_panel = SimpleNamespace(HEIGHT=172)
        self.mouse_position = (-1, -1)
        self.labels = {}

    def text(self, key, value, x, y, size=14, color=(223, 232, 244),
             width=None, max_width=None):
        label = self.labels.get(key)
        if label is None:
            label = arcade.Text('', x, y, color, size, font_name=FONT,
                                width=width, multiline=width is not None)
            self.labels[key] = label
        label.x, label.y, label.color = x, y, color
        label.font_size = size
        label.text = value
        label.multiline = width is not None
        if width is not None:
            label.width = max(1, int(width))
        label.draw()


def click(panel, exploration, deck, action):
    rect = next(hit for hit in panel.hits if hit[0] == action)
    x, y = (rect[1] + rect[2]) / 2, (rect[3] + rect[4]) / 2
    assert panel.hit_test(x, y) == action
    return panel.handle(action, exploration, deck)


def main():
    home = TILES.create(1, (0, 0))
    nearby = TILES.create(30, (1, 0))
    board = Board((home, nearby))
    player = PlayerState('sol', 'Sol', 'blu', 4,
                         [PlanetCard(planet) for planet in
                          (*home.planets, *nearby.planets)])
    rival = PlayerState('hacan', 'Hacan', 'ylw', 6)
    for tile in (home, nearby):
        for planet in tile.planets:
            tile.planet_owners[planet.planet_id] = player.faction
    engine = ExplorationController(board, (player, rival), relic_deck=[])
    deck = SimpleNamespace(discard=['fs1', 'mb1', 'dh1'],
                           name_for=lambda alias: alias.upper())
    panel = RelicActionPanel()
    window = Harness()
    try:
        window.switch_to()
        for alias in ('codex', 'nanoforge', 'titanprototype'):
            panel.begin(alias, player)
            window.clear()
            panel.draw(window, player, engine, deck)
            assert panel.bounds and panel.hits
            if alias == 'codex':
                click(panel, engine, deck, ('card', 0))
                window.clear()
                panel.draw(window, player, engine, deck)
                assert click(panel, engine, deck, ('confirm',)) == \
                    ('use_relic', 'codex', ('fs1',))
            elif alias == 'nanoforge':
                click(panel, engine, deck, ('planet', nearby.planets[0].planet_id))
                window.clear()
                panel.draw(window, player, engine, deck)
                assert click(panel, engine, deck, ('confirm',)) == \
                    ('use_relic', 'nanoforge', nearby.planets[0].planet_id)
            else:
                click(panel, engine, deck, ('planet', nearby.planets[1].planet_id))
                window.clear()
                panel.draw(window, player, engine, deck)
                click(panel, engine, deck, ('structure', 'pds'))
                click(panel, engine, deck, ('pay_planet', nearby.planets[1].planet_id))
                # Zohbat pays all three resources. Draw the completed payment state.
                window.clear()
                panel.draw(window, player, engine, deck)
                output = Path(__file__).resolve().parents[1] / 'previews' / 'relic-action-smoke.png'
                output.parent.mkdir(parents=True, exist_ok=True)
                arcade.get_image().save(output)
                assert click(panel, engine, deck, ('confirm',)) == \
                    ('use_relic', 'titanprototype', 'sol',
                     nearby.planets[1].planet_id, 'pds',
                     (nearby.planets[1].planet_id,), 0)

        for planet in nearby.planets:
            nearby.planet_owners[planet.planet_id] = 'hacan'
        home.units.append(Unit('smoke-dread', 'dreadnought', 'sol', 'blu',
                               UnitLocation(Region.SPACE)))
        panel.begin('stellarconverter', player)
        window.clear()
        panel.draw(window, player, engine, deck)
        click(panel, engine, deck, ('planet', nearby.planets[0].planet_id))
        window.clear()
        panel.draw(window, player, engine, deck)
        assert click(panel, engine, deck, ('confirm',)) == \
            ('use_relic', 'stellarconverter', nearby.planets[0].planet_id)
        assert panel.begin('dynamiscore', player) == ('use_relic', 'dynamiscore')

        player.action_cards[:] = [f'hand-{index}' for index in range(9)]
        assert panel.begin_discard(player)
        window.clear()
        panel.draw(window, player, engine, deck)
        assert not any(hit[0] == ('close',) for hit in panel.hits)
        assert panel.handle(('close',), engine, deck) is None and panel.open
        click(panel, engine, deck, ('card', 1))
        click(panel, engine, deck, ('card', 5))
        window.clear()
        panel.draw(window, player, engine, deck)
        assert click(panel, engine, deck, ('confirm',)) == \
            ('discard_to_limit', 'sol', ((1, 'hand-1'), (5, 'hand-5')))
        print('Action relic UI smoke passed.')
    finally:
        window.close()


if __name__ == '__main__':
    main()
