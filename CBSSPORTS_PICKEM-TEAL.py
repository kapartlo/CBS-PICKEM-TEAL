import base64
import io
import json
import re
import tempfile
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
import streamlit as st
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

st.set_page_config(layout="wide", page_title="NFL CBS Sports Pick'em Pool Simulation")

# =========================================================
# 1. GOOGLE DRIVE SECURE FILE DOWNLOADER
# =========================================================

@st.cache_data(ttl=600)
def load_excel_from_gdrive():
  # 1. Decode the Base64 secret from the root level
  creds_json = base64.b64decode(st.secrets["gdrive_b64"]).decode("utf-8")
  creds_dict = json.loads(creds_json)

  # 2. Write to a secure temporary file for Google's auth loader
  with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as temp:
    json.dump(creds_dict, temp)
    temp_path = temp.name

  # 3. Authenticate cleanly using the temporary file path
  creds = service_account.Credentials.from_service_account_file(
      temp_path, scopes=["https://www.googleapis.com/auth/drive.readonly"]
  )
  service = build("drive", "v3", credentials=creds)

  # 4. Your Google Drive File ID (Replace with your actual file ID string)
  file_id = "1by3okDqhmsITMO5Nig326OKOtJm-8Jw0"  

  # 5. Download the file buffer from Google Drive and load into Pandas
  request = service.files().get_media(fileId=file_id)
  file_buffer = io.BytesIO()
  downloader = MediaIoBaseDownload(file_buffer, request)

  done = False
  while not done:
    status, done = downloader.next_chunk()

  file_buffer.seek(0)
  df = pd.read_excel(file_buffer, sheet_name=0)
  return df


# Load your dataframe
picks_df = load_excel_from_gdrive()

# Quick sanity check display
st.write("Data successfully loaded!")
st.dataframe(picks_df)


   
# =========================================================
# 2. RUN SIMULATION & RENDER DASHBOARD
# =========================================================
st.title("🏈 CBS Sports Pick'em Pool Monte Carlo Dashboard")
st.write("Real-time win probabilities, projections, and customized rooting interests.")

if picks_df is not None:
    st.success("✅ Successfully loaded pool picks dynamically from Google Drive!")

    # Parsed Picks dictionary mapping
    parsed_picks = {}
    game_cols = [col for col in picks_df.columns if any(day in str(col) for day in ['1st', 'Thu', 'Sun', 'Mon', 'Tue', 'Wed', 'Fri', 'Sat'])]

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
                match = re.match(r"([A-Za-z]+)\((\d+)\)", pick_val)
                if match:
                    team = match.group(1).upper()
                    points = int(match.group(2))
                    parsed_picks[player_name][team] = points

    # Setup Team States
    COMPLETED_WINNERS = ['CHI', 'PIT', 'ATL', 'CIN', 'TB', 'NYG', 'MIN', 'MIA', 'CLE', 'DEN', 'DET', 'LAR']
    LIVE_PROBABILITIES = {}
    for team in COMPLETED_WINNERS:
        LIVE_PROBABILITIES[team] = 1.0
        for header in col_to_teams.values():
            if team in header:
                opp = header.replace(team, '')
                LIVE_PROBABILITIES[opp] = 0.0

    # Default baseline projection spreads
    DEFAULT_SLATE = {
        'KC': 0.60, 'SEA': 0.40, 'DAL': 0.50, 'PHI': 0.60,
        'BUF': 0.60, 'WSH': 0.45, 'JAC': 0.35
    }
    for team, prob in DEFAULT_SLATE.items():
        if team not in LIVE_PROBABILITIES:
            LIVE_PROBABILITIES[team] = prob

    # Math Setup
    players = list(parsed_picks.keys())
    n_players = len(players)
    completed_games_count = len(COMPLETED_WINNERS)
    total_games = len(col_to_teams)

    locked_pts_list = []
    current_pts_list = []
    projected_pts_list = []
    cbs_score_pts_list = []
    max_pts_list = []
    pending_teams_in_slate = [t for t, p in LIVE_PROBABILITIES.items() if p not in (1.0, 0.0)]

    for p in players:
        locked = sum(parsed_picks[p].get(t, 0) for t in COMPLETED_WINNERS if t in parsed_picks[p])
        locked_pts_list.append(locked)
        current_pts_list.append(locked)
        cbs_score = locked # Placeholder for Live leaders mapping
        cbs_score_pts_list.append(cbs_score)

        active_expected = sum(parsed_picks[p].get(t, 0) * LIVE_PROBABILITIES.get(t, 0.5) for t in LIVE_PROBABILITIES if LIVE_PROBABILITIES.get(t, 0.5) not in (1.0, 0.0) and t in parsed_picks[p])
        projected = locked + active_expected
        projected_pts_list.append(projected)

        max_possible_points = locked + sum(parsed_picks[p].get(t, 0) for t in pending_teams_in_slate if t in parsed_picks[p])
        max_pts_list.append(max_possible_points)

    # Build Simulation Results Dataframe
    sim_results = pd.DataFrame({
        'Player': players,
        'Games Completed': f"{completed_games_count}/{total_games}",
        'Locked Pts': locked_pts_list,
        'Current Pts': current_pts_list,
        'Proj CBS Sports Pts': cbs_score_pts_list,
        'Proj Pts': projected_pts_list,
        'Max Pts (All Picks Win)': max_pts_list
    })

    # =========================================================
    # 3. RUN MONTE CARLO (10,000 SIMULATIONS)
    # =========================================================
    N_SIMS = 10000
    pairing_map = [('KC', 'SEA'), ('DAL', 'PHI')]
    pending_pairs = [p for p in pairing_map if LIVE_PROBABILITIES.get(p[0], 0.5) not in (1.0, 0.0)]
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
        if tot_p > 0: prob_t1 = prob_t1 / tot_p

        wins_t1 = np.random.rand(N_SIMS) < prob_t1
        wins_t2 = ~wins_t1

        t1_idx = unresolved_teams.index(t1)
        t2_idx = unresolved_teams.index(t2)
        sim_wins[:, t1_idx] = wins_t1
        sim_wins[:, t2_idx] = wins_t2
        sim_wins_dict[t1] = wins_t1
        sim_wins_dict[t2] = wins_t2

    weekly_points = np.zeros((n_players, N_SIMS))
    for p_idx, player in enumerate(players):
        locked_part = locked_pts_list[p_idx]
        weekly_points[p_idx, :] = locked_part
        for t_idx, team in enumerate(unresolved_teams):
            picks_val = parsed_picks[player].get(team, 0)
            weekly_points[p_idx, :] += sim_wins[:, t_idx] * picks_val

    max_weekly_scores = np.max(weekly_points, axis=0)
    weekly_winners = (weekly_points == max_weekly_scores)
    weekly_win_shares = weekly_winners / np.sum(weekly_winners, axis=0)
    total_weekly_wins = np.sum(weekly_win_shares, axis=1)

    sim_results['Simulated Win %'] = total_weekly_wins / N_SIMS * 100
    
    # Display Interactive DataFrame
    st.subheader("📊 Live Standing & Win Probability Projections")
    sorted_results = sim_results.sort_values(by='Proj Pts', ascending=False).reset_index(drop=True)
    st.dataframe(sorted_results, use_container_width=True)

    # =========================================================
    # 4. CHART PLOTTING
    # =========================================================
    st.subheader("📈 Projected vs Current Performance")
    fig, ax = plt.subplots(figsize=(12, 6))
    plot_df = sorted_results.head(15).melt(id_vars=['Player'], value_vars=['Current Pts', 'Proj Pts'])
    sns.barplot(data=plot_df, x='Player', y='value', hue='variable', ax=ax, palette=['#2c3e50', '#1abc9c'])
    plt.xticks(rotation=45, ha='right')
    plt.ylabel("Points")
    st.pyplot(fig)

