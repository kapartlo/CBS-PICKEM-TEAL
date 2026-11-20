import base64
from html import escape as escapeHtml
import io
import json
import math
from itertools import product

# HTML escaping helper
def escapeHtml(text, quote=True):
    """Escape HTML special characters using Python's html.escape."""
    from html import escape
    return escape(str(text), quote=quote)

import re
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
import seaborn as sns
import streamlit as st
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

st.set_page_config(layout="wide", page_title="NFL CBS Sports Pick'em Pool Simulation")

st.markdown("""
    <style>
        .block-container { padding-top: 3rem !important; padding-bottom: 1rem; }
        h1 { font-size: 1.5rem !important; margin-bottom: 0.2rem !important; }
        h3 { font-size: 1.1rem !important; margin-top: 0.5rem !important; margin-bottom: 0.3rem !important; }
        p { margin-bottom: 0.3rem !important; }
        th { text-align: center !important; }
    </style>
""", unsafe_allow_html=True)

DEFAULT_PICKS_SPREADSHEET_ID = "1by3okDqhmsITMO5Nig326OKOtJm-8Jw0"

@st.cache_data(ttl=600)
def load_excel_from_gdrive(spreadsheet_id):
    try:
        creds_json = base64.b64decode(st.secrets["gdrive_b64"]).decode("utf-8")
        creds_dict = json.loads(creds_json)
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as temp:
            json.dump(creds_dict, temp)
            temp_path = temp.name
        creds = service_account.Credentials.from_service_account_file(
            temp_path, scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )
        service = build("drive", "v3", credentials=creds)
        request = service.files().get_media(fileId=spreadsheet_id.strip())
        file_buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(file_buffer, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        file_buffer.seek(0)
        return pd.read_excel(file_buffer, sheet_name=0)
    except Exception as e:
        st.error(f"Error loading Excel from Google Drive: {e}")
        return None

def american_odds_to_probability(odds):
    try:
        value = float(str(odds).replace("\u2212", "-"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value == 0:
        return None
    if value > 0:
        return 100 / (value + 100)
    return -value / (-value + 100)

def get_market_probabilities(odds_items, home_id, away_id):
    for odds in odds_items:
        home_odds = odds.get("homeTeamOdds", {})
        away_odds = odds.get("awayTeamOdds", {})
        odds_home_id = str(home_odds.get("team", {}).get("id", ""))
        odds_away_id = str(away_odds.get("team", {}).get("id", ""))
        if odds_home_id != home_id or odds_away_id != away_id:
            continue

        home_implied = american_odds_to_probability(
            home_odds.get("moneyLine", home_odds.get("moneyline"))
        )
        away_implied = american_odds_to_probability(
            away_odds.get("moneyLine", away_odds.get("moneyline"))
        )
        if home_implied is None or away_implied is None:
            continue

        total = home_implied + away_implied
        if total > 0:
            provider = odds.get("provider", {}).get("name", "market")
            return home_implied / total, away_implied / total, 0.0, f"No-vig moneyline ({provider})"
    return None

def fetch_summary_probabilities(event_id, home_id, away_id, is_live):
    url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary"
    response = requests.get(url, params={"event": event_id}, timeout=8)
    response.raise_for_status()
    summary = response.json()

    summary_competitions = summary.get("header", {}).get("competitions", [])
    if is_live and not summary_competitions:
        raise ValueError(f"ESPN summary has no matchup IDs for live event {event_id}")
    if summary_competitions:
        summary_sides = {
            competitor.get("homeAway"): str(competitor.get("id", ""))
            for competitor in summary_competitions[0].get("competitors", [])
        }
        if summary_sides.get("home") != home_id or summary_sides.get("away") != away_id:
            raise ValueError(f"ESPN summary teams do not match scoreboard teams for event {event_id}")

    if is_live:
        probability_points = summary.get("winprobability", [])
        if isinstance(probability_points, list):
            for point in reversed(probability_points):
                try:
                    home_probability = float(point["homeWinPercentage"])
                    tie_probability = float(point.get("tiePercentage", 0.0))
                except (KeyError, TypeError, ValueError):
                    continue
                if (
                    math.isfinite(home_probability)
                    and math.isfinite(tie_probability)
                    and 0 <= home_probability <= 1
                    and 0 <= tie_probability <= 1
                    and home_probability + tie_probability <= 1
                ):
                    away_probability = 1 - home_probability - tie_probability
                    return (
                        home_probability,
                        away_probability,
                        tie_probability,
                        "ESPN live win probability",
                    )
        raise ValueError(f"ESPN has no valid live win-probability update for event {event_id}")

    predictor = summary.get("predictor", {})
    if (
        str(predictor.get("homeTeam", {}).get("id", "")) != home_id
        or str(predictor.get("awayTeam", {}).get("id", "")) != away_id
    ):
        raise ValueError(f"ESPN predictor teams do not match scoreboard teams for event {event_id}")
    try:
        home_projection = float(predictor["homeTeam"]["gameProjection"])
        away_projection = float(predictor["awayTeam"]["gameProjection"])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"ESPN has no valid pregame projection for event {event_id}")
    total = home_projection + away_projection
    if (
        not math.isfinite(home_projection)
        or not math.isfinite(away_projection)
        or home_projection < 0
        or away_projection < 0
        or total <= 0
    ):
        raise ValueError(f"ESPN returned invalid pregame projections for event {event_id}")
    return (
        home_projection / total,
        away_projection / total,
        0.0,
        "ESPN matchup predictor",
    )

def get_ordinal_suffix(n):
    if 11 <= n <= 13:
        return 'th'
    return {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')

TEAM_ABBREVIATION_ALIASES = {
    "JAC": "JAX",
    "WSH": "WAS",
    "OAK": "LV",
    "SD": "LAC",
    "STL": "LAR",
}

def normalize_team_abbreviation(abbreviation):
    abbreviation = abbreviation.upper()
    return TEAM_ABBREVIATION_ALIASES.get(abbreviation, abbreviation)

def simulate_weekly_results(players, parsed_picks, locked_points, pairing_map, decided_teams, game_probabilities, n_sims=10000, rng_seed=42):
    weekly_points = np.zeros((len(players), n_sims))
    rng = np.random.default_rng(rng_seed)

    for pair in pairing_map:
        if pair[0] in decided_teams:
            continue
        home_probability, _, tie_probability = game_probabilities[pair]
        draws = rng.random(n_sims)
        home_wins = draws < home_probability
        away_wins = draws >= home_probability + tie_probability
        for player_index, player in enumerate(players):
            weekly_points[player_index] += home_wins * parsed_picks[player].get(pair[0], 0)
            weekly_points[player_index] += away_wins * parsed_picks[player].get(pair[1], 0)

    if not players:
        return weekly_points, np.zeros(0)

    weekly_points += np.asarray(locked_points)[:, np.newaxis]
    top_scores = np.max(weekly_points, axis=0)
    win_shares = (weekly_points == top_scores) / np.sum(weekly_points == top_scores, axis=0)
    first_place_shares = np.sum(win_shares, axis=1) / n_sims * 100
    return weekly_points, first_place_shares

def get_weekly_root_recommendations(players, parsed_picks, locked_points, pairing_map, decided_teams, game_probabilities):
    remaining_pairs = [pair for pair in pairing_map if pair[0] not in decided_teams]
    if not players or not 1 <= len(remaining_pairs) <= 3:
        return None

    possible_outcomes = []
    modeled_outcomes = []
    for pair in remaining_pairs:
        home_probability, away_probability, tie_probability = game_probabilities[pair]
        outcomes = [pair[0], pair[1]]
        modeled = [(pair[0], home_probability), (pair[1], away_probability)]
        if tie_probability > 0:
            outcomes.append(None)
            modeled.append((None, tie_probability))
        possible_outcomes.append(outcomes)

        positive_outcomes = [(winner, probability) for winner, probability in modeled if probability > 0]
        probability_total = sum(probability for _, probability in positive_outcomes)
        if not math.isfinite(probability_total) or probability_total <= 0:
            raise ValueError(f"{pair}: no valid outcome probabilities for rooting recommendations.")
        modeled_outcomes.append([
            (winner, probability / probability_total)
            for winner, probability in positive_outcomes
        ])

    def first_place_shares(outcomes):
        scores = np.asarray(locked_points, dtype=float).copy()
        for pair, winner in zip(remaining_pairs, outcomes):
            if winner is None:
                continue
            scores += np.asarray(
                [parsed_picks[player].get(winner, 0) for player in players],
                dtype=float,
            )
        leaders = scores == np.max(scores)
        return leaders / np.sum(leaders)

    possible_scenarios = [
        (outcomes, first_place_shares(outcomes))
        for outcomes in product(*possible_outcomes)
    ]
    recommendations = [None] * len(players)
    for player_index in range(len(players)):
        can_finish_first = any(
            shares[player_index] > 0
            for _, shares in possible_scenarios
        )
        if not can_finish_first:
            continue

        player = players[player_index]
        player_recommendations = []
        for game_index, pair in enumerate(remaining_pairs):
            conditional_shares = []
            other_game_indices = [
                index for index in range(len(remaining_pairs))
                if index != game_index
            ]
            for recommended_winner in possible_outcomes[game_index]:
                weighted_share = 0.0
                other_outcomes = [modeled_outcomes[index] for index in other_game_indices]
                for other_results in product(*other_outcomes):
                    outcomes = [None] * len(remaining_pairs)
                    outcomes[game_index] = recommended_winner
                    weight = 1.0
                    for index, (winner, probability) in zip(other_game_indices, other_results):
                        outcomes[index] = winner
                        weight *= probability
                    weighted_share += weight * first_place_shares(outcomes)[player_index]
                conditional_shares.append((recommended_winner, weighted_share))

            conditional_shares.sort(key=lambda result: result[1], reverse=True)
            best_winner, best_share = conditional_shares[0]
            second_share = conditional_shares[1][1] if len(conditional_shares) > 1 else best_share
            advantage = best_share - second_share
            if advantage <= 1e-12:
                player_recommendations.append({
                    "team": None,
                    "points": None,
                    "contrary": False,
                    "has_preference": False,
                })
                continue

            player_pick = next(
                (team for team in pair if team in parsed_picks[player]),
                None,
            )
            player_recommendations.append({
                "team": best_winner,
                "points": parsed_picks[player].get(player_pick, 0) if player_pick else 0,
                "contrary": player_pick is not None and player_pick != best_winner,
                "has_preference": True,
            })
        recommendations[player_index] = player_recommendations
    return recommendations

@st.cache_data(ttl=60)
def fetch_espn_nfl_data():
    url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
    completed_winners = []
    team_probabilities = {}
    pairing_map = []
    game_probabilities = {}
    probability_sources = {}
    current_scores = {}
    live_game_status = {}
    active_games = 0
    live_leaders = []
    decided_games = set()
    errors = [] 

    try:
        res = requests.get(url, timeout=5)
        res.raise_for_status()
        events = res.json().get("events", [])
    except (
        requests.RequestException,
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
    ) as e:
        return completed_winners, team_probabilities, pairing_map, active_games, live_leaders, decided_games, game_probabilities, probability_sources, current_scores, live_game_status, [f"Could not load ESPN scoreboard: {e}"]

    forecast_tasks = []
    seen_team_ids = set()
    seen_team_abbreviations = set()
    for event in events:
        event_name = event.get("name", event.get("id", "Unknown matchup"))
        event_id = str(event.get("id", ""))
        competitions = event.get("competitions", [])
        if not event_id or not competitions:
            errors.append(f"{event_name}: missing event ID or competition data.")
            continue

        comp = competitions[0]
        status_info = comp.get("status", {})
        status_data = status_info.get("type", {})
        status = status_data.get("name", "")
        game_state = status_data.get("state", "")
        is_final = status.startswith("STATUS_FINAL") or status_data.get("completed", False) or game_state == "post"
        is_live = game_state == "in" or status in {"STATUS_IN_PROGRESS", "STATUS_HALFTIME"}

        sides = {}
        for competitor in comp.get("competitors", []):
            side = competitor.get("homeAway")
            team = competitor.get("team", {})
            team_id = str(team.get("id", ""))
            abbreviation = normalize_team_abbreviation(str(team.get("abbreviation", "")))
            if side not in {"home", "away"} or not team_id or not abbreviation or side in sides:
                errors.append(f"{event_name}: invalid or ambiguous ESPN team matchup.")
                sides = {}
                break
            sides[side] = {
                "id": team_id,
                "abbr": abbreviation,
                "score": int(competitor.get("score") or 0),
                "winner": competitor.get("winner", False),
            }
        if set(sides) != {"home", "away"}:
            if not any(event_name in error for error in errors):
                errors.append(f"{event_name}: ESPN did not provide exactly one home and one away team.")
            continue

        home, away = sides["home"], sides["away"]
        if home["id"] == away["id"] or home["abbr"] == away["abbr"]:
            errors.append(f"{event_name}: ESPN returned duplicate team IDs or abbreviations.")
            continue
        if (
            home["id"] in seen_team_ids
            or away["id"] in seen_team_ids
            or home["abbr"] in seen_team_abbreviations
            or away["abbr"] in seen_team_abbreviations
        ):
            errors.append(f"{event_name}: a team ID or abbreviation appears in more than one matchup.")
            continue
        seen_team_ids.update((home["id"], away["id"]))
        seen_team_abbreviations.update((home["abbr"], away["abbr"]))

        pair = (home["abbr"], away["abbr"])
        pairing_map.append(pair)
        team_probabilities[home["abbr"]] = None
        team_probabilities[away["abbr"]] = None
        if is_live or is_final:
            current_scores[pair] = (home["score"], away["score"])
        if is_live:
            active_games += 1
            period = status_info.get("period")
            display_clock = status_info.get("displayClock")
            status_detail = status_data.get("detail", "")
            if "halftime" in str(status_detail).casefold():
                live_game_status[pair] = "Halftime"
            elif isinstance(period, int) and period > 4:
                overtime = "OT" if period == 5 else f"{period - 4}OT"
                live_game_status[pair] = f"{overtime} {display_clock}".strip() if display_clock else overtime
            elif isinstance(period, int) and period > 0:
                quarter_suffix = "st" if period == 1 else "nd" if period == 2 else "rd" if period == 3 else "th"
                quarter = f"{period}{quarter_suffix} Q"
                live_game_status[pair] = f"{quarter} {display_clock}" if display_clock else quarter
            elif status_detail:
                live_game_status[pair] = str(status_detail)
            if home["score"] > away["score"]:
                live_leaders.append(home["abbr"])
            elif away["score"] > home["score"]:
                live_leaders.append(away["abbr"])

        if is_final:
            decided_games.update(pair)
            if home["winner"]:
                completed_winners.append(home["abbr"])
                game_probabilities[pair] = (1.0, 0.0, 0.0)
            elif away["winner"]:
                completed_winners.append(away["abbr"])
                game_probabilities[pair] = (0.0, 1.0, 0.0)
            elif home["score"] == away["score"]:
                game_probabilities[pair] = (0.0, 0.0, 1.0)
            elif home["score"] > away["score"]:
                completed_winners.append(home["abbr"])
                game_probabilities[pair] = (1.0, 0.0, 0.0)
            else:
                completed_winners.append(away["abbr"])
                game_probabilities[pair] = (0.0, 1.0, 0.0)
            probability_sources[pair] = "Final result"
            continue

        market_probabilities = None if is_live else get_market_probabilities(
            comp.get("odds", []), home["id"], away["id"]
        )
        if market_probabilities is not None:
            forecast = market_probabilities
            game_probabilities[pair] = forecast[:3]
            probability_sources[pair] = forecast[3]
        else:
            forecast_tasks.append((pair, event_id, event_name, is_live, home["id"], away["id"]))

    with ThreadPoolExecutor(max_workers=8) as executor:
        pending = {
            executor.submit(fetch_summary_probabilities, event_id, home_id, away_id, is_live): (pair, event_name)
            for pair, event_id, event_name, is_live, home_id, away_id in forecast_tasks
        }
        for future in as_completed(pending):
            pair, event_name = pending[future]
            try:
                forecast = future.result()
            except (requests.RequestException, ValueError) as e:
                errors.append(f"{event_name}: {e}")
                continue
            game_probabilities[pair] = forecast[:3]
            probability_sources[pair] = forecast[3]

    for pair, (home_probability, away_probability, _) in game_probabilities.items():
        team_probabilities[pair[0]] = home_probability
        team_probabilities[pair[1]] = away_probability
    return (
        completed_winners,
        team_probabilities,
        pairing_map,
        active_games,
        live_leaders,
        decided_games,
        game_probabilities,
        probability_sources,
        current_scores,
        live_game_status,
        errors,
    )

def should_show_picks_pending_message(
    total_games,
    scheduled_game_count,
    parsed_picks,
    pick_parse_errors,
    data_errors,
    mismatched_pick_columns,
    populated_pick_columns,
    mismatched_pick_players,
):
    if (
        total_games <= 0
        or scheduled_game_count < max(1, math.ceil(total_games * 0.75))
        or pick_parse_errors
    ):
        return False

    pending_pick_error_fragments = (
        "picks contain teams from multiple ESPN matchups.",
        "picks do not match ESPN teams:",
        "picked both ",
    )
    pending_errors_only = all(
        any(fragment in error for fragment in pending_pick_error_fragments)
        or error.startswith("The spreadsheet has ")
        for error in data_errors
    )
    if not parsed_picks:
        return pending_errors_only
    minimum_mismatched_columns = max(3, math.ceil(populated_pick_columns * 0.6))
    minimum_mismatched_players = max(1, math.ceil(len(parsed_picks) * 0.5))
    if (
        mismatched_pick_columns < minimum_mismatched_columns
        or mismatched_pick_players < minimum_mismatched_players
    ):
        return False

    return bool(data_errors) and pending_errors_only

st.title("Teal CBS Sports Pick'em")
st.session_state.setdefault("simulation_seed", 42)
if st.button("REFRESH"):
    load_excel_from_gdrive.clear()
    fetch_espn_nfl_data.clear()
    st.session_state["simulation_seed"] += 1
    st.rerun()

picks_spreadsheet_id = st.secrets.get(
    "picks_spreadsheet_id",
    DEFAULT_PICKS_SPREADSHEET_ID,
)
if not isinstance(picks_spreadsheet_id, str) or not picks_spreadsheet_id.strip():
    st.error("The picks_spreadsheet_id Streamlit secret must be a non-empty string.")
    st.stop()
picks_df = load_excel_from_gdrive(picks_spreadsheet_id.strip())

if picks_df is None:
    st.error("Failed to load Excel data from Google Drive. Please check your secrets configuration and internet connection.")
else:
    game_cols = [col for col in picks_df.columns[3:] if 'TIE' not in str(col).upper()]
    parsed_picks, total_games = {}, len(game_cols) if len(game_cols) > 0 else 16
    pick_parse_errors = []
    column_teams = {col: set() for col in game_cols}
    ghost_player_rows = picks_df["Players"].astype(str).str.contains(
        r"\s+sFie[A-Za-z]*$",
        case=False,
        na=False,
    )

    for index in range(1, len(picks_df)):
        if ghost_player_rows.iloc[index]:
            continue
        raw_name = picks_df.loc[index, 'Players']
        if pd.isna(raw_name) or str(raw_name).strip().upper() in ['', '-', 'NAN', 'NONE']: continue
        name = re.sub(r'^\d*(?:st|nd|rd|th)?\s*', '', str(raw_name)).strip()
        name = re.sub(r'\s+sFie[A-Za-z]*$', '', name).strip()
        
        player_picks = {}
        for col in game_cols:
            val = str(picks_df.loc[index, col]).strip()
            m = re.fullmatch(r'([A-Za-z]{2,4})\s*\(\s*(\d+)\s*\)', val)
            if m:
                team = normalize_team_abbreviation(m.group(1))
                if team in player_picks:
                    pick_parse_errors.append(f"{name}: picked {team} more than once.")
                player_picks[team] = int(m.group(2))
                column_teams[col].add(team)
            elif val.upper() not in {"", "-", "NAN", "NONE"}:
                pick_parse_errors.append(f"{name}: could not parse pick {val!r} in {col!r}.")

        if len(player_picks) > 0:
            parsed_picks[name] = player_picks

    valid_player_indices = [
        index for index in range(len(picks_df))
        if re.sub(r'\s+sFie[A-Za-z]*$', '', re.sub(r'^\d*(?:st|nd|rd|th)?\s*', '', str(picks_df.loc[index, 'Players']))).strip() in parsed_picks
    ]
    valid_player_rows = picks_df.iloc[valid_player_indices]

    (
        COMPLETED_WINNERS,
        TEAM_PROBABILITIES,
        DEFAULT_PAIRING_MAP,
        ACTIVE_GAMES_COUNT,
        LIVE_LEADERS,
        DECIDED_GAMES,
        GAME_PROBABILITIES,
        PROBABILITY_SOURCES,
        CURRENT_SCORES,
        LIVE_GAME_STATUS,
        DATA_ERRORS,
    ) = fetch_espn_nfl_data()
    DATA_ERRORS.extend(pick_parse_errors)
    players = list(parsed_picks.keys())
    n_players = len(players)

    if len(DEFAULT_PAIRING_MAP) != total_games:
        DATA_ERRORS.append(
            f"The spreadsheet has {total_games} game columns, but ESPN returned {len(DEFAULT_PAIRING_MAP)} matchups."
        )
    scheduled_teams = set(TEAM_PROBABILITIES)
    mismatched_pick_players = 0
    for player, player_picks in parsed_picks.items():
        unknown_teams = sorted(set(player_picks) - scheduled_teams)
        player_has_mismatch = bool(unknown_teams)
        if unknown_teams:
            DATA_ERRORS.append(f"{player}: picks do not match ESPN teams: {', '.join(unknown_teams)}.")
        for home_team, away_team in DEFAULT_PAIRING_MAP:
            if home_team in player_picks and away_team in player_picks:
                DATA_ERRORS.append(f"{player}: picked both {home_team} and {away_team} in one matchup.")
                player_has_mismatch = True
        if player_has_mismatch:
            mismatched_pick_players += 1
    missing_forecasts = [pair for pair in DEFAULT_PAIRING_MAP if pair not in GAME_PROBABILITIES]
    if missing_forecasts:
        DATA_ERRORS.append(
            "Missing probabilities for: "
            + ", ".join(f"{home} vs {away}" for home, away in missing_forecasts)
            + "."
        )
    team_matchups = {team: pair for pair in DEFAULT_PAIRING_MAP for team in pair}
    column_matchups = {}
    for column_index, (col, teams) in enumerate(column_teams.items()):
        matchups = {team_matchups[team] for team in teams if team in team_matchups}
        if len(matchups) > 1:
            DATA_ERRORS.append(f"{col}: picks contain teams from multiple ESPN matchups.")
        if len(matchups) == 1:
            column_matchups[col] = next(iter(matchups))
        elif not teams and column_index < len(DEFAULT_PAIRING_MAP):
            column_matchups[col] = DEFAULT_PAIRING_MAP[column_index]
        else:
            column_matchups[col] = None

    mismatched_pick_columns = sum(
        bool(teams)
        and (
            any(team not in team_matchups for team in teams)
            or len({team_matchups[team] for team in teams if team in team_matchups}) > 1
        )
        for teams in column_teams.values()
    )
    populated_pick_columns = sum(bool(teams) for teams in column_teams.values())
    if should_show_picks_pending_message(
        total_games,
        len(DEFAULT_PAIRING_MAP),
        parsed_picks,
        pick_parse_errors,
        DATA_ERRORS,
        mismatched_pick_columns,
        populated_pick_columns,
        mismatched_pick_players,
    ):
        if parsed_picks:
            message = (
                "The spreadsheet still appears to contain last week's picks. "
                "This week's picks haven't been loaded yet; refresh after they're entered."
            )
        else:
            message = (
                "This week's picks haven't been entered in the spreadsheet yet. "
                "Refresh after they're loaded."
            )
        st.info(message, title="Waiting for this week's picks")
        st.stop()
    elif DATA_ERRORS:
        st.error("Predictions are paused because the matchup or probability data could not be verified.")
        for error in DATA_ERRORS:
            st.write(f"- {error}")
        st.stop()

    st.info(f"{len(COMPLETED_WINNERS)} of {total_games} games completed. {ACTIVE_GAMES_COUNT} active. | **Players:** {n_players}")

    locked_pts, active_proj, max_pts, root_for_raw, ytd_totals, ytd_active_totals = [], [], [], [], [], []
    ytd_col = 'YTD' if 'YTD' in picks_df.columns else (picks_df.columns[2] if len(picks_df.columns) > 2 else None)
    base_ytd_by_player = {}
    locked_pts_by_player = {}

    for p in players:
        row = valid_player_rows[
            valid_player_rows['Players'].astype(str).str.contains(p, na=False)
        ]
        base_ytd = float(row.iloc[0][ytd_col]) if not row.empty and ytd_col else 0

        locked = sum(parsed_picks[p].get(t, 0) for t in COMPLETED_WINNERS if t in parsed_picks[p])
        locked_pts.append(locked)
        base_ytd_by_player[p] = base_ytd
        locked_pts_by_player[p] = locked

        ytd_locked_val = int(round(base_ytd + locked))
        ytd_totals.append(ytd_locked_val)

        live_winning_pts = sum(parsed_picks[p].get(t, 0) for t in LIVE_LEADERS if t in parsed_picks[p])
        active_proj.append(locked + live_winning_pts)

        ytd_active_val = int(round(base_ytd + locked + live_winning_pts))
        ytd_active_totals.append(ytd_active_val)

        max_possible = locked + sum(
            pts for t, pts in parsed_picks[p].items()
            if t not in DECIDED_GAMES
        )
        max_pts.append(max_possible)

        pending_items = [
            (t, pts) for t, pts in parsed_picks[p].items()
            if t not in COMPLETED_WINNERS and t not in DECIDED_GAMES and TEAM_PROBABILITIES.get(t) not in (1.0, 0.0)
        ]
        pending = sorted(pending_items, key=lambda x: x[1], reverse=True)
        root_for_raw.append(", ".join([f"{t}({pts})" for t, pts in pending[:3]]) if pending else "NONE")

    raw_player_col = picks_df.columns[0] if len(picks_df.columns) > 0 else 'Players'
    blank_row = {
        col: np.nan if pd.api.types.is_numeric_dtype(picks_df[col]) else ""
        for col in picks_df.columns
    }
    matchup_row = blank_row.copy()
    favorite_row = blank_row.copy()
    matchup_row[raw_player_col] = ""
    favorite_row[raw_player_col] = ""
    tie_columns = [
        column
        for column in picks_df.columns
        if "TIE" in str(column).upper()
    ]
    for column in tie_columns:
        tie_matchup = re.fullmatch(
            r"TIE([A-Z]{2,4})-([A-Z]{2,4})-?",
            str(column).strip().upper(),
        )
        if tie_matchup:
            matchup_row[column] = (
                f"{normalize_team_abbreviation(tie_matchup.group(1))} & "
                f"{normalize_team_abbreviation(tie_matchup.group(2))}"
            )
    for col, pair in column_matchups.items():
        if pair is not None:
            home, away = pair
            score = CURRENT_SCORES.get(pair)
            if score is not None:
                matchup_text = f"{away} {score[1]} at {home} {score[0]}"
            else:
                matchup_text = f"{away} at {home}"
            matchup_row[col] = matchup_text
            home_probability, away_probability, _ = GAME_PROBABILITIES[pair]
            favorite = home if home_probability >= away_probability else away
            favorite_probability = max(home_probability, away_probability)
            favorite_row[col] = f"{favorite} ({favorite_probability:.1%})"
        else:
            matchup_row[col] = "No picks" if not column_teams[col] else "Unmatched"

    display_picks_df = pd.concat(
        [
            pd.DataFrame([matchup_row, favorite_row]),
            valid_player_rows,
        ],
        ignore_index=True,
    )
    pts_col = next(
        (column for column in picks_df.columns if str(column).strip().casefold() == "pts"),
        None,
    )
    ytd_display_col = next(
        (column for column in picks_df.columns if str(column).strip().casefold() == "ytd"),
        None,
    )
    weekly_points_by_player = dict(zip(players, active_proj))
    ytd_points_by_player = dict(zip(players, ytd_active_totals))
    if ytd_display_col is not None:
        for row_index in range(2, len(display_picks_df)):
            raw_name = display_picks_df.at[row_index, raw_player_col]
            if pd.isna(raw_name) or str(raw_name).strip() in {"", "-", "NaN"}:
                continue
            name = re.sub(r'^\d*(?:st|nd|rd|th)?\s*', '', str(raw_name)).strip()
            name = re.sub(r'\s+sFie[A-Za-z]*$', '', name).strip()
            if name not in ytd_points_by_player:
                base_ytd = pd.to_numeric(
                    display_picks_df.at[row_index, ytd_display_col],
                    errors="coerce",
                )
                ytd_points_by_player[name] = (
                    int(round(base_ytd)) if pd.notna(base_ytd) else 0
                )
    player_ranks = dict(
        zip(
            players,
            pd.Series(active_proj).rank(method="min", ascending=False).astype(int),
        )
    )
    if pts_col is not None or ytd_display_col is not None:
        for row_index in range(2, len(display_picks_df)):
            raw_name = display_picks_df.at[row_index, raw_player_col]
            if pd.isna(raw_name) or str(raw_name).strip() in {"", "-", "NaN"}:
                continue
            name = re.sub(r'^\d*(?:st|nd|rd|th)?\s*', '', str(raw_name)).strip()
            name = re.sub(r'\s+sFie[A-Za-z]*$', '', name).strip()
            if pts_col is not None:
                display_picks_df.at[row_index, pts_col] = weekly_points_by_player.get(name, 0)
            if ytd_display_col is not None:
                display_picks_df.at[row_index, ytd_display_col] = ytd_points_by_player.get(name, 0)
    def format_column_header(column):
        if column == raw_player_col:
            return "Players"
        normalized_column = re.sub(r"\.\d+$", "", str(column)).strip().casefold()
        if str(column).strip().upper().startswith("TIE"):
            return "Tie Pts"
        if normalized_column == "pts":
            return "Locked + Active"
        if normalized_column == "ytd":
            return "YTD Locked + Active"
        pair = column_matchups.get(column)
        if pair is not None:
            if set(pair).issubset(DECIDED_GAMES):
                return "Finished"
            if pair in LIVE_GAME_STATUS:
                return LIVE_GAME_STATUS[pair]
        return re.sub(r"\.\d+$", "", str(column))

    display_picks_df.columns = [
        format_column_header(column) for column in display_picks_df.columns
    ]
    for column in ("Players", "Locked + Active", "YTD Locked + Active"):
        if column in display_picks_df:
            display_picks_df[column] = display_picks_df[column].astype(object)
    display_picks_df.at[1, "Players"] = "Players"
    display_picks_df.at[1, "Locked + Active"] = "Locked + Active"
    if "YTD Locked + Active" in display_picks_df:
        display_picks_df.at[1, "YTD Locked + Active"] = "YTD Locked + Active"

    sort_columns = [
        column
        for column in ("Locked + Active", "YTD Locked + Active")
        if column in display_picks_df
    ]
    display_player_rows = display_picks_df.iloc[2:]
    if sort_columns:
        display_player_rows = display_player_rows.sort_values(
            by=sort_columns,
            ascending=[False] * len(sort_columns),
            kind="mergesort",
            na_position="last",
        )
    display_picks_df = pd.concat(
        [display_picks_df.iloc[:2], display_player_rows],
        ignore_index=True,
    )

    def format_pick_cell(value):
        if pd.isna(value):
            return "", ""
        if isinstance(value, (int, float, np.number)) and not isinstance(value, bool):
            if not np.isfinite(value):
                return "", ""
            display_value = f"{value:,.0f}"
            sort_value = f"{value:.15g}"
        else:
            display_value = str(value)
            sort_value = display_value
        return escapeHtml(display_value, quote=True), escapeHtml(sort_value, quote=True)

    table_headers = "".join(
        f'<th scope="col" tabindex="0" data-column="{index}">'
        f'{"" if index < 3 else escapeHtml(str(column))}</th>'
        for index, column in enumerate(display_picks_df.columns)
    )
    table_rows = []
    teams_to_emphasize = set(COMPLETED_WINNERS) | set(LIVE_LEADERS)
    for row_index, row in enumerate(display_picks_df.itertuples(index=False, name=None)):
        cells = []
        player_name = ""
        base_locked = 0
        base_ytd = 0
        if row_index >= 2:
            raw_player = str(row[0])
            player_name = re.sub(r'^\d*(?:st|nd|rd|th)?\s*', '', raw_player).strip()
            player_name = re.sub(r'\s+sFie[A-Za-z]*$', '', player_name).strip()
            base_locked = locked_pts_by_player.get(player_name, 0)
            base_ytd = base_ytd_by_player.get(player_name, 0)
            base_rank = player_ranks.get(player_name, 1)

        for column_index, value in enumerate(row):
            display_value, sort_value = format_pick_cell(value)
            cell_tabindex = ' tabindex="0"' if row_index < 2 else ""
            if row_index == 0 and column_index >= 3:
                original_column = picks_df.columns[column_index]
                pair = column_matchups.get(original_column)
                if pair is not None:
                    home, away = pair
                    is_decided = set(pair).issubset(DECIDED_GAMES)
                    score = CURRENT_SCORES.get(pair)
                    actual_leader = next(
                        (team for team in pair if team in LIVE_LEADERS),
                        None,
                    )
                    if is_decided:
                        leading_team = next(
                            (team for team in pair if team in teams_to_emphasize),
                            None,
                        )
                        if leading_team:
                            escaped_team = escapeHtml(leading_team, quote=True)
                            display_value = display_value.replace(
                                escaped_team,
                                f"<strong>{escaped_team}</strong>",
                                1,
                            )
                    else:
                        away_score_str = f" {score[1]}" if score is not None else ""
                        home_score_str = f" {score[0]}" if score is not None else ""
                        away_lead_cls = " live-lead" if actual_leader == away else ""
                        home_lead_cls = " live-lead" if actual_leader == home else ""
                        display_value = (
                            f'<div class="matchup-selector" data-col="{column_index}" '
                            f'data-away="{away}" data-home="{home}" '
                            f'data-live-leader="{actual_leader or ""}">'
                            f'<button type="button" class="whatif-team-btn{away_lead_cls}" '
                            f'data-team="{away}" data-col="{column_index}" '
                            f'title="Click to simulate {away} win">{away}{away_score_str}</button>'
                            f'<span class="matchup-at"> at </span>'
                            f'<button type="button" class="whatif-team-btn{home_lead_cls}" '
                            f'data-team="{home}" data-col="{column_index}" '
                            f'title="Click to simulate {home} win">{home}{home_score_str}</button>'
                            f'</div>'
                        )
            elif row_index >= 2 and column_index == 0:
                cells.append(
                    f'<td data-sort="{sort_value}" data-col-type="player" '
                    f'data-player-name="{escapeHtml(player_name, quote=True)}">'
                    f'<span class="rank-badge">{base_rank}{get_ordinal_suffix(base_rank)}</span>  '
                    f'<span class="player-name">{escapeHtml(player_name, quote=True)}</span></td>'
                )
                continue
            elif row_index >= 2 and column_index == 1:
                cells.append(
                    f'<td data-sort="{sort_value}" data-col-type="weekly-pts">{display_value}</td>'
                )
                continue
            elif row_index >= 2 and column_index == 2:
                cells.append(
                    f'<td data-sort="{sort_value}" data-col-type="ytd-pts">{display_value}</td>'
                )
                continue
            elif row_index >= 2 and column_index >= 3:
                original_column = picks_df.columns[column_index]
                pair = column_matchups.get(original_column)
                pick_match = re.fullmatch(
                    r"\s*([A-Za-z]{2,4})\s*\(\s*(\d+)\s*\)\s*",
                    str(value),
                )
                pick_attrs = ""
                if pair is not None and pick_match:
                    picked_team = normalize_team_abbreviation(pick_match.group(1))
                    picked_pts = int(pick_match.group(2))
                    pick_attrs = f' data-col="{column_index}" data-team="{picked_team}" data-pts="{picked_pts}"'
                    score = CURRENT_SCORES.get(pair)
                    if (
                        picked_team in pair
                        and set(pair).issubset(DECIDED_GAMES)
                        and picked_team in COMPLETED_WINNERS
                    ):
                        display_value += (
                            ' <span class="pick-status correct" '
                            'title="Correct pick — game final" aria-label="Correct pick, game final">'
                            '&#10004;</span>'
                        )
                    elif (
                        picked_team in pair
                        and set(pair).issubset(DECIDED_GAMES)
                        and score is not None
                        and score[0] != score[1]
                    ):
                        display_value += (
                            ' <span class="pick-status incorrect-final" '
                            'title="Incorrect pick — game final" '
                            'aria-label="Incorrect pick, game final">&#10006;</span>'
                        )
                    elif (
                        picked_team in pair
                        and pair in LIVE_GAME_STATUS
                        and score is not None
                        and score[0] != score[1]
                        and picked_team not in LIVE_LEADERS
                    ):
                        display_value += (
                            ' <span class="pick-status trailing" '
                            'title="Your pick is currently trailing" '
                            'aria-label="Your pick is currently trailing">&#9632;</span>'
                        )
                    elif picked_team in pair and picked_team in LIVE_LEADERS:
                        display_value += (
                            ' <span class="pick-status leading" '
                            'title="Your pick is currently leading" '
                            'aria-label="Your pick is currently leading">&#9650;</span>'
                        )
                    else:
                        display_value += ' <span class="pick-status"></span>'
                cells.append(
                    f'<td{pick_attrs} data-sort="{sort_value}">{display_value}</td>'
                )
                continue
            if row_index == 1 and column_index < 3:
                cells.append(
                    f'<td class="column-heading" tabindex="0" data-sort="{sort_value}">'
                    f'{display_value}</td>'
                )
                continue
            cells.append(
                f'<td{cell_tabindex} data-sort="{sort_value}">{display_value}</td>'
            )
        if row_index < 2:
            row_class = ' class="pinned-row"'
            row_attrs = ""
        else:
            row_class = ' class="player-row"'
            row_attrs = (
                f' data-player="{escapeHtml(player_name, quote=True)}" '
                f'data-base-rank="{base_rank}" '
                f'data-base-locked="{base_locked}" data-base-ytd="{base_ytd}"'
            )
        table_rows.append(f"<tr{row_class}{row_attrs}>{''.join(cells)}</tr>")

    matrix_html = f"""
        <div class="whatif-toolbar" id="whatif-toolbar" style="display:none;">
            <div>
                <span style="font-size: 1rem; margin-right: 4px;">⚡</span>
                <strong>What-If Simulation Active:</strong>
                <span id="whatif-summary" style="margin-left: 6px;">0 games simulated</span>
            </div>
            <button type="button" id="whatif-reset-btn" title="Clear all simulated picks and return to live scores">Reset to Live Scores</button>
        </div>
        <div class="picks-table-scroll weekly-picks-scroll">
            <table class="picks-table weekly-picks-table" id="weekly-picks-table">
                <thead><tr>{table_headers}</tr></thead>
                <tbody>{''.join(table_rows)}</tbody>
            </table>
        </div>
    """
    matrix_code = """
        <style>
            .whatif-toolbar {
                display: flex;
                align-items: center;
                justify-content: space-between;
                background: #f0fdf4;
                border: 1px solid #86efac;
                color: #166534;
                padding: 6px 14px;
                border-radius: 6px;
                font-size: 0.84rem;
                margin-bottom: 8px;
                box-sizing: border-box;
            }
            .whatif-toolbar button {
                background: #ffffff;
                border: 1px solid #16a34a;
                color: #15803d;
                padding: 3px 10px;
                border-radius: 4px;
                font-weight: 600;
                font-size: 0.8rem;
                cursor: pointer;
                transition: all 0.15s ease;
            }
            .whatif-toolbar button:hover {
                background: #15803d;
                color: #ffffff;
            }
            .matchup-selector {
                display: inline-flex;
                align-items: center;
                justify-content: center;
                gap: 3px;
                font-size: 0.82rem;
                white-space: nowrap;
            }
            .matchup-at {
                font-size: 0.76rem;
                color: #64748b;
                padding: 0 1px;
            }
            .whatif-team-btn {
                display: inline-block;
                padding: 2px 5px;
                margin: 0;
                font-size: 0.78rem;
                font-family: inherit;
                font-weight: 600;
                line-height: 1.2;
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                background: #f8fafc;
                color: #1e293b;
                cursor: pointer;
                transition: all 0.15s ease-in-out;
                user-select: none;
                vertical-align: middle;
            }
            .whatif-team-btn:hover {
                background: #e2e8f0;
                border-color: #94a3b8;
            }
            .whatif-team-btn.live-lead {
                font-weight: 800;
                border-color: #16803c;
                background: #f0fdf4;
                color: #15803d;
            }
            .whatif-team-btn.whatif-selected {
                background: #007791 !important;
                border-color: #005f73 !important;
                color: #ffffff !important;
                font-weight: 800 !important;
                box-shadow: 0 1px 3px rgba(0, 119, 145, 0.3);
            }
            .whatif-team-btn.whatif-dimmed {
                opacity: 0.45;
                background: #f1f5f9;
                color: #94a3b8;
                border-color: #e2e8f0;
            }
            .picks-table tbody tr.pinned-row:nth-child(1) td.col-whatif-active {
                background-color: #e0f2fe !important;
            }
            .picks-table tbody tr.pinned-row:nth-child(1) td {
                padding: 0.2rem 0.4rem;
            }
            .rank-badge {
                display: inline-block;
                min-width: 2.1rem;
                font-weight: 700;
                color: #1e293b;
            }
            .rank-badge.sim-active {
                color: #007791 !important;
                font-weight: 800 !important;
            }
            .rank-delta {
                display: inline-block;
                padding: 1px 4px;
                font-size: 0.72rem;
                font-weight: 700;
                border-radius: 3px;
                line-height: 1.1;
                margin-right: 3px;
                vertical-align: middle;
            }
            .rank-delta.rank-up {
                color: #15803d;
                background: #dcfce7;
                border: 1px solid #86efac;
            }
            .rank-delta.rank-down {
                color: #b91c1c;
                background: #fee2e2;
                border: 1px solid #fca5a5;
            }
            .rank-delta.rank-same {
                color: #64748b;
                background: #f1f5f9;
                border: 1px solid #cbd5e1;
            }
            .picks-table-scroll { width: 100%; height: 560px; overflow: auto; }
            .weekly-picks-scroll { overflow-x: auto; }
            .picks-table {
                width: max-content;
                min-width: 100%;
                border-collapse: separate;
                border-spacing: 0;
                table-layout: fixed;
                border-top: 1px solid #d5dce5;
                border-left: 1px solid #d5dce5;
                color-scheme: light;
            }
            #weekly-picks-table {
                width: max-content;
                min-width: 100%;
                table-layout: auto;
            }
            .picks-table th,
            .picks-table td {
                height: 38px;
                padding: 0.4rem 0.65rem;
                box-sizing: border-box;
                border-right: 1px solid #d5dce5;
                border-bottom: 1px solid #d5dce5;
                white-space: nowrap;
                text-align: center;
            }
            .picks-table th:first-child,
            .picks-table td:first-child {
                text-align: left;
            }
            .picks-table thead th,
            .picks-table tbody tr.pinned-row td {
                cursor: pointer;
            }
            .picks-table thead th {
                position: sticky;
                top: 0;
                z-index: 20;
                background: white;
                font-weight: bold;
            }
            .picks-table tbody tr.pinned-row:nth-child(1) td {
                position: sticky;
                top: 38px;
                z-index: 10;
                background: white;
                font-weight: bold;
            }
            .picks-table tbody tr.pinned-row:nth-child(2) td {
                position: sticky;
                top: 76px;
                z-index: 10;
                background: white;
                font-weight: bold;
            }
            .picks-table thead th,
            .picks-table tbody tr.pinned-row td {
                color: #202124 !important;
            }
            .picks-table tbody tr:not(.pinned-row) td:first-child {
                color: #202124 !important;
                background-color: #ffffff !important;
            }
            .picks-table th:nth-child(1),
            .picks-table td:nth-child(1) {
                position: sticky;
                left: 0;
                z-index: 5;
                background: white;
            }
            #weekly-picks-table thead th:first-child { z-index: 30; }
            #weekly-picks-table tbody tr.pinned-row td:first-child { z-index: 15; }
            #simulation-results-table thead th:first-child { z-index: 30; }
            @media (max-width: 640px) {
                #weekly-picks-table th:not(:first-child),
                #weekly-picks-table td:not(:first-child),
                #simulation-results-table th:not(:first-child),
                #simulation-results-table td:not(:first-child) {
                    width: 68px;
                    min-width: 48px;
                    max-width: 78px;
                    padding: 0.25rem 0.3rem;
                    font-size: 0.75rem;
                    white-space: normal;
                    overflow-wrap: anywhere;
                    vertical-align: middle;
                }
                #weekly-picks-table tbody tr.pinned-row td:nth-child(n+4) {
                    white-space: nowrap;
                    overflow-wrap: normal;
                }
                #simulation-results-table th:last-child,
                #simulation-results-table td:last-child {
                    width: max-content;
                    min-width: max-content;
                    max-width: none;
                    padding: 0.25rem 0.3rem;
                    white-space: nowrap;
                    overflow: visible;
                }
                #simulation-results-table col:not(:first-child) {
                    width: 68px !important;
                }
                #simulation-results-table col:last-child {
                    width: auto !important;
                }
                #weekly-picks-table th:first-child,
                #weekly-picks-table td:first-child {
                    width: 38vw;
                    min-width: 88px;
                    max-width: 128px;
                    white-space: nowrap;
                    overflow: hidden;
                    text-overflow: ellipsis;
                }
                #simulation-results-table col:first-child {
                    width: 38vw !important;
                }
                #simulation-results-table th:first-child,
                #simulation-results-table td:first-child {
                    width: 38vw;
                    min-width: 88px;
                    max-width: 128px;
                    white-space: nowrap;
                    overflow: hidden;
                    text-overflow: ellipsis;
                }
            }
            #weekly-picks-table th:nth-child(n+4),
            #weekly-picks-table td:nth-child(n+4) {
                width: max-content;
                min-width: max-content;
                max-width: none;
                white-space: nowrap;
                overflow-wrap: normal;
            }
            #weekly-picks-table td.column-heading {
                text-align: center;
                font-weight: bold;
                background-color: #eaf3ff !important;
            }
            .picks-table thead th,
            .picks-table tbody tr.pinned-row td,
            .picks-table thead th:nth-child(-n+3),
            .picks-table tbody tr.pinned-row:nth-child(1) td,
            .picks-table tbody tr.pinned-row:nth-child(2) td,
            .picks-table tbody tr.pinned-row td:nth-child(-n+3) {
                background-color: #eaf3ff !important;
            }
            .picks-table tbody tr:not(.pinned-row) td {
                color: #202124 !important;
                background-color: #ffffff !important;
            }
            .picks-table tbody tr:not(.pinned-row) td:first-child {
                color: #202124 !important;
                background-color: #ffffff !important;
            }
            .pick-status {
                display: inline-block;
                margin-left: 0.2rem;
                font-weight: bold;
            }
            .pick-status.leading { color: #16803c; }
            .pick-status.trailing { color: #d32f2f; }
            .pick-status.correct {
                color: #16803c;
                font-size: 1.25em;
                line-height: 0.8;
            }
            .pick-status.incorrect-final {
                color: #d32f2f;
                font-size: 1.25em;
                line-height: 0.8;
            }
            @media (min-width: 641px) {
                #weekly-picks-table th:first-child,
                #weekly-picks-table td:first-child,
                #simulation-results-table th:first-child,
                #simulation-results-table td:first-child {
                    color: #202124 !important;
                    background-color: #ffffff !important;
                }
            }
            .picks-table .sorted-asc::after { content: " ▲"; }
            .picks-table .sorted-desc::after { content: " ▼"; }
            .picks-table thead th:focus-visible,
            .picks-table tbody tr.pinned-row td:focus-visible {
                outline: 2px solid #2F6F9F;
            }
        </style>
        <script>
            (() => {
                const initWhatIfTable = () => {
                    const table = document.getElementById("weekly-picks-table");
                    if (!table) {
                        setTimeout(initWhatIfTable, 50);
                        return;
                    }
                    const body = table.tBodies[0];
                    if (!body || body.rows.length < 2) return;
                    const headerRow = table.tHead.rows[0];
                    const headers = Array.from(headerRow.cells);
                    const pinnedRow0 = body.rows[0];
                    const pinnedRow1 = body.rows[1];
                    const playerRows = Array.from(body.rows).slice(2).filter((row) => {
                        const p = (row.dataset.player || "").trim().toLowerCase();
                        return p && p !== "nan" && p !== "none";
                    });
                    const collator = new Intl.Collator(undefined, {numeric: true, sensitivity: "base"});
                    const directions = new Map([[1, "desc"], [2, "desc"]]);

                    const sortIndicators = [
                        ...headers.slice(3),
                        pinnedRow1.cells[0],
                        pinnedRow1.cells[1],
                        pinnedRow1.cells[2],
                    ];
                    if (sortIndicators.length >= 2) {
                        sortIndicators[sortIndicators.length - 2].classList.add("sorted-desc");
                        sortIndicators[sortIndicators.length - 1].classList.add("sorted-desc");
                    }

                    const whatIfOverrides = new Map();
                    const liveLeadersByCol = new Map();
                    pinnedRow0.querySelectorAll(".matchup-selector").forEach((sel) => {
                        const col = parseInt(sel.dataset.col, 10);
                        const liveLeader = sel.dataset.liveLeader;
                        if (liveLeader) {
                            liveLeadersByCol.set(col, liveLeader);
                        }
                    });

                    const getOrdinal = (n) => {
                        const rem100 = n % 100;
                        if (rem100 >= 11 && rem100 <= 13) return "th";
                        const last = n % 10;
                        if (last === 1) return "st";
                        if (last === 2) return "nd";
                        if (last === 3) return "rd";
                        return "th";
                    };

                    const escapeHtml = (str) =>
                        String(str)
                            .replace(/&/g, "&amp;")
                            .replace(/\\x3c/g, "&lt;")
                            .replace(/>/g, "&gt;")
                            .replace(/"/g, "&quot;");

                    let currentSortColumn = 1;
                    let currentSortDirection = "desc";

                    const applyCurrentSort = () => {
                        const index = currentSortColumn;
                        const direction = currentSortDirection;
                        const rows = Array.from(body.rows).slice(2);
                        rows.sort((left, right) => {
                            const a = left.cells[index]?.dataset?.sort ?? "";
                            const b = right.cells[index]?.dataset?.sort ?? "";
                            if (!a && b) return 1;
                            if (a && !b) return -1;
                            const comparison = collator.compare(a, b);
                            if (comparison === 0 && index === 1) {
                                const ytdA = left.cells[2]?.dataset?.sort ?? "";
                                const ytdB = right.cells[2]?.dataset?.sort ?? "";
                                const ytdComp = collator.compare(ytdA, ytdB);
                                return direction === "asc" ? ytdComp : -ytdComp;
                            }
                            return direction === "asc" ? comparison : -comparison;
                        });
                        rows.forEach((row) => body.appendChild(row));
                    };

                    const sortColumn = (index) => {
                        const previous = directions.get(index);
                        const direction = previous ? (previous === "desc" ? "asc" : "desc") : "asc";
                        directions.clear();
                        directions.set(index, direction);
                        currentSortColumn = index;
                        currentSortDirection = direction;
                        sortIndicators.forEach((item) => item.classList.remove("sorted-asc", "sorted-desc"));
                        const indicator = index < 3 ? pinnedRow1.cells[index] : headers[index];
                        if (indicator) indicator.classList.add(`sorted-${direction}`);
                        applyCurrentSort();
                    };

                    const updateWhatIf = () => {
                        const toolbar = document.getElementById("whatif-toolbar");
                        const summary = document.getElementById("whatif-summary");
                        if (whatIfOverrides.size > 0) {
                            if (toolbar) toolbar.style.display = "flex";
                            if (summary) {
                                const parts = [];
                                whatIfOverrides.forEach((team) => parts.push(team));
                                summary.textContent = `${whatIfOverrides.size} game${whatIfOverrides.size > 1 ? "s" : ""} simulated (${parts.join(", ")})`;
                            }
                        } else {
                            if (toolbar) toolbar.style.display = "none";
                        }

                        pinnedRow0.querySelectorAll(".matchup-selector").forEach((sel) => {
                            const col = parseInt(sel.dataset.col, 10);
                            const overridden = whatIfOverrides.get(col);
                            const liveLeader = liveLeadersByCol.get(col);
                            const cell = sel.closest("td");
                            if (cell) {
                                if (overridden) {
                                    cell.classList.add("col-whatif-active");
                                } else {
                                    cell.classList.remove("col-whatif-active");
                                }
                            }
                            sel.querySelectorAll(".whatif-team-btn").forEach((btn) => {
                                const team = btn.dataset.team;
                                btn.classList.remove("whatif-selected", "whatif-dimmed");
                                if (overridden) {
                                    if (team === overridden) {
                                        btn.classList.add("whatif-selected");
                                    } else {
                                        btn.classList.add("whatif-dimmed");
                                    }
                                } else {
                                    if (liveLeader && team === liveLeader) {
                                        btn.classList.add("live-lead");
                                    } else {
                                        btn.classList.remove("live-lead");
                                    }
                                }
                            });
                        });

                        const scoredPlayers = [];
                        playerRows.forEach((row) => {
                            const baseLocked = parseFloat(row.dataset.baseLocked || "0");
                            const baseYtd = parseFloat(row.dataset.baseYtd || "0");
                            const playerName = row.dataset.player || "";
                            const baseRank = parseInt(row.dataset.baseRank || "0", 10) || 1;
                            let weeklyTotal = baseLocked;

                            for (let c = 3; c < row.cells.length; c++) {
                                const cell = row.cells[c];
                                const pickedTeam = cell.dataset.team;
                                const pts = parseFloat(cell.dataset.pts || "0");
                                if (!pickedTeam || !pts) continue;

                                let winner = null;
                                let isSimulated = false;
                                if (whatIfOverrides.has(c)) {
                                    winner = whatIfOverrides.get(c);
                                    isSimulated = true;
                                } else if (liveLeadersByCol.has(c)) {
                                    winner = liveLeadersByCol.get(c);
                                }

                                const indicator = cell.querySelector(".pick-status");

                                if (winner) {
                                    if (pickedTeam === winner) {
                                        weeklyTotal += pts;
                                        if (indicator && !indicator.classList.contains("correct") && !indicator.classList.contains("incorrect-final")) {
                                            indicator.className = "pick-status leading";
                                            indicator.innerHTML = "&#9650;";
                                            indicator.title = isSimulated ? "Simulated winner" : "Currently leading";
                                        }
                                    } else {
                                        if (indicator && !indicator.classList.contains("correct") && !indicator.classList.contains("incorrect-final")) {
                                            indicator.className = "pick-status trailing";
                                            indicator.innerHTML = "&#9632;";
                                            indicator.title = isSimulated ? "Simulated loser" : "Currently trailing";
                                        }
                                    }
                                } else {
                                    if (indicator && !indicator.classList.contains("correct") && !indicator.classList.contains("incorrect-final")) {
                                        indicator.className = "pick-status";
                                        indicator.innerHTML = "";
                                        indicator.title = "";
                                    }
                                }
                            }

                            const ytdTotal = Math.round(baseYtd + weeklyTotal);
                            row.cells[1].textContent = weeklyTotal.toLocaleString();
                            row.cells[1].dataset.sort = String(weeklyTotal);
                            row.cells[2].textContent = ytdTotal.toLocaleString();
                            row.cells[2].dataset.sort = String(ytdTotal);

                            scoredPlayers.push({
                                row,
                                playerName,
                                baseRank,
                                weeklyTotal,
                                ytdTotal,
                            });
                        });

                        const sortedForRank = [...scoredPlayers].sort((a, b) => b.weeklyTotal - a.weeklyTotal);
                        let rank = 1;
                        for (let i = 0; i < sortedForRank.length; i++) {
                            if (i > 0 && sortedForRank[i].weeklyTotal < sortedForRank[i - 1].weeklyTotal) {
                                rank = i + 1;
                            }
                            sortedForRank[i].simRank = rank;
                        }

                        const isSimulating = whatIfOverrides.size > 0;

                        scoredPlayers.forEach((p) => {
                            const displayRank = isSimulating ? p.simRank : p.baseRank;
                            const ordinal = getOrdinal(displayRank);

                            if (isSimulating) {
                                const rankDiff = p.baseRank - p.simRank;
                                let deltaHtml = "";
                                if (rankDiff > 0) {
                                    deltaHtml = `\\x3cspan class="rank-delta rank-up" title="Up ${rankDiff} spot${rankDiff > 1 ? 's' : ''} in weekly standings (Live: ${p.baseRank}${getOrdinal(p.baseRank)} → Sim: ${p.simRank}${ordinal})">▲${rankDiff}\\x3c/span>`;
                                } else if (rankDiff < 0) {
                                    const down = -rankDiff;
                                    deltaHtml = `\\x3cspan class="rank-delta rank-down" title="Down ${down} spot${down > 1 ? 's' : ''} in weekly standings (Live: ${p.baseRank}${getOrdinal(p.baseRank)} → Sim: ${p.simRank}${ordinal})">▼${down}\\x3c/span>`;
                                } else {
                                    deltaHtml = `\\x3cspan class="rank-delta rank-same" title="Weekly place unchanged (${displayRank}${ordinal})">-\\x3c/span>`;
                                }
                                p.row.cells[0].innerHTML = `\\x3cspan class="rank-badge sim-active" title="Simulated weekly rank">${displayRank}${ordinal}\\x3c/span> ${deltaHtml} \\x3cspan class="player-name">${escapeHtml(p.playerName)}\\x3c/span>`;
                            } else {
                                p.row.cells[0].innerHTML = `\\x3cspan class="rank-badge">${displayRank}${ordinal}\\x3c/span>  \\x3cspan class="player-name">${escapeHtml(p.playerName)}\\x3c/span>`;
                            }
                            p.row.cells[0].dataset.sort = p.playerName;
                        });

                        applyCurrentSort();
                    };

                    pinnedRow0.querySelectorAll(".whatif-team-btn").forEach((btn) => {
                        btn.addEventListener("click", (e) => {
                            e.stopPropagation();
                            const col = parseInt(btn.dataset.col, 10);
                            const team = btn.dataset.team;
                            if (whatIfOverrides.get(col) === team) {
                                whatIfOverrides.delete(col);
                            } else {
                                whatIfOverrides.set(col, team);
                            }
                            updateWhatIf();
                        });
                    });

                    const resetBtn = document.getElementById("whatif-reset-btn");
                    if (resetBtn) {
                        resetBtn.addEventListener("click", (e) => {
                            e.stopPropagation();
                            whatIfOverrides.clear();
                            updateWhatIf();
                        });
                    }

                    Array.from(pinnedRow0.cells).forEach((cell) => {
                        cell.addEventListener("click", (e) => {
                            if (e.target.closest(".whatif-team-btn")) return;
                            sortColumn(cell.cellIndex);
                        });
                    });

                    sortIndicators.forEach((item) => {
                        item.addEventListener("click", () => sortColumn(item.cellIndex));
                        item.addEventListener("keydown", (event) => {
                            if (event.key === "Enter" || event.key === " ") {
                                event.preventDefault();
                                item.click();
                            }
                        });
                    });
                };

                if (document.readyState === "loading") {
                    document.addEventListener("DOMContentLoaded", initWhatIfTable);
                } else {
                    initWhatIfTable();
                }
            })();
        </script>
    """
    matrix_styles, matrix_script = matrix_code.split("<script>", 1)
    matrix_script = matrix_script.rsplit("</script>", 1)[0]
    with st.expander("📊 View this week's player picks."):
        st.html(
            f'{matrix_html}{matrix_styles}',
            width="stretch",
        )
    st.markdown("[View CBSSPORTS PICK'EM Website](https://picks.cbssports.com/football/pickem/pools/kbxw63b2ge3dknrugeytm===)")

    N_SIMS = 10000
    weekly_points, sim_win_pcts_raw = simulate_weekly_results(
        players,
        parsed_picks,
        locked_pts,
        DEFAULT_PAIRING_MAP,
        DECIDED_GAMES,
        GAME_PROBABILITIES,
        N_SIMS,
        st.session_state["simulation_seed"],
    )
    root_recommendations = get_weekly_root_recommendations(
        players,
        parsed_picks,
        locked_pts,
        DEFAULT_PAIRING_MAP,
        DECIDED_GAMES,
        GAME_PROBABILITIES,
    )

    median_scores = np.round(np.median(weekly_points, axis=1)).astype(int) if n_players > 0 else []

    final_root_for = []
    root_for_bold_by_player = {}
    final_sim_win_pcts = []

    for idx, p in enumerate(players):
        raw_pct = sim_win_pcts_raw[idx]
        recommendation = root_recommendations[idx] if root_recommendations else None
        if recommendation:
            root_labels = []
            root_html = []
            for item in recommendation:
                if item["has_preference"]:
                    label = item["team"] or "Tie"
                    points = 0 if item["contrary"] else item["points"]
                    label = f"{label}({points})"
                else:
                    label = "No preference"
                root_labels.append(label)
                safe_label = escapeHtml(label, quote=True)
                root_html.append(
                    f"<strong>{safe_label}</strong>"
                    if item["contrary"]
                    else safe_label
                )
            final_root_for.append(", ".join(root_labels))
            root_for_bold_by_player[p] = ", ".join(root_html)
        else:
            final_root_for.append(root_for_raw[idx])
        max_locked_opponent = max(
            (score for other_idx, score in enumerate(locked_pts) if other_idx != idx),
            default=float("-inf"),
        )
        if max_pts[idx] < max_locked_opponent:
            final_sim_win_pcts.append("None")
            continue
        if 0 <= raw_pct <= 0.1:
            final_sim_win_pcts.append("0.1% or less")
        else:
            final_sim_win_pcts.append(f"{raw_pct:5.1f}%".replace(" ", "\u2007"))

    sim_results = pd.DataFrame({
        'Player': players,
        'Locked': locked_pts,
        'Locked + Active': active_proj,
        'YTD Locked': ytd_totals,
        'YTD Locked + Active': ytd_active_totals,
        'Max': max_pts,
        'Sim Median Pts': median_scores,
        'Sim First-Place Share': final_sim_win_pcts,
        'Root For': final_root_for,
    }) if n_players > 0 else pd.DataFrame()

    if not sim_results.empty:
        sim_results['Wk Rank'] = sim_results['Locked + Active'].rank(ascending=False, method='min').astype(int)
        sim_results['YTD Rank'] = sim_results['YTD Locked'].rank(ascending=False, method='min').astype(int)
        
        sim_results['Players'] = sim_results.apply(
            lambda row: f"{row['Wk Rank']}{get_ordinal_suffix(row['Wk Rank'])}  {row['Player']}", axis=1
        )
        root_for_html_by_display_name = {
            display_name: root_for_bold_by_player[player]
            for player, display_name in zip(sim_results['Player'], sim_results['Players'])
            if player in root_for_bold_by_player
        }
        
        sorted_results = sim_results.sort_values(
            by=['Locked + Active', 'YTD Locked'],
            ascending=[False, False],
        ).reset_index(drop=True)
        
        column_order = [
            'Players', 
            'Locked', 
            'Locked + Active', 
            'YTD Rank', 
            'YTD Locked', 
            'YTD Locked + Active', 
            'Max', 
            'Sim Median Pts', 
            'Sim First-Place Share',
            'Root For'
        ]
        sorted_results = sorted_results[column_order]

        results_headers = "".join(
            f'<th scope="col" tabindex="0">'
            f'{escapeHtml("YTD Locked Rank" if column == "YTD Rank" else str(column))}</th>'
            for column in sorted_results.columns
        )
        results_rows = []
        for result_row in sorted_results.itertuples(index=False, name=None):
            cells = []
            for column_index, value in enumerate(result_row):
                display_value, sort_value = format_pick_cell(value)
                if sorted_results.columns[column_index] == "Sim First-Place Share":
                    if value == "None":
                        sort_value = ""
                    elif value == "0.1% or less":
                        sort_value = "0.1"
                    else:
                        sort_value = str(value).replace("%", "").replace("\u2007", "").strip()
                    sort_value = escapeHtml(sort_value, quote=True)
                elif sorted_results.columns[column_index] == "Root For":
                    display_value = root_for_html_by_display_name.get(
                        str(result_row[0]),
                        display_value,
                    )
                cells.append(f'<td data-sort="{sort_value}">{display_value}</td>')
            results_rows.append(f"<tr>{''.join(cells)}</tr>")
        results_colgroup = "".join(
            f'<col style="width:{"250px" if index == 0 else "250px" if index == len(sorted_results.columns) - 1 else "140px" if index == 1 else "110px"}">'
            for index in range(len(sorted_results.columns))
        )
        results_html = f"""
            <div class="picks-table-scroll simulation-results-scroll">
                <table class="picks-table simulation-results-table" id="simulation-results-table">
                    <colgroup>{results_colgroup}</colgroup>
                    <thead><tr>{results_headers}</tr></thead>
                    <tbody>{''.join(results_rows)}</tbody>
                </table>
            </div>
        """
        results_sorting = """
            <script>
                (() => {
                    const table = document.getElementById("simulation-results-table");
                    if (!table) return;
                    const headers = Array.from(table.tHead.rows[0].cells);
                    const body = table.tBodies[0];
                    const collator = new Intl.Collator(undefined, {numeric: true, sensitivity: "base"});
                    const initialSort = headers.findIndex((header) => header.textContent === "Locked + Active");
                    if (initialSort >= 0) headers[initialSort].classList.add("sorted-desc");
                    headers.forEach((header, index) => {
                        header.addEventListener("click", () => {
                            const direction = header.classList.contains("sorted-desc") ? "asc" : "desc";
                            headers.forEach((item) => item.classList.remove("sorted-asc", "sorted-desc"));
                            header.classList.add(`sorted-${direction}`);
                            const rows = Array.from(body.rows);
                            rows.sort((left, right) => {
                                const a = left.cells[index].dataset.sort || "";
                                const b = right.cells[index].dataset.sort || "";
                                if (!a && b) return 1;
                                if (a && !b) return -1;
                                const comparison = collator.compare(a, b);
                                return direction === "asc" ? comparison : -comparison;
                            });
                            rows.forEach((row) => body.appendChild(row));
                        });
                        header.addEventListener("keydown", (event) => {
                            if (event.key === "Enter" || event.key === " ") {
                                event.preventDefault();
                                header.click();
                            }
                        });
                    });
                })();
            </script>
        """
        results_sorting_script = results_sorting.split("<script>", 1)[1].rsplit("</script>", 1)[0]
        st.html(
            f"{results_html}{matrix_styles}"
            f"<script>{matrix_script}{results_sorting_script}</script>",
            width="stretch",
            unsafe_allow_javascript=True,
        )
        st.caption(
            "Sim First-Place Share is each player's share of first place across 10,000 weekly simulations "
            "(ties split the share). Simulations use ESPN live win probabilities. "
            "With 3 or fewer games left, Root For lists the preferred outcomes for contenders. "
            "A bold 0 means the recommended outcome goes against that player's pick. "
            "Eliminated players still see their highest-point picks."
        )

        st.subheader(
            "📈 Locked + Active Pts, Simulation Median Pts, and Maximum Possible"
        )
        fig, ax = plt.subplots(figsize=(8, 5))
        plot_df = sorted_results.head(15).melt(id_vars=['Players'], value_vars=['Locked + Active', 'Sim Median Pts', 'Max'])
        plot_df['variable'] = plot_df['variable'].map({
            'Locked + Active': 'Locked + Active Pts',
            'Sim Median Pts': 'Simulation Median Pts',
            'Max': 'Maximum Possible',
        })
        sns.barplot(
            data=plot_df,
            x='value',
            y='Players',
            hue='variable',
            saturation=1,
            hue_order=[
                'Locked + Active Pts',
                'Simulation Median Pts',
                'Maximum Possible',
            ],
            ax=ax,
            palette={
                'Locked + Active Pts': '#16803c',
                'Simulation Median Pts': '#808080',
                'Maximum Possible': '#d3d3d3',
            },
        )
        ax.tick_params(axis='y', labelsize=6, pad=2)
        ax.tick_params(axis='x', labelsize=7)
        ax.set_ylabel('Players', fontsize=8)
        ax.set_xlabel('Points', fontsize=8)
        ax.legend(fontsize=7, title_fontsize=7, loc='upper center', bbox_to_anchor=(0.5, 1.16), ncol=3)
        
        for label in ax.get_yticklabels():
            label.set_horizontalalignment('right')

        for container in ax.containers: 
            ax.bar_label(container, fmt='%.0f', padding=3, fontsize=6)
        fig.subplots_adjust(left=0.30, right=0.98, bottom=0.14, top=0.78)
        st.pyplot(fig)
    else:
        st.warning("No player picks could be parsed from the uploaded spreadsheet.")

# HTML escaping helper
def escapeHtml(text, quote=True):
    """Escape HTML special characters."""
    from html import escape
    return escape(str(text), quote=quote)
