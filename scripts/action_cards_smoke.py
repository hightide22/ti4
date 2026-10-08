"""GUI smoke test for action-card timing highlights and card play."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import arcade

import app as app_module
from app import BoardWindow
from movement import capital_ship
from units import Region, Unit, UnitLocation
from ui_theme import VARIANT


def click(window, rect):
    window.on_mouse_press((rect[1] + rect[2]) / 2, (rect[3] + rect[4]) / 2,
                          arcade.MOUSE_BUTTON_LEFT, 0)
    window.on_draw()


def hover(window, rect):
    x, y = (rect[1] + rect[2]) / 2, (rect[3] + rect[4]) / 2
    window.on_mouse_motion(x, y, 0, 0)
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
        target = next(tile for tile in window.board.values()
                      if not tile.command_tokens and
                      not any(window.movement.route(origin, tile, unit, player)
                              for origin in window.board.values() for unit in origin.units
                              if unit.owner == player.faction and
                              capital_ship(unit)) and
                      any(window.movement.route(origin, tile, unit, player, move_bonus=1)
                          for origin in window.board.values() for unit in origin.units
                          if unit.owner == player.faction and
                          capital_ship(unit)))
        player.action_cards[:] = ['flank_speed']
        window.movement.activate(player, target.position)
        window.selected = target.position
        assert not window.movement.session.sources
        real_button = app_module.button
        action_button_styles = []

        def track_action_button(*args, **kwargs):
            if args[1] == 'action_card_hand_button':
                action_button_styles.append(kwargs.get('primary', False))
            return real_button(*args, **kwargs)

        app_module.button = track_action_button
        try:
            window.on_draw()
            assert action_button_styles and action_button_styles[-1]
            arcade.get_image().save(output / 'action-card-window-highlight.png')
        finally:
            app_module.button = real_button
        control = next(hit for hit in window.movement_panel.buttons if hit[0] == ('action_cards',))
        click(window, control)
        assert window.action_card_panel.open
        target_label = window.labels['move_target']
        target_layout = (target_label.x, target_label.y, target_label.font_size)
        playable = next(hit for hit in window.action_card_panel.hits if hit[0] == ('play', 0))
        arcade.get_image().save(output / 'action-cards-movement.png')
        click(window, playable)
        target_label = window.labels['move_target']
        assert (target_label.x, target_label.y, target_label.font_size) == target_layout
        assert window.movement.session.movement_bonus == 1
        assert window.movement.session.sources
        assert player.action_cards == []

        player.action_cards[:] = ['flank_speed', 'morale_boost', 'fighter_prototype',
                                  'shields_holding', 'emergency_repairs', 'skilled_retreat',
                                  'flank_speed']
        window.action_card_panel.open = True
        window.on_mouse_motion(-1, -1, 0, 0)
        window.on_draw()
        fan = window.action_card_panel.card_bounds
        assert len(fan) == 7
        assert fan[0][1] >= window.action_card_panel.bounds[0]
        assert fan[-1][2] <= window.action_card_panel.bounds[1]
        assert all(left[2] > right[1] for left, right in zip(fan, fan[1:]))
        assert {'flank_speed', 'morale_boost', 'fighter_prototype', 'shields_holding',
                'emergency_repairs', 'skilled_retreat'} <= set(window.action_card_panel._textures)
        arcade.get_image().save(output / 'action-cards-full-hand.png')
        window.action_card_panel.open = False
        player.action_cards.clear()

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
        assert {'morale_boost', 'fighter_prototype'} <= set(window.action_card_panel._textures)
        first_card = window.action_card_panel.card_bounds[0]
        hover(window, first_card)
        assert window.action_card_panel.hovered_card_index == first_card[0]
        lifted_hit = next(hit for hit in window.action_card_panel.hits if hit[0] == ('play', first_card[0]))
        assert lifted_hit[2] - lifted_hit[1] > first_card[2] - first_card[1]
        arcade.get_image().save(output / 'action-cards-combat.png')
        play_button = lifted_hit
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
