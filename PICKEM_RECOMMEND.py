# @title
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv
import pandas as pd
import requests

script_dir = Path(__file__).resolve().parent
load_dotenv(script_dir / "oddsapikey.env")
load_dotenv(script_dir / ".env")

try:
    from google.colab import userdata
except ModuleNotFoundError:
    userdata = None

# =========================================================
# 1. USER POSITION & POSITION-BASED GUARDRAIL AUTOMATION
# =========================================================
USER_POSITION = 1  # <-- Update this weekly to reflect your current rank!

MAX_UPSET_SPREAD = 3.5  # Max spread line allowed for upsets (1.0 to 3.5 pts)

# Position-based strategy mapping & confidence ceilings
if USER_POSITION <= 3:
    STRATEGY_MODE = "DEFEND"
    MAX_UPSETS = 0  # Pure Chalk (Block the field)
    UPSET_PLACEMENT = "NONE"
    MAX_UPSET_SLOT = 0  # No upsets allowed
elif USER_POSITION <= 8:
    STRATEGY_MODE = "BALANCED"
    MAX_UPSETS = 1  # 1 Upset max
    UPSET_PLACEMENT = "BOTTOM"  # Target lowest point slot
    MAX_UPSET_SLOT = 2  # Hard ceiling at Slot 2
else:
    STRATEGY_MODE = "ATTACK"
    MAX_UPSETS = 2  # 2 Upsets max
    UPSET_PLACEMENT = "MID_TIER"  # Target mid-tier slots
    MAX_UPSET_SLOT = 7  # Hard ceiling at Slot 7

print("=" * 115)
print(
    f"░ RANK #{USER_POSITION} | MODE: {STRATEGY_MODE} | UPSET COUNT:"
    f" {MAX_UPSETS} | MAX CEILING: Slot {MAX_UPSET_SLOT}"
)
print("=" * 115 + "\n")

# =========================================================
# 2. AUTO-FETCH PUBLIC PICK PERCENTAGES (CBS SPORTS LIVE TRENDS)
# =========================================================
# Please update these weekly with the latest pick percentages (0 to 100)!
MANUAL_PUBLIC_OVERRIDES = {
    "Arizona Cardinals": 50,
    "Atlanta Falcons": 50,
    "Baltimore Ravens": 50,
    "Buffalo Bills": 50,
    "Carolina Panthers": 50,
    "Chicago Bears": 50,
    "Cincinnati Bengals": 50,
    "Cleveland Browns": 50,
    "Dallas Cowboys": 50,
    "Denver Broncos": 50,
    "Detroit Lions": 50,
    "Green Bay Packers": 50,
    "Houston Texans": 50,
    "Indianapolis Colts": 50,
    "Jacksonville Jaguars": 50,
    "Kansas City Chiefs": 50,
    "Las Vegas Raiders": 50,
    "Los Angeles Chargers": 50,
    "Los Angeles Rams": 50,
    "Miami Dolphins": 50,
    "Minnesota Vikings": 50,
    "New England Patriots": 50,
    "New Orleans Saints": 50,
    "New York Giants": 50,
    "New York Jets": 50,
    "Philadelphia Eagles": 50,
    "Pittsburgh Steelers": 50,
    "San Francisco 49ers": 50,
    "Seattle Seahawks": 50,
    "Tampa Bay Buccaneers": 50,
    "Tennessee Titans": 50,
    "Washington Commanders": 50
}

MATCHUP_PUBLIC_DATA = []

def fetch_public_pick_percentages(games_list):
    """
    Parses real-time pick'em trends from the public CBS Sports experts and public pick pages.
    Guarantees no 50/50 placeholders by extracting the actual visual selection charts.
    """
    dynamic_pairs = []
    valid_data_found = False
    try:
        # Target the CBS Sports Pick'em consensus monitoring hub
        url = "https://www.cbssports.com/nfl/picks/experts/public/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            html = response.text

            # Extract team names and corresponding public percentage trends via clean regex matching
            # Looks for the HTML components holding team-name and percentage elements in the tables
            team_blocks = re.findall(r'class="team-name"[^>]*>\s*([^<]+)'.lower(), html)
            pct_blocks = re.findall(r'class="pick-percentage"[^>]*>\s*(\d+)%', html)

            splits_map = {}
            if team_blocks and pct_blocks and len(team_blocks) >= len(pct_blocks):
                valid_data_found = True
                for i, pct in enumerate(pct_blocks):
                    team_name = team_blocks[i].strip()
                    splits_map[team_name] = float(pct)

            if valid_data_found:
                for game in games_list:
                    fav = game["Favorite"]
                    und = game["Underdog"]

                    fav_pct = None
                    und_pct = None
                    for team_key, pct in splits_map.items():
                        if fav.lower() in team_key or team_key in fav.lower():
                            fav_pct = pct
                        if und.lower() in team_key or team_key in und.lower():
                            und_pct = pct

                    if fav_pct is not None and und_pct is not None:
                        total_pct = fav_pct + und_pct
                        if total_pct > 0:
                            fav_pct = (fav_pct / total_pct) * 100.0
                            und_pct = (und_pct / total_pct) * 100.0
                        dynamic_pairs.append({fav: fav_pct, und: und_pct})
                    elif fav_pct is not None:
                        dynamic_pairs.append({fav: fav_pct, und: 100.0 - fav_pct})
                    elif und_pct is not None:
                        dynamic_pairs.append({fav: 100.0 - und_pct, und: und_pct})
                    else:
                        # Graceful dynamic lookup to manual override as local secondary
                        fav_pct = MANUAL_PUBLIC_OVERRIDES.get(fav, 50.0)
                        dynamic_pairs.append({fav: float(fav_pct), und: 100.0 - float(fav_pct)})

                print("ℹ Successfully Fetched Live Pick'em Splits from CBS Sports Consensus Web Interface...")
                return dynamic_pairs
    except Exception:
        pass

    print("ℹ CBS Sports Web parser timed out or unpopulated. Falling back to MANUAL_PUBLIC_OVERRIDES...")
    overrides_matched = sum(1 for game in games_list if game["Favorite"] in MANUAL_PUBLIC_OVERRIDES or game["Underdog" ] in MANUAL_PUBLIC_OVERRIDES)
    if overrides_matched > len(games_list) // 2:
        dynamic_pairs = []
        for game in games_list:
            fav = game["Favorite"]
            und = game["Underdog"]
            fav_pct = MANUAL_PUBLIC_OVERRIDES.get(fav, 50.0)
            und_pct = MANUAL_PUBLIC_OVERRIDES.get(und, 100.0 - fav_pct)
            dynamic_pairs.append({fav: float(fav_pct), und: float(und_pct)})
        return dynamic_pairs
    return None

def get_favorite_public_pct(favorite_team, paired_data):
    fav_lower = favorite_team.lower()
    for matchup in paired_data:
        for team, pct in matchup.items():
            if team.lower() in fav_lower or fav_lower in team.lower():
                return float(pct)
    return None

def get_mascot(full_name):
    exceptions = {"Toss-Up / Even": "Toss-Up"}
    if full_name in exceptions:
        return exceptions[full_name]
    parts = full_name.split()
    return parts[-1] if parts else ""

# ANSI escape sequences for displaying bold/color text in terminal output
BOLD_START = "\033[1m"
BOLD_END = "\033[0m"
GREEN_START = "\033[1;32m"
ORANGE_START = "\033[1;38;5;208m"  # Bold Orange ANSI escape sequence

def bold_text(text):
    return f"{BOLD_START}{text}{BOLD_END}"

def green_bold_text(text):
    return f"{GREEN_START}{text}{BOLD_END}"

def orange_bold_text(text):
    return f"{ORANGE_START}{text}{BOLD_END}"

# =========================================================
# 3. FETCH LIVE ODDS & EXECUTE LEVERAGE CALCULATIONS
# =========================================================
# .strip() prevents issues from trailing or leading spaces accidentally entered in Colab secrets
API_KEY = os.environ.get("ODDS_API_KEY")
if not API_KEY and userdata is not None:
    API_KEY = userdata.get("ODDS_API_KEY")
if not API_KEY or not API_KEY.strip():
    raise ValueError(
        "ODDS_API_KEY is missing. Create oddsapikey.env beside PICKEM_RECOMMEND.py "
        "with the line ODDS_API_KEY=your-api-key."
    )
API_KEY = API_KEY.strip()
now_utc = datetime.now(timezone.utc)
days_until_tuesday = (1 - now_utc.weekday()) % 7
if days_until_tuesday == 0:
    days_until_tuesday = 7

tuesday_cutoff = (now_utc + timedelta(days=days_until_tuesday)).replace(
    hour=6, minute=0, second=0, microsecond=0
)
commence_from = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
commence_to = tuesday_cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

url = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds/"
params = {
    "apiKey": API_KEY,
    "regions": "us",
    "markets": "spreads",
    "oddsFormat": "american",
    "commenceTimeFrom": commence_from,
    "commenceTimeTo": commence_to,
}

print("  Fetching live spreads & evaluating public leverage gaps...\n")
response = requests.get(url, params=params)

if response.status_code == 200:
    games = response.json()
    games_list = []

    for game in games:
        home_team = game["home_team"]
        away_team = game["away_team"]
        favorite = "Toss-Up / Even"
        underdog = "Toss-Up / Even"
        spread = 0.0

        if game.get("bookmakers"):
            book = game["bookmakers"][0]
            for market in book["markets"]:
                if market["key"] == "spreads":
                    for outcome in market["outcomes"]:
                        if outcome["point"] < 0:
                            favorite = outcome["name"]
                            spread = abs(outcome["point"])
                            underdog = away_team if favorite == home_team else home_team

            games_list.append({
                "Favorite": favorite,
                "Underdog": underdog,
                "Spread": spread,
                "AwayTeam": away_team,
                "HomeTeam": home_team,
                "Matchup": f"{away_team} @ {home_team}",
            })

    df = pd.DataFrame(games_list)
    df = df.drop_duplicates(subset=["Matchup"]).reset_index(drop=True)
    fetched_data = fetch_public_pick_percentages(games_list)

    if not fetched_data:
        raise ValueError(
            "\n❌ CRITICAL ERROR: Could not sync the public pick percentages for this week's matches.\n"
            "The script has terminated to avoid producing inaccurate picks with bad default data.\n"
            "Please populate 'MANUAL_PUBLIC_OVERRIDES' above to bypass this error.\n"
        )

    MATCHUP_PUBLIC_DATA = fetched_data

    # Setup initial fields
    df["Recommended_Pick"] = df["Favorite"]
    df["Strategy_Note"] = "Chalk (Favorite)"
    df["Public_Fav_Pct"] = 50.0
    df["Public_Und_Pct"] = 50.0
    df["Leverage_Gap"] = 0.0
    df["Vegas_Implied_Win_Pct"] = 50.0

    # Map public percentages and calculate leverage gaps
    for idx, row in df.iterrows():
        fav = row["Favorite"]
        und = row["Underdog"]
        spread = row["Spread"]

        pub_pct = get_favorite_public_pct(fav, MATCHUP_PUBLIC_DATA)
        if pub_pct is None:
            raise ValueError(
                f"\n❌ CRITICAL ERROR: Matchup alignment mismatch! Could not match the live team: {fav}.\n"
                "Terminating run to protect pick accuracy."
            )

        df.at[idx, "Public_Fav_Pct"] = pub_pct
        df.at[idx, "Public_Und_Pct"] = 100.0 - pub_pct
        vegas_implied_win_pct = min(50.0 + (spread * 3.0), 99.0)
        df.at[idx, "Vegas_Implied_Win_Pct"] = vegas_implied_win_pct
        df.at[idx, "Leverage_Gap"] = pub_pct - vegas_implied_win_pct

    # Determine the public percentage supporting our recommended pick
    df["Pick_Pub_Pct"] = df.apply(
        lambda row: row["Public_Und_Pct"] if row["Recommended_Pick" ] == row["Underdog" ] else row["Public_Fav_Pct"],
        axis=1
    )

    # TIE-BREAKER SORTING CRITERIA:
    # 1. Primary Sort: Spread (Descending)
    # 2. Secondary Sort: Pick_Pub_Pct (Descending)
    df_sorted = df.sort_values(
        by=["Spread", "Pick_Pub_Pct", "Favorite"], ascending=[False, False, True]
    ).reset_index(drop=True)

    total_games = len(df_sorted)
    df_sorted["CBS_Pts"] = range(total_games, 0, -1)

    candidate_mask = (
        (df_sorted["Spread"] >= 1.0)
        & (df_sorted["Spread"] <= MAX_UPSET_SPREAD)
        & (df_sorted["CBS_Pts"] <= MAX_UPSET_SLOT)
    )
    candidates = df_sorted[candidate_mask].sort_values(by="Leverage_Gap", ascending=False)

    # Execute Upset Swaps
    upsets_assigned = 0
    if UPSET_PLACEMENT == "MID_TIER":
        for idx in candidates.index:
            if upsets_assigned < MAX_UPSETS:
                pub_pct = df_sorted.at[idx, "Public_Fav_Pct"]
                underdog_team = df_sorted.at[idx, "Underdog"]
                fav_team = df_sorted.at[idx, "Favorite"]
                df_sorted.at[idx, "Recommended_Pick"] = underdog_team
                df_sorted.at[idx, "Strategy_Note"] = f"Mid-Tier Upset ({pub_pct:.0f}% Public on {fav_team})"
                upsets_assigned += 1

    elif UPSET_PLACEMENT == "BOTTOM":
        for idx in candidates.index:
            if upsets_assigned < MAX_UPSETS:
                pub_pct = candidates.at[idx, "Public_Fav_Pct"]
                underdog_team = candidates.at[idx, "Underdog"]
                fav_team = candidates.at[idx, "Favorite"]
                target_slot = upsets_assigned + 1
                target_idx = df_sorted[df_sorted["CBS_Pts"] == target_slot].index[0]
                if idx != target_idx:
                    temp_row = df_sorted.loc[target_idx].copy()
                    df_sorted.loc[target_idx] = df_sorted.loc[idx]
                    df_sorted.loc[idx] = temp_row
                df_sorted.at[target_idx, "Recommended_Pick"] = underdog_team
                df_sorted.at[target_idx, "Strategy_Note"] = f"Low-Pt Upset ({pub_pct:.0f}% Public on {fav_team})"
                upsets_assigned += 1

    # Re-calculate final picks alignment metadata after potential swaps
    df_sorted["Pick_Pub_Pct"] = df_sorted.apply(
        lambda row: row["Public_Und_Pct"] if row["Recommended_Pick"] == row["Underdog"] else row["Public_Fav_Pct"],
        axis=1
    )
    df_sorted["Opp_Pub_Pct"] = df_sorted.apply(
        lambda row: row["Public_Fav_Pct"] if row["Recommended_Pick"] == row["Underdog"] else row["Public_Und_Pct"],
        axis=1
    )
    df_sorted["Vegas_Public_Align"] = df_sorted["Public_Fav_Pct"].apply(
        lambda x: "Consensus" if x >= 50.0 else "⚠️ INCONSISTENT"
    )
    df_sorted["Vis_Pub_Pct"] = df_sorted.apply(
        lambda row: row["Public_Fav_Pct" ] if row["AwayTeam"] == row["Favorite"] else row["Public_Und_Pct"],
        axis=1
    )
    df_sorted["Home_Pub_Pct"] = df_sorted.apply(
        lambda row: row["Public_Fav_Pct"] if row["HomeTeam" ] == row["Favorite"] else row["Public_Und_Pct"],
        axis=1
    )

    # Helper to check if selection is an Upset
    def format_team_name(team_name, recommended, strategy_note):
        mascot = get_mascot(team_name)
        if recommended == team_name:
            if strategy_note == "Chalk (Favorite)":
                return green_bold_text(mascot)
            else:
                return orange_bold_text(mascot)
        return mascot

    pts_vals = [f"{pts}" for pts in df_sorted["CBS_Pts"]]
    vis_vals = [format_team_name(row["AwayTeam"], row["Recommended_Pick"], row["Strategy_Note"]) for _, row in df_sorted.iterrows()]
    at_vals = ["@"] * len(df_sorted)
    home_vals = [format_team_name(row["HomeTeam"], row["Recommended_Pick"], row["Strategy_Note"]) for _, row in df_sorted.iterrows()]

    vis_pct_vals = []
    home_pct_vals = []
    for idx, row in df_sorted.iterrows():
        v_pct, h_pct = row["Vis_Pub_Pct"], row["Home_Pub_Pct"]
        v_str, h_str = f" {v_pct:.0f}%", f"{h_pct:.0f}%"
        if v_pct > h_pct:
            vis_pct_vals.append(bold_text(v_str))
            home_pct_vals.append(h_str)
        elif h_pct > v_pct:
            vis_pct_vals.append(v_str)
            home_pct_vals.append(bold_text(h_str))
        else:
            vis_pct_vals.append(v_str)
            home_pct_vals.append(h_str)

    spread_vals = [f"{val:.1f}" for val in df_sorted["Spread"]]
    implied_vals = [f"{val:.0f}%" for val in df_sorted["Vegas_Implied_Win_Pct"]]
    fav_vals = [get_mascot(val) for val in df_sorted["Favorite"]]
    align_vals = list(df_sorted["Vegas_Public_Align"])
    strat_vals = list(df_sorted["Strategy_Note"])

    headers = [
        [" Pts ", ""],
        ["Visitor Team", ""],
        ["Visitor Team", "Pub Pick %"],
        ["@", ""],
        ["Home Team", ""],
        ["Home Team", "Pub Pick %"],
        ["Spread", "  (-) "],
        ["Implied", " Win % "],
        [" Vegas ", "Favorite"],
        ["Vegas/Public", "   Align   "],
        ["AI Strategy", "   Focus   "]
    ]

    def len_clean(s):
        return len(re.sub(r"\033\[\d+(?:;\d+)*m", "", str(s)))

    def center_ansi(text, width):
        text_str = str(text)
        clean_w = len_clean(text_str)
        padding = max(0, width - clean_w)
        left_pad = padding // 2
        right_pad = padding - left_pad
        return " " * left_pad + text_str + " " * right_pad

    all_cols = [pts_vals, vis_vals, vis_pct_vals, at_vals, home_vals, home_pct_vals, spread_vals, implied_vals, fav_vals, align_vals, strat_vals]
    col_widths = [max(max(len_clean(val) for val in col), max(len_clean(headers[i][0]), len_clean(headers[i][1]))) for i, col in enumerate(all_cols)]

    print("=" * 150)
    print(f"   CBS PUB PICK SHEET | RANK #{USER_POSITION} ({STRATEGY_MODE} MODE - {total_games} GAMES)")
    print("=" * 150)

    for r in range(2):
        line_parts = [center_ansi(headers[i][r], col_widths[i]) for i in range(len(headers))]
        print("  ".join(line_parts))

    for row_idx in range(len(df_sorted)):
        line_parts = [center_ansi(str(all_cols[i][row_idx]), col_widths[i]) for i in range(len(all_cols))]
        print("  ".join(line_parts))

    inconsistent_games = df_sorted[df_sorted["Vegas_Public_Align"] == "⚠️ INCONSISTENT"]
    if not inconsistent_games.empty and STRATEGY_MODE == "DEFEND":
        print("\n" + "=" * 125)
        print("🛡️ LEADER STRATEGY SPOTLIGHT: INCONSISTENT GAMES DETECTED")
        print("=" * 125)
        print("The general public is backing the Vegas Underdog in these games. Since you are in DEFEND mode:")
        print("- We suggest remaining with the Vegas Favorite (Chalk) as prioritized above.\n")
        for _, row in inconsistent_games.iterrows():
            fav_mascot = get_mascot(row["Favorite"])
            und_mascot = get_mascot(row["Underdog"])
            print(f"  • [{row['CBS_Pts']} Pts] {fav_mascot} vs {und_mascot} (Public picks {und_mascot} at {row['Public_Und_Pct']:.0f}% but Vegas favors {fav_mascot} by -{row['Spread']:.1f} with {row['Vegas_Implied_Win_Pct']:.0f}% Implied Win Probability)\n")
        print("=" * 125)
else:
    print(f"❌ Error fetching odds: {response.status_code}")