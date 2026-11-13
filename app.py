import datetime as dt
import json
from pathlib import Path
import pandas as pd
import streamlit as st
from model import player_forecasts, score
from tracking import select_candidates, load_predictions, summarize
from validation import validate_logs, season_hit_rates
from espn_stats import fetch_espn_logs, ESPNFeedError
from sources import nba_schedule, nba_player_logs, kalshi_markets, kalshi_orderbook, extract_best_asks


def nba_three_point_candidates(markets):
    """Candidate discovery only; never an automatically verified contract match."""
    if markets is None or markets.empty:
        return pd.DataFrame()
    fields=[c for c in ('ticker','title','subtitle','yes_sub_title','no_sub_title',
                       'rules_primary','rules_secondary','event_ticker','series_ticker')
            if c in markets.columns]
    if not fields:
        return pd.DataFrame()
    hay=markets[fields].fillna('').astype(str).agg(' '.join,axis=1)
    nba=hay.str.contains(r'\bNBA\b|KXNBA|basketball',case=False,regex=True)
    threes=hay.str.contains(r'3[- ]?point|3pt|three[- ]?point|three[- ]?pointer|3PM|made threes',
                            case=False,regex=True)
    return markets.loc[nba & threes].copy()

st.set_page_config(page_title="Scanner-Milk | NBA 3PT Research",page_icon="🥛",layout="wide",initial_sidebar_state="collapsed")
st.markdown("""
<style>
.block-container {max-width:1200px;padding-top:1.3rem;padding-bottom:3rem;}
h1,h2,h3 {letter-spacing:-.035em;}
h1 {font-size:clamp(2rem,4vw,3.1rem)!important;font-weight:800!important;}
h2 {font-size:1.55rem!important;margin-top:1.4rem!important;}
[data-testid="stMetric"] {background:#14233a;border:1px solid #293c58;border-radius:16px;padding:14px 16px;}
[data-testid="stMetricLabel"] {color:#bdcde2;}
[data-testid="stMetricValue"] {font-weight:750;letter-spacing:-.035em;}
[data-testid="stDataFrame"] {border-radius:14px;overflow:hidden;}
.stButton button,.stDownloadButton button {border-radius:12px;font-weight:650;min-height:2.7rem;}
div[data-testid="stAlert"] {border-radius:12px;}
@media(max-width:640px){.block-container{padding-left:1rem;padding-right:1rem;}h2{font-size:1.35rem!important;}}
</style>
""",unsafe_allow_html=True)
brand_logo=Path(__file__).parent/"assets"/"scanner_milk_logo.svg"
hero_logo,hero_copy=st.columns([1,4],vertical_alignment="center",gap="medium")
with hero_logo:
    if brand_logo.exists(): st.image(str(brand_logo),use_container_width=True)
with hero_copy:
    st.title("Scanner-Milk")
    st.caption("NBA three-point research • Live market discovery • Independent forecasts")

with st.sidebar:
    season=st.text_input("NBA season",value="2026-27")
    min_games=st.slider("Minimum games",3,25,5)
    min_edge=st.slider("Minimum estimated edge (%)",0,20,5)/100
    st.warning("Uncalibrated model. No injury, minutes or opponent adjustment yet. REVIEW is not a trade recommendation.")
    if st.button("Clear cache"):
        st.cache_data.clear()
        st.rerun()

@st.cache_data(ttl=21600,show_spinner=False)
def get_espn_logs(days,as_of):
    return fetch_espn_logs(days=days,today=as_of)

@st.cache_data(ttl=900)
def get_logs(s): return nba_player_logs(s)
@st.cache_data(ttl=300)
def get_schedule(eastern_date): return nba_schedule(eastern_date)
@st.cache_data(ttl=300)
def get_markets(): return kalshi_markets()



def kalshi_nba_three_point_markets():
    """Direct series queries, isolated from the general catalog import."""
    import requests
    base="https://api.elections.kalshi.com/trade-api/v2"
    series=("KXNBA3PT",)
    rows=[]
    diagnostics=[]
    for ticker in series:
        cursor=None
        count=0
        pages=0
        error=""
        for _ in range(8):
            params={"series_ticker":ticker,"status":"open","limit":200}
            if cursor:
                params["cursor"]=cursor
            try:
                response=requests.get(base+"/markets",params=params,timeout=12)
                response.raise_for_status()
                payload=response.json()
            except Exception as exc:
                error=str(exc)
                break
            batch=payload.get("markets",[])
            rows.extend(batch)
            count+=len(batch)
            pages+=1
            next_cursor=payload.get("cursor")
            if not next_cursor or next_cursor==cursor:
                break
            cursor=next_cursor
        diagnostics.append({"series":ticker,"markets_found":count,"pages":pages,
                            "status":"ERROR" if error else "OK","detail":error})
    markets=pd.DataFrame(rows)
    if not markets.empty and "ticker" in markets.columns:
        markets=markets.drop_duplicates("ticker")
    return markets,pd.DataFrame(diagnostics)

@st.cache_data(ttl=180)
def get_targeted_markets():
    return kalshi_nba_three_point_markets()

def show_targeted_markets():
    st.subheader("Targeted NBA three-point markets")
    st.caption("Standard NBA 3PT contracts only (KXNBA3PT). No escalator, protection, or combo RFQ markets.")
    if st.button("Scan NBA 3PT series",key="scan_targeted"):
        try:
            with st.spinner("Querying NBA 3PT series..."):
                st.session_state["nba_3pt_scan"]=get_targeted_markets()
        except Exception as exc:
            st.error(f"Series scan failed: {exc}")
    if "nba_3pt_scan" in st.session_state:
        markets,diagnostics=st.session_state["nba_3pt_scan"]
        st.metric("Targeted contracts found",len(markets))
        st.dataframe(diagnostics,hide_index=True,use_container_width=True)
        if markets.empty:
            st.info("No open markets found in these series, or the API reported an error. See diagnostics above.")
        else:
            st.caption("Prices below are public API snapshots, not executable combo RFQ quotes. Empty prices mean no available quote.")
            display=markets.copy()
            if "series_ticker" in display.columns:
                display=display[display["series_ticker"]=="KXNBA3PT"]
            player_query=st.text_input("Search player",placeholder="e.g. Jaylen Clark",key="targeted_player")
            if player_query and "title" in display.columns:
                display=display[display["title"].fillna("").str.contains(player_query,case=False,regex=False)]
            if "title" in display.columns:
                extracted=display["title"].fillna("").str.extract(r"(?P<player>.+?):\s*(?P<threshold>\d+)\+\s*threes",expand=True)
                display["player"]=extracted["player"]
                display["threshold"]=pd.to_numeric(extracted["threshold"],errors="coerce")
            cols=[x for x in ("player","threshold","title","yes_bid_dollars","yes_ask_dollars","no_bid_dollars","no_ask_dollars","ticker","status") if x in display.columns]
            st.metric("Matching contracts",len(display))
            st.dataframe(display[cols].head(500),hide_index=True,use_container_width=True)
            st.download_button("Download visible contracts",display.to_csv(index=False),"kalshi_nba_3pt_markets.csv",key="download_targeted")

schedule=pd.DataFrame()
try:
    schedule=get_schedule(dt.datetime.now(__import__("zoneinfo").ZoneInfo("America/New_York")).date())
    st.subheader("Tonight’s matchups")
    st.dataframe(schedule,hide_index=True,use_container_width=True)
except Exception as e:
    st.warning(f"Schedule feed unavailable: {e}")

# Reserve this spot so curated signals appear immediately after tonight's games.
curated_slot=st.container()

st.subheader("Season data")
st.caption("Current season ONLY. Saved game logs are updated by GitHub Actions and persist across dashboard restarts.")
logs=None
saved_path=Path("data/nba_2026_27_regular_season_3pt.csv")
manifest_path=Path("data/nba_2026_27_manifest.json")
if saved_path.exists():
    try:
        saved=pd.read_csv(saved_path,dtype={"PLAYER_ID":str,"GAME_ID":str})
        required={"PLAYER_ID","PLAYER_NAME","TEAM_ABBREVIATION","GAME_DATE","FG3M","FG3A"}
        if not required.issubset(saved.columns):
            raise ValueError("Saved season file is missing required columns")
        saved["GAME_DATE"]=pd.to_datetime(saved["GAME_DATE"],errors="coerce")
        saved=saved[saved["GAME_DATE"].between(pd.Timestamp("2026-10-01"),pd.Timestamp("2027-04-30"))]
        saved["GAME_DATE"]=saved["GAME_DATE"].dt.strftime("%Y-%m-%d")
        if not saved.empty:
            logs=saved
            st.success(f"Saved current-season data: {len(saved):,} player-game records across {saved.GAME_ID.nunique() if 'GAME_ID' in saved else '?'} games.")
        if manifest_path.exists():
            manifest=json.loads(manifest_path.read_text())
            st.caption(f"Last collection: {manifest.get('last_checked','unknown')} · Latest stored date: {saved.GAME_DATE.max() if not saved.empty else 'none'}")
            if not manifest.get("complete",False):
                st.warning("Historical collection was incomplete. GitHub Actions will retry missing games; forecasts may be biased.")
                with st.expander("Collection diagnostics"):
                    st.json({k:v for k,v in manifest.items() if k!="completed_game_ids"})
    except Exception as exc:
        st.error(f"Stored stats could not be loaded: {exc}")
if logs is None:
    st.info("No saved current-season dataset yet. In GitHub, open Actions → Update current-season NBA three-point stats → Run workflow. Wait for the first backfill to finish and Streamlit to redeploy.")
with st.expander("Optional manual ESPN fallback (current season only)"):
    days=st.select_slider("Recent days",options=[7,14,21,28],value=14,key="espn_days")
    if st.button("Load recent ESPN logs",key="load_espn"):
        st.session_state["espn_requested"]=True
    if st.session_state.get("espn_requested"):
        try:
            with st.spinner("Collecting recent ESPN games..."):
                recent,diagnostics=get_espn_logs(days,dt.datetime.now(dt.timezone(dt.timedelta(hours=-5))).date().isoformat())
            recent["GAME_DATE"]=pd.to_datetime(recent["GAME_DATE"],errors="coerce")
            recent=recent[recent["GAME_DATE"].between(pd.Timestamp("2026-10-01"),pd.Timestamp("2027-04-30"))]
            recent["GAME_DATE"]=recent["GAME_DATE"].dt.strftime("%Y-%m-%d")
            if logs is None and not recent.empty:
                logs=recent
                st.warning("Using recent-only fallback, not complete historical season data.")
            st.dataframe(recent.head(80),hide_index=True,use_container_width=True)
            st.download_button("Download recent logs",recent.to_csv(index=False),"espn_recent_3pt.csv")
            if not diagnostics["complete"]:
                st.warning("Recent ESPN fetch incomplete.")
        except Exception as exc:
            st.error(f"ESPN fallback unavailable: {exc}")
if logs is None or logs.empty:
    st.info("Player forecasts need current-season game logs. Run the GitHub Actions historical collector or use the recent ESPN fallback.")
    show_targeted_markets()
    st.subheader("Kalshi public market catalog")
    if st.button("Fetch Kalshi markets"):
        try: st.session_state["markets"]=get_markets()
        except Exception as e: st.error(f"Kalshi API: {e}")
    markets=st.session_state.get("markets",pd.DataFrame())
    if not markets.empty:
        candidates=nba_three_point_candidates(markets)
        st.metric("Markets sampled",len(markets))
        st.metric("Possible NBA 3PT contracts",len(candidates))
        st.caption("This is a capped sample of open markets, not an exhaustive NBA scan. Text matching can miss contracts or include false positives.")
        columns=[x for x in ("ticker","title","subtitle","status","yes_ask_dollars","no_ask_dollars") if x in candidates.columns]
        if candidates.empty:
            st.info("No NBA 3PT contracts identified in the sampled pages. This does not mean Kalshi has none.")
        else:
            st.dataframe(candidates[columns].head(500),hide_index=True,use_container_width=True)
            st.download_button("Download filtered candidates",candidates.to_csv(index=False),"nba_3pt_candidates.csv")
    st.stop()
st.subheader("Data health")
manifest_data=json.loads(manifest_path.read_text()) if manifest_path.exists() else None
logs,quality=validate_logs(logs,manifest_data)
q1,q2,q3=st.columns(3)
q1.metric("Validated player-game rows",quality.get("rows",0))
q2.metric("Unique games",quality.get("games",0))
q3.metric("Invalid / duplicate rows",quality.get("invalid_rows",0)+quality.get("duplicate_rows",0))
if quality.get("errors"):
    st.error("Data validation issues detected. Forecasts are withheld until the source data is corrected.")
    for issue in quality["errors"]: st.write("• "+issue)
if quality.get("warnings"):
    st.warning("Data completeness warnings. Treat all model outputs as preliminary.")
    for issue in quality["warnings"]: st.write("• "+issue)
with st.expander("Data validation details"):
    st.json(quality)
if quality.get("errors"):
    st.stop()
st.subheader("Historical hit rates")
st.caption("Observed regular-season results only; not predicted probabilities. Last-5 and last-10 samples use actual games played.")
hit_rates=season_hit_rates(logs,min_games=min_games)
if not schedule.empty and not hit_rates.empty:
    today_teams=set(schedule["away"])|set(schedule["home"])
    hit_rates=hit_rates[hit_rates["team"].isin(today_teams)]
if not hit_rates.empty:
    selected_threshold=st.selectbox("Made threes threshold",[1,2,3,4],index=1,format_func=lambda v:f"{v}+ threes")
    rate_view=hit_rates[hit_rates["threshold"]==selected_threshold].sort_values(["season_hit_rate","games"],ascending=False)
    st.dataframe(rate_view,hide_index=True,use_container_width=True)
    st.download_button("Download current-season hit rates",hit_rates.to_csv(index=False),"nba_2026_27_3pt_hit_rates.csv",key="download_hit_rates")
else:
    st.info("No players match the minimum games and today's teams.")
logs=logs.copy()
logs["GAME_DATE"]=pd.to_datetime(logs["GAME_DATE"],errors="coerce")
logs=logs[logs["GAME_DATE"].dt.date < dt.datetime.now(dt.timezone(dt.timedelta(hours=-5))).date()]
forecasts=player_forecasts(logs,min_games)
if not schedule.empty and not forecasts.empty:
    teams=set(schedule["away"])|set(schedule["home"])
    forecasts=forecasts[forecasts["team"].isin(teams)]
if forecasts.empty:
    st.warning("No qualifying player logs.")
    st.stop()
# Curated model estimates and prospective results directly beneath matchups.
with curated_slot:
    st.subheader("Model picks · YES 90%+")
    st.caption("YES to made threes only · 90%+ model probability · Current season · Not price-verified or injury-adjusted")
    picks=select_candidates(forecasts,min_games=min_games,minimum=.90)
    if not picks.empty:
        # The 90% gate selects players; their entire available Kalshi ladder is shown.
        # Query the standard NBA 3PT series, never escalator/protection contracts.
        import re
        import unicodedata
        from zoneinfo import ZoneInfo

        def normalize_player_name(name):
            name=unicodedata.normalize("NFKD",str(name))
            name="".join(ch for ch in name if not unicodedata.combining(ch))
            return re.sub(r"[^a-z0-9]","",name.casefold())

        market_thresholds={}
        market_error=None
        try:
            all_markets,market_diagnostics=get_targeted_markets()
            if not market_diagnostics.empty and market_diagnostics["status"].eq("ERROR").any():
                market_error="Kalshi returned an incomplete market scan."
            if not all_markets.empty:
                today_et=dt.datetime.now(ZoneInfo("America/New_York")).date()
                date_token=today_et.strftime("%y%b%d").upper()
                markets_today=all_markets[
                    all_markets["ticker"].fillna("").str.upper().str.contains(date_token,regex=False)
                ]
                for _,market in markets_today.iterrows():
                    # Match standard titles, e.g. "Player Name: 2+ threes".
                    match=re.fullmatch(r"\s*(.+?):\s*(\d+)\+\s*threes\s*",
                                       str(market.get("title","")),re.IGNORECASE)
                    if match:
                        key=normalize_player_name(match.group(1))
                        market_thresholds.setdefault(key,set()).add(int(match.group(2)))
        except Exception as exc:
            market_error=f"Kalshi market lookup failed: {exc}"

        if market_error:
            st.warning(market_error+" Model estimates are still displayed below.")
        if not market_thresholds:
            st.info("No matching standard Kalshi NBA 3PT contracts confirmed for today's games. Showing modeled 1+ through 4+ probabilities as a fallback; these thresholds are NOT verified as listed.")
        rows=[]
        for _,pick in picks.head(25).iterrows():
            name=normalize_player_name(pick["player"])
            listed=sorted(market_thresholds.get(name,set()))
            # When the market feed is missing, keep the independent model usable.
            available=listed if listed else [1,2,3,4]
            player_ladder=forecasts[forecasts["player_id"].astype(str)==str(pick["player_id"])]
            row={"Player":pick["player"],"Team":pick["team"],
                 "90%+ at":str(int(pick["threshold"]))+"+",
                 "Kalshi listing":"Confirmed" if listed else "Unverified",
                 "Games":int(pick["games"]),
                 "3PA / game":round(float(pick["projected_pa"]),1)}
            for threshold in available:
                matching=player_ladder[player_ladder["threshold"]==threshold]
                row[f"{threshold}+"]=(round(100*float(matching.iloc[0]["fair_yes"]),1)
                                       if not matching.empty else float("nan"))
            rows.append(row)
        ladder=pd.DataFrame(rows)
        if not ladder.empty:
            st.dataframe(ladder,hide_index=True,use_container_width=True)
            st.caption("All threshold cells show independent model YES probability (%), NOT Kalshi market odds. Players qualify at 90%+ for at least one threshold. Confirmed rows use standard Kalshi 3PT listings for today's date; Unverified rows show modeled 1+ to 4+ as fallback. No escalator or protection contracts. Forecasts are uncalibrated, not injury-adjusted, and not price-verified.")
    else:
        st.info("No YES picks currently meet the 90% model cutoff and minimum-games requirement.")

    st.subheader("Model results tracker")
    st.caption("Only predictions saved before games are graded. Historical season hit rates are not counted as model wins.")
    tracked=load_predictions()
    settled=tracked[tracked["correct"].notna()].copy()
    period=st.selectbox("Results period",["Last 7 days","Last 30 days","This season","Custom dates"],index=1,key="tracking_period")
    today=dt.datetime.now(dt.timezone.utc).date()
    if period=="Custom dates":
        d1,d2=st.columns(2)
        start_date=d1.date_input("From",value=today-dt.timedelta(days=30),key="track_from")
        end_date=d2.date_input("To",value=today,key="track_to")
    elif period=="Last 7 days":
        start_date,end_date=today-dt.timedelta(days=6),today
    elif period=="Last 30 days":
        start_date,end_date=today-dt.timedelta(days=29),today
    else:
        start_date,end_date=dt.date(2026,10,1),today
    if not settled.empty:
        settled=settled[settled["prediction_date"].between(str(start_date),str(end_date))]
    if not settled.empty:
        correct=settled["correct"].astype(str).str.lower().eq("true")
        a,b,d=st.columns(3)
        a.metric("Accuracy",f"{correct.mean()*100:.1f}%")
        b.metric("Correct",f"{int(correct.sum())} / {len(correct)}")
        d.metric("Pending",int(tracked["correct"].isna().sum()))
        daily=summarize(settled)
        st.dataframe(daily.sort_values("Date",ascending=False),hide_index=True,use_container_width=True)
        st.line_chart(daily.set_index("Date")["Accuracy"])
        with st.expander("Individual prediction results"):
            view=settled[["prediction_date","player","side","threshold","probability","result","correct"]].copy()
            view["probability"]=(view["probability"]*100).round(1)
            st.dataframe(view.sort_values("prediction_date",ascending=False),hide_index=True,use_container_width=True)
        st.download_button("Download tracked results",settled.to_csv(index=False),"scanner_milk_model_results.csv",key="track_download")
    else:
        st.info("No settled, pregame predictions in this period yet. Daily snapshots will begin after the next successful GitHub Actions run.")
        if not tracked.empty:
            st.caption(f"{int(tracked['correct'].isna().sum())} predictions are awaiting completed player boxscores.")
    st.caption("Tracking starts with saved prospective predictions; it does not backfill hypothetical historical picks. Missing player boxscores remain ungraded.")

st.subheader("Player forecasts")
st.caption("Current 2026–27 regular-season data only. Volume mixture with observed game-to-game variation; 65% season + 35% last-five attempts; beta-binomial shooting accuracy shrunk toward 36%. Uncalibrated; no injury, expected minutes, or opponent adjustment yet.")
st.dataframe(forecasts,hide_index=True,use_container_width=True)

show_targeted_markets()
st.subheader("Public Kalshi market catalog")
st.warning("Automatic player-to-contract matching is not enabled. Exact contract verification required.")
if st.button("Fetch Kalshi markets"):
    try: st.session_state["markets"]=get_markets()
    except Exception as e: st.error(f"Kalshi API: {e}")
markets=st.session_state.get("markets",pd.DataFrame())
if not markets.empty:
    candidates=nba_three_point_candidates(markets)
    st.metric("Markets sampled",len(markets))
    st.metric("Possible NBA 3PT contracts",len(candidates))
    st.caption("Capped market sample; candidate matching is unverified and not exhaustive.")
    columns=[c for c in ("ticker","title","subtitle","status","yes_ask_dollars","no_ask_dollars") if c in candidates.columns]
    if not candidates.empty:
        st.dataframe(candidates[columns].head(500),hide_index=True,use_container_width=True)
        st.download_button("Download filtered candidates",candidates.to_csv(index=False),"nba_3pt_candidates.csv")
    else:
        st.info("No candidates in the sample. This does not prove none exist.")

st.subheader("Contract mapping & orderbooks")
st.caption("Upload CSV with player,threshold,ticker. Mapping must match the exact 3PT YES contract. RFQ combos are not included.")
mapping_upload=st.file_uploader("Verified contract mapping CSV",type="csv",key="mapping")
if mapping_upload is not None:
    try:
        mapping=pd.read_csv(mapping_upload)
        if not {"player","threshold","ticker"}.issubset(mapping.columns):
            raise ValueError("CSV requires player,threshold,ticker")
        if mapping.duplicated(["player","threshold"]).any():
            raise ValueError("Duplicate player/threshold mapping")
        if st.button("Fetch mapped orderbooks"):
            rows=[]
            for _,r in mapping.iterrows():
                try:
                    quote=extract_best_asks(kalshi_orderbook(str(r.ticker)))
                    rows.append({"player":r.player,"threshold":int(r.threshold),**quote})
                except Exception as e:
                    st.warning(f"{r.ticker}: {e}")
            st.session_state["quotes"]=pd.DataFrame(rows)
    except Exception as e: st.error(str(e))
quotes=st.session_state.get("quotes",pd.DataFrame())
results=score(forecasts,quotes,min_edge)
c1,c2,c3=st.columns(3)
c1.metric("Players",forecasts.player.nunique())
c2.metric("YES/NO rows",len(results))
c3.metric("Review flags",int((results.decision=="REVIEW").sum()))
st.subheader("Ranked research signals")
filter_=st.selectbox("Show",["All","REVIEW","NO QUOTE","PASS"])
view=results if filter_=="All" else results[results.decision==filter_]
cols=["player","team","side","threshold","three_pa","last5_pa","hit_rate","fair","price","net_edge","decision"]
st.dataframe(view[cols],hide_index=True,use_container_width=True)
st.download_button("Download ranked CSV",results.to_csv(index=False),"nba_3pt_signals.csv")
st.caption("Fee estimate is approximate, not an exchange quote. Orderbooks can change. No automated orders, combo RFQs, or guarantees.")
