"""Exercise PoK leader and relic interactions in a real Arcade window."""

from pathlib import Path
import sys

import arcade

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import BoardWindow  # noqa: E402
from exploration import PendingExploration, PendingTerragenesis  # noqa: E402
from movement import Session, Snapshot  # noqa: E402
from player import PlanetCard  # noqa: E402
from strategic_action import StrategyResolution  # noqa: E402
from units import Region, Unit, UnitLocation  # noqa: E402


def click(window, hit):
    _, left, right, bottom, top = hit
    window.on_mouse_press((left + right) / 2, (bottom + top) / 2,
                          arcade.MOUSE_BUTTON_LEFT, 0)


def main():
    window = BoardWindow(smoke=True)
    try:
        player = window.turn_order.active_player
        window.on_draw()
        assert window.leader_button_hit is not None
        left, right, bottom, top = window.leader_button_hit
        window.on_mouse_press((left + right) / 2, (bottom + top) / 2,
                              arcade.MOUSE_BUTTON_LEFT, 0)
        window.on_draw()
        assert window.leader_panel.open and window.leader_panel.hits
        for _ in range(3):
            click(window, next(hit for hit in window.leader_panel.hits
                               if hit[0] == ('record_objective', False)))
            window.on_draw()
        assert player.scored_objectives == 3
        assert any(state.unlocked for alias, state in player.leaders.items()
                   if 'hero' in alias)
        close_hit = next(hit for hit in window.leader_panel.hits
                         if hit[0] == ('close',))
        click(window, close_hit)
        assert not window.leader_panel.open

        relics = window.exploration.relics
        relics.owned[player.faction].append('dynamiscore')
        starting_goods = player.trade_goods
        window.exploration_panel.open = True
        window.on_draw()
        use_hit = next(hit for hit in window.exploration_panel.hits
                       if hit[0] == ('relic_action', 'dynamiscore'))
        click(window, use_hit)
        assert player.trade_goods == starting_goods + player.commodity_limit + 2
        assert 'dynamiscore' not in relics.owned[player.faction]
        assert window.movement.undo()
        assert player.trade_goods == starting_goods
        assert 'dynamiscore' in relics.owned[player.faction]

        relics.owned[player.faction].append('codex')
        player.action_cards[:] = ['fs1'] * 6
        window.action_cards.discard[:] = ['mb1', 'sh1']
        window.exploration_panel.open = True
        window.on_draw()
        use_hit = next(hit for hit in window.exploration_panel.hits
                       if hit[0] == ('relic_action', 'codex'))
        click(window, use_hit)
        window.on_draw()
        assert window.relic_action_panel.alias == 'codex'
        for index in (0, 1):
            click(window, next(hit for hit in window.relic_action_panel.hits
                               if hit[0] == ('card', index)))
        window.on_draw()
        click(window, next(hit for hit in window.relic_action_panel.hits
                           if hit[0] == ('confirm',)))
        window.on_draw()
        assert window.relic_action_panel.alias == 'hand_discard'
        assert len(player.action_cards) == 8
        click(window, next(hit for hit in window.relic_action_panel.hits
                           if hit[0] == ('card', 0)))
        window.on_draw()
        click(window, next(hit for hit in window.relic_action_panel.hits
                           if hit[0] == ('confirm',)))
        assert len(player.action_cards) == 7
        assert not window.relic_action_panel.open
        assert window.movement_error is None

        window.configure_game(Path(__file__).resolve().parents[1] /
                              'maps/three_player.json', ('sol', 'jolnar', 'hacan'))
        player = window.turn_order.active_player
        player.technologies |= {'sdn', 'sar', 'aida', 'dd2'}
        target = next(tile for tile in window.board.values()
                      if tile.player is None and any(
                          planet.planet_type.lower() in window.exploration.decks
                          for planet in tile.planets))
        planet = next(planet for planet in target.planets
                      if planet.planet_type.lower() in window.exploration.decks)
        target.planet_owners[planet.planet_id] = player.faction
        player.planets.append(PlanetCard(planet))
        for kind in ('infantry', 'spacedock'):
            target.units.append(Unit(f'smoke-{kind}', kind, player.faction,
                                     player.color_code,
                                     UnitLocation(Region.PLANET, planet.planet_id)))
        window.exploration.decks[planet.planet_type.lower()] = []
        window.movement.activate(player, target.position)
        window.on_draw()
        scanlink = next(hit for hit in window.movement_panel.hits
                        if hit[0] == ('scanlink', planet.planet_id))
        click(window, scanlink)
        assert window.movement.session.scanlink_used
        window.movement.prepare_production(window.movement.session)
        window.on_draw()
        aida = next(hit for hit in window.movement_panel.hits
                    if hit[0] == ('aida_discount',))
        click(window, aida)
        assert window.movement.session.aida_production_discount
        window.on_draw()
        sar = next(hit for hit in window.movement_panel.hits
                   if hit[0] == ('sar_planet', planet.planet_id))
        click(window, sar)
        assert window.movement.session.self_assembly_planet == planet.planet_id

        window.configure_game(Path(__file__).resolve().parents[1] /
                              'maps/three_player.json', ('cabal', 'sol', 'jolnar'))
        player = window.turn_order.active_player
        player.captured_units['destroyer'] = 1
        home = next(tile for tile in window.board.home_tiles
                    if player.faction in tile.planet_owners.values())
        window.movement.activate(player, home.position)
        window.movement.prepare_production(window.movement.session)
        window.movement.adjust_production('destroyer', 1)
        cost_before = window.movement.production_cost(window.movement.session)
        window.on_draw()
        amalgamation = next(hit for hit in window.movement_panel.hits
                            if hit[0] == ('amalgamation', 'destroyer', 1))
        click(window, amalgamation)
        assert window.movement.session.amalgamation_choices['destroyer'] == 1
        assert window.movement.production_cost(window.movement.session) < cost_before

        window.movement.session = None
        window.strategy.session = StrategyResolution(7, player, player, [],
                                                     stage='technology')
        window.turn_order.strategy_resolution = window.strategy.session
        window.strategy.select_technology('dd2')
        window.technology_panel.tab = 'UNITS'
        window.technology_panel.preview_alias = 'destroyer'
        window.on_draw()
        riftmeld = next(hit for hit in window.technology_panel.hits
                        if hit[0] == ('riftmeld',))
        click(window, riftmeld)
        assert window.strategy.session.riftmeld_kind == 'destroyer'

        window.configure_game(Path(__file__).resolve().parents[1] /
                              'maps/three_player.json', ('naaz', 'sol', 'jolnar'))
        player = window.turn_order.active_player
        player.leaders['naazhero'].unlocked = True
        window.leader_panel.open = True
        window.leader_panel.select('naazhero', player)
        window.on_draw()
        hero = next(hit for hit in window.leader_panel.hits
                    if hit[0] == ('use', 'naazhero'))
        click(window, hero)
        assert window.naaz_hero_pending
        assert not window.leader_panel.open
        window.on_draw()
        first = next(hit for hit in window.naaz_hero_hits if hit[0] == 5)
        click(window, first)
        assert window.strategy.session.hero_secondary
        assert window.strategy.session.card == 5
        window.strategy.decline_secondary()
        window.sync_strategy_actor()
        window.on_draw()
        assert window.naaz_hero_pending
        assert not any(hit[0] == 5 for hit in window.naaz_hero_hits)
        second = next(hit for hit in window.naaz_hero_hits if hit[0] == 1)
        click(window, second)
        assert window.strategy.session.hero_secondary
        assert window.strategy.session.card == 1
        assert not window.naaz_hero_pending

        window.configure_game(Path(__file__).resolve().parents[1] /
                              'maps/three_player.json', ('naaz', 'sol', 'jolnar'))
        player = window.turn_order.active_player
        fragment_alias = next(alias for alias, card in window.exploration.catalog.items()
                              if card.resolution == 'fragment')
        trait = window.exploration.catalog[fragment_alias].trait
        window.exploration.fragments[player.faction].append(fragment_alias)
        before_tokens = player.command_pools['tactical']
        window.exploration_panel.open = True
        window.on_draw()
        click(window, next(hit for hit in window.exploration_panel.hits
                           if hit[0] == ('fabrication_trait', trait)))
        window.on_draw()
        click(window, next(hit for hit in window.exploration_panel.hits
                           if hit[0] == ('fabricate_token', fragment_alias, 'tactical')))
        assert player.command_pools['tactical'] == before_tokens + 1
        assert fragment_alias not in window.exploration.fragments[player.faction]

        window.configure_game(Path(__file__).resolve().parents[1] /
                              'maps/three_player.json', ('sol', 'jolnar', 'hacan'))
        player = window.turn_order.active_player
        primor_tile = next(tile for tile in window.board.values()
                           if any(planet.planet_id == 'primor' for planet in tile.planets))
        primor = next(planet for planet in primor_tile.planets
                      if planet.planet_id == 'primor')
        primor_tile.planet_owners['primor'] = player.faction
        player.planets.append(PlanetCard(primor))
        before_infantry = sum(unit.owner == player.faction and unit.kind == 'infantry'
                              for tile in window.board.values() for unit in tile.units)
        window.on_draw()
        left, right, bottom, top = window.turn_button_hit
        window.on_mouse_press((left + right) / 2, (bottom + top) / 2,
                              arcade.MOUSE_BUTTON_LEFT, 0)
        assert window.legendary_modal
        window.on_draw()
        click(window, next(hit for hit in window.legendary_hits
                           if hit[0] == ('select', 'primor')))
        window.on_draw()
        click(window, next(hit for hit in window.legendary_hits
                           if hit[0] == ('count', 1)))
        window.on_draw()
        click(window, next(hit for hit in window.legendary_hits
                           if hit[0] == ('use', 'primor', 'infantry', 'jord', 1)))
        assert sum(unit.owner == player.faction and unit.kind == 'infantry'
                   for tile in window.board.values() for unit in tile.units) == before_infantry + 1
        assert next(card for card in player.planets
                    if card.planet.planet_id == 'primor').exhausted

        window.configure_game(Path(__file__).resolve().parents[1] /
                              'maps/three_player.json', ('naaz', 'sol', 'jolnar'))
        player, opponent = window.player_panel.players[:2]
        player.technologies |= {'sc'}
        target = next(tile for tile in window.board.values() if not tile.units)
        target.units.extend((
            Unit('smoke-naaz-fighter', 'fighter', player.faction, player.color_code,
                 UnitLocation(Region.SPACE)),
            Unit('smoke-sol-fighter', 'fighter', opponent.faction, opponent.color_code,
                 UnitLocation(Region.SPACE)),
        ))
        session = Session(player, target, {}, Snapshot.capture(
            window.board, player, window.player_panel.players))
        session.stage = 'space_combat'
        session.combat_factions = (player.faction, opponent.faction)
        window.movement.session = session
        window.on_draw()
        supercharge = next(hit for hit in window.combat_panel.action_hits
                           if hit[0] == ('supercharge', player.faction))
        click(window, supercharge)
        assert 'sc' in player.exhausted_technologies
        window.on_draw()

        window.configure_game(Path(__file__).resolve().parents[1] /
                              'maps/three_player.json', ('sol', 'jolnar', 'hacan'))
        player = window.turn_order.active_player
        tile = next(tile for tile in window.board.values()
                    if tile.player is None and tile.planets and not tile.units)
        player.technologies = {'aida', 'ff2', 'st'}
        player.trade_goods = 2
        window.exploration.freelancer_offer = (player.faction, tile.position, None)
        window.on_draw()
        assert not any(hit[0] == ('freelancer_aida',)
                       for hit in window.exploration_panel.hits)
        click(window, next(hit for hit in window.exploration_panel.hits
                           if hit[0] == ('freelancer_kind', 'cruiser')))
        window.on_draw()
        for _ in range(2):
            click(window, next(hit for hit in window.exploration_panel.hits
                               if hit[0] == ('freelancer_goods', 1)))
            window.on_draw()
        window.on_draw()
        click(window, next(hit for hit in window.exploration_panel.hits
                           if hit[0] == ('freelancer_produce',)))
        assert any(unit.kind == 'cruiser' and unit.owner == player.faction
                   for unit in tile.units)
        assert player.trade_goods == 0
        assert 'aida' not in player.exhausted_technologies
        assert window.exploration.freelancer_offer is None
        assert window.movement.session is None

        deck = window.exploration.secret_objectives
        deck.draw(player, 3)
        assert deck.pending_returns[player.faction] == 1
        window.on_draw()
        click(window, next(hit for hit in window.exploration_panel.hits
                           if hit[0][0] == 'return_secret'))
        assert player.faction not in deck.pending_returns
        assert len(deck.hand_for(player.faction)) == 3

        window.configure_game(Path(__file__).resolve().parents[1] /
                              'maps/three_player.json', ('saar', 'sol', 'hacan'))
        player = window.turn_order.active_player
        tile = next(tile for tile in window.board.values()
                    if tile.player is None and tile.planets and not tile.units)
        planet = tile.planets[0]
        tile.planet_owners[planet.planet_id] = player.faction
        player.planets.append(PlanetCard(planet))
        dock = Unit('smoke-floating-factory', 'spacedock', player.faction,
                    player.color_code, UnitLocation(Region.SPACE))
        tile.units.append(dock)
        player.trade_goods = 1
        window.exploration.freelancer_offer = (player.faction, tile.position,
                                                planet.planet_id)
        window.on_draw()
        click(window, next(hit for hit in window.exploration_panel.hits
                           if hit[0] == ('freelancer_kind', 'infantry')))
        window.on_draw()
        click(window, next(hit for hit in window.exploration_panel.hits
                           if hit[0] == ('freelancer_planet', None)))
        window.on_draw()
        click(window, next(hit for hit in window.exploration_panel.hits
                           if hit[0] == ('freelancer_goods', 1)))
        window.on_draw()
        click(window, next(hit for hit in window.exploration_panel.hits
                           if hit[0] == ('freelancer_produce',)))
        infantry = next(unit for unit in tile.units if unit.kind == 'infantry')
        assert infantry.location.region == Region.TRANSPORT
        assert infantry.location.carrier_id == dock.unit_id
        print('PASS: leaders/relics, exploration, Freelancers, secrets, production, Riftmeld, Naaz, legendary planets and Supercharge')

        # Exploration can award a command token mid-turn. It must be allocated
        # without ending or resetting the current player's action.
        window.configure_game(Path(__file__).resolve().parents[1] /
                              'maps/three_player.json', ('sol', 'hacan', 'jolnar'))
        player = window.turn_order.active_player
        active = player
        owned_system = next(tile for tile in window.board.values()
                            if player.faction in tile.planet_owners.values())
        window.movement.activate(player, owned_system.position)
        assert window.movement.session is not None
        had_action = window.turn_order.action_used
        original_tactical = player.command_pools['tactical']
        player.pending_commands = 1
        window.on_draw()
        assert window.turn_button_hit is not None
        left, right, bottom, top = window.turn_button_hit
        window.on_mouse_press((left + right) / 2, (bottom + top) / 2,
                              arcade.MOUSE_BUTTON_LEFT, 0)
        assert window.immediate_command_allocation
        window.on_draw()
        pool_control = next(control for control in window.player_panel.controls
                            if control.action == ('pool', 'tactical'))
        window.on_mouse_press(pool_control.left + pool_control.width / 2,
                              pool_control.bottom + pool_control.height / 2,
                              arcade.MOUSE_BUTTON_LEFT, 0)
        assert player.pending_commands == 0
        assert player.command_pools['tactical'] == original_tactical + 1
        assert window.turn_order.active_player is active
        assert window.turn_order.action_used == had_action
        assert window.movement.session is not None
        assert not window.immediate_command_allocation

        # Verify both the effect and the actual modal button for declining it.
        tile = next(tile for tile in window.board.values()
                    if any(planet.planet_type == 'HAZARDOUS' for planet in tile.planets))
        planet = next(planet for planet in tile.planets
                      if planet.planet_type == 'HAZARDOUS')
        tile.planet_owners[planet.planet_id] = player.faction
        if not any(card.planet.planet_id == planet.planet_id for card in player.planets):
            player.planets.append(PlanetCard(planet, exhausted=True))
        infantry = Unit('smoke-decline-infantry', 'infantry', player.faction,
                        player.color_code, UnitLocation(Region.PLANET, planet.planet_id))
        tile.units.append(infantry)
        window.exploration.pending = PendingExploration(
            player.faction, tile.position, planet.planet_id, 'exp1',
            ('remove_infantry', 'decline'))
        window.on_draw()
        decline = next(hit for hit in window.exploration_panel.hits
                      if hit[0] == ('resolve', 'decline'))
        click(window, decline)
        window.on_draw()
        assert window.exploration.pending is None
        assert infantry in tile.units
        assert 'exp1' in window.exploration.discards['hazardous']

        # The system inspector and player dashboard share the effective
        # specialty/value view for exploration attachments.
        attachment_planet = next(p for p in tile.planets
                                 if not p.tech_specialties)
        window.exploration.attachments[attachment_planet.planet_id] = ['biotic']
        assert window.exploration.planet_specialties(attachment_planet) == ('BIOTIC',)
        assert window.exploration.planet_values(attachment_planet) == (
            attachment_planet.resources, attachment_planet.influence)
        window.system_panel.draw(window, tile, window.width - window.sidebar)
        assert window.labels[(attachment_planet.planet_id, 'specialties')].text == 'Technology: BIOTIC'
        assert window.labels[(attachment_planet.planet_id, 'attachments')].text == 'Attached: Biotic'

        # Attachment and Titans sleeper tokens render on planets; when the
        # sleeper supply is full, the Terragenesis prompt moves one by choice.
        window.configure_game(Path(__file__).resolve().parents[1] /
                              'maps/three_player.json', ('titans', 'sol', 'hacan'))
        titans = window.turn_order.active_player
        marker_tile = next(tile for tile in window.board.values() if len(tile.planets) >= 2)
        marked_planet, destination = marker_tile.planets[:2]
        window.exploration.attachments[marked_planet.planet_id] = ['ds', 'biotic']
        marker_tile.sleeper_tokens.add(marked_planet.planet_id)
        window.selected = marker_tile.position
        window.on_draw()
        loaded = {Path(path).name for path in window.player_panel.textures}
        assert {'token_sleeper.png', 'attachment_dysonsphere.png',
                'attachment_biotic.png'} <= loaded

        destination_tile = marker_tile
        destination_tile.planet_owners[destination.planet_id] = titans.faction
        if not any(card.planet.planet_id == destination.planet_id for card in titans.planets):
            titans.planets.append(PlanetCard(destination))
        all_planets = [(tile, planet) for tile in window.board.values() for planet in tile.planets
                       if planet.planet_id != destination.planet_id]
        source_planets = [planet.planet_id for _, planet in all_planets[:5]]
        for tile, planet in all_planets[:5]:
            tile.sleeper_tokens.add(planet.planet_id)
        window.exploration.terragenesis_pending = PendingTerragenesis(
            titans.faction, destination_tile.position, destination.planet_id)
        window.on_draw()
        assert 'terragenesis_help' in window.labels
        source = source_planets[0]
        move_hit = next(hit for hit in window.exploration_panel.hits
                        if hit[0] == ('move_sleeper', source))
        click(window, move_hit)
        assert destination.planet_id in destination_tile.sleeper_tokens
        assert not any(source in tile.sleeper_tokens for tile in window.board.values())
        print('PASS: token allocation, exploration decline, attachment and sleeper markers, and Terragenesis move choice')
    finally:
        window.close()


if __name__ == '__main__':
    main()
