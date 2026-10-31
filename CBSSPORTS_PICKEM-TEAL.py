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
    game_cols = [col for col in picks_df.columns[3:] if 'TIE' not in str(col).upper()]
    parsed_picks, total_games = {}, len(game_cols) if len(game_cols) > 0 else 16

    for index in range(1, len(picks_df)):
        raw_name = picks_df.loc[index, 'Players']
        if pd.isna(raw_name) or str(raw_name).strip() in ['', '-', 'NaN']: continue
        name = re.sub(r'^\d*(?:st|nd|rd|th)?\s*', '', str(raw_name)).strip()
        name = re.sub(r'\s+sFie[A-Za-z]*$', '', name).strip()
        
        player_picks = {}
        for col in game_cols:
            val = str(picks_df.loc[index, col]).strip()
            if '(' in val and ')' in val:
                m = re.match(r'([A-Za-z]+)\((\d+)\)', val)
                if m: player_picks[m.group(1).upper()] = int(m.group(2))
        
        if len(player_picks) > 0:
            parsed_picks[name] = player_picks

    COMPLETED_WINNERS, LIVE_PROBABILITIES, DEFAULT_PAIRING_MAP, ACTIVE_GAMES_COUNT, LIVE_LEADERS, DECIDED_GAMES = fetch_espn_nfl_data()
    players = list(parsed_picks.keys())
    n_players = len(players)

    st.info(f"**Week Status:** {len(COMPLETED_WINNERS)} of {total_games} games completed. {ACTIVE_GAMES_COUNT} active. | **Players:** {n_players}")

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
            if t not in COMPLETED_WINNERS and t not in DECIDED_GAMES and LIVE_PROBABILITIES.get(t, 0.5) not in (1.0, 0.0)
        ]
        pending = sorted(pending_items, key=lambda x: x[1], reverse=True)
        root_for_raw.append(", ".join([f"{t}({pts})" for t, pts in pending[:3]]) if pending else "NONE")

    remaining_cards = {
        p: tuple(sorted([
            (t, pts) for t, pts in parsed_picks[p].items()
            if t not in COMPLETED_WINNERS and t not in DECIDED_GAMES
        ]))
        for p in players
    }

    N_SIMS, pending_pairs = 10000, [pair for pair in DEFAULT_PAIRING_MAP if LIVE_PROBABILITIES.get(pair[0], 0.5) not in (1.0, 0.0) and pair[0] not in DECIDED_GAMES]
    unresolved = []
    for t1, t2 in pending_pairs: unresolved.extend([t1, t2])

    np.random.seed(42)
    weekly_points = np.zeros((n_players, N_SIMS))
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
    else:
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
            final_sim_win_pcts.append("ZERO")
        else:
            final_root_for.append(root_for_raw[idx])
            if raw_pct < 0.1:
                final_sim_win_pcts.append("Under 0.1%")
            else:
                final_sim_win_pcts.append(f"{raw_pct:.1f}%")

    sim_results = pd.DataFrame({
        'Player': players,
        'Locked': locked_pts,
        'Locked + Active': active_proj,
        'YTD Locked': ytd_totals,
        'YTD Locked + Active': ytd_active_totals,
        'Max': max_pts,
        'Sim Median Pts': median_scores,
        'Sim Win%': final_sim_win_pcts,
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
            'Sim Win%', 
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
                "Sim Win%": st.column_config.TextColumn("Sim Win%"),
                "Root For": st.column_config.TextColumn("Root For"),
            }
        )

        st.markdown("[Link to CBSSPORTS PICK'EM](https://picks.cbssports.com/football/pickem/pools/kbxw63b2ge3dknrugeytm===)")

        st.subheader("📈 Projected vs Current Performance")
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