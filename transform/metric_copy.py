"""Display copy for the metric register. Hand-written for shipped metrics only."""

from __future__ import annotations

# Opta season counts that ship (extended). Everything else from Pulse stays archive.
# Grouped so the catalogue has a home for passing, territory and duels.
OPTA_COUNTS: dict[str, str] = {
    # Passing
    "total_pass": "Passing",
    "accurate_pass": "Passing",
    "fwd_pass": "Passing",
    "backward_pass": "Passing",
    "total_fwd_zone_pass": "Passing",
    "accurate_fwd_zone_pass": "Passing",
    "total_back_zone_pass": "Passing",
    "accurate_back_zone_pass": "Passing",
    "total_long_balls": "Passing",
    "accurate_long_balls": "Passing",
    "total_cross": "Passing",
    "accurate_cross": "Passing",
    "total_through_ball": "Passing",
    "accurate_through_ball": "Passing",
    "total_final_third_passes": "Passing",
    "successful_final_third_passes": "Passing",
    "open_play_pass": "Passing",
    "successful_open_play_pass": "Passing",
    "total_chipped_pass": "Passing",
    "accurate_chipped_pass": "Passing",
    "blocked_pass": "Passing",
    "head_pass": "Passing",
    "long_pass_own_to_opp": "Passing",
    "long_pass_own_to_opp_success": "Passing",
    # Territory
    "touches": "Territory",
    "touches_in_final_third": "Territory",
    "touches_in_opp_box": "Territory",
    "carries": "Territory",
    "progressive_carries": "Territory",
    "final_third_entries": "Territory",
    "pen_area_entries": "Territory",
    "unsuccessful_touch": "Territory",
    "dispossessed": "Territory",
    "poss_lost_all": "Territory",
    "turnover": "Territory",
    "total_offside": "Territory",
    # Duels
    "duel_won": "Duels",
    "duel_lost": "Duels",
    "aerial_won": "Duels",
    "aerial_lost": "Duels",
    "total_contest": "Duels",
    "won_contest": "Duels",
    "total_tackle": "Duels",
    "won_tackle": "Duels",
    "attempted_tackle_foul": "Duels",
    "times_tackled": "Duels",
    "was_fouled": "Duels",
    "fouls": "Duels",
    # Attacking
    "goals": "Attacking",
    "goal_assist": "Attacking",
    "total_scoring_att": "Attacking",
    "ontarget_scoring_att": "Attacking",
    "blocked_scoring_att": "Attacking",
    "shot_off_target": "Attacking",
    "hit_woodwork": "Attacking",
    "att_hd_total": "Attacking",
    "att_ibox_goal": "Attacking",
    "att_ibox_target": "Attacking",
    "att_ibox_miss": "Attacking",
    "att_ibox_blocked": "Attacking",
    "att_ibox_post": "Attacking",
    "attempts_ibox": "Attacking",
    "attempts_obox": "Attacking",
    "big_chance_created": "Attacking",
    "big_chance_missed": "Attacking",
    "big_chance_scored": "Attacking",
    "total_att_assist": "Attacking",
    "penalty_won": "Attacking",
    "att_openplay": "Attacking",
    "att_setpiece": "Attacking",
    # Defensive
    "interception": "Defensive",
    "interception_won": "Defensive",
    "effective_clearance": "Defensive",
    "total_clearance": "Defensive",
    "ball_recovery": "Defensive",
    "poss_won_att_3rd": "Defensive",
    "poss_won_mid_3rd": "Defensive",
    "poss_won_def_3rd": "Defensive",
    "outfielder_block": "Defensive",
    "blocked_cross": "Defensive",
    "clearance_off_line": "Defensive",
    "last_man_tackle": "Defensive",
    "error_lead_to_goal": "Defensive",
    "error_lead_to_shot": "Defensive",
    "goals_conceded": "Defensive",
    "clean_sheet": "Defensive",
    # Goalkeeping
    "saves": "Goalkeeping",
    "diving_save": "Goalkeeping",
    "penalty_save": "Goalkeeping",
    "punches": "Goalkeeping",
    "good_high_claim": "Goalkeeping",
    "total_keeper_sweeper": "Goalkeeping",
    "keeper_pick_up": "Goalkeeping",
    "goal_kicks": "Goalkeeping",
    # Discipline
    "yellow_card": "Discipline",
    "red_card": "Discipline",
    "own_goals": "Discipline",
    "hand_ball": "Discipline",
    "second_yellow": "Discipline",
}

OPTA_RATIOS = {
    "pass_accuracy": "Passing",
    "cross_accuracy": "Passing",
    "duel_win_pct": "Duels",
    "aerial_win_pct": "Duels",
    "shot_accuracy": "Attacking",
}

OPTA_LOWER_BETTER = {
    "duel_lost",
    "aerial_lost",
    "unsuccessful_touch",
    "dispossessed",
    "poss_lost_all",
    "turnover",
    "total_offside",
    "fouls",
    "attempted_tackle_foul",
    "times_tackled",
    "shot_off_target",
    "big_chance_missed",
    "error_lead_to_goal",
    "error_lead_to_shot",
    "goals_conceded",
    "yellow_card",
    "red_card",
    "own_goals",
    "hand_ball",
    "second_yellow",
}

# id -> (label, definition, caveat, group, direction)
# direction 1 = higher is better, -1 = lower is better
FPL_COPY: dict[str, tuple[str, str, str, str, int]] = {
    "goals": (
        "Goals",
        "Number of goals FPL credited to the player this season. Own goals are counted separately.",
        "FPL's scorer can differ from Opta's on deflections and messy goalmouths. Use this figure for fantasy; use o_goals for the football count.",
        "Attacking",
        1,
    ),
    "assists": (
        "Assists",
        "Number of assists FPL credited. The last touch before a goal, under FPL's own rules — not Opta's.",
        "FPL is generous with deflections and rebounds. o_goal_assist is the Opta football count and will not match.",
        "Attacking",
        1,
    ),
    "xg": (
        "Expected goals",
        "The sum of the scoring probability of every shot taken. Includes penalties at roughly 0.79 each, which inflates the figure for designated takers.",
        "A model, not a count. Opta does not publish expected goals here; this is FPL's feed.",
        "Attacking",
        1,
    ),
    "xa": (
        "Expected assists",
        "The sum of the scoring probability of shots that followed this player's key passes. It asks how good the chances created were, not whether they were finished.",
        "Does not credit the assist if FPL would not. Quiet builders who rarely play the last pass will look low.",
        "Attacking",
        1,
    ),
    "xgi": (
        "Expected goal involvements",
        "Expected goals plus expected assists. A single number for 'how many goals this player's actions were worth'.",
        "Double-counts nothing, but a penalty taker who also creates will dominate the ranking.",
        "Attacking",
        1,
    ),
    "xgc": (
        "Expected goals conceded",
        "The sum of the scoring probability of shots faced while the player was on the pitch, according to FPL.",
        "High for centre-backs and keepers in weak sides. Read alongside saves and goals conceded, not as a personal failing.",
        "Defensive",
        -1,
    ),
    "tackles": (
        "Tackles",
        "Tackles FPL counted toward defensive contribution. The definition is FPL's, not Opta's.",
        "Present 2016-17 to 2018-19, then absent until 2025-26. Do not fill the hole with Opta tackles — they are a different count.",
        "Defensive",
        1,
    ),
    "recoveries": (
        "Recoveries",
        "Loose-ball recoveries FPL counted toward defensive contribution.",
        "Same 2019-24 hole as tackles. Opta ball recoveries (o_ball_recovery) are not a substitute.",
        "Defensive",
        1,
    ),
    "clearances_blocks_interceptions": (
        "Clearances, blocks and interceptions",
        "FPL's combined clearance, block and interception count. One of the inputs to defensive contribution.",
        "Inflated for defenders in sides that spend long spells under pressure. Same 2019-24 hole as tackles.",
        "Defensive",
        1,
    ),
    "defensive_contribution": (
        "Defensive contribution",
        "FPL's scoring stat: tackles, interceptions, clearances and recoveries combined, with a per-position threshold that awards points.",
        "Fantasy always uses this number because it scored the points. Opta tackles, interceptions and clearances are the football measurement of those actions, not a correction.",
        "Defensive",
        1,
    ),
    "saves": (
        "Saves",
        "Shots FPL recorded as saved by this keeper.",
        "High for keepers in weak teams, who face more shots. Read alongside expected goals conceded.",
        "Goalkeeping",
        1,
    ),
    "goals_conceded": (
        "Goals conceded",
        "Goals FPL charged to the player while they were on the pitch. Keepers and defenders are scored on this; midfielders and forwards usually are not.",
        "Almost entirely a team-quality signal. A full-back on a leaking side will look worse than a centre-back on a tight one.",
        "Goalkeeping",
        -1,
    ),
    "clean_sheets": (
        "Clean sheets",
        "Matches of 60+ minutes in which the player's side conceded no goals, as FPL scores them.",
        "Almost entirely a team-quality signal.",
        "Goalkeeping",
        1,
    ),
    "penalties_saved": (
        "Penalties saved",
        "Penalties FPL recorded as saved by this keeper.",
        "Rare. A season with one save will dominate any per-90 ranking; the adjusted rate exists to damp that.",
        "Goalkeeping",
        1,
    ),
    "penalties_missed": (
        "Penalties missed",
        "Penalties this player took and did not score, as FPL scored them (saved or off target).",
        "FPL deducts points. A single miss is a large swing on a thin sample.",
        "Discipline",
        -1,
    ),
    "total_points": (
        "Total points",
        "Sum of FPL fantasy points for the season, including bonus.",
        "A game construct, not a football measurement. Minutes, position and bonus luck all sit inside it.",
        "Fantasy",
        1,
    ),
    "bps": (
        "Bonus point system",
        "FPL's match-level score used to award the 3-2-1 bonus. Higher BPS is more likely to take the bonus.",
        "The weights are FPL's and unpublished in full. Do not treat BPS as a complete performance rating.",
        "Fantasy",
        1,
    ),
    "bonus": (
        "Bonus",
        "Bonus fantasy points actually awarded (3, 2 or 1 among the top BPS scores in the match).",
        "A ranking prize, not a count of good actions. Tied BPS can leave a high-scoring player with nothing.",
        "Fantasy",
        1,
    ),
    "influence": (
        "Influence",
        "One of FPL's three ICT components. Intended to capture how much the player affected the match.",
        "FPL's own composite. Weightings are proprietary and unpublished.",
        "Fantasy",
        1,
    ),
    "creativity": (
        "Creativity",
        "ICT component intended to capture chance creation.",
        "FPL's own composite. Weightings are proprietary and unpublished.",
        "Fantasy",
        1,
    ),
    "threat": (
        "Threat",
        "ICT component intended to capture shooting threat.",
        "FPL's own composite. Weightings are proprietary and unpublished.",
        "Fantasy",
        1,
    ),
    "ict_index": (
        "ICT index",
        "FPL's composite of influence, creativity and threat.",
        "FPL's own composite. Weightings are proprietary and unpublished.",
        "Fantasy",
        1,
    ),
    "yellow_cards": (
        "Yellow cards",
        "Bookings FPL recorded. Each costs fantasy points.",
        "A discipline count, not a toughness rating. FPL and Opta booking totals can differ on delayed cards.",
        "Discipline",
        -1,
    ),
    "red_cards": (
        "Red cards",
        "Dismissals FPL recorded, including second yellows.",
        "Rare. Per-90 rankings on one red are not meaningful.",
        "Discipline",
        -1,
    ),
    "own_goals": (
        "Own goals",
        "Goals FPL charged as own goals.",
        "FPL and Opta sometimes disagree on deflections. Rare; do not rank on per-90.",
        "Discipline",
        -1,
    ),
    "minutes": (
        "Minutes",
        "Minutes FPL recorded on the pitch this season. The denominator for every per-90 the frontend derives.",
        "FPL minutes can differ from Opta mins_played on stoppage time. Use this figure with FPL stats; do not mix.",
        "Playing time",
        1,
    ),
    "starts": (
        "Starts",
        "Matches FPL recorded as started (named in the XI).",
        "A start can still be a 45-minute half. Pair with minutes and appearances.",
        "Playing time",
        1,
    ),
    "appearances": (
        "Appearances",
        "Matches with any minutes. A double gameweek is two if the player played both, not one.",
        "Not a gameweek-row count. The fixture grain is the match.",
        "Playing time",
        1,
    ),
    "appearances_60_plus": (
        "Appearances 60+",
        "Matches with at least 60 minutes. Aligns with FPL's clean-sheet threshold.",
        "A 59-minute game does not count. Useful as a 'full match' floor, not as playing time itself.",
        "Playing time",
        1,
    ),
    "team_minutes_available": (
        "Team minutes available",
        "Minutes the player's club was playing fixtures while they were at that club this season.",
        "The denominator for minutes share. NULL-safe; movers are split by spell in the mart.",
        "Playing time",
        1,
    ),
    "minutes_share": (
        "Minutes share",
        "The player's minutes as a fraction of team minutes available.",
        "A rotation and fitness signal, not a quality signal.",
        "Playing time",
        1,
    ),
    "price": (
        "Price",
        "FPL selling price in millions, from bootstrap snapshots (now_cost / 10).",
        "Historical days come from fplcache, current days from our own snapshots. Both are FPL. NULL in the table before 2021-22.",
        "Fantasy",
        1,
    ),
    "ownership": (
        "Ownership",
        "Share of FPL teams owning the player, in percentage points, from the same snapshots as price.",
        "own_7d and own_30d are changes versus a snapshot about 7 or 30 days earlier, not rolling averages. NULL if no snapshot sat close enough.",
        "Fantasy",
        1,
    ),
}

OPTA_COPY: dict[str, tuple[str, str, str]] = {
    # label, definition, caveat
    "total_pass": (
        "Total passes",
        "Every pass attempted, including short balls in defence and unsuccessful ones. Not a measure of quality; see pass accuracy.",
        "Tracks minutes and team possession more than passing skill. A holding midfielder on a dominant side will lead the league.",
    ),
    "accurate_pass": (
        "Accurate passes",
        "Passes Opta recorded as reaching a teammate. The numerator of pass accuracy.",
        "A five-yard sideways pass counts the same as a 40-yard switch.",
    ),
    "fwd_pass": (
        "Forward passes",
        "Passes Opta classified as travelling toward the opposition goal, regardless of whether they were completed.",
        "Direction, not progress. A forward pass can still be played in the defensive third.",
    ),
    "backward_pass": (
        "Backward passes",
        "Passes Opta classified as travelling toward the player's own goal.",
        "High for wingers who receive high and come back to feet. Not inherently negative.",
    ),
    "total_fwd_zone_pass": (
        "Forward-zone passes",
        "Passes attempted from the attacking half of the pitch.",
        "Where the pass was struck, not where it arrived.",
    ),
    "accurate_fwd_zone_pass": (
        "Accurate forward-zone passes",
        "Completed passes struck from the attacking half.",
        "Completion here is easier on a dominant team playing around the box.",
    ),
    "total_back_zone_pass": (
        "Back-zone passes",
        "Passes attempted from the defensive half.",
        "Centre-backs and deep midfielders accumulate these by default.",
    ),
    "accurate_back_zone_pass": (
        "Accurate back-zone passes",
        "Completed passes struck from the defensive half.",
        "Very high completion is normal; this is not a skill ranking on its own.",
    ),
    "total_long_balls": (
        "Long balls",
        "Passes Opta classified as long, attempted. Includes switches and clearances played as passes.",
        "Opta's distance cut-off is not published. Do not treat as 'accurate long passing' without the accurate companion.",
    ),
    "accurate_long_balls": (
        "Accurate long balls",
        "Long passes that reached a teammate.",
        "A clearance that happens to find a teammate can count. Pair with total long balls.",
    ),
    "total_cross": (
        "Crosses",
        "Open-play and set-piece crosses attempted, including corners in some Opta flavours of this field.",
        "High for wide players and overlapping full-backs. Completion is usually low; volume is the style signal.",
    ),
    "accurate_cross": (
        "Accurate crosses",
        "Crosses that found a teammate.",
        "A completed cross is not a chance. See big chances created and expected assists for end product.",
    ),
    "total_through_ball": (
        "Through balls",
        "Passes Opta labelled as through balls — typically splitting the last line.",
        "Opta's label is unpublished in detail. Rare; a handful of players dominate the count.",
    ),
    "accurate_through_ball": (
        "Accurate through balls",
        "Through balls that reached a teammate.",
        "Small samples. Do not rank a squad player on two completed through balls.",
    ),
    "total_final_third_passes": (
        "Final-third passes",
        "Passes attempted in the attacking third.",
        "Team-possession dependent. A deep-lying playmaker on a cautious side may look quiet.",
    ),
    "successful_final_third_passes": (
        "Successful final-third passes",
        "Completed passes in the attacking third.",
        "Completion in this zone is harder than at the back; still not a chance-creation stat.",
    ),
    "open_play_pass": (
        "Open-play passes",
        "Passes attempted from open play, excluding set pieces.",
        "Most of total_pass. Useful when you want to ignore corners and free kicks.",
    ),
    "successful_open_play_pass": (
        "Successful open-play passes",
        "Completed open-play passes.",
        "Same caution as accurate_pass: volume follows possession.",
    ),
    "total_chipped_pass": (
        "Chipped passes",
        "Passes Opta recorded as chipped (lifted over a defender or line).",
        "Opta's chip definition is unpublished. Not the same as a through ball.",
    ),
    "accurate_chipped_pass": (
        "Accurate chipped passes",
        "Chipped passes that reached a teammate.",
        "Rare. Treat as a flavour of passing, not a ranking stat.",
    ),
    "blocked_pass": (
        "Blocked passes",
        "Attempted passes Opta recorded as blocked by an opponent.",
        "High for players who try to play through pressure. Not a tackle by the blocker — that is on the other team.",
    ),
    "head_pass": (
        "Headed passes",
        "Passes played with the head.",
        "Centre-backs and target forwards accumulate these. Not aerial duels.",
    ),
    "long_pass_own_to_opp": (
        "Long passes into the opposition half",
        "Long passes that started in the player's own half and were aimed into the opposition half.",
        "A direct-play signal. Completion is in the companion field.",
    ),
    "long_pass_own_to_opp_success": (
        "Successful long passes into the opposition half",
        "Those long passes that reached a teammate.",
        "A completed long ball up the pitch, not necessarily a chance.",
    ),
    "touches": (
        "Touches",
        "Times the player had the ball, by Opta's touch definition. The usual volume denominator for carry and take-on rates.",
        "Follows minutes and team possession. A 90-minute centre-back on a dominant side can out-touch a winger.",
    ),
    "touches_in_final_third": (
        "Final-third touches",
        "Touches in the attacking third.",
        "Not populated league-wide until 2024-25. Earlier seasons have stray players only; treat pre-2024-25 as missing, not zero.",
    ),
    "touches_in_opp_box": (
        "Opposition-box touches",
        "Touches inside the opponent's penalty area.",
        "Forwards and attacking full-backs. A box touch is not a shot.",
    ),
    "carries": (
        "Carries",
        "Times the player moved the ball with their feet rather than passing it.",
        "Not populated league-wide until 2024-25. 2020-21 and 2022-23 have no carries at all. Do not back-fill.",
    ),
    "progressive_carries": (
        "Progressive carries",
        "Carries that move the ball a meaningful distance toward the opposition goal. Opta's distance threshold is not published.",
        "Same 2024-25 coverage boundary as carries. Without the threshold, do not over-interpret small differences.",
    ),
    "final_third_entries": (
        "Final-third entries",
        "Times the player carried or passed the ball into the attacking third.",
        "An entry is not a chance. High for ball-progressors, wide or central.",
    ),
    "pen_area_entries": (
        "Penalty-area entries",
        "Times the player carried or passed the ball into the opposition box.",
        "Closer to chance creation than final-third entries, but still not a shot.",
    ),
    "unsuccessful_touch": (
        "Unsuccessful touches",
        "Touches Opta recorded as miscontrolled — the player had the ball and immediately lost it without being tackled.",
        "High for players who receive under pressure. Pair with dispossessed for on-the-ball losses.",
    ),
    "dispossessed": (
        "Dispossessed",
        "Times the player was tackled in possession and lost the ball.",
        "Wingers who dribble a lot will rank high. Not a passing turnover.",
    ),
    "poss_lost_all": (
        "Possessions lost",
        "All ways of giving the ball away, as Opta totals them.",
        "Volume follows touches. Per-touch loss rate is the style signal, not the raw count.",
    ),
    "turnover": (
        "Turnovers",
        "Possessions lost in situations Opta labels a turnover.",
        "Opta's split versus poss_lost_all is unpublished. Use as a flavour of loss, not a second ranking.",
    ),
    "total_offside": (
        "Offsides",
        "Times the player was caught offside.",
        "A movement and timing tell for forwards. Team line and referee style both sit inside it.",
    ),
    "duel_won": (
        "Duels won",
        "Ground and aerial 50-50s Opta recorded as won.",
        "Winning lots of duels can mean you were in lots of duels — often a press or a long-ball side, not a 'better' player.",
    ),
    "duel_lost": (
        "Duels lost",
        "50-50s Opta recorded as lost.",
        "Read with duels won. A high loss count with a high win count is volume, not failure.",
    ),
    "aerial_won": (
        "Aerials won",
        "Aerial duels won.",
        "Centre-backs and target forwards. Heading technique and the service they get are mixed together.",
    ),
    "aerial_lost": (
        "Aerials lost",
        "Aerial duels lost.",
        "Short players in a long-ball side lose many without that meaning they 'cannot head'.",
    ),
    "total_contest": (
        "Take-ons attempted",
        "Dribbles / 1v1s attempted. The Opta field is total_contest.",
        "Wingers live here. Attempt volume is a style signal; success rate is quality and is not used in clustering.",
    ),
    "won_contest": (
        "Take-ons completed",
        "Take-ons Opta recorded as beaten.",
        "A completed take-on is not a chance. Pair with box touches and shots.",
    ),
    "total_tackle": (
        "Tackles (Opta)",
        "Tackles Opta recorded. This is the football count, not FPL's defensive-contribution tackle.",
        "Will not match FPL tackles. Use this in the Defensive / football view; use FPL tackles only where points were scored.",
    ),
    "won_tackle": (
        "Tackles won",
        "Tackles Opta recorded as successful (possession won or the ball going out from the tackle).",
        "The gap between total_tackle and won_tackle is failed tackles, not fouls — those sit in attempted_tackle_foul.",
    ),
    "attempted_tackle_foul": (
        "Tackles that became fouls",
        "Attempted tackles Opta recorded as fouls.",
        "A foul tackle is still a defensive action. Not the same as a yellow card.",
    ),
    "times_tackled": (
        "Times tackled",
        "Times this player was tackled by an opponent.",
        "High for dribblers. The other side of won_tackle.",
    ),
    "was_fouled": (
        "Fouled",
        "Times an opponent fouled this player.",
        "Dribblers and players who receive between the lines draw fouls. Not a diving count.",
    ),
    "fouls": (
        "Fouls committed",
        "Fouls this player committed.",
        "A discipline and a defending-style signal. Not the same as yellow cards.",
    ),
    "goals": (
        "Goals (Opta)",
        "Goals Opta credited. Own goals are separate.",
        "Can disagree with FPL on deflections. Use o_goals for football, goals for fantasy points.",
    ),
    "goal_assist": (
        "Assists (Opta)",
        "Assists Opta credited — typically the last pass before the goal under Opta rules.",
        "Stricter than FPL assists on rebounds. Do not mix with the FPL assist column.",
    ),
    "total_scoring_att": (
        "Shots",
        "All shots recorded, including blocked, off target and goals. Opta's total_scoring_att.",
        "A blocked shot still counts. Volume follows role more than finishing.",
    ),
    "ontarget_scoring_att": (
        "Shots on target",
        "Shots on target, including goals.",
        "The numerator of shot accuracy. Does not say how good the chances were.",
    ),
    "blocked_scoring_att": (
        "Shots blocked",
        "Shots blocked by an outfielder before they reached the goalkeeper.",
        "High for players who shoot through crowds. Not a save.",
    ),
    "shot_off_target": (
        "Shots off target",
        "Shots that missed the frame without being blocked.",
        "Includes wild efforts and narrow misses. Pair with on target, not with xG.",
    ),
    "hit_woodwork": (
        "Hit woodwork",
        "Shots that struck the post or bar.",
        "Rare. Do not rank on it.",
    ),
    "att_hd_total": (
        "Headed shots",
        "Shots taken with the head, on or off target.",
        "Target forwards and attacking centre-backs. A headed shot is not an aerial duel won.",
    ),
    "att_ibox_goal": (
        "Goals from inside the box",
        "Goals Opta tagged as from inside the penalty area.",
        "Most goals. Outside-the-box goals sit in the open-play / set-piece splits, not here.",
    ),
    "att_ibox_target": (
        "Shots on target from inside the box",
        "On-target shots from inside the penalty area.",
        "The usual finishing zone. Volume follows box entries.",
    ),
    "att_ibox_miss": (
        "Shots missed from inside the box",
        "Off-target shots from inside the penalty area.",
        "A miss from close range is still a miss; xG would have been high.",
    ),
    "att_ibox_blocked": (
        "Shots blocked from inside the box",
        "Blocked shots from inside the penalty area.",
        "Often a crowded six-yard box. Not a keeper save.",
    ),
    "att_ibox_post": (
        "Shots that hit the woodwork from inside the box",
        "Inside-the-box shots that struck post or bar.",
        "Rare.",
    ),
    "attempts_ibox": (
        "Shots from inside the box",
        "All shots from inside the penalty area.",
        "The parent of the att_ibox_* split. Prefer this for a single inside-box volume.",
    ),
    "attempts_obox": (
        "Shots from outside the box",
        "All shots from outside the penalty area.",
        "High volume here with low xG is a long-shot profile, not a high scorer.",
    ),
    "big_chance_created": (
        "Big chances created",
        "Chances Opta labelled 'big' that this player created for a teammate.",
        "Opta's 'big chance' cut-off is unpublished. Clearer than raw shot assists, still not xA.",
    ),
    "big_chance_missed": (
        "Big chances missed",
        "Big chances this player had and did not score.",
        "Finishing luck sits here. A striker can rank high because they get the chances, not because they are wasteful.",
    ),
    "big_chance_scored": (
        "Big chances scored",
        "Big chances this player converted.",
        "The companion to big chances missed. Conversion = scored / (scored + missed), if you derive it.",
    ),
    "total_att_assist": (
        "Shot assists",
        "Passes that led directly to a teammate's shot, on or off target.",
        "Broader than Opta assists and broader than big chances created. A shot assist is not an expected assist.",
    ),
    "penalty_won": (
        "Penalties won",
        "Penalties awarded after a foul on this player, as Opta recorded it.",
        "Not the same as penalties taken or scored. Rare.",
    ),
    "att_openplay": (
        "Open-play shots",
        "Shots from open play, excluding set pieces and penalties.",
        "The usual attacking-pattern shot. Compare with att_setpiece.",
    ),
    "att_setpiece": (
        "Set-piece shots",
        "Shots from free kicks, corners and similar restarts, as Opta grouped them.",
        "Specialists and penalty-box attackers on corners. Not the same as att_freekick_total.",
    ),
    "interception": (
        "Interceptions",
        "Passes Opta recorded this player as intercepting.",
        "High for players in passing lanes, often in a mid or low block. Not FPL's CBI interception slice.",
    ),
    "interception_won": (
        "Interceptions won",
        "Interceptions that ended with this player in possession.",
        "The successful subset of interception. A cut-out that bounces away may sit only on interception.",
    ),
    "effective_clearance": (
        "Effective clearances",
        "Clearances Opta judged to have actually relieved pressure.",
        "Inflated for defenders under siege. total_clearance is the rawer count.",
    ),
    "total_clearance": (
        "Clearances",
        "All clearances attempted.",
        "Same pressure bias as effective_clearance. Not FPL CBI.",
    ),
    "ball_recovery": (
        "Ball recoveries",
        "Loose balls Opta credited this player with recovering.",
        "Not FPL recoveries. High for energetic midfielders and for players on the second ball from long kicks.",
    ),
    "poss_won_att_3rd": (
        "Possessions won in the attacking third",
        "Balls won back in the final third. The numerator of press height.",
        "A high press signal. Low totals can mean a low block, not laziness.",
    ),
    "poss_won_mid_3rd": (
        "Possessions won in the middle third",
        "Balls won back in midfield.",
        "The middle slice of press height.",
    ),
    "poss_won_def_3rd": (
        "Possessions won in the defensive third",
        "Balls won back in the defensive third.",
        "High for a low block. The denominator of press height with the other two thirds.",
    ),
    "outfielder_block": (
        "Blocks",
        "Shots blocked by this outfielder.",
        "Centre-backs in a low block accumulate these. Not a keeper save.",
    ),
    "blocked_cross": (
        "Crosses blocked",
        "Crosses this player blocked.",
        "Full-backs defending the byline. Different from blocked_pass.",
    ),
    "clearance_off_line": (
        "Goal-line clearances",
        "Clearances off the line, preventing a goal.",
        "Rare. Do not rank on it.",
    ),
    "last_man_tackle": (
        "Last-man tackles",
        "Tackles Opta tagged as last-man (covering a through ball or break).",
        "Rare and context-heavy. A missed last-man tackle is a different field.",
    ),
    "error_lead_to_goal": (
        "Errors leading to a goal",
        "Mistakes Opta judged led directly to a goal.",
        "Subjective and rare. A high-usage defender will have more chances to error.",
    ),
    "error_lead_to_shot": (
        "Errors leading to a shot",
        "Mistakes Opta judged led to a shot.",
        "Broader than errors leading to a goal, still rare and labelled.",
    ),
    "goals_conceded": (
        "Goals conceded (Opta)",
        "Goals conceded while this player was on the pitch, by Opta.",
        "Team-quality signal. Can disagree with FPL on timing of substitutions.",
    ),
    "clean_sheet": (
        "Clean sheets (Opta)",
        "Matches Opta recorded as a clean sheet for this player.",
        "Opta's minutes rule may differ from FPL's 60-minute threshold. Do not mix with FPL clean sheets.",
    ),
    "saves": (
        "Saves (Opta)",
        "Shots Opta recorded this keeper as saving.",
        "High for keepers who face more shots. Read with o_goals_conceded, not as a ranking of quality alone.",
    ),
    "diving_save": (
        "Diving saves",
        "Saves Opta tagged as diving.",
        "A subset of saves. Spectacular, not necessarily higher leverage than a standing save.",
    ),
    "penalty_save": (
        "Penalties saved (Opta)",
        "Penalties Opta recorded as saved.",
        "Rare. FPL penalties_saved is the fantasy figure.",
    ),
    "punches": (
        "Punches",
        "Crosses or balls the keeper punched clear.",
        "A claiming style. High punches with low claims is a punching keeper, not a better one.",
    ),
    "good_high_claim": (
        "High claims",
        "Crosses the keeper came and caught cleanly, as Opta judged 'good'.",
        "The companion to punches. Opta's 'good' label is unpublished.",
    ),
    "total_keeper_sweeper": (
        "Sweeper-keeper actions",
        "Times the keeper left their box to intercept or clear, as Opta recorded them.",
        "A style signal. Failed sweepers sit on related fields, not as negative here.",
    ),
    "keeper_pick_up": (
        "Keeper pick-ups",
        "Times the keeper picked up a loose back-pass or similar, rather than claiming a cross.",
        "Routine. High totals follow minutes more than skill.",
    ),
    "goal_kicks": (
        "Goal kicks",
        "Goal kicks taken.",
        "Distribution volume. Accuracy is accurate_goal_kicks, which is archive-adjacent unless shipped.",
    ),
    "yellow_card": (
        "Yellow cards (Opta)",
        "Bookings Opta recorded.",
        "Can disagree with FPL on timing. Use FPL yellows for points.",
    ),
    "red_card": (
        "Red cards (Opta)",
        "Dismissals Opta recorded.",
        "Rare. FPL reds are the fantasy figure.",
    ),
    "own_goals": (
        "Own goals (Opta)",
        "Own goals Opta credited.",
        "Deflections are the usual disagreement with FPL.",
    ),
    "hand_ball": (
        "Handballs",
        "Handball offences Opta recorded.",
        "Not all are bookings. Rare as a ranking stat.",
    ),
    "second_yellow": (
        "Second yellow cards",
        "Dismissals via a second yellow.",
        "A subset of reds. Rare.",
    ),
    "pass_accuracy": (
        "Pass accuracy",
        "Completed passes divided by attempted passes. NULL if the player attempted none.",
        "Easy to inflate with safe passes. Clustering leaves this out because it is quality, not shape.",
    ),
    "cross_accuracy": (
        "Cross accuracy",
        "Completed crosses divided by attempted crosses. NULL if none were attempted.",
        "Low for almost everyone. A 30% rate is already high.",
    ),
    "duel_win_pct": (
        "Duel win rate",
        "Duels won divided by duels won plus lost. NULL if the player had no duels.",
        "A 60% rate in 10 duels is not the same as 60% in 200.",
    ),
    "aerial_win_pct": (
        "Aerial win rate",
        "Aerials won divided by aerials contested. NULL if none were contested.",
        "Quality, not style — clustering leaves it out. Short samples mislead.",
    ),
    "shot_accuracy": (
        "Shot accuracy",
        "Shots on target divided by shots. NULL if the player took none.",
        "Does not say how good the chances were. A tap-in and a 30-yard drive both count as on or off.",
    ),
}

# Display format for clustering features in clusters.json features_meta.
# pct = 0-1 share shown as a percentage; 3dp = small event rates.
STYLE_FEATURE_FORMAT: dict[str, str] = {
    "through_ball_share": "3dp",
    "shots_per_touch": "3dp",
    "take_on_rate": "3dp",
    "loss_rate": "3dp",
}

STYLE_COPY: dict[str, tuple[str, str, str, str]] = {
    "fwd_pass_share": (
        "Forward pass share",
        "Forward passes as a fraction of all passes. A shape signal: how often the player looks up the pitch.",
        "Needs 200 passes or it is left blank. High for deep progressors; low for wingers who play backward into feet.",
        "Passing",
    ),
    "backward_pass_share": (
        "Backward pass share",
        "Backward passes as a fraction of all passes.",
        "High for players who receive high and recycle. Needs 200 passes.",
        "Passing",
    ),
    "long_ball_share": (
        "Long-ball share",
        "Long balls as a fraction of all passes.",
        "Direct-play shape, not accuracy. Needs 200 passes.",
        "Passing",
    ),
    "cross_share": (
        "Cross share",
        "Crosses as a fraction of all passes.",
        "Wide-deliverer shape. Needs 200 passes.",
        "Passing",
    ),
    "through_ball_share": (
        "Through-ball share",
        "Through balls as a fraction of all passes.",
        "Rare events on a large denominator. Needs 200 passes.",
        "Passing",
    ),
    "fwd_zone_pass_share": (
        "Forward-zone pass share",
        "Passes struck from the attacking half, as a fraction of all passes.",
        "Where the player stands more than how they pass. Needs 200 passes.",
        "Passing",
    ),
    "pass_accuracy": (
        "Pass accuracy",
        "Completed passes over attempted passes, after the style floors.",
        "Quality, not shape — excluded from clustering. Needs 200 passes.",
        "Passing",
    ),
    "final_third_touch_share": (
        "Final-third touch share",
        "Final-third touches as a fraction of all touches.",
        "Needs 200 touches. Carries and final-third touches are not league-wide until 2024-25.",
        "Territory",
    ),
    "opp_box_touch_share": (
        "Box touch share",
        "Opposition-box touches as a fraction of all touches.",
        "Needs 200 touches.",
        "Territory",
    ),
    "carry_rate": (
        "Carry rate",
        "Carries divided by touches. How often a touch becomes a carry.",
        "Needs 200 touches. Carries are not league-wide until 2024-25; 2020-21 and 2022-23 are missing entirely.",
        "Territory",
    ),
    "progressive_carry_share": (
        "Progressive carry share",
        "Progressive carries as a fraction of carries.",
        "Needs 200 touches and a non-zero carry count. Same 2024-25 boundary as carries.",
        "Territory",
    ),
    "shots_per_touch": (
        "Shots per touch",
        "Shots divided by touches. How shoot-first the player is.",
        "Needs 200 touches. Forwards will dominate; that is shape, not finishing.",
        "Attacking",
    ),
    "ibox_shot_share": (
        "Inside-box shot share",
        "Inside-the-box shots as a fraction of all shots.",
        "NULL if the player took no shots. A poacher vs a long-shot profile.",
        "Attacking",
    ),
    "head_shot_share": (
        "Headed shot share",
        "Headed shots as a fraction of all shots.",
        "NULL if no shots. Aerial attackers vs ground shooters.",
        "Attacking",
    ),
    "aerial_duel_share": (
        "Aerial duel share",
        "Aerial duels as a fraction of all duels.",
        "How much of the player's contesting is in the air.",
        "Duels",
    ),
    "aerial_win_pct": (
        "Aerial win rate",
        "Aerials won over aerials contested, after style floors.",
        "Quality, not shape — excluded from clustering.",
        "Duels",
    ),
    "tackle_share": (
        "Tackle share of defending",
        "Tackles as a fraction of tackles + interceptions + clearances + recoveries.",
        "How the player defends, not how much. NULL if that pool is empty.",
        "Duels",
    ),
    "clearance_share": (
        "Clearance share of defending",
        "Effective clearances as a fraction of the same defensive pool.",
        "High for last-line defenders. NULL if the pool is empty.",
        "Duels",
    ),
    "recovery_share": (
        "Recovery share of defending",
        "Ball recoveries as a fraction of the same defensive pool.",
        "High for scavengers rather than tacklers. NULL if the pool is empty.",
        "Duels",
    ),
    "press_height": (
        "Press height",
        "Possessions won in the attacking third as a fraction of possessions won in all thirds.",
        "A high-press vs low-block shape. NULL if the player won the ball nowhere.",
        "Duels",
    ),
    "take_on_rate": (
        "Take-on rate",
        "Take-ons attempted per touch.",
        "Needs 200 touches. The dribbler signal.",
        "Duels",
    ),
    "take_on_success": (
        "Take-on success",
        "Completed take-ons over attempted take-ons.",
        "Quality, not shape — excluded from clustering. NULL if no take-ons.",
        "Duels",
    ),
    "loss_rate": (
        "Loss rate",
        "Dispossessed plus unsuccessful touches, per touch.",
        "Needs 200 touches. High for players who try things in tight spaces.",
        "Duels",
    ),
    "pass_share_of_team": (
        "Pass share of team",
        "The player's passes as a fraction of their club's passes that season.",
        "NULL if they changed club mid-season — the Pulse row cannot be split. Not a mover approximation.",
        "Passing",
    ),
    "shot_share_of_team": (
        "Shot share of team",
        "The player's shots as a fraction of their club's shots.",
        "NULL for mid-season movers. A focal-point signal.",
        "Attacking",
    ),
    "touch_share_of_team": (
        "Touch share of team",
        "The player's touches as a fraction of their club's touches.",
        "NULL for mid-season movers. High for metronomes on dominant sides.",
        "Territory",
    ),
}

STYLE_CLUSTER_DEF = (
    "Nine playing-style archetypes from a single k-means fit on 2019-20 onward "
    "(effectively 2024-25 and 2025-26, when carries exist league-wide). "
    "0 Generalist — no dimension above ±0.15, balanced, low-volume. "
    "1 Wide deliverer — crosses, forward-zone passes. "
    "2 Between the lines — backward passes, losses, progressive carries, box touches. "
    "3 Aerial defender — headed shots, clearances, aerials. "
    "4 Shooter — shot share, shots per touch, box touches. "
    "5 Dribbler — take-ons, box touches, final-third touches. "
    "6 Deep recycler — forward and long passes, low final third, low losses. "
    "7 Progressive distributor — forward passes, long balls, through balls, high touch share. "
    "8 Ball-playing defender — aerial-defender profile plus high team pass and touch share. "
    "Keepers and managers are not clustered."
)

STYLE_CLUSTER_CAVEAT = (
    "Clusters 3 and 8 are separated largely by team pass and touch share, so the split "
    "partly reflects team possession role rather than individual technique. Do not present "
    "it as purely a player attribute. The model is 682 player-seasons, 658 of them 2024-25 "
    "and 2025-26, and will deepen by one season per year."
)


OPTA_RATIO_FORMULAS = {
    "pass_accuracy": "accurate_pass / total_pass",
    "cross_accuracy": "accurate_cross / total_cross",
    "duel_win_pct": "duel_won / (duel_won + duel_lost)",
    "aerial_win_pct": "aerial_won / (aerial_won + aerial_lost)",
    "shot_accuracy": "ontarget_scoring_att / total_scoring_att",
}

# Explicit holes the first_season column cannot carry on its own.
KNOWN_COVERAGE: dict[str, str] = {
    "tackles": (
        "Present 2016-17 to 2018-19, absent 2019-20 through 2024-25, back from 2025-26. "
        "Do not fill the hole from Opta tackles."
    ),
    "recoveries": (
        "Present 2016-17 to 2018-19, absent 2019-20 through 2024-25, back from 2025-26. "
        "Do not fill the hole from Opta ball recoveries."
    ),
    "clearances_blocks_interceptions": (
        "Present 2016-17 to 2018-19, absent 2019-20 through 2024-25, back from 2025-26. "
        "Do not fill the hole from Opta clearances or interceptions."
    ),
    "defensive_contribution": "FPL scoring stat from 2025-26 only.",
    "xg": "FPL expected-goals family from 2022-23. 2022-23 itself is partial (the scraper started mid-season).",
    "xa": "FPL expected-assists from 2022-23. 2022-23 itself is partial (the scraper started mid-season).",
    "xgi": "FPL expected goal involvements from 2022-23. 2022-23 itself is partial (the scraper started mid-season).",
    "xgc": "FPL expected goals conceded from 2022-23. 2022-23 itself is partial (the scraper started mid-season).",
    "price": "Web export starts 2021-22 (fplcache coverage). Earlier seasons are NULL, never zero.",
    "ownership": "Web export starts 2021-22 (fplcache coverage). Earlier seasons are NULL, never zero.",
    "o_carries": (
        "First appears 2019-20 for stray players, missing entirely in 2020-21 and 2022-23, "
        "not league-wide until 2024-25. Treat earlier seasons as missing, not zero."
    ),
    "o_progressive_carries": (
        "Same coverage as carries: stray values from 2019-20, holes in 2020-21 and 2022-23, "
        "not league-wide until 2024-25."
    ),
    "o_touches_in_final_third": (
        "First appears 2019-20 for stray players; not league-wide until 2024-25. "
        "Treat earlier seasons as missing, not zero."
    ),
    "pass_share_of_team": "NULL before 2020-21 (no GW team_code) and NULL for mid-season club movers.",
    "shot_share_of_team": "NULL before 2020-21 (no GW team_code) and NULL for mid-season club movers.",
    "touch_share_of_team": "NULL before 2020-21 (no GW team_code) and NULL for mid-season club movers.",
    "share_of_team": "NULL before 2020-21 (no GW team_code) and NULL for players who changed club mid-season.",
}

ARCHIVE_DEFINITION = (
    "Pulse season total of this named Opta field. Counting rules are not published; "
    "this register does not attempt a definition."
)


def opta_id(col: str) -> str:
    return col if col.startswith("o_") else f"o_{col}"


def sentence_label(name: str) -> str:
    text = name.replace("_", " ").strip()
    if not text:
        return name
    return text[0].upper() + text[1:]


def archive_group(name: str) -> str:
    n = name.lower()
    if any(
        x in n
        for x in ("save", "keeper", "punch", "claim", "goal_kick", "launch", "sweeper")
    ):
        return "Goalkeeping"
    if any(x in n for x in ("card", "hand_ball", "own_goal", "dangerous_play", "second_yellow")):
        return "Discipline"
    if any(
        x in n
        for x in (
            "pass",
            "cross",
            "through",
            "long_ball",
            "throw",
            "layoff",
            "flick",
            "chip",
            "corner",
        )
    ):
        return "Passing"
    if any(
        x in n
        for x in (
            "touch",
            "carry",
            "entries",
            "poss_lost",
            "dispossess",
            "offside",
            "distance",
            "overrun",
            "turnover",
        )
    ):
        return "Territory"
    if any(x in n for x in ("duel", "aerial", "contest", "tackle", "fouled", "fifty")):
        return "Duels"
    if any(
        x in n
        for x in (
            "interception",
            "clearance",
            "recover",
            "block",
            "poss_won",
            "error",
            "clean_sheet",
            "goals_conceded",
            "last_man",
        )
    ):
        return "Defensive"
    if any(x in n for x in ("foul",)):
        return "Discipline"
    return "Attacking"


def archive_direction(name: str) -> int:
    n = name.lower()
    if any(
        x in n
        for x in (
            "lost",
            "error",
            "conceded",
            "card",
            "foul",
            "offside",
            "dispossess",
            "unsuccessful",
            "turnover",
            "poss_lost",
            "own_goal",
            "hand_ball",
            "miss",
        )
    ):
        return -1
    return 1
