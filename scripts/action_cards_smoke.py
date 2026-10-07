"""GUI smoke test for action-card timing highlights and card play."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import arcade

from app import BoardWindow
from units import Region, Unit, UnitLocation
from ui_theme import VARIANT


def click(window, rect):
    window.on_mouse_press((rect[1] + rect[2]) / 2, (rect[3] + rect[4]) / 2,
                          arcade.MOUSE_BUTTON_LEFT, 0)
    window.on_draw()


def main():
    output = Path(__file__).resolve().parents[1] / 'previews' / VARIANT.lower()
    output.mkdir(parents=True, exist_ok=True)
    window = BoardWindow(smoke=True)
    try:
        window.set_size(1120, 720)
        window.dispatch_events()
        window.switch_to()
        player = next(p for p in window.player_panel.players if p.faction == 'sol')
        enemy = next(p for p in window.player_panel.players if p.faction == 'hacan')
        window.player_panel.active = window.player_panel.players.index(player)
        home = next(tile for tile in window.board.values()
                    if any(unit.owner == player.faction for unit in tile.units))
        target = next(tile for tile in window.movement.neighbors(home)
                      if not tile.command_tokens and
                      any(window.movement.route(home, tile, unit, player)
                          for unit in home.units if unit.owner == player.faction))
        player.action_cards[:] = ['flank_speed']
        window.movement.activate(player, target.position)
        window.selected = target.position
        window.on_draw()
        control = next(hit for hit in window.movement_panel.buttons if hit[0] == ('action_cards',))
        click(window, control)
        assert window.action_card_panel.open
        playable = next(hit for hit in window.action_card_panel.hits if hit[0] == ('play', 0))
        arcade.get_image().save(output / 'action-cards-movement.png')
        click(window, playable)
        assert window.movement.session.movement_bonus == 1
        assert player.action_cards == []

        session = window.movement.session
        session.stage = 'space_combat'
        session.combat_type = 'space'
        session.combat_factions = ('sol', 'hacan')
        session.combat_round = 0
        session.combat_rolls.clear()
        target.units.extend((
            Unit('smoke-action-fighter', 'fighter', player.faction, player.color_code,
                 UnitLocation(Region.SPACE)),
            Unit('smoke-action-opponent', 'carrier', enemy.faction, enemy.color_code,
                 UnitLocation(Region.SPACE)),
        ))
        player.action_cards[:] = ['morale_boost', 'fighter_prototype']
        window.on_draw()
        combat_button = next(hit for hit in window.combat_panel.action_hits
                             if hit[0] == ('action_cards',))
        click(window, combat_button)
        assert window.action_card_panel.open
        assert any(hit[0] == ('play', 0) for hit in window.action_card_panel.hits)
        arcade.get_image().save(output / 'action-cards-combat.png')
        play_button = next(hit for hit in window.action_card_panel.hits if hit[0] == ('play', 0))
        click(window, play_button)
        assert session.combat_modifiers[player.faction] == 1
        assert player.action_cards == ['fighter_prototype']

        damaged = Unit('smoke-action-dread', 'dreadnought', player.faction, player.color_code,
                       UnitLocation(Region.SPACE), damaged=True)
        target.units.append(damaged)
        session.stage = 'combat_end'
        session.combat_needs_resolution = False
        player.action_cards[:] = ['emergency_repairs']
        window.on_draw()
        end_button = next(hit for hit in window.combat_panel.action_hits
                          if hit[0] == ('action_cards',))
        click(window, end_button)
        repair_button = next(hit for hit in window.action_card_panel.hits if hit[0] == ('play', 0))
        arcade.get_image().save(output / 'action-cards-round-end.png')
        click(window, repair_button)
        assert not damaged.damaged
        print('Action-card movement and combat UI smoke passed.')
    finally:
        window.close()


if __name__ == '__main__':
    main()
