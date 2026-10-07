"""Player-to-player transactions, including Hacan's trade exceptions."""
from dataclasses import dataclass, field


class TransactionError(ValueError):
    pass


@dataclass
class Transaction:
    initiator: object
    partner: object | None = None
    give_trade_goods: int = 0
    give_commodities: int = 0
    take_trade_goods: int = 0
    take_commodities: int = 0
    give_action_cards: set[str] = field(default_factory=set)
    take_action_cards: set[str] = field(default_factory=set)


class TransactionController:
    def __init__(self, board, players, turn_order, movement):
        self.board = board
        self.players = list(players)
        self.turn_order = turn_order
        self.movement = movement
        self.session: Transaction | None = None
        self.used_partners: set[str] = set()
        self.turn_serial = turn_order.turn_serial

    def _sync_turn(self):
        if self.turn_serial != self.turn_order.turn_serial:
            self.turn_serial = self.turn_order.turn_serial
            self.used_partners.clear()

    @staticmethod
    def _has_units(tile, faction):
        return faction in tile.planet_owners.values() or any(unit.owner == faction for unit in tile.units)

    def are_neighbors(self, first, second):
        first_systems = [tile for tile in self.board.values() if self._has_units(tile, first)]
        second_systems = [tile for tile in self.board.values() if self._has_units(tile, second)]
        second_positions = {tile.position for tile in second_systems}
        for tile in first_systems:
            if tile.position in second_positions:
                return True
            if any(neighbor.position in second_positions for neighbor in self.movement.neighbors(tile)):
                return True
        return False

    def can_transact(self, initiator, partner):
        if initiator is partner or partner not in self.players:
            return False
        self._sync_turn()
        if partner.faction in self.used_partners:
            return False
        return (initiator.faction == 'hacan' or partner.faction == 'hacan' or
                self.are_neighbors(initiator.faction, partner.faction))

    def eligible_partners(self, initiator):
        return [player for player in self.players if self.can_transact(initiator, player)]

    def open(self):
        if self.session:
            raise TransactionError('A transaction is already open')
        if self.turn_order.strategy_selection or self.turn_order.strategy_resolution or self.turn_order.command_allocation:
            raise TransactionError('Finish the current phase before trading')
        self._sync_turn()
        self.session = Transaction(self.turn_order.active_player)

    def choose_partner(self, faction):
        if not self.session or self.session.partner:
            raise TransactionError('Choose a trading partner first')
        partner = next((player for player in self.players if player.faction == faction), None)
        if not partner or not self.can_transact(self.session.initiator, partner):
            raise TransactionError('This player is not eligible for a transaction')
        self.session.partner = partner

    def change(self, field_name, delta):
        if not self.session or not self.session.partner or field_name not in {
                'give_trade_goods', 'give_commodities', 'take_trade_goods', 'take_commodities'}:
            raise TransactionError('Choose a partner before changing an offer')
        s = self.session
        sender, attribute = (s.initiator, field_name[5:]) if field_name.startswith('give_') else (
            s.partner, field_name[5:])
        amount = getattr(s, field_name)
        setattr(s, field_name, max(0, min(getattr(sender, attribute), amount + delta)))

    def toggle_action_card(self, faction, card_id):
        if not self.session or not self.session.partner:
            raise TransactionError('Choose a partner before offering cards')
        s = self.session
        if 'hacan' not in (s.initiator.faction, s.partner.faction):
            raise TransactionError('Hacan Arbiters are required to trade action cards')
        if faction == s.initiator.faction:
            hand, selected = s.initiator.action_cards, s.give_action_cards
        elif faction == s.partner.faction:
            hand, selected = s.partner.action_cards, s.take_action_cards
        else:
            raise TransactionError('This card does not belong to either player')
        if card_id not in hand:
            raise TransactionError('That action card is no longer in this hand')
        selected.symmetric_difference_update((card_id,))

    def confirm(self):
        if not self.session or not self.session.partner:
            raise TransactionError('Choose a trading partner first')
        s = self.session
        if not self.can_transact(s.initiator, s.partner):
            raise TransactionError('This transaction is no longer available')
        if not any((s.give_trade_goods, s.give_commodities, s.take_trade_goods,
                    s.take_commodities, s.give_action_cards, s.take_action_cards)):
            raise TransactionError('Add something to the transaction first')
        for player, trade_goods, commodities, cards in (
                (s.initiator, s.give_trade_goods, s.give_commodities, s.give_action_cards),
                (s.partner, s.take_trade_goods, s.take_commodities, s.take_action_cards)):
            if trade_goods > player.trade_goods or commodities > player.commodities or not cards.issubset(
                    set(player.action_cards)):
                raise TransactionError('An offered item is no longer available')
        if (s.initiator.commodities - s.give_commodities + s.take_commodities >
                s.initiator.commodity_limit or
                s.partner.commodities - s.take_commodities + s.give_commodities >
                s.partner.commodity_limit):
            raise TransactionError('The recipient has no room for those commodities')
        s.initiator.trade_goods += s.take_trade_goods - s.give_trade_goods
        s.partner.trade_goods += s.give_trade_goods - s.take_trade_goods
        s.initiator.commodities += s.take_commodities - s.give_commodities
        s.partner.commodities += s.give_commodities - s.take_commodities
        s.initiator.action_cards[:] = [card for card in s.initiator.action_cards
                                       if card not in s.give_action_cards]
        s.partner.action_cards[:] = [card for card in s.partner.action_cards
                                     if card not in s.take_action_cards]
        s.initiator.action_cards.extend(s.take_action_cards)
        s.partner.action_cards.extend(s.give_action_cards)
        self.used_partners.add(s.partner.faction)
        self.session = None

    def cancel(self):
        self.session = None
