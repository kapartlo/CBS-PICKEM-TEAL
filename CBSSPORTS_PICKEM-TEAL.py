"""
TEST...........................................
TEST...........................................
TEST...........................................
TEST...........................................
TEST...........................................
TEST...........................................
TEST...........................................
TEST...........................................
TEST...........................................
TEST...........................................
TEST...........................................
TEST...........................................
"""


import base64
import io
import json
import math
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

@st.cache_data(ttl=600)
def load_excel_from_gdrive():
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
        request = service.files().get_media(fileId="1by3okDqhmsITMO5Nig326OKOtJm-8Jw0")
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

def simulate_weekly_results(players, parsed_picks, locked_points, pairing_map, decided_teams, game_probabilities, n_sims=10000):
    weekly_points = np.zeros((len(players), n_sims))
    rng = np.random.default_rng(42)

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

@st.cache_data(ttl=60)
def fetch_espn_nfl_data():
    url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
    completed_winners = []
    team_probabilities = {}
    pairing_map = []
    game_probabilities = {}
    probability_sources = {}
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
        return completed_winners, team_probabilities, pairing_map, active_games, live_leaders, decided_games, game_probabilities, probability_sources, [f"Could not load ESPN scoreboard: {e}"]

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
        status_data = comp.get("status", {}).get("type", {})
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
        if is_live:
            active_games += 1
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
        errors,
    )

st.title("Teal CBS Sports Pick'em")

picks_df = load_excel_from_gdrive()

if picks_df is None:
    st.error("Failed to load Excel data from Google Drive. Please check your secrets configuration and internet connection.")
else:
    game_cols = [col for col in picks_df.columns[3:] if 'TIE' not in str(col).upper()]
    parsed_picks, total_games = {}, len(game_cols) if len(game_cols) > 0 else 16
    pick_parse_errors = []

    for index in range(1, len(picks_df)):
        raw_name = picks_df.loc[index, 'Players']
        if pd.isna(raw_name) or str(raw_name).strip() in ['', '-', 'NaN']: continue
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
            elif val.upper() not in {"", "-", "NAN", "NONE"}:
                pick_parse_errors.append(f"{name}: could not parse pick {val!r} in {col!r}.")

        if len(player_picks) > 0:
            parsed_picks[name] = player_picks

    (
        COMPLETED_WINNERS,
        TEAM_PROBABILITIES,
        DEFAULT_PAIRING_MAP,
        ACTIVE_GAMES_COUNT,
        LIVE_LEADERS,
        DECIDED_GAMES,
        GAME_PROBABILITIES,
        PROBABILITY_SOURCES,
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
    for player, player_picks in parsed_picks.items():
        unknown_teams = sorted(set(player_picks) - scheduled_teams)
        if unknown_teams:
            DATA_ERRORS.append(f"{player}: picks do not match ESPN teams: {', '.join(unknown_teams)}.")
        for home_team, away_team in DEFAULT_PAIRING_MAP:
            if home_team in player_picks and away_team in player_picks:
                DATA_ERRORS.append(f"{player}: picked both {home_team} and {away_team} in one matchup.")
    missing_forecasts = [pair for pair in DEFAULT_PAIRING_MAP if pair not in GAME_PROBABILITIES]
    if missing_forecasts:
        DATA_ERRORS.append(
            "Missing probabilities for: "
            + ", ".join(f"{home} vs {away}" for home, away in missing_forecasts)
            + "."
        )
    if DATA_ERRORS:
        st.error("Predictions are paused because the matchup or probability data could not be verified.")
        for error in DATA_ERRORS:
            st.write(f"- {error}")
        st.stop()

    st.info(f"**Week Status:** {len(COMPLETED_WINNERS)} of {total_games} games completed. {ACTIVE_GAMES_COUNT} active. | **Players:** {n_players}")
    st.caption(
        "Sim First-Place Share is each player's share of first place across 10,000 weekly simulations "
        "(ties split the share). Simulations use ESPN live win probabilities, no-vig market moneylines, "
        "or ESPN's matchup predictor when market odds are unavailable. Games are modeled independently; "
        "this is not a season-long pool-winning probability."
    )

    forecasts = pd.DataFrame(
        [
            {
                "Matchup": f"{home} vs {away}",
                "Home win": GAME_PROBABILITIES[(home, away)][0],
                "Away win": GAME_PROBABILITIES[(home, away)][1],
                "Tie": GAME_PROBABILITIES[(home, away)][2],
                "Probability source": PROBABILITY_SOURCES[(home, away)],
            }
            for home, away in DEFAULT_PAIRING_MAP
        ]
    )
    if not forecasts.empty:
        for column in ("Home win", "Away win", "Tie"):
            forecasts[column] = (forecasts[column] * 100).round(1).map(lambda value: f"{value:.1f}%")
        with st.expander("🏈 Game-by-game probabilities and sources"):
            st.dataframe(forecasts, use_container_width=True, hide_index=True)

    raw_player_col = picks_df.columns[0] if len(picks_df.columns) > 0 else 'Players'
    with st.expander("📊 View this week's player picks."):
        st.dataframe(
            picks_df, 
            use_container_width=True, 
            hide_index=True,
            column_config={
                raw_player_col: st.column_config.TextColumn(raw_player_col, pinned=True)
            }
        )

    locked_pts, active_proj, max_pts, root_for_raw, ytd_totals, ytd_active_totals = [], [], [], [], [], []
    ytd_col = 'YTD' if 'YTD' in picks_df.columns else (picks_df.columns[2] if len(picks_df.columns) > 2 else None)

    for p in players:
        row = picks_df[picks_df['Players'].astype(str).str.contains(p, na=False)]
        base_ytd = float(row.iloc[0][ytd_col]) if not row.empty and ytd_col else 0
        
        locked = sum(parsed_picks[p].get(t, 0) for t in COMPLETED_WINNERS if t in parsed_picks[p])
        locked_pts.append(locked)
        
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

    N_SIMS = 10000
    weekly_points, sim_win_pcts_raw = simulate_weekly_results(
        players,
        parsed_picks,
        locked_pts,
        DEFAULT_PAIRING_MAP,
        DECIDED_GAMES,
        GAME_PROBABILITIES,
        N_SIMS,
    )

    median_scores = np.round(np.median(weekly_points, axis=1)).astype(int) if n_players > 0 else []

    final_root_for = []
    final_sim_win_pcts = []

    for idx, p in enumerate(players):
        raw_pct = sim_win_pcts_raw[idx]
        final_root_for.append(root_for_raw[idx])
        if 0 < raw_pct < 0.1:
            final_sim_win_pcts.append("Under 0.1%")
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
        
        sorted_results = sim_results.sort_values(by=['Locked + Active', 'Sim Median Pts'], ascending=[False, False]).reset_index(drop=True)
        
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

        st.dataframe(
            sorted_results, 
            use_container_width=True, 
            hide_index=True, 
            column_config={
                "Players": st.column_config.TextColumn("Players", pinned=True),
                "Locked": st.column_config.NumberColumn("Locked", format="%d"),
                "Locked + Active": st.column_config.NumberColumn("Locked + Active", format="%d"),
                "YTD Rank": st.column_config.NumberColumn("YTD Rank", format="%d"),
                "YTD Locked": st.column_config.NumberColumn("YTD Locked", format="%d"),
                "YTD Locked + Active": st.column_config.NumberColumn("YTD Locked + Active", format="%d"),
                "Max": st.column_config.NumberColumn("Max", format="%d"),
                "Sim Median Pts": st.column_config.NumberColumn("Sim Median Pts", format="%d"),
                "Sim First-Place Share": st.column_config.TextColumn("Sim First-Place Share", alignment="right"),
                "Root For": st.column_config.TextColumn("Root For"),
            }
        )

        st.markdown("[Link to CBSSPORTS PICK'EM](https://picks.cbssports.com/football/pickem/pools/kbxw63b2ge3dknrugeytm===)")

        st.subheader("📈 Locked, Simulated and Maximum Points")
        fig, ax = plt.subplots(figsize=(6, 4))
        plot_df = sorted_results.head(15).melt(id_vars=['Players'], value_vars=['Locked', 'Sim Median Pts', 'Max'])
        sns.barplot(data=plot_df, x='value', y='Players', hue='variable', ax=ax, palette=['#95a5a6', '#00cec9', '#f39c12'])
        ax.tick_params(axis='y', labelsize=8)
        ax.tick_params(axis='x', labelsize=8)
        ax.set_ylabel('Players', fontsize=9)
        ax.set_xlabel('Points', fontsize=9)
        ax.legend(fontsize=8, title_fontsize=8, loc='lower right')
        
        for label in ax.get_yticklabels():
            label.set_horizontalalignment('left')
        ax.yaxis.set_tick_params(pad=-95)

        for container in ax.containers: 
            ax.bar_label(container, fmt='%.0f', padding=3, fontsize=7)
        plt.tight_layout()
        st.pyplot(fig)
    else:
        st.warning("No player picks could be parsed from the uploaded spreadsheet.")