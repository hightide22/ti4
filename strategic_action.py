"""Strategic actions resolve fully before normal turns resume."""
from dataclasses import dataclass, field
from uuid import uuid4

from player import command_tokens_in_reinforcements
from technology import (available_technologies, is_unit_upgrade, missing_prerequisites,
                        research_technology, upgrade_profile)
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
    technology_selected: str | None = None
    technology_count: int = 0
    technology_brilliant: bool = False
    technology_planets: set[str] = field(default_factory=set)
    technology_use_aida: bool = False
    selected_system: tuple[int, int] | None = None
    pending_system: tuple[int, int] | None = None
    drawn_cards: list[str] = field(default_factory=list)


class StrategyController:
    def __init__(self, board, turn, movement, action_cards=None):
        self.board, self.turn, self.movement = board, turn, movement
        self.action_cards = action_cards
        self.session = None

    @property
    def player(self):
        return self.session.player if self.session else self.turn.active_player

    def start(self, card):
        player = self.turn.active_player
        if (self.session or self.movement.session or self.turn.strategy_selection or
                self.turn.qdn_pending or
                self.turn.command_allocation or not self.turn.can_take_action or player.pending_commands):
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
                   7: 'technology', 8: 'placeholder'}[card]
        if card == 1:
            s.base_gain = min(3, command_tokens_in_reinforcements(player, self.board))
        elif card == 3 and self.action_cards:
            s.drawn_cards = self.action_cards.draw(player, 2)

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
            s.technology_planets.clear()
            s.technology_selected = None
            s.technology_count = 0
            s.technology_brilliant = False
            s.technology_use_aida = False
            s.ready_planets.clear()
            s.pool_source = None
            s.selected_system = None
            s.pending_system = None
        else:
            self.session = self.turn.strategy_resolution = None
            self.turn.mark_action_completed()

    def secondary_cost(self):
        s = self.session
        return 0 if (s.card == 1 or
                     (s.card == 5 and (s.player.faction == 'hacan' or s.player.faction in s.free_trade))) else 1

    def secondary_unavailable(self):
        s = self.session
        if s.card == 8:
            return 'This secondary ability is not implemented yet.'
        if s.player.command_pools['strategic'] < self.secondary_cost():
            return 'No token in the strategy pool.'
        if s.card == 1 and not command_tokens_in_reinforcements(s.player, self.board):
            return 'No command tokens remain in reinforcements.'
        if s.card == 2 and not self.readyable_planets(s.player):
            return 'No exhausted planets to ready.'
        if s.card == 3 and (not self.action_cards or not self.action_cards.can_draw(s.player, 2)):
            return 'Your hand is full or the action-card deck is empty.'
        if s.card == 6 and not self.home_docks():
            return 'No controlled Space Dock in your home system.'
        return ''

    def accept_secondary(self, brilliant=False):
        s = self.session
        if s.stage != 'offer':
            raise ValueError('There is no secondary offer to accept.')
        reason = self.secondary_unavailable()
        if reason:
            raise ValueError(reason)
        if brilliant and (s.card != 7 or s.player.faction != 'jolnar'):
            raise ValueError('Only Jol-Nar may use Brilliant with Technology.')
        # Construction puts its strategy token on the board when placing a structure;
        # Warfare pays when the player chooses a dock.
        if s.card not in (4, 6):
            s.player.command_pools['strategic'] -= self.secondary_cost()
        s.stage = {1: 'leadership', 2: 'diplomacy_secondary_system', 3: 'action_cards',
                   4: 'construction', 5: 'trade_secondary', 6: 'production_site',
                   7: 'technology'}[s.card]
        s.technology_brilliant = brilliant
        s.builds_left = 1
        if s.card == 3:
            s.drawn_cards = self.action_cards.draw(s.player, 2)
            self._participant_done()
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

    def technology_cost(self):
        s = self.session
        if s.primary or s.technology_brilliant:
            return 0 if s.technology_count == 0 else 6
        return 4

    def technology_payment(self):
        s = self.session
        return s.trade_goods + sum(card.planet.resources for card in s.player.planets
                                   if card.planet.planet_id in s.payment_planets)

    def select_technology(self, alias):
        s = self.session
        if not s or s.stage != 'technology':
            raise ValueError('Technology research is not active.')
        if alias is not None and (not any(card['alias'] == alias for card in available_technologies(s.player)) or
                                  alias in s.player.technologies):
            raise ValueError('This technology is unavailable or already researched.')
        s.technology_selected = alias
        s.technology_planets.clear()
        s.technology_use_aida = False

    def toggle_technology_planet(self, planet_id, specialty=False):
        s = self.session
        if not s or s.stage != 'technology':
            raise ValueError('Technology research is not active.')
        card = next((card for card in s.player.planets if card.planet.planet_id == planet_id), None)
        if not card:
            raise ValueError('Choose a planet you control.')
        selected = s.technology_planets if specialty else s.payment_planets
        other = s.payment_planets if specialty else s.technology_planets
        if planet_id in selected:
            selected.remove(planet_id)
            return
        if planet_id in other or (card.exhausted and not (specialty and 'pa' in s.player.technologies)):
            raise ValueError('This planet is exhausted or already committed.')
        if specialty and not card.planet.tech_specialties:
            raise ValueError('This planet has no technology specialty.')
        selected.add(planet_id)

    def toggle_aida(self):
        s = self.session
        if not s or s.stage != 'technology' or 'aida' not in s.player.technologies or \
                'aida' in s.player.exhausted_technologies:
            raise ValueError('AI Development Algorithm is not ready.')
        s.technology_use_aida = not s.technology_use_aida

    def research_selected(self):
        s = self.session
        if not s or s.stage != 'technology' or not s.technology_selected:
            raise ValueError('Choose a technology to research.')
        from technology import technology_catalog
        tech = technology_catalog()[s.technology_selected]
        if tech not in available_technologies(s.player) or tech['alias'] in s.player.technologies:
            raise ValueError('This technology is unavailable or already researched.')
        if s.technology_use_aida and (not is_unit_upgrade(tech) or
                                      'aida' not in s.player.technologies or
                                      'aida' in s.player.exhausted_technologies):
            raise ValueError('AI Development Algorithm cannot be used for this research.')
        cards = {card.planet.planet_id: card for card in s.player.planets}
        specialties = [cards[planet_id] for planet_id in s.technology_planets]
        if missing_prerequisites(s.player, tech, specialties, s.technology_use_aida):
            raise ValueError('Technology prerequisites are not satisfied.')
        if any(card.exhausted and 'pa' not in s.player.technologies for card in specialties):
            raise ValueError('A selected specialty planet is exhausted.')
        if any(cards[planet_id].exhausted for planet_id in s.payment_planets):
            raise ValueError('A payment planet is exhausted.')
        if s.trade_goods > s.player.trade_goods or self.technology_payment() < self.technology_cost():
            raise ValueError('Choose enough ready resources or trade goods.')
        s.player.trade_goods -= s.trade_goods
        for planet_id in s.payment_planets:
            cards[planet_id].exhausted = True
        if 'pa' not in s.player.technologies:
            for planet_id in s.technology_planets:
                cards[planet_id].exhausted = True
        if s.technology_use_aida:
            s.player.exhausted_technologies.add('aida')
        research_technology(s.player, s.technology_selected, self.board)
        s.technology_count += 1
        s.technology_selected = None
        s.technology_planets.clear()
        s.payment_planets.clear()
        s.technology_use_aida = False
        s.trade_goods = 0
        if not (s.primary or s.technology_brilliant) or s.technology_count >= 2 or \
                not any(card['alias'] not in s.player.technologies
                        for card in available_technologies(s.player)):
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
        if s.stage in ('diplomacy_system', 'diplomacy_secondary_system'):
            if s.stage == 'diplomacy_system' and tile.number == 18:
                raise ValueError('Choose a system other than Mecatol Rex.')
            if s.player.faction not in tile.planet_owners.values():
                raise ValueError('Choose a system with a planet you control.')
            if s.stage == 'diplomacy_secondary_system' and not any(
                    self.planet_system(card.planet.planet_id) is tile
                    for card in self.readyable_planets(s.player)):
                raise ValueError('Choose a system with an exhausted planet you control.')
            if s.stage == 'diplomacy_system':
                for player in self.turn.players:
                    if player is s.player or player.faction in tile.command_tokens:
                        continue
                    if not command_tokens_in_reinforcements(player, self.board):
                        pool = next((p for p in ('tactical', 'strategic', 'fleet') if player.command_pools[p]), None)
                        if pool is None:
                            continue
                        player.command_pools[pool] -= 1
                    tile.command_tokens.add(player.faction)
            s.selected_system = position
            s.ready_planets.clear()
            s.stage = 'ready_planets'
        elif s.stage == 'warfare_system':
            if s.player.faction not in tile.command_tokens:
                raise ValueError('Choose a system containing your command token.')
            s.selected_system = position
            s.pending_system = None
        elif s.stage == 'construction':
            if not any(self.planet_system(card.planet.planet_id) is tile
                       for card in self.buildable_planets(selected_only=False)):
                raise ValueError('Choose a system with an eligible planet you control.')
            s.selected_system = position
        else:
            raise ValueError('This ability does not select a system.')

    def selectable_systems(self):
        s = self.session
        if not s:
            return set()
        if s.stage == 'diplomacy_system':
            return {tile.position for tile in self.board.values()
                    if tile.number != 18 and s.player.faction in tile.planet_owners.values()}
        if s.stage == 'diplomacy_secondary_system':
            return {tile.position for tile in self.board.values()
                    if any(
                        self.planet_system(card.planet.planet_id) is tile
                        for card in self.readyable_planets(s.player))}
        if s.stage == 'warfare_system':
            return {tile.position for tile in self.board.values() if s.player.faction in tile.command_tokens}
        if s.stage == 'construction':
            return {self.planet_system(card.planet.planet_id).position
                    for card in self.buildable_planets(selected_only=False)}
        return set()

    def planet_system(self, planet_id):
        return next((tile for tile in self.board.values()
                     if any(planet.planet_id == planet_id for planet in tile.planets)), None)

    def readyable_planets(self, player=None):
        player = player or self.player
        result = []
        for card in player.planets:
            planet_id = card.planet.planet_id
            tile = self.planet_system(planet_id)
            if (card.exhausted and tile is not None and
                    tile.planet_owners.get(planet_id) == player.faction):
                result.append(card)
        return result

    def buildable_planets(self, selected_only=True):
        s = self.session
        if not s or s.stage != 'construction':
            return []
        kind = s.structure
        owned = [unit for tile in self.board.values()
                 for unit in tile.units if unit.owner == s.player.faction and unit.kind == kind]
        if len(owned) >= (6 if kind == 'pds' else 3):
            return []
        per_planet_limit = 2 if kind == 'pds' else 1
        return [card for card in s.player.planets
                if (tile := self.planet_system(card.planet.planet_id)) is not None
                and (not selected_only or s.selected_system is None or tile.position == s.selected_system)
                and tile.planet_owners.get(card.planet.planet_id) == s.player.faction
                and sum(unit.location.planet_id == card.planet.planet_id for unit in owned) < per_planet_limit]

    def select_warfare_token(self, position, faction):
        s = self.session
        if s.stage != 'warfare_system' or position not in self.selectable_systems() or \
                position != s.selected_system or faction != s.player.faction:
            raise ValueError('Select your command token in a highlighted system.')
        s.pending_system = None if s.pending_system == position else position

    def confirm_warfare_removal(self):
        s = self.session
        if s.stage != 'warfare_system' or s.pending_system is None:
            raise ValueError('Select a command token on the map first.')
        tile = self.board[s.pending_system]
        if s.player.faction not in tile.command_tokens:
            raise ValueError('That command token is no longer in the system.')
        tile.command_tokens.remove(s.player.faction)
        s.player.pending_commands += 1
        s.selected_system = s.pending_system = None
        s.stage = 'warfare_allocate'

    def toggle_ready(self, planet_id):
        s = self.session
        if s.stage != 'ready_planets' or not any(
                card.planet.planet_id == planet_id for card in self.readyable_planets(s.player)):
            return
        if planet_id in s.ready_planets:
            s.ready_planets.remove(planet_id)
        elif len(s.ready_planets) < 2:
            s.ready_planets.add(planet_id)

    def confirm_ready(self):
        s = self.session
        for card in s.player.planets:
            tile = self.planet_system(card.planet.planet_id)
            if (card.planet.planet_id in s.ready_planets and tile and
                    tile.planet_owners.get(card.planet.planet_id) == s.player.faction):
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
        tile = self.planet_system(planet_id)
        if s.stage != 'construction' or tile is None or tile.position != s.selected_system or \
                not any(card.planet.planet_id == planet_id for card in self.buildable_planets()):
            raise ValueError('Choose an eligible planet in the selected system.')
        kind = s.structure
        if not s.primary:
            if not s.player.command_pools['strategic']:
                raise ValueError('A strategy token is required.')
            s.player.command_pools['strategic'] -= 1
            tile.command_tokens.add(s.player.faction)
        tile.units.append(Unit(f'{s.player.faction}-structure-{uuid4().hex}', kind, s.player.faction,
                               s.player.color_code, UnitLocation(Region.PLANET, planet_id),
                               profile_id=(upgrade_profile(s.player, kind) or {}).get('id')))
        s.builds_left -= 1
        if s.builds_left:
            s.structure = 'pds'
            s.selected_system = None
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
        if s.stage in ('placeholder', 'construction', 'warfare_allocate', 'technology'):
            self._participant_done()
        elif s.stage == 'warfare_system' and not any(s.player.faction in t.command_tokens for t in self.board.values()):
            s.stage = 'warfare_allocate'
        elif s.stage in ('diplomacy_system', 'diplomacy_secondary_system', 'warfare_system') and \
                not self.selectable_systems():
            self._participant_done()
        else:
            raise ValueError('Complete the current step first.')
