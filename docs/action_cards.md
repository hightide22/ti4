# Combat, movement, and economy action cards

The draw pile contains a curated set of base-game, Prophecy of Kings, and Codex I cards. Politics draws up to two cards for its owner; each other player can spend a Strategy token to draw up to two. Each completed status phase also draws one card for every player. The normal hand limit is seven.

The card window keeps cards dim outside their timing windows and marks playable cards with **PLAY NOW**. Every card in this focused shortlist has a playable effect.

| Card | Timing | Short effect | Status |
| --- | --- | --- | --- |
| Bunker | Start of invasion | Apply −4 to bombardment rolls against planets you control | Implemented |
| Courageous to the End | After one of your ships is destroyed in space combat | Roll to destroy opposing ships | Implemented |
| Direct Hit | After an enemy ship uses Sustain Damage against your hit | Destroy that ship | Implemented |
| Disable | Start of invasion against opposing PDS | Turns off Planetary Shield and Space Cannon for that invasion | Implemented |
| Emergency Repairs | Start or end of a combat round | Repair your damaged Sustain Damage units in the active system | Implemented |
| Experimental Battlestation | After ships move into the active system | Fire a nearby Space Dock's Space Cannon | Implemented |
| Fighter Prototype | Start of first space-combat round | +2 to your fighters' combat rolls this round | Implemented |
| Fire Team | After ground-combat rolls | Reroll any number of your dice | Implemented |
| Flank Speed | After system activation | +1 movement for each of your ships this tactical action | Implemented |
| In the Silence of Space | After system activation | Let ships in a chosen system move through enemy-occupied systems | Implemented |
| Intercept | After an opponent announces a retreat | Prevent that retreat this round | Implemented |
| Maneuvering Jets | Before assigning Space Cannon hits | Cancel one hit | Implemented |
| Morale Boost | Start of a combat round | +1 to all your combat rolls this round | Implemented |
| Salvage | After winning a space combat | Take the opponent's commodities | Implemented |
| Shields Holding | Before assigning hits to your ships in space combat | Cancel up to two hits | Implemented |
| Skilled Retreat | Start of a space-combat round | Retreat all your ships and end combat in a draw | Implemented |
| Upgrade | After activation when your ships are in the system | Replace a cruiser with a dreadnought | Implemented |
| War Effort | Component action | Place a cruiser in a system containing your ship | Implemented |

The Codex I additions emphasize combat, activation, and economic decisions:

| Card | Timing | Effect |
| --- | --- | --- |
| Rally | After activating a system with opposing ships | Move two command tokens from reinforcements to fleet supply |
| Harness Energy | After activating an anomaly | Replenish commodities |
| Counterstroke | After an opponent activates a system with your token | Return that token to your tactic pool |
| Forward Supply Base | After an opponent activates a system with your units | Gain three trade goods; choose another player to gain one |
| Blitz | Start of an invasion | Give non-fighter ships without Bombardment Bombardment 6 for this invasion |
| Solar Flare | After activation | Block other players' Space Cannon against your ships during movement |
| War Machine | When your units use Production | Add four to total Production and reduce the combined cost by one |
| Fighter Conscription | Component action | Add one fighter to each of your systems with a Space Dock or capacity unit and no opposing ships; choose any capacity or fleet losses |
| Plagiarize | Component action | Spend five influence using ready planets and trade goods, then gain a neighboring player's non-faction technology |
| Master Plan | After performing an action | Take one additional action this turn |

The Prophecy of Kings addition is **Refit Troops**: as an action, replace one or two infantry on the board with mechs from reinforcements. The choice window pages through available infantry and enforces the four-mech supply.

This remains a focused deck. Reflective Shielding and Scramble Frequency still need interrupt windows for Sustain Damage and ability rolls; they are excluded until those reactions can resolve before combat continues. Agenda cards and cards whose timing cannot yet be resolved are also excluded. Card text and counts come from the pinned AsyncTI4 data; [Codex I](https://images-cdn.fantasyflightgames.com/filer_public/94/c5/94c59830-533e-47aa-a708-25634575161b/twilight_codex_v1-compressed.pdf) and the [Prophecy of Kings rules](https://images-cdn.fantasyflightgames.com/filer_public/c4/b9/c4b94e1e-d392-4ef8-a305-a37bb5660be3/ti10_rulebook_web.pdf) provide official context.

## Card artwork

Face images for the 18 shortlisted base-game cards are included in `assets/action_cards/`. New Codex and Prophecy of Kings cards currently use the in-game text card frame. The base files were taken from the English action-card textures in the public [TI4-TTPG repository](https://github.com/TI4-Online/TI4-TTPG/tree/main/prebuild/Textures/en/card/action). Twilight Imperium card artwork remains the property of its original rights holder.
