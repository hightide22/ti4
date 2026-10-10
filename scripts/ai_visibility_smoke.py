"""GUI smoke test: bot choices stay private while a human can defend."""
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import arcade

from app import BoardWindow
from units import Region, UNIT_TYPES, Unit, UnitLocation


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
        refuge = next(tile for tile in window.board.neighbors(target.position)
                      if tile is not origin and not any(
                          unit.owner != 'hacan' and unit.location.region == Region.SPACE and
                          UNIT_TYPES[unit.kind]['ship']
                          for unit in tile.units))
        refuge.units.append(Unit('visibility-human-refuge', 'cruiser', 'hacan',
                                 hacan.color_code, UnitLocation(Region.SPACE)))
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

        session.stage = 'space_combat'
        session.combat_needs_resolution = False
        session.combat_round = 0
        session.combat_hits.clear()
        session.combat_rolls.clear()
        session.combat_assignments.clear()
        session.retreat_announced = None
        session.retreat_declined_round = None
        window.ai.resolve_movement()
        assert session.stage == 'space_combat' and not session.combat_round
        window.on_draw()
        announce = next(hit for hit in window.combat_panel.action_hits
                        if hit[0] == ('announce_retreat', hacan.faction))
        assert any(hit[0] == ('decline_retreat', hacan.faction)
                   for hit in window.combat_panel.action_hits)
        window.on_mouse_press((announce[1] + announce[2]) / 2,
                              (announce[3] + announce[4]) / 2,
                              arcade.MOUSE_BUTTON_LEFT, 0)
        assert session.retreat_announced == hacan.faction
        with patch('movement.random.randint', return_value=1):
            window.movement.advance_combat()
            window.movement.advance_combat()
        assert session.stage == 'retreat_selection'
        window.on_draw()
        retreat_hits = [hit for hit in window.combat_panel.action_hits
                        if hit[0][0] == 'retreat_to']
        assert retreat_hits and window.bot_actor
        chosen = next(hit for hit in retreat_hits if hit[0][1] == refuge.position)
        window.on_mouse_press((chosen[1] + chosen[2]) / 2,
                              (chosen[3] + chosen[4]) / 2,
                              arcade.MOUSE_BUTTON_LEFT, 0)
        assert session.stage != 'retreat_selection'
        assert any(unit.owner == hacan.faction and unit.kind == 'cruiser'
                   for unit in refuge.units)

        human_cruiser = next(unit for unit in refuge.units
                             if unit.unit_id == 'visibility-human-cruiser')
        refuge.units.remove(human_cruiser)
        target.units.append(human_cruiser)
        session.stage = 'space_combat'
        session.combat_needs_resolution = False
        session.combat_round = 0
        session.combat_hits.clear()
        session.combat_rolls.clear()
        session.combat_assignments.clear()
        session.retreat_announced = None
        session.retreat_declined_round = None
        session.space_combat_resolved = False
        session.skilled_retreat = False
        hacan.action_cards[:] = ['skilled_retreat']
        window.action_cards.play(hacan, 0)
        assert session.stage == 'retreat_selection'
        window.on_draw()
        skilled_hits = [hit for hit in window.combat_panel.action_hits
                        if hit[0][0] == 'retreat_to']
        assert skilled_hits
        skilled_choice = skilled_hits[-1]
        window.on_mouse_press((skilled_choice[1] + skilled_choice[2]) / 2,
                              (skilled_choice[3] + skilled_choice[4]) / 2,
                              arcade.MOUSE_BUTTON_LEFT, 0)
        assert session.stage != 'retreat_selection'
        assert any(unit.owner == hacan.faction and unit.kind == 'cruiser'
                   for unit in window.board[skilled_choice[0][1]].units)
        print('PASS: bot combat waits for a human defender; ordinary and Skilled Retreat destinations are selectable')
    finally:
        window.close()


if __name__ == '__main__':
    main()
