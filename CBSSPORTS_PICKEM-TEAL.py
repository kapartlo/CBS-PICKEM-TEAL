# @title Run CBS Sports Pick'em Simulation (Teal)
import numpy as np
import pandas as pd
import requests
import itertools
import scipy.stats as stats
import re
from datetime import datetime, timedelta, timezone

# =========================================================
# 1. DYNAMICALLY LOAD ALL_POOL_PICKS (WEB-FIRST / EXCEL FALLBACK)
# =========================================================
EXCEL_FILE_PATH = '/content/drive/MyDrive/Colab Notebooks/CBSSPORTS PICKEM Files/CBSSPORTS Pickem Weekly Picks.xlsx'

def parse_pool_picks(picks_df):
    game_cols = [
        col for col in picks_df.columns
        if any(day in str(col) for day in ['1st', 'Thu', 'Sun', 'Mon', 'Tue', 'Wed', 'Fri', 'Sat'])
    ]
    parsed_picks = {}
    col_to_teams = {}
    player_rows = picks_df

    if not picks_df.empty:
        first_row = picks_df.iloc[0]
        first_player = first_row.get('Players')
        if pd.isna(first_player) or str(first_player).strip() in ['', '-', 'NaN']:
            player_rows = picks_df.iloc[1:]
            for col in game_cols:
                match_val = first_row[col]
                if pd.notna(match_val) and str(match_val).strip():
                    col_to_teams[col] = str(match_val).strip()

    for _, row in player_rows.iterrows():
        player_name = row.get('Players')
        if pd.isna(player_name) or str(player_name).strip() in ['', '-', 'NaN']:
            continue
        player_name = str(player_name).strip()
        player_name = re.sub(r'^\s*\d*(?:st|nd|rd|th)?\s*', '', player_name).strip()
        player_name = re.sub(r'\s+sFie[A-Za-z]*$', '', player_name).strip()

        parsed_picks[player_name] = {}
        for col in game_cols:
            pick_val = row[col]
            if pd.notna(pick_val) and '(' in str(pick_val) and ')' in str(pick_val):
                match = re.match(r"([A-Za-z]+)\((\d+)\)", str(pick_val).strip())
                if match:
                    team = match.group(1).upper()
                    team = {'JAX': 'JAC'}.get(team, team)
                    points = int(match.group(2))
                    parsed_picks[player_name][team] = points

    return parsed_picks, col_to_teams

if 'ALL_POOL_PICKS' not in globals() or not ALL_POOL_PICKS:
    ALL_POOL_PICKS = {}

    if 'SHEET_CSV_URL' in globals() and SHEET_CSV_URL:
        print("⏳ Attempting to load picks from your Google Sheets Web Link...")
        try:
            picks_df = pd.read_csv(SHEET_CSV_URL)
            parsed_picks, col_to_teams = parse_pool_picks(picks_df)
            ALL_POOL_PICKS = parsed_picks
            print(f"SUCCESS: Loaded {len(ALL_POOL_PICKS)} players from Google Sheet Web Link!")
        except Exception as e:
            print(f"★ Google Sheets Web Link loading failed: {e}. Falling back to local Drive...")

    if not ALL_POOL_PICKS:
        try:
            picks_df = pd.read_excel(EXCEL_FILE_PATH)
            parsed_picks, col_to_teams = parse_pool_picks(picks_df)
            ALL_POOL_PICKS = parsed_picks
            print(f"Successfully parsed picks for {len(ALL_POOL_PICKS)} players from Excel!")
        except Exception as e:
            print(f"Error loading/parsing Excel file: {e}. Falling back to default empty state.")
            ALL_POOL_PICKS = {}
else:
    print(f"Using {len(ALL_POOL_PICKS)} players currently loaded in memory.")

# =========================================================
# 2. FREE ESPN API MULTI-WEEK SEARCH FOR ALIGNMENT
# =========================================================
ESPN_TEAM_MAP = {
    'ARI': 'ARI', 'ATL': 'ATL', 'BAL': 'BAL', 'BUF': 'BUF', 'CAR': 'CAR', 'CHI': 'CHI',
    'CIN': 'CIN', 'CLE': 'CLE', 'DAL': 'DAL', 'DEN': 'DEN', 'DET': 'DET', 'GB': 'GB',
    'HOU': 'HOU', 'IND': 'IND', 'JAX': 'JAC', 'JAC': 'JAC', 'KC': 'KC', 'LV': 'LV', 'LAC': 'LAC',
    'LAR': 'LAR', 'MIA': 'MIA', 'MIN': 'MIN', 'NE': 'NE', 'NO': 'NO', 'NYG': 'NYG',
    'NYJ': 'NYJ', 'PHI': 'PHI', 'PIT': 'PIT', 'SF': 'SF', 'SEA': 'SEA', 'TB': 'TB',
    'TEN': 'TEN', 'WSH': 'WAS', 'WAS': 'WAS'
}

def spread_to_prob(spread):
  return stats.norm.cdf(-spread, loc=0, scale=13.8)

# Populate matchups from Excel headers
active_match_headers = list(col_to_teams.values()) if 'col_to_teams' in globals() else []
print(f"ፔ Active Weekly Slate Matchups (Excel): {active_match_headers}")

LIVE_PROBABILITIES = {}
LIVE_WINNING_TEAMS = []
LIVE_CURRENT_LEADERS = []
completed_games_count = 0

# Multi-week historical search to locate these exact matchups
print("🔍 Searching through NFL schedule weeks to match Excel slate...")
matched_games = {}
for week in range(1, 19):
    url = f"https://site.api.espn.com/apis/site/v2/sports/american-football/nfl/scoreboard?week={week}"
    try:
        res = requests.get(url).json()
        for event in res.get('events', []):
            competition = event.get('competitions', [{}])[0]
            competitors = competition.get('competitors', [])
            home = next(c for c in competitors if c.get('homeAway') == 'home')
            away = next(c for c in competitors if c.get('homeAway') == 'away')
            h_code = ESPN_TEAM_MAP.get(home.get('team', {}).get('abbreviation', ''), '').upper()
            a_code = ESPN_TEAM_MAP.get(away.get('team', {}).get('abbreviation', ''), '').upper()
            status_state = event.get('status', {}).get('type', {}).get('state', '')
            status_name = event.get('status', {}).get('type', {}).get('name', '')
            completed = (status_state == 'post') or (status_name in ['STATUS_FINAL', 'STATUS_FINAL_OVERTIME'])

            for m in active_match_headers:
                if (h_code and a_code) and ((h_code in m) and (a_code in m)):
                    h_score = int(home.get('score', 0))
                    a_score = int(away.get('score', 0))
                    matched_games[m] = {
                        'completed': completed,
                        'home': h_code,
                        'away': a_code,
                        'home_score': h_score,
                        'away_score': a_score
                    }
    except Exception as e:
        continue

# Reconstruct live tracking based on correct historical/live matchups matched
for m, g in matched_games.items():
    h_code, a_code = g['home'], g['away']
    h_score, a_score = g['home_score'], g['away_score']
    if g['completed']:
        completed_games_count += 1
        if h_score > a_score:
            LIVE_PROBABILITIES[h_code] = 1.0
            LIVE_PROBABILITIES[a_code] = 0.0
            LIVE_WINNING_TEAMS.append(h_code)
        elif a_score > h_score:
            LIVE_PROBABILITIES[a_code] = 1.0
            LIVE_PROBABILITIES[h_code] = 0.0
            LIVE_WINNING_TEAMS.append(a_code)
    else:
        if h_score > a_score:
            LIVE_CURRENT_LEADERS.append(h_code)
        elif a_score > h_score:
            LIVE_CURRENT_LEADERS.append(a_code)

# Do not invent completed results when ESPN has no completed matchup data.
if completed_games_count == 0:
    print("★ No completed games matched your slate; treating all games as unresolved.")

print(f"🎯 Dynamic alignment found {completed_games_count} completed games matching your Excel sheet matchups!")

# Fallback probability mapping for remaining unresolved teams
DEFAULT_SLATE = {
    'CHI': 0.50, 'PIT': 0.50, 'SF': 0.60, 'ATL': 0.40, 'TB': 0.45, 'CAR': 0.55,
    'NYG': 0.35, 'HOU': 0.65, 'MIN': 0.50, 'NYJ': 0.50, 'MIA': 0.50, 'CLE': 0.40, 'DEN': 0.60,
    'DET': 0.50, 'LAR': 0.55, 'LV': 0.45, 'KC': 0.60, 'SEA': 0.40, 'DAL': 0.50,
    'CIN': 0.55, 'NE': 0.30, 'BAL': 0.65, 'BUF': 0.60, 'ARI': 0.40, 'IND': 0.45,
    'NO': 0.50, 'PHI': 0.60, 'WSH': 0.45, 'TEN': 0.40, 'GB': 0.55, 'JAC': 0.35
}

for team, prob in DEFAULT_SLATE.items():
  if team not in LIVE_PROBABILITIES:
    LIVE_PROBABILITIES[team] = prob

for team in LIVE_WINNING_TEAMS:
  LIVE_PROBABILITIES[team] = 1.0

# =========================================================
# 4. CALCULATE STANDINGS & RUN MONTE CARLO (IF DATA EXISTS)
# =========================================================
if not ALL_POOL_PICKS:
  print("\n★ Cannot run simulation: No player picks are currently loaded.")
else:
  sim_results = pd.DataFrame({'Player': list(ALL_POOL_PICKS.keys())})

  locked_pts_list = []
  current_pts_list = []
  projected_pts_list = []
  cbs_score_pts_list = []
  max_pts_list = []

  total_games = len(col_to_teams) if 'col_to_teams' in locals() and col_to_teams else 14

  # Identify which teams are still playing / pending
  pending_teams_in_slate = []
  for team, prob in LIVE_PROBABILITIES.items():
    if prob not in (1.0, 0.0):
      pending_teams_in_slate.append(team)

  for p in ALL_POOL_PICKS:
    # Only sum points for teams the player actually selected that won (probability == 1.0)
    locked = sum(ALL_POOL_PICKS[p].get(t, 0) for t in LIVE_WINNING_TEAMS if t in ALL_POOL_PICKS[p])
    locked_pts_list.append(locked)

    current = locked
    current_pts_list.append(current)

    cbs_score = locked + sum(ALL_POOL_PICKS[p].get(t, 0) for t in LIVE_CURRENT_LEADERS if t in ALL_POOL_PICKS[p])
    cbs_score_pts_list.append(cbs_score)

    active_expected = sum(ALL_POOL_PICKS[p].get(t, 0) * LIVE_PROBABILITIES.get(t, 0.5) for t in LIVE_PROBABILITIES if LIVE_PROBABILITIES.get(t, 0.5) not in (1.0, 0.0) and t in ALL_POOL_PICKS[p])
    projected = locked + active_expected
    projected_pts_list.append(projected)

    # A missing probability means unresolved, not a confirmed loss.
    max_possible_points = sum(
        points for team, points in ALL_POOL_PICKS[p].items()
        if LIVE_PROBABILITIES.get(team, 0.5) != 0.0
    )
    max_pts_list.append(max_possible_points)

  sim_results['Games Completed'] = f"{completed_games_count}/{total_games}"
  sim_results['Locked Pts'] = locked_pts_list
  sim_results['Current Pts'] = current_pts_list
  sim_results['Proj CBS Sports Pts'] = cbs_score_pts_list
  sim_results['Proj Pts'] = projected_pts_list
  sim_results['Max Pts (All Picks Win)'] = max_pts_list

  # =========================================================
  # 5. MONTE CARLO SIMULATION & DYNAMIC LEVERAGE ENGINE
  # =========================================================
  N_SIMS = 10000
  players = list(ALL_POOL_PICKS.keys())
  n_players = len(players)

  pairing_map = [
      ('CHI', 'NE'),
      ('PIT', 'NO'),
      ('SF', 'ATL'),
      ('CIN', 'BAL'),
      ('TB', 'CAR'),
      ('NYG', 'HOU'),
      ('IND', 'MIN'),
      ('MIA', 'NYJ'),
      ('CLE', 'TEN'),
      ('DEN', 'ARI'),
      ('GB', 'DET'),
      ('LAR', 'LV'),
      ('KC', 'SEA'),
      ('DAL', 'PHI')
  ]

  pending_pairs = []
  unresolved_teams = []

  for team1, team2 in pairing_map:
    p1 = LIVE_PROBABILITIES.get(team1, 0.5)
    p2 = LIVE_PROBABILITIES.get(team2, 0.5)
    if p1 not in (1.0, 0.0) or p2 not in (1.0, 0.0):
      pending_pairs.append((team1, team2))
      unresolved_teams.extend([team1, team2])

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
      picks_val = ALL_POOL_PICKS[player].get(team, 0)
      weekly_points[p_idx, :] += sim_wins[:, t_idx] * picks_val

  max_weekly_scores = np.max(weekly_points, axis=0)
  weekly_winners = (weekly_points == max_weekly_scores)
  weekly_win_shares = weekly_winners / np.sum(weekly_winners, axis=0)
  total_weekly_wins = np.sum(weekly_win_shares, axis=1)

  n_unresolved = len(pending_pairs)
  outcomes_grids = list(itertools.product([0, 1], repeat=min(n_unresolved, 10)))

  math_status_map = {p: 0 for p in players}
  for outcome in outcomes_grids:
    path_outcome_wins = {}
    for idx, (team1, team2) in enumerate(pending_pairs[:10]):
      if outcome[idx] == 0:
        path_outcome_wins[team1] = 1.0
        path_outcome_wins[team2] = 0.0
      else:
        path_outcome_wins[team1] = 0.0
        path_outcome_wins[team2] = 1.0

    scenario_scores = {}
    for p in players:
      score = locked_pts_list[players.index(p)]
      for t, w in path_outcome_wins.items():
        if w == 1.0:
          score += ALL_POOL_PICKS[p].get(t, 0)
      scenario_scores[p] = score

    max_pool_score = max(scenario_scores.values())
    for p, score in scenario_scores.items():
      if score == max_pool_score:
        math_status_map[p] += 1

  leverage_swings_list = []
  for p_idx, player in enumerate(players):
    # Check if this player has non-zero configuration picks overall
    has_active_picks = any(v > 0 for v in ALL_POOL_PICKS[player].values())

    if not has_active_picks:
      leverage_swings_list.append("")
      continue

    player_alive = (math_status_map[player] > 0)
    swings = []

    for (t1, t2) in pending_pairs:
      p_t1 = ALL_POOL_PICKS[player].get(t1, 0)
      p_t2 = ALL_POOL_PICKS[player].get(t2, 0)
      chosen_team = t1 if p_t1 > p_t2 else t2
      conf = max(p_t1, p_t2)

      if player_alive:
        pts_if_t1 = weekly_points.copy()
        pts_if_t1[p_idx, :] = np.where(sim_wins_dict[t1], weekly_points[p_idx, :], weekly_points[p_idx, :] + p_t1 - p_t2)
        max_scores_t1 = np.max(pts_if_t1, axis=0)
        win_share_t1 = np.sum((pts_if_t1[p_idx, :] == max_scores_t1) / np.sum(pts_if_t1 == max_scores_t1, axis=0)) / N_SIMS

        pts_if_t2 = weekly_points.copy()
        pts_if_t2[p_idx, :] = np.where(sim_wins_dict[t2], weekly_points[p_idx, :], weekly_points[p_idx, :] + p_t2 - p_t1)
        max_scores_t2 = np.max(pts_if_t2, axis=0)
        win_share_t2 = np.sum((pts_if_t2[p_idx, :] == max_scores_t2) / np.sum(pts_if_t2 == max_scores_t2, axis=0)) / N_SIMS

        swing_val = abs(win_share_t1 - win_share_t2)
        best_team = t1 if win_share_t1 > win_share_t2 else t2
        best_conf = ALL_POOL_PICKS[player].get(best_team, 0)
        swings.append((best_team, best_conf, swing_val))
      else:
        ev_t1 = p_t1 * LIVE_PROBABILITIES.get(t1, 0.5)
        ev_t2 = p_t2 * LIVE_PROBABILITIES.get(t2, 0.5)
        swing_val = abs(ev_t1 - ev_t2)
        swings.append((chosen_team, conf, swing_val))

    swings.sort(key=lambda x: x[2], reverse=True)
    top_4 = swings[:4]

    # Only include non-zero point selections in interest labels
    interest_labels = [f"{team} ({conf}pt)" for team, conf, val in top_4 if conf > 0]
    leverage_swings_list.append(", ".join(interest_labels))

  sim_results['Simulated Weekly 1st Place %'] = total_weekly_wins / N_SIMS * 100
  sim_results['Math Survival Count'] = [math_status_map[p] for p in players]
  sim_results['Best Rooting Interests'] = leverage_swings_list
  sim_results['Proj 1st Place %'] = sim_results['Simulated Weekly 1st Place %']

  # Updated visual headers: placed "Max Pts" right after "Current Pts" and before "Proj CBS", with decreased width (from 25 to 18)
  header_line1 = f"{'Proj':<6} {'Player':<18} {'Games':>10} {'Locked':>8} {'Current':>8} {'Max Pts':>18} {'Proj CBS':>14} {'Proj':>8}  {'Simulated Weekly':<12}    {'Best Rooting Interests'}"
  header_line2 = f"{'Rank':<6} {'':<18} {'Completed':>10} {'Pts':>8} {'Pts':>8} {'(All Picks Win)':>18} {'Sports Pts':>14} {'Pts':>8}  {'1st Place %':<12}"
  title_header = f"ጁ ESPN Powered Pick'em Projections (10,000 Monte Carlo Simulations via Spreads)"

  print('\n' + '=' * 170)
  print(title_header)
  print('=' * 170)
  print(header_line1)
  print(header_line2)
  print('-' * 170)

  for rank, (idx, row) in enumerate(sim_results.sort_values(by='Proj Pts', ascending=False).iterrows(), start=1):
    win_pct = row['Proj 1st Place %']
    math_surv = row['Math Survival Count']

    if math_surv == 0:
      win_pct_str = f"{' -':>12}"
    elif win_pct < 0.5:
      win_pct_str = f"{'Under 0.5%':>12}"
    else:
      win_pct_str = f"{win_pct:>11.1f}%"

    print(
        f"{rank:<6} {row['Player']:<18} {row['Games Completed']:>10}"
        f" {row['Locked Pts']:>8d} {row['Current Pts']:>8d} {row['Max Pts (All Picks Win)']:>18d} {row['Proj CBS Sports Pts']:>14d} {row['Proj Pts']:>8.1f}"
        f"  {win_pct_str:<12}    {row['Best Rooting Interests']}"
    )
  print('=' * 170 + '\n')
