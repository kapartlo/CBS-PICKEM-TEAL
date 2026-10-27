import base64
import io
import json
import re
import tempfile
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

# Compact styling for mobile landscape view with centered headers & compact padding
st.markdown(
    """
    <style>
        .block-container { padding-top: 1rem; padding-bottom: 1rem; }
        h1 { font-size: 1.6rem !important; margin-bottom: 0.2rem !important; }
        h3 { font-size: 1.2rem !important; margin-top: 0.8rem !important; margin-bottom: 0.4rem !important; }
        p { margin-bottom: 0.4rem !important; }
        th { text-align: center !important; }
    </style>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# 1. GOOGLE DRIVE SECURE FILE DOWNLOADER
# =========================================================
@st.cache_data(ttl=600)
def load_excel_from_gdrive():
    creds_json = base64.b64decode(st.secrets["gdrive_b64"]).decode("utf-8")
    creds_dict = json.loads(creds_json)

    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as temp:
        json.dump(creds_dict, temp)
        temp_path = temp.name

    creds = service_account.Credentials.from_service_account_file(
        temp_path, scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )
    service = build("drive", "v3", credentials=creds)
    file_id = "1by3okDqhmsITMO5Nig326OKOtJm-8Jw0"

    request = service.files().get_media(fileId=file_id)
    file_buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(file_buffer, request)

    done = False
    while not done:
        status, done = downloader.next_chunk()

    file_buffer.seek(0)
    df = pd.read_excel(file_buffer, sheet_name=0)
    return df


# =========================================================
# 2. AUTOMATED ESPN SCOREBOARD FETCHER
# =========================================================
@st.cache_data(ttl=300)
def fetch_espn_nfl_data():
    url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
    completed_winners = []
    live_probabilities = {}
    pairing_map = []

    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            for event in data.get("events", []):
                competitions = event.get("competitions", [])
                if not competitions:
                    continue
                comp = competitions[0]

                status_type = (
                    comp.get("status", {}).get("type", {}).get("name", "")
                )
                is_final = status_type == "STATUS_FINAL"

                competitors = comp.get("competitors", [])
                home_team, away_team = None, None
                home_winner, away_winner = False, False

                for team_info in competitors:
                    team_abbr = team_info.get("team", {}).get("abbreviation", "")
                    is_home = team_info.get("homeAway") == "home"
                    winner = team_info.get("winner", False)

                    if is_home:
                        home_team = team_abbr
                        home_winner = winner
                    else:
                        away_team = team_abbr
                        away_winner = winner

                if home_team and away_team:
                    pairing_map.append((home_team, away_team))
                    if is_final:
                        if home_winner:
                            completed_winners.append(home_team)
                            live_probabilities[home_team] = 1.0
                            live_probabilities[away_team] = 0.0
                        elif away_winner:
                            completed_winners.append(away_team)
                            live_probabilities[away_team] = 1.0
                            live_probabilities[home_team] = 0.0
                    else:
                        live_probabilities[home_team] = 0.5
                        live_probabilities[away_team] = 0.5
    except Exception as e:
        st.warning(f"Could not connect to ESPN API: {e}")

    return completed_winners, live_probabilities, pairing_map


# Load Data
picks_df = load_excel_from_gdrive()

# =========================================================
# 3. RUN SIMULATION & RENDER DASHBOARD
# =========================================================
st.title("🏈 CBS Sports Pick'em Pool Monte Carlo Dashboard")

if picks_df is not None:
    # Parse Picks dictionary mapping
    parsed_picks = {}
    game_cols = [
        col
        for col in picks_df.columns
        if any(
            day in str(col)
            for day in ['1st', 'Thu', 'Sun', 'Mon', 'Tue', 'Wed', 'Fri', 'Sat']
        )
    ]

    col_to_teams = {}
    for col in game_cols:
        match_val = str(picks_df.loc[0, col]).strip()
        if pd.notna(match_val) and match_val != 'nan':
            col_to_teams[col] = match_val

    for index in range(1, len(picks_df)):
        player_name = picks_df.loc[index, 'Players']
        if pd.isna(player_name) or str(player_name).strip() in ['', '-', 'NaN']:
            continue
        player_name = str(player_name).strip()
        player_name = re.sub(r'^\d*(?:st|nd|rd|th)?\s*', '', player_name).strip()
        player_name = re.sub(r'\s+sFie[A-Za-z]*$', '', player_name).strip()

        parsed_picks[player_name] = {}
        for col, match_str in col_to_teams.items():
            pick_val = str(picks_df.loc[index, col]).strip()
            if pd.notna(pick_val) and '(' in pick_val and ')' in pick_val:
                match = re.match(r'([A-Za-z]+)\((\d+)\)', pick_val)
                if match:
                    team = match.group(1).upper()
                    points = int(match.group(2))
                    parsed_picks[player_name][team] = points

    # Automatically fetch winners and probabilities from ESPN API
    COMPLETED_WINNERS, LIVE_PROBABILITIES, DEFAULT_PAIRING_MAP = (
        fetch_espn_nfl_data()
    )

    # Math Setup
    players = list(parsed_picks.keys())
    n_players = len(players)
    completed_games_count = len(COMPLETED_WINNERS)
    total_games = len(col_to_teams)

    locked_pts_list = []
    projected_pts_list = []
    cbs_score_pts_list = []
    max_pts_list = []
    root_for_list = []

    pending_teams_in_slate = [
        t for t, p in LIVE_PROBABILITIES.items() if p not in (1.0, 0.0)
    ]

    for p in players:
        locked = sum(
            parsed_picks[p].get(t, 0)
            for t in COMPLETED_WINNERS
            if t in parsed_picks[p]
        )
        locked_pts_list.append(locked)
        cbs_score = locked
        cbs_score_pts_list.append(cbs_score)

        active_expected = sum(
            parsed_picks[p].get(t, 0) * LIVE_PROBABILITIES.get(t, 0.5)
            for t in LIVE_PROBABILITIES
            if LIVE_PROBABILITIES.get(t, 0.5) not in (1.0, 0.0)
            and t in parsed_picks[p]
        )
        projected = locked + active_expected
        projected_pts_list.append(projected)

        max_possible_points = locked + sum(
            parsed_picks[p].get(t, 0)
            for t in pending_teams_in_slate
            if t in parsed_picks[p]
        )
        max_pts_list.append(max_possible_points)

        # Calculate Root For Teams (Pending picks with highest confidence points)
        pending_picks = [
            (t, parsed_picks[p].get(t, 0))
            for t in pending_teams_in_slate
            if t in parsed_picks[p]
        ]
        pending_picks.sort(key=lambda x: x[1], reverse=True)
        top_roots = [f"{t}({pts})" for t, pts in pending_picks[:3]]
        root_for_list.append(", ".join(top_roots) if top_roots else "-")

    # Build Condensed Dataframe with Locked Pts & Centered Layout
    sim_results = pd.DataFrame({
        'Player': players,
        'Games': f"{completed_games_count}/{total_games}",
        'Locked Pts': locked_pts_list,
        'CBS Proj': cbs_score_pts_list,
        'Proj Pts': np.round(projected_pts_list, 1),
        'Max': max_pts_list,
        'Root For': root_for_list,
    })

    # =========================================================
    # 4. RUN MONTE CARLO (10,000 SIMULATIONS)
    # =========================================================
    N_SIMS = 10000
    pending_pairs = [
        p for p in DEFAULT_PAIRING_MAP if LIVE_PROBABILITIES.get(p[0], 0.5) not in (1.0, 0.0)
    ]
    unresolved_teams = []
    for t1, t2 in pending_pairs:
        unresolved_teams.extend([t1, t2])

    np.random.seed(42)
    sim_wins_dict = {}
    sim_wins = np.zeros((N_SIMS, len(unresolved_teams)), dtype=bool)

    for pair_idx, (t1, t2) in enumerate(pending_pairs):
        prob_t1 = LIVE_PROBABILITIES.get(t1, 0.5)
        prob_t2 = LIVE_PROBABILITIES.get(t2, 0.5)
        tot_p = prob_t1 + prob_t2
        if tot_p > 0:
            prob_t1 = prob_t1 / tot_p

        wins_t1 = np.random.rand(N_SIMS) < prob_t1
        wins_t2 = ~wins_t1

        try:
            t1_idx = unresolved_teams.index(t1)
            t2_idx = unresolved_teams.index(t2)
            sim_wins[:, t1_idx] = wins_t1
            sim_wins[:, t2_idx] = wins_t2
        except ValueError:
            pass

        sim_wins_dict[t1] = wins_t1
        sim_wins_dict[t2] = wins_t2

    weekly_points = np.zeros((n_players, N_SIMS))
    for p_idx, player in enumerate
