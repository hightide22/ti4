"""Shared component-supply checks for units placed on the board."""

INFANTRY_SUPPLY = 12


def infantry_count(player, board):
    """Count infantry on the board and infantry temporarily stored on unit cards."""
    on_board = sum(unit.owner == player.faction and unit.kind == 'infantry'
                   for tile in board.values() for unit in tile.units)
    on_cards = sum(getattr(player, 'infantry_on_cards', {}).values())
    return on_board + on_cards


def infantry_remaining(player, board):
    return max(0, INFANTRY_SUPPLY - infantry_count(player, board))
