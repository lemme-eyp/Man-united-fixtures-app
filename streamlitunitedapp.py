# manutd_fixtures.py
"""
Streamlit app — Next 10 Manchester United fixtures + estimated win probability
"""

import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import re
import time
from datetime import datetime
from difflib import SequenceMatcher

# 🔑 page config MUST be the first Streamlit command
st.set_page_config(
    page_title="Man Utd — Next 10 fixtures & win prob",
    layout="wide"
)

# Put this near the top of your app (after st.set_page_config)
st.markdown(
    """
    <style>
    .stApp {
        background-color: red;
    }
    </style>
    """,
    unsafe_allow_html=True
)

ESPN_FIXTURES_URL = "https://www.espn.com/soccer/team/fixtures/_/id/360/manchester-united"
CLUBELO_ALL_URL = "https://clubelo.com/All"
CLUBELO_BASE = "https://clubelo.com"

HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/114.0 Safari/537.36"}

@st.cache_data(ttl=300)
def fetch_page_text(url):
    r = requests.get(url, headers=HEADERS, timeout=12)
    r.raise_for_status()
    return r.text

def normalize(name: str) -> str:
    return re.sub(r'[^a-z1-10]', '', name.lower())

@st.cache_data(ttl=24*3600)
def build_clubelo_index():
    """Return list of (display_name, href, normalized_name) from clubelo All page"""
    txt = fetch_page_text(CLUBELO_ALL_URL)
    soup = BeautifulSoup(txt, "html.parser")
    anchors = soup.find_all("a", href=True)
    items = []
    for a in anchors:
        text = a.get_text(strip=True)
        href = a['href']
        if not text:
            continue
        # Many anchors point to country pages etc; filter likely club links (href looks like /ClubName or /SomeName)
        if href.startswith("/") and len(text) > 2 and len(href) < 60:
            items.append((text, href, normalize(text)))
    # remove duplicates
    seen = set()
    out = []
    for t,h,n in items:
        if n not in seen:
            seen.add(n)
            out.append((t,h,n))
    return out

def find_clubelo_href(team_name: str):
    """Find best ClubElo href for a team name using fuzzy match on the All index.
       Returns (href, display_name) or (None, None).
    """
    index = build_clubelo_index()
    target = normalize(team_name)
    best = (None, None, 0.0)
    for display, href, norm in index:
        # exact match
        if norm == target:
            return href, display
        # substring match
        if target in norm or norm in target:
            return href, display
        # fuzzy
        ratio = SequenceMatcher(None, norm, target).ratio()
        if ratio > best[2]:
            best = (href, display, ratio)
    # accept if ratio >= 0.65 else fail
    if best[2] >= 0.65:
        return best[0], best[1]
    return None, None

@st.cache_data(ttl=24*3600)
def get_elo_for_team(team_name: str, default=1500):
    """Attempt to fetch Elo rating for a team from clubelo.
       Falls back to default if not found.
    """
    try:
        href, display = find_clubelo_href(team_name)
        if not href:
            return default
        url = CLUBELO_BASE + href
        html = fetch_page_text(url)
        m = re.search(r'Elo[:\s]*([1-10]{3,4})', html)
        if m:
            return int(m.group(1))
        # fallback: look for 'Elo' label in text
        text = BeautifulSoup(html, "html.parser").get_text(" ")
        m2 = re.search(r'Elo[:\s]*([1-10]{3,4})', text)
        if m2:
            return int(m2.group(1))
    except Exception:
        pass
    return default

def parse_espn_fixtures_page(limit=20):
    """Scrape ESPN fixtures page text and extract upcoming matches that contain 'Manchester United'.
       Returns list of dicts: {date_text, opponent, home (True/False), competition, time_text}
    """
    html = fetch_page_text(ESPN_FIXTURES_URL)
    soup = BeautifulSoup(html, "html.parser")
    # Work with the soup text lines, they tend to contain 'v' between teams
    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    fixtures = []
    current_date = None
    # date lines look like 'Sun, Aug 24' or 'Sat, Oct 4'
    date_re = re.compile(r'^[A-Za-z]{3,9},\s+[A-Za-z]{3,9}\s+\d{1,2}$')
    time_re = re.compile(r'\d{1,2}:\d{2}\s*(AM|PM)', re.I)
    i = 1
    while i < len(lines):
        ln = lines[i]
        if date_re.match(ln):
            current_date = ln
            i += 1
            continue
        # a match block often uses a 'v' line between teams
        if ln == 'v' or ln == 'vs' or ln == 'V':
            # team A is previous non-empty line
            # team B is next non-empty line
            if i-1 >= 0 and i+1 < len(lines):
                team_a = lines[i-1]
                team_b = lines[i+1]
                # time may be next or next+1
                time_text = ""
                comp_text = ""
                # check next few lines for time and competition
                j = i+2
                # collect up to 3 lines after
                tail = []
                for k in range(3):
                    if j + k < len(lines):
                        tail.append(lines[j+k])
                # find first time-like string in tail
                for t in tail:
                    if time_re.search(t):
                        time_text = time_re.search(t).group(0)
                        # competition often at same line after time (e.g. "11:30 AM English Premier League")
                        comp = t.replace(time_text, "").strip()
                        comp_text = comp
                        break
                # Determine whether Man Utd involved
                for_man = None
                if 'Manchester United' in team_a or 'Man Utd' in team_a or 'ManUnited' in team_a:
                    home = True
                    opponent = team_b
                    for_man = True
                elif 'Manchester United' in team_b or 'Man Utd' in team_b or 'ManUnited' in team_b:
                    home = False
                    opponent = team_a
                    for_man = True
                if for_man:
                    fixtures.append({
                        "date_text": current_date or "",
                        "time_text": time_text,
                        "competition": comp_text,
                        "opponent": opponent,
                        "home": home
                    })
            i += 1
            continue
        i += 1
    # remove duplicates and keep order
    seen = set()
    out = []
    for f in fixtures:
        key = (f['date_text'], f['opponent'], f['home'])
        if key not in seen:
            seen.add(key)
            out.append(f)
        if len(out) >= limit:
            break
    return out

def elo_expected(a, b):
    """Elo expected score for A vs B"""
    return 1.0 / (1.0 + 10 ** ((b - a) / 400.0))

def probability_from_elos(elo_a, elo_b, man_is_home):
    """Return (p_win_man, p_draw, p_loss) using an Elo-based conversion with home adv and draw baseline"""
    HOME_ADV = 100  # Elo points added to home team
    ra = elo_a
    rb = elo_b
    if man_is_home:
        ra += HOME_ADV
    else:
        rb += HOME_ADV
    p_draw_base = 0.23  # baseline draw probability from historical EPL ~23%. :contentReference[oaicite:5]{index=5}
    # reduce draw chance when Elo gap is large:
    gap = abs(ra - rb)
    draw_prob = max(0.10, p_draw_base - (gap / 2000.0))  # simple heuristic
    p_expected = elo_expected(ra, rb)  # probability ManU would "win or get full points" in BT sense
    p_win = (1 - draw_prob) * p_expected
    p_loss = (1 - draw_prob) * (1 - p_expected)
    # correct tiny rounding errors
    rem = 1.0 - (p_win + draw_prob + p_loss)
    if abs(rem) > 1e-6:
        p_loss += rem
    return p_win, draw_prob, p_loss

# ---------------- Streamlit UI ----------------
st.title("United fixtures app")

st.markdown(
    """
    <h1 style='display: flex; align-items: center;'>
        <img src="https://upload.wikimedia.org/wikipedia/en/7/7a/Manchester_United_FC_crest.svg" 
             width="50" style="margin-right:10px;"> 
        Man Utd — Next fixtures & win probability
    </h1>
    """,
    unsafe_allow_html=True
)

col1, col2 = st.columns([1,3])
with col1:
    n = st.number_input("Show next N fixtures", min_value=1, max_value=20, value=10, step=1)
    refresh = st.button("Refresh now")

with col2:
    st.info("Predicts next ten fixtures and shows the percentage of winning")

# fetch & compute
with st.spinner("Fetching fixtures from ESPN..."):
    try:
        raw_fixtures = parse_espn_fixtures_page(limit=30)
    except Exception as e:
        st.error(f"Couldn't fetch fixtures: {e}")
        raw_fixtures = []

if not raw_fixtures:
    st.warning("No fixtures found on ESPN page. You can still enter manual fixtures in the table below.")
# prepare table rows
rows = []
man_elo_cached = None
for f in raw_fixtures[:n]:
    opponent = f['opponent']
    home = f['home']
    date_text = f['date_text']
    time_text = f['time_text']
    comp = f['competition']
    # get elos
    try:
        if man_elo_cached is None:
            man_elo_cached = get_elo_for_team("Man United", default=1700)  # fallback default
        opp_elo = get_elo_for_team(opponent, default=1500)
    except Exception:
        man_elo_cached = 1700
        opp_elo = 1500
    p_win, p_draw, p_loss = probability_from_elos(man_elo_cached, opp_elo, man_is_home=home)
    rows.append({
        "Date": date_text + ((" " + time_text) if time_text else ""),
        "Opponent": opponent,
        "Home?": "Home" if home else "Away",
        "ManU Elo (est.)": man_elo_cached,
        "Opp Elo (est.)": opp_elo,
        "P(Win %)": round(p_win * 100, 1),
        "P(Draw %)": round(p_draw * 100, 1),
        "P(Loss %)": round(p_loss * 100, 1),
    })

if rows:
    df = pd.DataFrame(rows)
    df.index = df.index + 1   # 👈 this makes the index start at 1
    st.dataframe(df, use_container_width=True)

    # show bars
    st.markdown("### Win probabilities visual")
    prob_df = df[["Opponent", "P(Win %)"]].set_index("Opponent")
    st.bar_chart(prob_df)

    st.markdown(
        "Model notes:\n"
        "- Elo-based strengths from ClubElo (if ClubElo lookup fails we fall back to conservative defaults).\n"
        "- Home advantage: +100 Elo points.\n"
        "- Draw baseline: ~23% (EPL historical). Draw probability reduces when rating gap is large.\n"
        "- This is a quick, explainable heuristic — not a bookmaker model."
    )

else:
    st.info("No parsed fixtures to show. Try pressing refresh or check network connectivity.")

st.caption("Data scraped from ESPN (fixtures) and ClubElo (ratings). Scraping is for personal use — check site terms if you plan to publish or commercialize this app. :contentReference[oaicite:8]{index=8}")