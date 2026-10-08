"""Render visible misses for Space Cannon and Bombardment in the game UI."""
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import arcade

from app import BoardWindow
from units import Region, Unit, UnitLocation


def main():
    previews = Path(__file__).resolve().parents[1] / 'previews'
    previews.mkdir(exist_ok=True)
    window = BoardWindow()
    try:
        window.set_size(1440, 900)
        window.dispatch_events()
        window.switch_to()
        window.turn_order.strategy_selection = False
        player = next(player for player in window.turn_order.players if player.faction == 'sol')
        home = next(tile for tile in window.board.values()
                    if any(unit.owner == 'sol' and unit.kind == 'carrier' for unit in tile.units))
        target = next(tile for tile in window.movement.neighbors(home)
                      if tile.planets and any(window.movement.route(home, tile, unit, player)
                                              for unit in home.units if unit.kind == 'carrier'))
        planet = target.planets[0]
        cannon = Unit('dice-smoke-pds', 'pds', 'hacan', 'ylw',
                      UnitLocation(Region.PLANET, planet.planet_id))
        target.units.append(cannon)

        session = window.movement.activate(player, target.position)
        carrier = next(unit for unit in session.sources[home.position].ships if unit.kind == 'carrier')
        session.toggle(carrier.unit_id)
        with patch('movement.random.randint', return_value=1):
            window.movement.confirm()
        window.on_draw()
        assert '1-1=0 miss' in window.labels[('action_result_detail', 0)].text
        arcade.get_image().save(previews / 'space-cannon-miss.png')

        window.movement.cancel()
        target.units.remove(cannon)
        target.planet_owners[planet.planet_id] = 'hacan'
        defender = Unit('dice-smoke-defender', 'infantry', 'hacan', 'ylw',
                        UnitLocation(Region.PLANET, planet.planet_id))
        bomber = Unit('dice-smoke-bomber', 'dreadnought', 'sol', player.color_code,
                      UnitLocation(Region.SPACE))
        target.units.extend((defender, bomber))
        session = window.movement.activate(player, target.position)
        window.movement.confirm()
        assert session.stage == 'bombardment'
        window.movement.cycle_bombardment_target(bomber.unit_id)
        with patch('movement.random.randint', return_value=1):
            window.movement.resolve_bombardment()
        window.on_draw()
        assert defender in target.units
        assert '1 miss' in window.labels[('action_result_detail', 0)].text
        arcade.get_image().save(previews / 'bombardment-miss.png')
        print('PASS: Space Cannon and Bombardment misses are visible in the game UI')
    finally:
        window.close()


if __name__ == '__main__':
    main()
