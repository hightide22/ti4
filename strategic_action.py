"""Strategic actions resolve fully before normal turns resume."""
from dataclasses import dataclass, field
from uuid import uuid4

from player import command_tokens_in_reinforcements
from units import Unit, UnitLocation, Region


@dataclass
class StrategyResolution:
    card: int
    owner: object
    player: object
    remaining: list
    primary: bool = True
    stage: str = ''
    purchases: int = 0
    base_gain: int = 0
    payment_planets: set = field(default_factory=set)
    trade_goods: int = 0
    ready_planets: set = field(default_factory=set)
    free_trade: set = field(default_factory=set)
    structure: str = 'spacedock'
    builds_left: int = 2
    pool_source: str | None = None


class StrategyController:
    def __init__(self, board, turn, movement):
        self.board, self.turn, self.movement = board, turn, movement
        self.session = None

    @property
    def player(self):
        return self.session.player if self.session else self.turn.active_player

    def start(self, card):
        player = self.turn.active_player
        if (self.session or self.movement.session or self.turn.strategy_selection or
                self.turn.command_allocation or self.turn.action_used or player.pending_commands):
            raise ValueError('Finish the current action before playing a strategy card.')
        if (card not in self.turn.strategy_assignments[player.faction] or
                card in self.turn.strategy_used[player.faction]):
            raise ValueError('That strategy card is not ready or does not belong to you.')
        s = StrategyResolution(card, player, player, self.turn.clockwise_players_after(player))
        self.session = self.turn.strategy_resolution = s
        self.turn.strategy_used[player.faction].add(card)
        # A strategic action is a boundary for tactical undo history.
        self.movement.history.clear()
        s.stage = {1: 'leadership', 2: 'diplomacy_system', 3: 'speaker',
                   4: 'construction', 5: 'trade', 6: 'warfare_system',
                   7: 'placeholder', 8: 'placeholder'}[card]
        if card == 1:
            s.base_gain = min(3, command_tokens_in_reinforcements(player, self.board))

    def _participant_done(self):
        s = self.session
        if s.player.pending_commands:
            raise ValueError('Allocate all new command tokens first.')
        if not s.primary:
            self.turn.strategy_secondary_used[s.player.faction].add(s.card)
        if s.remaining:
            s.primary = False
            s.player = s.remaining.pop(0)
            s.stage = 'offer'
            s.base_gain = s.purchases = s.trade_goods = 0
            s.payment_planets.clear()
            s.ready_planets.clear()
            s.pool_source = None
        else:
            self.session = self.turn.strategy_resolution = None
            self.turn.mark_action_completed()

    def secondary_cost(self):
        s = self.session
        return 0 if (s.card == 1 or
                     (s.card == 5 and (s.player.faction == 'hacan' or s.player.faction in s.free_trade))) else 1

    def secondary_unavailable(self):
        s = self.session
        if s.card in (3, 7, 8):
            return 'This secondary ability is not implemented yet.'
        if s.player.command_pools['strategic'] < self.secondary_cost():
            return 'No token in the strategy pool.'
        if s.card == 1 and not command_tokens_in_reinforcements(s.player, self.board):
            return 'No command tokens remain in reinforcements.'
        if s.card == 6 and not self.home_docks():
            return 'No controlled Space Dock in your home system.'
        return ''

    def accept_secondary(self):
        s = self.session
        if s.stage != 'offer':
            raise ValueError('There is no secondary offer to accept.')
        reason = self.secondary_unavailable()
        if reason:
            raise ValueError(reason)
        # Construction puts its strategy token on the board when placing a structure;
        # Warfare pays when the player chooses a dock.
        if s.card not in (4, 6):
            s.player.command_pools['strategic'] -= self.secondary_cost()
        s.stage = {1: 'leadership', 2: 'ready_planets', 4: 'construction',
                   5: 'trade_secondary', 6: 'production_site'}[s.card]
        s.builds_left = 1
        if s.card == 5:
            s.player.commodities = s.player.commodity_limit
            self._participant_done()

    def decline_secondary(self):
        if self.session.stage != 'offer':
            raise ValueError('Finish this ability before continuing.')
        self._participant_done()

    def payment(self):
        s = self.session
        return s.trade_goods + sum(c.planet.influence for c in s.player.planets
                                  if c.planet.planet_id in s.payment_planets)

    def toggle_payment(self, planet_id):
        s = self.session
        if s.stage != 'leadership':
            return
        card = next((c for c in s.player.planets if c.planet.planet_id == planet_id), None)
        if not card:
            raise ValueError('You do not control that planet.')
        if planet_id in s.payment_planets:
            s.payment_planets.remove(planet_id)
            card.exhausted = False
        elif not card.exhausted:
            s.payment_planets.add(planet_id)
            card.exhausted = True

    def reset_payment(self):
        for planet_id in tuple(self.session.payment_planets):
            self.toggle_payment(planet_id)
        self.session.trade_goods = 0

    def change_purchase(self, delta):
        s = self.session
        remaining = command_tokens_in_reinforcements(s.player, self.board) - s.base_gain
        s.purchases = max(0, min(remaining, s.purchases + delta))

    def change_goods(self, delta):
        s = self.session
        s.trade_goods = max(0, min(s.player.trade_goods, s.trade_goods + delta))

    def pay_leadership(self):
        s = self.session
        if s.stage != 'leadership':
            raise ValueError('Leadership payment is not active.')
        if self.payment() < s.purchases * 3 or s.trade_goods > s.player.trade_goods:
            raise ValueError('Select enough planet influence and trade goods to pay.')
        if s.base_gain + s.purchases > command_tokens_in_reinforcements(s.player, self.board):
            raise ValueError('Not enough command tokens remain in reinforcements.')
        if not s.purchases:
            self.reset_payment()
        s.player.trade_goods -= s.trade_goods
        s.player.pending_commands += s.base_gain + s.purchases
        s.payment_planets.clear()
        s.stage = 'allocate'
        if not s.player.pending_commands:
            self._participant_done()

    def allocate(self, pool):
        s = self.session
        if s.stage not in ('allocate', 'warfare_allocate'):
            raise ValueError('Command allocation is not active.')
        s.player.allocate_command(pool)
        self.allocation_changed()

    def allocation_changed(self):
        if self.session.stage == 'allocate' and not self.player.pending_commands:
            self._participant_done()

    def select_system(self, position):
        s = self.session
        tile = self.board[position]
        if s.stage == 'diplomacy_system':
            if tile.number == 18 or s.player.faction not in tile.planet_owners.values():
                raise ValueError('Choose a system with a planet you control, excluding Mecatol Rex.')
            for player in self.turn.players:
                if player is s.player or player.faction in tile.command_tokens:
                    continue
                if not command_tokens_in_reinforcements(player, self.board):
                    pool = next((p for p in ('tactical', 'strategic', 'fleet') if player.command_pools[p]), None)
                    if pool is None:
                        continue
                    player.command_pools[pool] -= 1
                tile.command_tokens.add(player.faction)
            s.stage = 'ready_planets'
        elif s.stage == 'warfare_system':
            if s.player.faction not in tile.command_tokens:
                raise ValueError('Choose a system containing your command token.')
            tile.command_tokens.remove(s.player.faction)
            s.player.pending_commands += 1
            s.stage = 'warfare_allocate'
        else:
            raise ValueError('This ability does not select a system.')

    def toggle_ready(self, planet_id):
        s = self.session
        card = next((c for c in s.player.planets if c.planet.planet_id == planet_id), None)
        if s.stage != 'ready_planets' or not card or not card.exhausted:
            return
        if planet_id in s.ready_planets:
            s.ready_planets.remove(planet_id)
        elif len(s.ready_planets) < 2:
            s.ready_planets.add(planet_id)

    def confirm_ready(self):
        s = self.session
        for card in s.player.planets:
            if card.planet.planet_id in s.ready_planets:
                card.exhausted = False
        self._participant_done()

    def choose_speaker(self, faction):
        self.turn.set_speaker(next(p for p in self.turn.players if p.faction == faction))
        self._participant_done()

    def confirm_trade(self):
        s = self.session
        s.player.trade_goods += 3
        s.player.commodities = s.player.commodity_limit
        self._participant_done()

    def build(self, planet_id):
        s = self.session
        tile = next((t for t in self.board.values() if t.planet_owners.get(planet_id) == s.player.faction), None)
        if s.stage != 'construction' or tile is None:
            raise ValueError('Choose a planet you control.')
        kind = s.structure
        owned = [u for t in self.board.values() for u in t.units if u.owner == s.player.faction and u.kind == kind]
        if len(owned) >= (6 if kind == 'pds' else 3):
            raise ValueError('No structure of this type remains in reinforcements.')
        if sum(u.location.planet_id == planet_id for u in owned) >= (2 if kind == 'pds' else 1):
            raise ValueError('That planet already has the maximum number of these structures.')
        if not s.primary:
            if not s.player.command_pools['strategic']:
                raise ValueError('A strategy token is required.')
            s.player.command_pools['strategic'] -= 1
            tile.command_tokens.add(s.player.faction)
        tile.units.append(Unit(f'{s.player.faction}-structure-{uuid4().hex}', kind, s.player.faction,
                               s.player.color_code, UnitLocation(Region.PLANET, planet_id)))
        s.builds_left -= 1
        if s.builds_left:
            s.structure = 'pds'
        else:
            self._participant_done()

    def home_docks(self):
        player = self.player
        return [(tile, unit) for tile in self.board.values()
                if any(p.faction_homeworld == player.faction for p in tile.planets)
                for unit in tile.units if unit.owner == player.faction and unit.kind == 'spacedock'
                and tile.planet_owners.get(unit.location.planet_id) == player.faction]

    def produce_at(self, unit_id):
        s = self.session
        tile, dock = next((t, u) for t, u in self.home_docks() if u.unit_id == unit_id)
        if not s.player.command_pools['strategic']:
            raise ValueError('A strategy token is required.')
        s.player.command_pools['strategic'] -= 1
        self.movement.start_strategy_production(s.player, tile, dock)
        s.stage = 'production'

    def poll(self):
        if self.session and self.session.stage == 'production' and self.movement.session is None:
            self._participant_done()

    def continue_stage(self):
        s = self.session
        if s.stage in ('placeholder', 'construction', 'warfare_allocate'):
            self._participant_done()
        elif s.stage == 'warfare_system' and not any(s.player.faction in t.command_tokens for t in self.board.values()):
            s.stage = 'warfare_allocate'
        elif s.stage == 'diplomacy_system' and not any(
                s.player.faction in t.planet_owners.values() and t.number != 18 for t in self.board.values()):
            self._participant_done()
        else:
            raise ValueError('Complete the current step first.')
