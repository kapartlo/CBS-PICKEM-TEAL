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

def spread_to_win_prob(spread):
    if spread is None:
        return 0.5
    import scipy.stats as stats
    prob = stats.norm.cdf(-spread / 13.5)
    return float(np.clip(prob, 0.05, 0.95))

def get_ordinal_suffix(n):
    if 11 <= n <= 13:
        return 'th'
    return {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')

@st.cache_data(ttl=60)
def fetch_espn_nfl_data():
    url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
    completed_winners = []
    live_probabilities = {}
    pairing_map = []
    active_games = 0
    live_leaders = []
    decided_games = set()
    
    try:
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            for event in res.json().get("events", []):
                comp = event.get("competitions", [{}])[0]
                status = comp.get("status", {}).get("type", {}).get("name", "")
                is_final = (status == "STATUS_FINAL")
                is_live = (status in ["STATUS_IN_PROGRESS", "STATUS_HALFTIME"])
                if is_live: 
                    active_games += 1
                
                competitors = comp.get("competitors", [])
                home_team, away_team, home_score, away_score = None, None, 0, 0
                home_win, away_win = False, False
                
                for t in competitors:
                    abbr = t.get("team", {}).get("abbreviation", "")
                    score = int(t.get("score", 0)) if t.get("score") else 0
                    if t.get("homeAway") == "home":
                        home_team, home_score, home_win = abbr, score, t.get("winner", False)
                    else:
                        away_team, away_score, away_win = abbr, score, t.get("winner", False)
                
                if home_team and away_team:
                    pairing_map.append((home_team, away_team))
                    
                    if is_final or home_win or away_win:
                        decided_games.add(home_team)
                        decided_games.add(away_team)
                        if home_win or (is_final and home_score > away_score):
                            completed_winners.append(home_team)
                            live_probabilities[home_team], live_probabilities[away_team] = 1.0, 0.0
                        elif away_win or (is_final and away_score > home_score):
                            completed_winners.append(away_team)
                            live_probabilities[away_team], live_probabilities[home_team] = 1.0, 0.0
                    elif is_live:
                        if home_score > away_score:
                            live_leaders.append(home_team)
                        elif away_score > home_score:
                            live_leaders.append(away_team)
                        live_probabilities[home_team] = 0.5
                        live_probabilities[away_team] = 0.5
                    else:
                        odds_data = comp.get("odds", [{}])[0] if comp.get("odds") else {}
                        details = odds_data.get("details", "")
                        home_prob = 0.5
                        if details:
                            match = re.search(r'([A-Za-z]+)\s+([+-]?\d+\.?\d*)', details)
                            if match:
                                fav_team = match.group(1).upper()
                                spread_val = float(match.group(2))
                                fav_prob = spread_to_win_prob(spread_val)
                                home_prob = fav_prob if fav_team == home_team else 1.0 - fav_prob
                        live_probabilities[home_team] = home_prob
                        live_probabilities[away_team] = 1.0 - home_prob
    except Exception as e:
        st.warning(f"ESPN API warning: {e}")
        
    return completed_winners, live_probabilities, pairing_map, active_games, live_leaders, decided_games

st.title("🏈 CBS Sports Pick'em Pool Standings and Win Projections")

picks_df = load_excel_from_gdrive()

if picks_df is None:
    st.error("Failed to load Excel data from Google Drive. Please check your secrets configuration and internet connection.")
else:
    # Dynamically find the player column and game columns
    possible_player_cols = [c for c in picks_df.columns if any(k in str(c).lower() for k in ['player', 'name', 'unnamed: 0'])]
    player_col = possible_player_cols[0] if possible_player_cols else picks_df.columns[0]
    
    ytd_candidates = [c for c in picks_df.columns if str(c).strip().upper() == 'YTD']
    ytd_col = ytd_candidates[0] if ytd_candidates else (picks_df.columns[2] if len(picks_df.columns) > 2 else None)

    game_cols = [col for col in picks_df.columns if col not in [player_col, ytd_col] and 'TIE' not in str(col).upper() and 'TOTAL' not in str(col).upper()]
    parsed_picks, total_games = {}, len(game_cols) if len(game_cols) > 0 else 16

    for index in range(len(picks_df)):
        raw_name = picks_df.loc[index, player_col]
        if pd.isna(raw_name) or str(raw_name).strip() in ['', '-', 'NaN', 'Players', 'Player']: 
            continue
        name = re.sub(r'^\d*(?:st|nd|rd|th)?\s*', '', str(raw_name)).strip()
        name = re.sub(r'\s+sFie[A-Za-z]*$', '', name).strip()
        if not name or name.lower() == 'players':
            continue
            
        parsed_picks[name] = {}
        for col in game_cols:
            val = str(picks_df.loc[index, col]).strip()
            if '(' in val and ')' in val:
                m = re.match(r'([A-Za-z]+)\((\d+)\)', val)
                if m: 
                    parsed_picks[name][m.group(1).upper()] = int(m.group(2))

    COMPLETED_WINNERS, LIVE_PROBABILITIES, DEFAULT_PAIRING_MAP, ACTIVE_GAMES_COUNT, LIVE_LEADERS, DECIDED_GAMES = fetch_espn_nfl_data()
    players = list(parsed_picks.keys())
    n_players = len(players)

    st.info(f"**Week Status:** {len(COMPLETED_WINNERS)} of {total_games} games completed. {ACTIVE_GAMES_COUNT} active. | **Players:** {n_players}")

    with st.expander("📊 View Raw Pick Sheet"):
        st.dataframe(picks_df, use_container_width=True, hide_index=True)

    locked_pts, active_proj, max_pts, root_for_raw, ytd_totals, ytd_active_totals = [], [], [], [], [], []

    for p in players:
        row = picks_df[picks_df[player_col].astype(str).str.contains(p, na=False)]
        base_ytd = float(row.iloc[0][ytd_col]) if not row.empty and ytd_col and pd.notna(row.iloc[0][ytd_col]) else 0
        
        locked = sum(parsed_picks[p].get(t, 0) for t in COMPLETED_WINNERS if t in parsed_picks[p])
        locked_pts.append(locked)
        
        ytd_locked_val = int(round(base_ytd + locked))
        ytd_totals.append(ytd_locked_val)
        
        live_winning_pts = sum(parsed_picks[p].get(t, 0) for t in LIVE_LEADERS if t in parsed_picks[p])
        active_proj.append(locked + live_winning_pts)
        
        ytd_active_val = int(round(base_ytd + locked + live_winning_pts))
        ytd_active_totals.append(ytd_active_val)
        
        max_possible = locked + sum(pts for t, pts in parsed_picks[p].items() if t not in DECIDED_GAMES)
        max_pts.append(max_possible)
        
        pending_items = [(t, pts) for t, pts in parsed_picks[p].items() if t not in COMPLETED_WINNERS and t not in DECIDED_GAMES and LIVE_PROBABILITIES.get(t, 0.5) not in (1.0, 0.0)]
        pending = sorted(pending_items, key=lambda x: x[1], reverse=True)
        root_for_raw.append(", ".join([f"{t}({pts})" for t, pts in pending[:3]]) if pending else "NONE")

    remaining_cards = {
        p: tuple(sorted([(t, pts) for t, pts in parsed_picks[p].items() if t not in COMPLETED_WINNERS and t not in DECIDED_GAMES]))
        for p in players
    }

    N_SIMS = 10000
    pending_pairs = [pair for pair in DEFAULT_PAIRING_MAP if LIVE_PROBABILITIES.get(pair[0], 0.5) not in (1.0, 0.0) and pair[0] not in DECIDED_GAMES]
    unresolved = []
    for t1, t2 in pending_pairs: unresolved.extend([t1, t2])

    np.random.seed(42)
    weekly_points = np.zeros((n_players, N_SIMS)) if n_players > 0 else np.zeros((1, N_SIMS))
    sim_win_pcts_raw = np.zeros(n_players)

    if len(pending_pairs) > 0 and n_players > 0:
        sim_wins = np.zeros((N_SIMS, len(unresolved)), dtype=bool)
        for t1, t2 in pending_pairs:
            p1 = LIVE_PROBABILITIES.get(t1, 0.5)
            p2 = LIVE_PROBABILITIES.get(t2, 0.5)
            total_p = p1 + p2
            p1_norm = p1 / total_p if total_p > 0 else 0.5
            wins = np.random.rand(N_SIMS) < p1_norm
            try:
                sim_wins[:, unresolved.index(t1)] = wins
                sim_wins[:, unresolved.index(t2)] = ~wins
            except ValueError: pass

        for p_idx, player in enumerate(players):
            weekly_points[p_idx, :] = locked_pts[p_idx]
            for t_idx, team in enumerate(unresolved):
                weekly_points[p_idx, :] += sim_wins[:, t_idx] * parsed_picks[player].get(team, 0)

        max_scores = np.max(weekly_points, axis=0)
        win_shares = (weekly_points == max_scores) / np.sum(weekly_points == max_scores, axis=0)
        sim_win_pcts_raw = (np.sum(win_shares, axis=1) / N_SIMS) * 100
    elif n_players > 0:
        for p_idx in range(n_players): weekly_points[p_idx, :] = locked_pts[p_idx]

    median_scores = np.round(np.median(weekly_points, axis=1)).astype(int) if n_players > 0 else []
    max_leading_locked = max(locked_pts) if locked_pts else 0

    final_root_for = []
    final_sim_win_pcts = []

    for idx, p in enumerate(players):
        p_score = active_proj[idx]
        p_max = max_pts[idx]
        raw_pct = sim_win_pcts_raw[idx]
        
        has_zero_ways = False
        if p_max < max_leading_locked or p_max == 0 or len(parsed_picks[p]) == 0 or len(remaining_cards[p]) == 0:
            has_zero_ways = True
        else:
            for other_idx, other_p in enumerate(players):
                if p != other_p and len(parsed_picks[other_p]) > 0:
                    if active_proj[other_idx] > p_score and remaining_cards[p] == remaining_cards[other_p]:
                        has_zero_ways = True
                        break
                        
        if has_zero_ways:
            final_root_for.append("NONE")
            final_sim_win_pct
