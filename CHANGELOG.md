# Changes

Newest first. Dates are when the build was pushed.

## Nov 24 2026

- **Cluster Themes 0.19.10, HUD Widgets 0.15.9:** "Show Waze map inside the theme" works again. Waze on Cluster 0.10.10 and newer stopped taking the way the theme used to move its map, so no theme showed the map and the Map tab stayed on "Moving the map into the theme". The theme now asks cluster-render to show the map in its window, a way robertnisipeanu added after we asked (robertnisipeanu/idrive-apps#250). It needs Waze on Cluster 0.14.5 or newer and cluster-render 0.8.2 or newer. GT3, Sport, M, M view and Vector show the same part of the map as before. The holiday lights show again when a badge is set (on some themes they found no room). M view's top line now runs into its corner in one smooth sweep instead of bending suddenly. Not yet tested on a car.

## Nov 22 2026

- **Cluster Themes 0.19.9, HUD Widgets 0.15.8:** M view's scale lines are now perfectly round curves (before, they bent in small corners you could see on the car). The holiday lights were redone on every theme: they never cross a theme's own lines, hang the same on both sides, and leave out the short pieces that looked broken. On M view they hang between the two dials under the M stripes, and on Vector they no longer run into the rev counter or the music panel. Not yet tested on a car.

## Nov 21 2026

- **Cluster Themes 0.19.8:** Cruise's clock, range and outside temperature along the bottom are now as big as the fuel and oil figures, about twice their old height, so they read at a glance (issue #33). Not yet tested on a car.
- **Cluster Themes 0.19.7, HUD Widgets 0.15.7:** plug-in hybrids get **In Hybrid** and **In Electric** next to Comfort, Sport and Eco Pro on the Themes tab (issue #31). Hybrid, Hybrid Eco Pro and Battery Control use the Hybrid theme, Electric uses the Electric one. Both start on "Same as the drive mode", so nothing changes until you pick one. They show once the car has sent a plug-in hybrid mode. The idea comes from FDX100's KurdBimmer Themes. Not yet tested on a car.
- **Cluster Themes 0.19.6, HUD Widgets 0.15.6:** a **Start with the car** switch on the Settings tab, on by default. Turn it off and the app waits until you open it before it draws anything, as it did before 0.19.4. On smaller screens the Settings tab's rows sit a little closer together so all of them fit.
- **Cluster Themes 0.19.5, HUD Widgets 0.15.5:** in Adaptive, the themes and layouts now say ADAPTIVE next to the gear instead of COMFORT (issue #29). Adaptive still uses your Comfort theme or layout.
- **Cluster Themes 0.19.4, HUD Widgets 0.15.4: both apps start with the car** (issue #16). The car now starts them by itself when it starts, so the theme and the HUD layout come up without you opening the app first. You only need to open them to change something. Not yet tested on a car: if the theme doesn't come up by itself, or the app's page won't open after that, please say so in #16.
- **Cluster Themes 0.19.3, HUD Widgets 0.15.3:** a safety net for installing. When the Store builds the app on your car, anything your car's software doesn't have (a signal, or one way of reading it) is now left out of that build, instead of the Store refusing the whole app as it did with 0.19.0. The app then runs without that one readout and says so in its log. Nothing changes on cars that have everything. This comes from the MGU App Kit, which alexpereap pointed to in #26. Not yet tested on a car.

## Nov 20 2026

- **Cluster Themes 0.19.2, HUD Widgets 0.15.2:**
  - **Plug-in hybrids switch with the drive mode** (issue #31): Sport and Sport Plus pick your Sport theme or layout, Hybrid, Electric and Battery Control count as Comfort, and Hybrid Eco Pro as Eco Pro. Before, a plug-in hybrid's modes weren't recognised and nothing switched. Thanks to the reporter of #31 for finding the values on a 330e. Other cars switch as before.
  - **Temperature and pressure units of their own** (issues #24, #32): two new settings under Units in Cluster Themes, Temperature (°C or °F) and Pressure (bar or psi), for mph with °C and psi as in the UK. Both start on Match speed, so nothing changes until you pick one. HUD Widgets keeps its one Units setting.
  - **Leave BMW's bottom strip clear** (Cluster Themes, off by default): for cars where BMW still draws two black boxes over the theme, like M cars and the G22 4 Series (issues #11, #1). The theme then ends just above BMW's bottom strip, and BMW shows its own clock, range and outside temperature there. Nothing is squeezed or shrunk.
  - The Settings tab's rows get a little shorter on smaller screens so all of them fit.
  - Not yet tested on a car.
- **Cluster Themes 0.19.1, HUD Widgets 0.15.1:** fixes the Store refusing to install 0.19.0 and 0.15.0 on some cars ("instance "1" is not on this unit"). The music panel now asks the car for the front screen's now playing by the name the car uses. Nothing else changes. Not yet tested on a car.

## Nov 15 2026

- **Cluster Themes 0.19.0, HUD Widgets 0.15.0:**
  - **New theme: Vector,** a race-sim style dash with angled frames: a round segmented rev counter with the speed and gear inside, ring gauges for coolant, oil, fuel and boost, and boost, torque and power bars in the middle. With "Show Waze map inside the theme" on, the map shows in the middle instead of the bars.
  - **Music panel (experimental):** Vector shows the song that is playing, with the title, artist and time, read from the car's own now playing. When nothing is playing, or your car doesn't send it, the panel shows average speed, peak boost and lateral G instead. So far this has only been tried on the bench, so please tell us in an issue whether it works with radio, Bluetooth, USB and CarPlay on your car.
  - **M view:** closer to BMW's own M view. Dark panels inside both scales, the map window is now an oval that fades into the black, the speed in the other unit sits under the line (km/h under mph, or the other way round), and the red glow by the needle shows even when parked.
  - **HUD Widgets: two new layouts.** M view: the rev bar, a big gear with the drive mode, and the speed in the other unit next to BMW's own. Vector: shift lights, oil, coolant and boost rings, and the song playing (or range and outside temperature when nothing plays).
  - Not yet tested on a car.
- **HUD Widgets 0.14.9:** on Redline, two readouts showing the same long name (like TORQUE LB-FT in both) no longer run together into one line.
- **Cluster Themes 0.18.1, HUD Widgets 0.14.8:**
  - **Every theme redrawn for full screen.** Now that leaving BMW's gear letter uncovered keeps its black box away (0.17.8), the themes no longer leave room for it: the right side of each theme lines up with the left again, and everything reaches down to the theme's own clock, range and outside temperature row.
  - **GT3:** the right column mirrors the left, the boxes are a little taller, and the Waze map window is wider.
  - **Sport:** the brackets are taller, and the boost and coolant bar sits right above the bottom row.
  - **Dyno:** Power and Torque are each two rows tall, in line with the grid.
  - **Drag, Track, Rally, Retro 80s and Ribbon:** the right side mirrors the left, so the theme sits centred.
  - **Retro 80s:** the clock, range and outside temperature are in seven-segment digits.
  - **M, M view, Cruise and Minimal:** the range is centred in the bottom row.
  - HUD Widgets: no change you will see.
  - Not yet tested on a car.
- **Cluster Themes 0.18.0, HUD Widgets 0.14.7:**
  - **New theme: M view,** after the M view of the M3 / M5 Live Cockpit in Sport (issue #15). Speed on the left (0 to 200 mph or 0 to 330 km/h) and revs on the right (0 to 8000), each scale lit up to its needle, with the speed and gear in big figures, the drive mode under the gear, and fuel and oil temperature dials at the far edges. Pick it on the Themes tab.
  - **M view and the Waze map:** the middle of the theme is a window for the map. It opens when the map shows and closes when it goes, without the theme redrawing.
  - Thanks to Asksket, who drew the first version of this theme on their own branch; its shape and look are used here.
  - The theme previews on the Themes tab now leave the same small gap over BMW's gear letter that the cluster does.
  - HUD Widgets: no change you will see.
  - Not yet tested on a car.

## Nov 14 2026

- **Cluster Themes 0.17.8, HUD Widgets 0.14.6:**
  - **Roadster is gone.** If it was your theme, the cluster switches to GT3; pick another on the Themes tab.
  - **M:** SPORT PLUS in the setup rows no longer gets cut off (it read SPORT PLL).
  - **Full screen** covers a little more of BMW's drive mode, so no letter of it shows next to the gear letter.
  - HUD Widgets: no change you will see.
  - Not yet tested on a car.
- **Cluster Themes 0.17.7, HUD Widgets 0.14.5:**
  - **Full screen leaves only BMW's gear letter uncovered.** On a test car, 0.17.6 kept BMW's black box away by leaving the drive mode and gear letter open; now the theme also covers the drive mode and leaves just the gear letter. The theme's own drive mode still shows next to its gear.
  - **Leave BMW's bottom strip clear is gone.** With BMW's gear letter uncovered, BMW no longer draws its black box (issue #5), so the switch has nothing left to fix. If you had it on, your theme goes full screen.
  - HUD Widgets: no change you will see.
  - Not yet tested on a car.
- **Cluster Themes 0.17.6, HUD Widgets 0.14.4:**
  - **Full screen leaves BMW's drive mode and gear letter uncovered.** On a test car, BMW drew its black box whenever a theme covered the gear letter in its bottom strip, and it went away once the strip was clear. Full screen now covers the strip on both sides of the drive mode and gear letter but leaves that spot open, to see if full screen can work without the box. If the box still shows on your car, turn on Leave BMW's bottom strip clear (issue #5); it goes away at the next gear change.
  - **M:** the fuel and oil dials are cleaner. A sharp red line marks the level inside the glass, the unlit glass shows faintly above it, the glass and the bezel end on the same line, and the °F and oil can sit clear of the bezel.
  - **Sport:** the theme reaches further down, so the gap above BMW's strip is gone. The boost and coolant bar sits just above it.
  - HUD Widgets only gets the shared engine change.
  - Not yet tested on a car.
- **Cluster Themes 0.17.5, HUD Widgets 0.14.3:** **full screen is back as the default, and Leave BMW's bottom strip clear is back as a switch.** Every theme fills the whole cluster again and shows its own clock, range and outside temperature along the bottom, in the theme's style. Turn on Leave BMW's bottom strip clear on the Settings tab and the theme ends just above BMW's own strip (the 0.17 look), without the squeeze that uncovered BMW's gauges before 0.17.4. If BMW draws a black box over the theme on your car, try the switch: owners found that leaving the strip clear stops it (issue #5). Both apps also stop logging a false "still stalled" warning after cluster-render restarts (a car log showed the layers were on screen the whole time); that is the only change in HUD Widgets. Not yet tested on a car.
- **Cluster Themes 0.17.4, HUD Widgets 0.14.2:** M cars now switch with the drive mode (issue #8). The apps read the mode M Setup shows, so Efficient uses your In Comfort pick and Sport and Sport+ use In Sport. Thanks to the reporter of issue #8 for pointing to the signal, found by the idv7lol project and tested there on an X4 M Competition. On other cars nothing changes. The drive mode check on the Settings tab now shows that signal. **Leave BMW's bottom strip clear is gone:** since 0.17.0 every theme already ends above BMW's strip, and the setting only squeezed the theme further up, which uncovered the bottoms of BMW's own gauges. If you had it on, the theme goes back to its normal size. Not yet tested on a car.
- **Cluster Themes 0.17.3, HUD Widgets 0.14.1:** fixes from a code review.
  - **Readout names and values no longer get cut off.** Some picks on the Readouts tab could lose letters or digits: a clock or 4-digit value in M's readouts, LAST SHIFT in M, TORQUE LB-FT in GT3, Rally and the HUD's Sport and Redline, LB-FT next to a value in GT3 and the HUD's Daily. Every box now fits every readout. On M, Last shift is called SHIFTED, the readouts sit a little further left so LB-FT clears the rev blade, and with the Waze map on a clock or 4-digit value uses smaller digits. On the HUD's Sport layout the values sit a little further right.
  - **Drive mode check (issue #8):** all four lines now fit on every screen; before, only the first one showed on some screens. The Settings rows get slightly shorter to make room when needed. HUD Widgets lists only the two signals it reads, and the check does no work while Change with the drive mode is off.
  - **Play demo on the calibration screens** (Calibration grid and Map calibration, the HUD's grid and Window calibration) shows the DEMO tag again.
  - Not yet tested on a car.

## Nov 13 2026

- **Cluster Themes 0.17.2:** a drive mode check for M cars (issue #8). With Change with the drive mode on, the Settings tab lists what the car sends on four signals, so an M owner can photograph it in Efficient, Sport and Sport+ and the M modes can be added. Nothing else changes.
- **Cluster Themes 0.17.1:** Badge: added the 545e (issue #10).
- **Cluster Themes 0.17.0, HUD Widgets 0.14.0:**
  - **Cleaner cluster themes:** every theme now ends just above BMW's own bottom strip, so the car's range, clock, street, drive mode and outside temperature always show and no theme repeats them. Every theme shows the drive mode next to the gear. Readouts that used to show the clock, range or outside temperature now show fuel (GT3, Roadster, Rally), torque and power (Roadster), or your shift point and last shift (Dyno). Ribbon shows your average speed in their place, Cruise shows oil, coolant and rpm, and Drag, Track and Minimal gained fuel, oil or coolant.
  - **M:** the tyre block is gone. In its place are four readouts, boost, coolant, oil and intake air out of the box, each one changeable on the Readouts tab. The four setup rows sit together where the M8 has them, and the badge moved off the M stripes. With the Waze map on, the blades keep their shape and numbers and move out to the sides, the map fills the middle, and the gear, speed and drive mode sit in a row under it.
  - **Sport:** a big speed in the middle with the gear and drive mode under it, over a road like BMW's own Sport view whose lane dashes run towards you at your speed. The fuel and oil bars moved up the brackets, and the boost and coolant bar sits higher, clear of BMW's strip.
  - **M and Sport no longer leave a gap for BMW's black gear box.** It may be a leftover of a coding change; if your car still draws it, it can cover part of the right side.
  - **Holiday lights:** a string of glowing lights along the top of every cluster theme and the HUD while a holiday edition is on (Christmas, Thanksgiving, New Year, Fourth of July). They twinkle gently only while you're stopped. The Cluster Themes setting is now called **Holiday editions** and covers every theme, not just Ribbon.
  - **Play demo starts faster:** it no longer reloads the layout to start or stop.
  - **Time zone names:** BMW's own settings list now shows the zone names (Not set (UTC), US Eastern, UTC+5:30 and so on) instead of number codes.
  - Not yet tested on a car.
- **Cluster Themes 0.16.0, HUD Widgets 0.13.0:**
  - **Directions on the HUD:** while a route runs, the next turn, its distance and the street or lanes show in a corner of every HUD layout, with "then" for a turn close behind and roundabout exits. Ported from Redline (cauliflowermossgrape/idrive7-apps, MIT). Works with CarPlay and Apple Maps; Waze over CarPlay sends the car no turns. Setting: While a route runs / Near turns / Off.
  - **Play demo:** a button on both apps' pages plays a 60-second show on the HUD or cluster while the car stands, with a DEMO tag. It stops as soon as the car moves, and nothing from it is kept. **Demo data On** now also switches back to the car's own data as soon as the car moves faster than 5 km/h.
  - **Shift lights count in:** they fill over about the last second before the shift point however fast the revs climb, and flash early by your reaction time, learned from your upshifts. The old fixed rpm band is still a choice. From Redline.
  - **Temperatures only when they matter** (off by default): oil and coolant only while cold, warming up or hot, coloured blue, yellow or red. The outside temperature turns blue at 3°C (37°F) and under.
  - **Holiday colours on the HUD:** Christmas (red, green and gold, the shift lights as a string of bulbs, MERRY CHRISTMAS while stopped), Thanksgiving, New Year and the Fourth of July, by the date. Nothing moves on the glass.
  - **Switch-off guard:** nothing new is put on the HUD or cluster while the car switches on or off, and the layers come down at switch-off, so cluster-render never counts a strike against the display. From Redline.
  - **Lighter G-meter:** the same picture with less than half the GPU uniforms, kept well inside what the cluster's graphics handle safely.
  - **Halloween is gone** from Ribbon's holiday editions. If you had picked it, Ribbon shows in its normal colours.
- **Cluster Themes 0.15.0, HUD Widgets 0.12.9:** a new Ribbon theme, calm and premium. One ribbon of light runs along under the top edge of the cluster and fills with the revs, cool white warming to amber and red, with a dot and a number at every thousand. The speed sits in the middle in big thin numbers, the gear in a small frame above it, and oil, coolant and boost on the left with range, fuel and outside temperature on the right. Ribbon has holiday editions: Christmas (the ribbon becomes a string of coloured bulbs that light up with the revs, with falling snow), Halloween, Thanksgiving, New Year and the Fourth of July, each with its own colours, a little artwork and a greeting. A new setting, **Holiday editions**, picks one or leaves it on Auto (the default), which puts on the right one by the date and goes back to plain Ribbon after. With Leave room for the driver camera on, the ribbon dips under the camera instead of being cut. The numbers use the Outfit font (SIL OFL, credit in NOTICE). HUD Widgets only gets the shared engine update. Not yet tested on a car.
- **Cluster Themes 0.14.9, HUD Widgets 0.12.8:** quieter logs. A layer that takes a moment to appear after a theme or layout loads (its pixels are still on their way to cluster-render) is normal and is no longer logged as a warning. The apps now warn only when a layer is still missing after 5 seconds, naming the part of the layout, and note when it shows up.
- **Cluster Themes 0.14.8:** a new setting, off by default: **Leave room for the driver camera**, for newer cars with a camera in the top middle of the cluster. The shift lights and rev bars of GT3, Track, Dyno and Rally become two mirrored wings either side of the camera that fill from the outer ends in towards it, Track's rpm numbers are left out and M's logo is hidden. Not yet tested on a car with a camera; please report how it lines up on yours.

## Nov 12 2026

- **Cluster Themes 0.14.7, HUD Widgets 0.12.7:** shift grades updated to match Redline 2.0.40. LIMITER now means within 25 rpm of the redline (it was 60, which caught good shifts near the shift point). Only shifts aimed at the shift point are graded: an everyday shift more than 1500 rpm under it, or one the gearbox made itself in D or S, is left out and the last grade stays on screen. When Redline 2.0.28 or newer is running, the shift lights and grades use Redline's own shift point for the gear you're in (it learns one per gear) and its grade, read from the file Redline shares for other apps (thanks @biscotit, issue #4). Without Redline, nothing changes.

## Nov 11 2026

- **Cluster Themes 0.14.6, HUD Widgets 0.12.6:** the Time zone (clock) setting now also lists named zones (US Eastern, Central, Mountain, Pacific, Alaska, Hawaii, Arizona, Canada Atlantic, Mexico, UK / Ireland / Portugal, Central Europe, Eastern Europe). A named zone switches to and from daylight saving by itself, so you set it once. The plain UTC offsets are still there for everywhere else. Night colours (Auto) now works out sunset for the zone you pick instead of one fixed US city. The apps' reads from the car's control units (boost, intake, fuel %, hybrid battery) now go through the kit's read-only link, which refuses anything that isn't a read before it leaves the head unit. New Cluster Themes setting, off by default: **Leave BMW's bottom strip clear** draws the theme only above BMW's own bottom strip (range, clock, belt light, P), squeezed to fit. One M car owner found that leaving the strip clear stopped BMW drawing black boxes over the theme (issue #5). Not yet tested on a car with this setting; please report how it works on yours. HUD Widgets now makes way for Redline: while Redline holds the HUD, HUD Widgets draws nothing, and it comes back by itself when Redline lets go (it uses the kit's HUD claim, the same one Cluster functions uses).
- **Cluster Themes 0.14.5, HUD Widgets 0.12.5:** a cluster theme and a HUD layout can show at the same time. cluster-render 0.5.1 and newer give the cluster its own screen, so the two apps no longer ask you to turn one off. On older cluster-render, where only one can show, they still take turns as before. Both apps also get new icons (sources in tools/icons/).
- **Cluster Themes 0.14.4, HUD Widgets 0.12.4:** two new settings. Time zone (clock): the head unit's clock runs on UTC, so the clock could show 5 to 7 hours off; pick your offset from UTC (for example UTC-5 for New York in winter) and the clock shows your time. Automatic keeps the old behaviour. Fuel: the gauge now asks the engine unit for the fuel level in % (standard OBD), which is right on any car; where the engine unit doesn't answer, it works it out from the litres the car reports and a new Fuel tank size setting (65 L on an X3, 59 L on a 3 Series). On cars with drive modes the apps don't know yet (M cars' Efficient, Sport and Sport+), the Settings page now shows the value the car sends, so it can be added.

## Nov 10 2026

- **Cluster Themes 0.14.3, HUD Widgets 0.12.3:** a new readout for plug-in hybrids (330e, X5 45e and others): Hybrid battery, the high-voltage battery's charge in %. Pick it for any readout box in Settings > Readouts. It is read from the car's battery unit only while a box shows it, and shows -- on a car without one. Not yet checked on a hybrid; please report how it reads on yours.
- **Cluster Themes 0.14.2, HUD Widgets 0.12.2:** Dyno rebuilt so it no longer blacks out the cluster. 0.14.1 split the tiles into smaller layers, but the car still restarted the cluster, so the trouble was the tile drawing itself. The tile frames now use the same drawing as the GT3 theme, which runs fine on the car, and each bar is its own small layer. A tile no longer turns red over its warning limit; its number flashes red instead.
- **Cluster Themes 0.14.1:** a first try at fixing the Dyno theme blacking out the cluster. Dyno drew every tile with one large shader over the whole cluster, and on the car that made the cluster's display drop and restart every few seconds, sometimes leaving it black. Each tile is now its own small layer over a plain black background, and Dyno draws steadily. If your cluster is stuck black, lock the car for a couple of minutes and it comes back.
- **Cluster Themes 0.14.1, HUD Widgets 0.12.1:** the layout check writes each warning once instead of on every reload, so the apps' logs keep their history.

## Nov 9 2026

- **Cluster Themes 0.14.0, HUD Widgets 0.12.0:** a better G-meter for Rally and Track. The dot turns amber from 0.5 g and red from 0.8 g and leaves a fading trail, an outline shows the most g reached this drive in every direction, and the meter has a bezel with ticks and finer rings. Under it (Rally) or beside it (Track): the g now, as left or right and accelerating or braking, and the best of the drive. A new G-meter tab on the center screen sets the range (0.8 to 2.0 g, 1.2 by default) and the trail length, holds the two swap switches ("swap accel / brake" is now "Brake on top"), shows the bests of the drive with a Reset button, and has a live copy of the meter.
- **HUD Widgets 0.11.0:** every layout spread out to the HUD's real edges. BMW's own height-adjust frame shows the picture reaching about 80 px further right than the layouts used, so they now run from x 120 to 680 instead of stopping at 600. Every layout also leaves the bottom-left corner empty, where BMW draws its speed and speed-limit sign and, in Sport, its own rev band, so no layout gets covered in any drive mode. Cruise's figures are bigger. The Window calibration frame shows the new edges.

## Nov 8 2026

- **Cluster Themes 0.13.0:** a new Dyno theme in BMW M colours: twelve readout tiles (rpm, speed, boost, oil, coolant, intake, cornering and braking g, gear, fuel, range, outside temperature) and tall Power and Torque tiles, each with a bar, and a rev bar across the top. A tile turns red while its oil, coolant or boost reading is over the warning limit. Every tile but the gear can show another sensor (the Readouts tab now lists long slot lists in two columns). The G-meter now moves the way the car accelerates (into the turn, up when speeding up), and two new settings swap left and right or accelerating and braking. G readings are smoothed over 0.15 s and a reading over 3 g is ignored. A readout set to intake air or boost now asks the engine unit for it (before, only Warm-up and readouts that named it did).
- **HUD Widgets 0.10.0:** the same G-meter changes and settings for the Track layout, and the same intake and boost fix for the readouts.

## Nov 7 2026

- **Cluster Themes 0.12.0:** three more themes. Rally (a segmented rev arc sweeping across the top, a huge gear, a G-meter, boost with a peak-hold bar), Retro 80s (amber seven-segment digits over their unlit 8s, a segmented rev bar and bar graphs for oil, water and fuel) and Cruise (calm and quiet: the speed and a row of range, fuel, outside temperature, average speed and the time). Track's readouts show the ° after a temperature again (0.11.0 dropped it), and a readout whose font can't draw a character switches to one that can. Credits for the Orbitron and DSEG7 fonts are in NOTICE.
- **HUD Widgets 0.9.0:** three more layouts. Track (shift lights, the gear and a G-meter), Cruise (range, outside temperature, average speed and the time) and Retro (amber seven-segment figures with bar graphs).
- **Cluster Themes 0.11.0:** four new themes. Minimal (just the speed, the gear and a fuel line), Roadster (two classic round dials with chrome rings), Track (a big gear, the four tyres, oil, coolant and boost) and Drag (a full-width shift bar and a 0 to 60 timer that keeps your best time). The Themes tab shows every theme in two rows. A readout can now show the intake air temperature, read from the engine unit.
- **HUD Widgets 0.8.0:** four new layouts. Minimal (built round BMW's own speed and speed-limit sign), Shift only, Boost (a gauge that holds the peak) and Warm-up (oil, coolant and intake air as bars until the engine is warm). The Layouts tab shows the cards in two rows.
- **Cluster Themes 0.10.0:** M gets a map version too: a window between the rev blades, with the gear and speed side by side under it. The map window in GT3 and Sport is narrower, so it ends before BMW's black gear box instead of running into it, and Sport's speed and gear move beside the map while its fuel and oil gauges step off it. M uses much less of cluster-render's picture memory (9.1 MiB instead of 13.2): on the car its speed unit, clock and oil numbers could go missing when the memory ran out. Picking a theme while HUD Widgets is on now asks whether to turn the HUD off.
- **HUD Widgets 0.7.0:** an Off card first among the layouts turns the HUD off in every drive mode. Picking a layout while a cluster theme is on asks whether to turn the theme off. The layout pictures show the day colours.
- **Cluster Themes 0.9.0:** a page of its own on the center screen instead of BMW's settings list: the themes with pictures, the readout boxes (pick any car sensor for each GT3 box), a model badge, the Waze map, alerts and settings. "Show Waze map inside the theme" replaces "Leave room for the map": Waze on Cluster draws its map inside GT3 or Sport, above BMW's own map view, so BMW's speed and gauges no longer show twice. Warnings flash a readout red over an oil, coolant or boost limit, a button tests the shift lights, and the colours dim after sunset.
- **HUD Widgets 0.6.0:** the same kind of page: layouts with pictures, the readouts, the HUD's position and size on the glass, colours, alerts and settings. Brighter, solid colours that read better on the glass, warnings, and the shift-light test.
- **Cluster Themes 0.8.4:** the M theme fills the panel: taller blades and cards that reach from the top of the screen to the bottom, bigger side groups, and black cards with a grey rim so BMW's black gear box no longer shows as a hole. New Map calibration theme for measuring the map cut-out on a photo.

## Nov 6 2026

- **Cluster Themes 0.8.2:** installs from the Store again. 0.8.1 (and 0.8.0) asked to read the car's M Drive gearbox display, which the car only sends as updates, so the head unit's app manager refused to install the app. It now only listens for those updates; the S or M next to the gear works as before.
- **Cluster Themes 0.8.1:** the M theme traced 1:1 off the M8 photo instead of restyled from it: the cards' curved top edge and big rounded corners, the blades' exact curve, width and ticks, the fuel and oil dials as the M8's thin glass brackets cut from the photo's own circles (a hatched chrome bezel, glass lit from the bottom up to the level, a red streak at the level and at the reserve or hot end), every number and label in a squared bold face like BMW's (Orbitron, open licence; S no longer reads as 8), and every readout at the photo's position and size. The only departures are forced by the X3: the setup rows are split round BMW's black box (two above it, two below), the badge is the M stripes alone, and the rev scale ends at 7.
- **Cluster Themes 0.8.0:** the M theme reworked from the car photos: near-black cards stretched to the panel's full height, round fuel and oil dials in the lower corners, taller rev blades, the gear with its shifter programme (D, S or M), the setup rows for all five drive modes (Individual shows as INDIVIDUAL: the car does not report what each part is set to), and the tyre block reading the car's own units (it showed 32 psi as 464 psi). The layer budget per theme is 24 after the car drew all sixteen probe layers of the calibration grid.
- **Cluster Themes 0.7.5:** the Sport and M rev scales end at 7000 rpm, like the M40i's own cluster (they read to 8 before). The red marks always cover at least the last 500 rpm, with the yellow ones in the 700 rpm before them.
- **Cluster Themes 0.7.4:** the M theme rebuilt against the M8 photo. Bowed glass rev blades with a red marker at the rpm, charcoal cards, white gear and speed, and the tyre pressure and temperature block (from the car's tyre monitor) in place of the G-meter.

## Nov 5 2026

- **Cluster Themes 0.7.3, HUD Widgets 0.5.3:** the calibration grids carry a line of text drawn by a shader from a glyph atlas, a test for a lighter way to draw readouts.
- **Cluster Themes 0.7.2, HUD Widgets 0.5.2:** the drawing engine can use several connections to cluster-render (8 layers each), counts the pixel memory a layout costs, and sends only the pixels of text that changed.
- **Cluster Themes 0.7.1, HUD Widgets 0.5.1:** the shift lights come on from 3000 rpm, so they are not dark when revving parked.
- **Cluster Themes 0.7.0:** the Dials theme is removed after the car test.
- **Cluster Themes 0.6.0, HUD Widgets 0.5.0:** fixes from the second car test. Every theme fits in the car's limit of 8 layers, backdrops are pure black (dark tints show on the panel), and nothing sits under the hood line or BMW's black gear box. The HUD layouts are fitted to the visible window measured from the grid photo.
- **Cluster Themes 0.5.1:** the M and Sport themes match BMW's own clusters.
- **Cluster Themes 0.5.0:** a Dials theme (removed again in 0.7.0), Sport restyled, 200 mph speed scale.

## Nov 4 2026

- **Cluster Themes 0.4.0:** Sport theme, after BMW's Live Cockpit view.
- **Cluster Themes 0.3.0:** M theme.
- **Cluster Themes 0.2.0, HUD Widgets 0.4.0:** fixes from the first car test. Shapes drew upside down, boost did not show (now read from the engine computer as a backup), and the rpm wobbled at idle. Both apps get an Off option and drive mode switching; the calibration grids are added.
- **Cluster Themes 0.1.0:** first release, with the GT3 race dash.
- **HUD Widgets 0.3.0:** Daily, Sport and Redline layouts.
