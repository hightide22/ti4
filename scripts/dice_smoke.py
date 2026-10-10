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
        assert '1 miss' in window.labels[('action_result_detail', 0)].text
        arcade.get_image().save(previews / 'space-cannon-miss.png')

        window.movement.cancel()
        target.units.remove(cannon)
        upgraded_pds = Unit('dice-smoke-pds-ii', 'pds', 'sol', player.color_code,
                            UnitLocation(Region.PLANET, home.planets[0].planet_id),
                            profile_id='pds2')
        first_enemy = next(p for p in window.turn_order.players if p.faction != 'sol')
        second_enemy = next(p for p in window.turn_order.players
                            if p.faction not in ('sol', first_enemy.faction))
        for owner in window.turn_order.players:
            owner.action_cards = []
        first_ship = Unit('dice-smoke-first-ship', 'cruiser', first_enemy.faction,
                          first_enemy.color_code, UnitLocation(Region.SPACE))
        second_ship = Unit('dice-smoke-second-ship', 'carrier', second_enemy.faction,
                           second_enemy.color_code, UnitLocation(Region.SPACE))
        home.units.append(upgraded_pds)
        target.units.extend((first_ship, second_ship))
        window.player_panel.active = window.player_panel.players.index(player)
        cannon_session = window.movement.activate(player, target.position)
        with patch.object(window.action_cards, 'can_play', return_value=False), \
                patch('movement.random.randint', return_value=10):
            window.movement.confirm()
        assert cannon_session.stage == 'space_cannon_choose_target'
        window.on_draw()
        assert window.labels['combat_modal_title'].text == 'SPACE CANNON OFFENSE'
        assert ('  Space Cannon Offense', 'Current') in window.movement_panel.timeline_rows
        choose = next(hit for hit in window.combat_panel.action_hits
                      if hit[0] == ('space_cannon_choose', first_enemy.faction))
        with patch.object(window.action_cards, 'can_play', return_value=False), \
                patch('movement.random.randint', return_value=10):
            window.on_mouse_press((choose[1] + choose[2]) / 2, (choose[3] + choose[4]) / 2,
                                  arcade.MOUSE_BUTTON_LEFT, 0)
        assert cannon_session.stage == 'space_cannon_assign'
        window.on_draw()
        assert window.labels['combat_modal_title'].text == 'SPACE CANNON OFFENSE'
        casualty = next(hit for hit in window.combat_panel.action_hits
                        if hit[0] == ('space_cannon_target', first_ship.unit_id))
        with patch.object(window.action_cards, 'can_play', return_value=False):
            window.on_mouse_press((casualty[1] + casualty[2]) / 2,
                                  (casualty[3] + casualty[4]) / 2,
                                  arcade.MOUSE_BUTTON_LEFT, 0)
        assert first_ship not in target.units and second_ship in target.units
        arcade.get_image().save(previews / 'space-cannon-pds-ii.png')
        window.movement.cancel()
        home.units.remove(upgraded_pds)
        target.units.remove(first_ship)
        target.units.remove(second_ship)

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
        print('PASS: PDS II target selection and hit assignment, Space Cannon misses, and Bombardment misses work in the game UI')
    finally:
        window.close()


if __name__ == '__main__':
    main()
