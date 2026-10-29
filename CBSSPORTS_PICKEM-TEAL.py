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

# Compact CSS styling for mobile landscape view with adjusted top padding
st.markdown(
    """
    <style>
        .block-container { padding-top: 3rem !important; padding-bottom: 1rem; }
        h1 { font-size: 1.5rem !important; margin-bottom: 0.2rem !important; }
        h3 { font-size: 1.1rem !important; margin-top: 0.5rem !important; margin-bottom: 0.3rem !important; }
        p { margin-bottom: 0.3rem !important; }
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
    try:
        if "gdrive_b64" not in st.secrets:
            st.error("Missing 'gdrive_b64' in Streamlit secrets!")
            return None

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
    except Exception as e:
        st.error(f"Error loading Excel from Google Drive: {str(e)}")
        return None


# =========================================================
# 2. AUTOMATED ESPN SCOREBOARD FETCHER
# =========================================================
@st.cache_data(ttl=300)
def fetch_espn_nfl_data():
    url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
    completed_winners = []
    live_probabilities = {}
    pairing_map = []
    active_games_count = 0

    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            events = data.get("events", []) if isinstance(data, dict) else []
            for event in events:
                competitions = event.get("competitions", [])
                if not competitions:
                    continue
                comp = competitions[0]

                status_type = (
                    comp.get("status", {}).get("type", {}).get("name", "")
                )
                is_final = status_type == "STATUS_FINAL"
                is_in_progress = status_type in ["STATUS_IN_PROGRESS", "STATUS_HALFTIME"]

                if is_in_progress:
                    active_games_count += 1

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

    return completed_winners, live_probabilities, pairing_map, active_games_count


# Load Data
picks_df = load_excel_from_gdrive()

# =========================================================
# 3. RUN SIMULATION & RENDER DASHBOARD
# =========================================================
st.title("🏈 CBS Sports Pick'em Pool Standings and Win Projections")

if picks_df is not None:
    game_cols = [col for col in picks_df.columns[3:] if 'TIE' not in str(col).upper()]

    parsed_picks = {}
    total_games = len(game_cols) if len(game_cols) > 0 else 16

    for index in range(1, len(picks_df)):
        player_name = picks_df.loc[index, 'Players']
        if pd.isna(player_name) or str(player_name).strip() in ['', '-', 'NaN']:
            continue
        player_name = str(player_name).strip()
        player_name = re.sub(r'^\d*(?:st|nd|rd|th)?\s*', '', player_name).strip()

        parsed_picks[player_name] = {}
        for col in game_cols:
            pick_val = str(picks_df.loc[index, col]).strip()
            if pd.notna(pick_val) and '(' in pick_val and ')' in pick_val:
                match = re.match(r'([A-Za-z]+)\((\d+)\)', pick_val)
                if match:
                    team = match.group(1).upper()
                    points = int(match.group(2))
                    parsed_picks[player_name][team] = points

    COMPLETED_WINNERS, LIVE_PROBABILITIES, DEFAULT_PAIRING_MAP, ACTIVE_GAMES_COUNT = (
        fetch_espn_nfl_data()
    )

    players = list(parsed_picks.keys())
    n_players = len(players)
    completed_games_count = len(COMPLETED_WINNERS)

    st.info(
        f"**Week Status:** {completed_games_count} of {total_games} games completed. {ACTIVE_GAMES_COUNT} active. | **Player: {n_players}**"
    )

    # Raw Excel Data Debug Viewer (cleaned of text list)
    with st.expander("📊 View Raw Excel Data"):
        st.dataframe(picks_df, use_container_width=True, hide_index=True)

    locked_pts_list = []
    active_proj_list = []
    max_pts_list = []
    root_for_list = []
    ytd_total_list = []

    ytd_column_name = 'YTD' if 'YTD' in picks_df.columns else (picks_df.columns[2] if len(picks_df.columns) > 2 else None)

    for p in players:
        player_row = picks_df[picks_df['Players'].astype(str).str.contains(p, na=False)]
        
        base_ytd = 0
        if not player_row.empty and ytd_column_name is not None:
            try:
                base_ytd = float(player_row.iloc[0][ytd_column_name])
            except:
                base_ytd = 0

        locked = sum(
            parsed_picks[p].get(t, 0)
            for t in COMPLETED_WINNERS
            if t in parsed_picks[p]
        )
        locked_pts_list.append(locked)

        ytd_total_list.append(int(round(base_ytd + locked)))

        live_winning_points = sum(
            parsed_picks[p].get(t, 0)
            for t, prob in LIVE_PROBABILITIES.items()
            if prob == 1.0 and t in parsed_picks[p] and t not in COMPLETED_WINNERS
        )
        active_proj = locked + live_winning_points
        active_proj_list.append(active_proj)

        max_possible_points = locked + sum(
            pts for team, pts in parsed_picks[p].items()
            if team not in COMPLETED_WINNERS
        )
        max_pts_list.append(max_possible_points)

        all_player_picks = parsed_picks[p]
        pending_picks = [
            (t, pts) for t, pts in all_player_picks.items() 
            if t not in COMPLETED_WINNERS and LIVE_PROBABILITIES.get(t, 0.5) not in (1.0, 0.0)
        ]
        pending_picks.sort(key=lambda x: x[1], reverse=True)
        top_roots = [f"{t}({pts})" for t, pts in pending_picks[:3]]
        root_for_list.append(", ".join(top_roots) if top_roots else "-")

    # =========================================================
    # 4. RUN MONTE CARLO (10,000 SIMULATIONS)
    # =========================================================
    N_SIMS = 10000
    pending_pairs = [
        pair for pair in DEFAULT_PAIRING_MAP 
        if LIVE_PROBABILITIES.get(pair[0], 0.5) not in (1.0, 0.0)
    ]
    unresolved_teams = []
    for t1, t2 in pending_pairs:
        unresolved_teams.extend([t1, t2])

    np.random.seed(42)

    weekly_points = np.zeros((n_players, N_SIMS))

    if len(pending_pairs) > 0 and n_players > 0:
        sim_wins = np.zeros((N_SIMS, len(unresolved_teams)), dtype=bool)
        for pair_idx, (t1, t2) in enumerate(pending_pairs):
            prob_t1 = LIVE_PROBABILITIES.get(t1, 0.5)
            prob_t2 = LIVE_PROBABILITIES.get(t2, 0.5)
            tot_p = prob_t1 + prob_t2
            if tot_p > 0:
                prob_t1 = prob_t1 / tot_p

            wins_t1 = np.random.rand(N_SIMS) < prob_t1
            try:
                t1_idx = unresolved_teams.index(t1)
                t2_idx = unresolved_teams.index(t2)
                sim_wins[:, t1_idx] = wins_t1
                sim_wins[:, t2_idx] = ~wins_t1
            except ValueError:
                pass

        for p_idx, player in enumerate(players):
            locked_part = locked_pts_list[p_idx]
            weekly_points[p_idx, :] = locked_part
            for t_idx, team in enumerate(unresolved_teams):
                picks_val = parsed_picks[player].get(team, 0)
                weekly_points[p_idx, :] += sim_wins[:, t_idx] * picks_val

        max_weekly_scores = np.max(weekly_points, axis=0)
        weekly_winners = weekly_points == max_weekly_scores
        weekly_win_shares = weekly_winners / np.sum(weekly_winners, axis=0)
        total_weekly_wins = np.sum(weekly_win_shares, axis=1)
        sim_win_pcts = np.round((total_weekly_wins / N_SIMS) * 100, 1)
    else:
        for p_idx in range(n_players):
            weekly_points[p_idx, :] = locked_pts_list[p_idx]
        sim_win_pcts = np.zeros(n_players)

    median_sim_scores = np.median(weekly_points, axis=1) if n_players > 0 else []

    sim_results = pd.DataFrame({
        'Player': players,
        'Locked': locked_pts_list,
        'Locked YTD Total': ytd_total_list,
        'Locked + Active': active_proj_list,
        'Max': max_pts_list,
        'Sim Median Pts': np.round(median_sim_scores, 1),
        'Sim Win%': sim_win_pcts,
        'Root For': root_for_list,
    }) if n_players > 0 else pd.DataFrame()

    if not sim_results.empty:
        sim_results['Wk Rank'] = sim_results['Sim Median Pts'].rank(ascending=False, method='min').astype(int)
        sim_results['YTD Rank'] = sim_results['Locked YTD Total'].rank(ascending=False, method='min').astype(int)

        sorted_results = sim_results.sort_values(
            by='Sim Median Pts', ascending=False
        ).reset_index(drop=True)

        column_order = [
            'Wk Rank',
            'Player',
            'Locked',
            'Locked YTD Total',
            'YTD Rank',
            'Locked + Active',
            'Max',
            'Sim Median Pts',
            'Sim Win%',
            'Root For'
        ]
        sorted_results = sorted_results[column_order]

        st.dataframe(
            sorted_results,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Wk Rank": st.column_config.NumberColumn("Wk Rank", format="%d"),
                "Player": st.column_config.TextColumn("Player"),
                "Locked": st.column_config.NumberColumn("Locked", format="%d"),
                "Locked YTD Total": st.column_config.NumberColumn("Locked YTD Total", format="%d"),
                "YTD Rank": st.column_config.NumberColumn("YTD Rank", format="%d"),
                "Locked + Active": st.column_config.NumberColumn("Locked + Active", format="%d"),
                "Max": st.column_config.NumberColumn("Max", format="%d"),
                "Sim Median Pts": st.column_config.NumberColumn("Sim Median Pts", format="%.1f"),
                "Sim Win%": st.column_config.NumberColumn("Sim Win%", format="%.1f
