# picks_strategy.py
# Generates picks for tomorrow using API-Football (or similar). Replace API_KEY.

import requests
import pandas as pd
import datetime
import pytz
from typing import List, Dict, Any

# ========== CONFIG ==========
API_KEY = "0da49e4fd6909de5571a4baabf883b84"   # <<< PUT YOUR API KEY HERE
TIMEZONE = "Africa/Lagos"
# League IDs for API-Football (common ids):
# Premier League = 39, La Liga = 140, Championship =  Championship id often 2 (API-Football uses 2)
LEAGUE_IDS = {
    "Premier League": 39,
    "La Liga": 140,
    "England Championship": 2
}
# Minimum timeframe: we will pull fixtures for tomorrow's date (Nigeria TZ)
# ============================

HEADERS = {"x-apisports-key": API_KEY}

def _api_get(url: str, params: dict = None) -> dict:
    r = requests.get(url, headers=HEADERS, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def _tomorrow_str() -> str:
    tz = pytz.timezone(TIMEZONE)
    tomorrow = datetime.datetime.now(tz) + datetime.timedelta(days=1)
    return tomorrow.strftime("%Y-%m-%d"), tomorrow.strftime("%d-%b-%Y")

def fetch_fixtures_for_league_on_date(league_id: int, date_iso: str) -> List[dict]:
    """
    Returns raw fixtures for a league on given date (ISO YYYY-MM-DD).
    Uses API-Football: /fixtures?league={id}&season={year}&date={date}
    """
    year = int(date_iso.split("-")[0])
    url = "https://v3.football.api-sports.io/fixtures"
    params = {"league": league_id, "season": year, "date": date_iso}
    data = _api_get(url, params)
    return data.get("response", [])

def fetch_fixture_stats(fixture_id: int) -> Dict[str, Any]:
    """
    Fetch fixture statistics endpoint: /fixtures/statistics
    returns dict with home/away stats (shots on target etc.) if available.
    """
    url = "https://v3.football.api-sports.io/fixtures/statistics"
    params = {"fixture": fixture_id}
    data = _api_get(url, params)
    # data['response'] is list of dicts per team with statistics list
    # convert into a dict for easier lookup
    stats = {"home": {}, "away": {}}
    for team_stat in data.get("response", []):
        team = "home" if team_stat.get("team", {}).get("id") else None
        # Actually API returns team id and statistics list. We'll map by 'team' position later if possible
        # We'll transform list of 'stat' items into dict
        # team_stat: {'team': {...}, 'statistics':[{'type':'Shots on Goal','value':3},...]}
        t = team_stat.get("team", {})
        position = team_stat.get("team", {}).get("name", "")
        # determine whether this is home or away by presence? fallback: parse keys later
        # For now we return raw list and let caller interpret
        stats_entry = {}
        for item in team_stat.get("statistics", []):
            key = item.get("type")
            val = item.get("value")
            stats_entry[key] = val
        # store using team name as key
        stats[t.get("name", f"team_{t.get('id','?')}")] = stats_entry
    return stats

def extract_b365_home_odds(fixture_obj: dict) -> float:
    """
    Extract Bet365 home odds (B365H) from fixture object if present in 'odds' or 'bookmakers' field.
    Returns float or None.
    """
    # API-Football v3 includes 'odds' under fixture['odds'] or 'bookmakers' list
    # We'll scan bookmakers array for bookmaker name 'bet365' (case-insensitive)
    bookmakers = fixture_obj.get("odds") or fixture_obj.get("bookmakers") or fixture_obj.get("bookmaker")
    # Some fixtures include "bookmakers": [{ "bookmaker": { "id":..., "name":"bet365" }, "bets": [...] }]
    if not bookmakers:
        return None

    # Normalized approach: check 'bookmakers' or 'odds' shapes
    # Try 'bookmakers' common structure
    if isinstance(bookmakers, list):
        for bm in bookmakers:
            # if dict contains 'bookmaker' key
            name = ""
            if isinstance(bm, dict):
                # Sometimes 'bookmaker' is nested
                name = (bm.get("bookmaker") or {}).get("name") or bm.get("title") or bm.get("name") or ""
                # also try top-level name
                if not name:
                    name = bm.get("name","")
                if name and "bet365" in name.lower():
                    # extract odds from bets -> outcomes
                    bets = bm.get("bets") or bm.get("bets", [])
                    for bet in bets:
                        # bet could be 'Match Winner' with 'values' containing outcomes
                        if isinstance(bet, dict):
                            for val in bet.get("values", []) or []:
                                if val.get("value") and val.get("odd"):
                                    # check if this is home odd by matching '1' or home label
                                    label = val.get("value")
                                    # Accept label 'Home' or '1' etc.
                                    if label in ("Home", "1"):
                                        try:
                                            return float(val.get("odd"))
                                        except:
                                            pass
    # Fallback: try fixture_obj['odds'] if structured differently
    odds = fixture_obj.get("odds") or {}
    # Not guaranteed — if none, return None
    return None

# ---------- Strategy filter logic ----------
def match_passes_filter(fixture_raw: dict) -> dict:
    """
    Given a raw fixture from API, compute HST, AST (shots on target),
    and B365H and decide if it passes your filter.

    Returns:
      None if it doesn't pass,
      dict with pick fields if it does.
    """
    # We need:
    # - HST: home shots on target (Shots on Goal or Shots on Target) — look for 'Shots on Goal' or 'Shots on target'
    # - AST: away shots on target
    # - B365H: home win odd from Bet365 if present
    fixture = fixture_raw
    fixture_id = fixture.get("fixture", {}).get("id") or fixture.get("id")
    # teams names:
    home = fixture.get("teams", {}).get("home", {}).get("name")
    away = fixture.get("teams", {}).get("away", {}).get("name")

    # Try to get statistics via /fixtures/statistics endpoint
    # We'll fetch statistics and then try to read 'Shots on Goal' or 'Shots on Target'
    try:
        stats_data = _api_get("https://v3.football.api-sports.io/fixtures/statistics", params={"fixture": fixture_id})
        # stats_data['response'] is list of two items: one for home team, one for away team
        hst = None
        ast = None
        for team_stat in stats_data.get("response", []):
            team_name = team_stat.get("team", {}).get("name", "")
            for s in team_stat.get("statistics", []):
                ttype = s.get("type", "").lower()
                val = s.get("value")
                if "shots on goal" in ttype or "shots on target" in ttype:
                    if team_name == home:
                        hst = val if isinstance(val, (int, float)) else (int(val) if val and str(val).isdigit() else None)
                    elif team_name == away:
                        ast = val if isinstance(val, (int, float)) else (int(val) if val and str(val).isdigit() else None)
        # fallback: try 'statistics' structure differently
    except Exception:
        hst = None
        ast = None

    # Extract B365 home odd (if available)
    b365h = None
    # Some fixtures include 'odds' in fixture object; attempt extraction via helper
    b365h = extract_b365_home_odds(fixture_raw)

    # If we couldn't find stats or odds, skip
    if hst is None or ast is None or b365h is None:
        return None

    # Apply your filters:
    # • HST / AST ≥ 2.5
    # • AST ≥ 1
    # • HST ≥ 4
    # • B365H between 2.0 and 3.5
    try:
        ratio = hst / ast if ast != 0 else None
    except Exception:
        ratio = None

    if ast is None or ast < 1:
        return None
    if hst is None or hst < 4:
        return None
    if ratio is None or ratio < 2.5:
        return None
    if not (2.0 <= float(b365h) <= 3.5):
        return None

    # Compute a simple confidence score (0-100)
    # - higher HST and higher ratio gives higher confidence
    # - odds nearer 2.0 increase confidence slightly, nearer 3.5 reduce a bit
    conf = 50
    conf += min(max((hst - 4) * 3, 0), 20)        # + up to 20
    conf += min(max((ratio - 2.5) * 8, 0), 20)    # + up to 20
    # adjust by odds: if odds close to 2.0 -> +5, close to 3.5 -> -5
    conf += int((3.25 - float(b365h)) * 3)  # crude
    conf = int(max(40, min(conf, 95)))

    pick = {
        "Date": _tomorrow_str()[1],  # dd-Mon-YYYY
        "Match": f"{home} vs {away}",
        "Prediction": "Home win",
        "Confidence": conf,
        "HST": hst,
        "AST": ast,
        "B365H": b365h,
        "fixture_id": fixture_id
    }
    return pick

def generate_picks() -> pd.DataFrame:
    """
    Generate picks for tomorrow across configured LEAGUE_IDS.
    Returns a DataFrame with columns: Date,Match,Prediction,Confidence,HST,AST,B365H,fixture_id
    """
    date_iso, date_ddmon = _tomorrow_str()
    collected = []
    for lname, lid in LEAGUE_IDS.items():
        try:
            fixtures = fetch_fixtures_for_league_on_date(lid, date_iso)
        except Exception as e:
            print(f"Warning: failed to fetch fixtures for league {lname} id {lid}: {e}")
            fixtures = []
        for f in fixtures:
            # apply filter
            try:
                pick = match_passes_filter(f)
            except Exception as e:
                pick = None
            if pick:
                collected.append(pick)

    if not collected:
        return pd.DataFrame(columns=["Date","Match","Prediction","Confidence","HST","AST","B365H","fixture_id"])

    df = pd.DataFrame(collected)
    # Ensure columns order
    df = df[["Date","Match","Prediction","Confidence","HST","AST","B365H","fixture_id"]]
    return df

# If run directly, generate and save CSV
if __name__ == "__main__":
    df = generate_picks()
    if df.empty:
        print("No picks generated for tomorrow.")
    else:
        df.to_csv("daily_picks.csv", index=False)
        print(f"{len(df)} picks written to daily_picks.csv")
