"""GUI smoke test: bot choices stay private while a human can defend."""
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import arcade

from app import BoardWindow
from units import Region, Unit, UnitLocation


def main():
    window = BoardWindow(smoke=False, show_menu=True)
    try:
        window.switch_to()
        window.main_menu.vs_ai = True
        window.main_menu.human_slot = 2
        window.start_from_menu()
        sol = next(player for player in window.turn_order.players if player.faction == 'sol')
        hacan = next(player for player in window.turn_order.players if player.faction == 'hacan')
        sol.action_cards[:] = ['morale_boost']
        hacan.action_cards[:] = ['morale_boost']
        bot_details = ' '.join(line for line, _ in window.roster.detail_lines(window, sol))
        human_details = ' '.join(line for line, _ in window.roster.detail_lines(window, hacan))
        assert 'Hidden until played' in bot_details and 'Morale Boost' not in bot_details
        assert 'Morale Boost' in human_details
        window.on_draw()
        assert window.bot_actor and window.action_card_button_hit is None
        assert window.turn_button_hit is None and not window.strategy_panel.hits
        window.action_card_panel.open = True
        window.on_draw()
        assert not window.action_card_panel.hits and window.action_card_panel.bounds is None
        window.action_card_panel.open = False

        window.turn_order.strategy_selection = False
        origin = next(tile for tile in window.board.values() if any(
            unit.owner == 'sol' and unit.kind == 'carrier' for unit in tile.units))
        target = next(tile for tile in window.board.neighbors(origin.position)
                      if not tile.anomalies and tile.planets)
        target.units.append(Unit('visibility-human-cruiser', 'cruiser', 'hacan',
                                 hacan.color_code, UnitLocation(Region.SPACE)))
        session = window.movement.activate(sol, target.position)
        for ship in session.sources[origin.position].ships:
            session.toggle(ship.unit_id)
        window.movement.confirm()
        assert session.stage == 'space_combat'
        window.on_draw()
        assert not window.movement_panel.hits and not window.movement_panel.buttons
        assert window.action_card_button_hit is not None
        assert any(action[0] == ('action_cards',) for action in window.combat_panel.action_hits)
        window.action_card_panel.open = True
        window.on_draw()
        assert window.action_card_panel.player_faction == 'hacan'
        assert not any(action[0][0] == 'player' for action in window.action_card_panel.hits)
        preview = Path(__file__).resolve().parents[1] / 'previews' / 'ai-human-defense.png'
        preview.parent.mkdir(exist_ok=True)
        arcade.get_image().save(preview)
        window.action_card_panel.open = False

        with patch('movement.random.randint', return_value=10):
            window.movement.advance_combat()
        window.on_draw()
        assert session.combat_needs_resolution
        assert any(action[0][:2] == ('assign_hit', 'hacan')
                   for action in window.combat_panel.assignment_hits)
        assert not any(action[0][:2] == ('assign_hit', 'sol')
                       for action in window.combat_panel.assignment_hits)
        human_hit = next(action for action in window.combat_panel.assignment_hits
                         if action[0][:2] == ('assign_hit', 'hacan'))
        window.on_mouse_press((human_hit[1] + human_hit[2]) / 2,
                              (human_hit[3] + human_hit[4]) / 2,
                              arcade.MOUSE_BUTTON_LEFT, 0)
        assert session.combat_assignments['hacan']
        print('PASS: bot controls and cards hidden; human combat cards and hit assignment available')
    finally:
        window.close()


if __name__ == '__main__':
    main()
