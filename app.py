import datetime as dt
import json
from pathlib import Path
import pandas as pd
import streamlit as st
from model import player_forecasts, score
from no_scanner import build_no_candidates
from no_prop_model import forecast_props, backtest as backtest_no_props
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

st.set_page_config(page_title="Milkball | NBA 3PT Intel",page_icon="🥛",layout="wide",initial_sidebar_state="collapsed")
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
    st.title("Milkball")
    st.caption("NBA 3PT INTEL • Live market discovery • Independent forecasts")

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
    confidence_floor=st.slider("Minimum model confidence (%)",65,95,80,5,key="milkball_confidence_floor")
    minimum_probability=confidence_floor/100
    st.subheader(f"Model picks · YES {confidence_floor}%+")
    st.caption("YES to made threes only · Adjustable confidence floor · Current season · Not price-verified or injury-adjusted")
    picks=select_candidates(forecasts,min_games=min_games,minimum=minimum_probability)
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
        # Do not block the picks table on a slow Kalshi network scan.
        verify_listings=st.button("Verify Kalshi listings",key="verify_picks_listings")
        try:
            if verify_listings:
                with st.spinner("Checking Kalshi listings..."):
                    all_markets,market_diagnostics=get_targeted_markets()
            else:
                all_markets=pd.DataFrame()
                market_diagnostics=pd.DataFrame()
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
            st.info("Showing model picks immediately using 1+ through 4+ probabilities. Tap Verify Kalshi listings to check live contracts; until then, thresholds are unverified.")
        # Build the table directly from model forecasts. Avoid per-row
        # rendering errors and keep the probabilities visible even without Kalshi.
        selected_ids=set(picks.head(25)["player_id"].astype(str))
        visible=forecasts[forecasts["player_id"].astype(str).isin(selected_ids)].copy()
        visible=visible[visible["threshold"].between(1,4)]
        visible["Model YES %"]=(visible["fair_yes"]*100).round(1)
        probability_table=visible.pivot_table(
            index=["player_id","player","team","games","projected_pa"],
            columns="threshold",values="Model YES %",aggfunc="first"
        ).reset_index()
        probability_table.columns=[str(c)+"+" if isinstance(c,int) else c for c in probability_table.columns]
        probability_table=probability_table.rename(columns={
            "player":"Player","team":"Team","games":"Games",
            "projected_pa":"3PA / game"})
        threshold_columns=[str(t)+"+" for t in (1,2,3,4)]
        for column in threshold_columns:
            if column not in probability_table.columns:
                probability_table[column]=float("nan")
        def qualifying_label(row):
            valid=[t for t in (1,2,3,4)
                   if pd.notna(row[str(t)+"+"]) and row[str(t)+"+"]>=confidence_floor]
            if not valid:return "—"
            t=max(valid)
            return f"{t}+ ({row[str(t)+'+']:.1f}%)"
        probability_table["Best qualifying"]=probability_table.apply(qualifying_label,axis=1)
        probability_table["Kalshi listing"]=probability_table["Player"].map(
            lambda name:"Confirmed" if market_thresholds.get(normalize_player_name(name)) else "Unverified")
        probability_table=probability_table.sort_values(["1+","Games"],ascending=False)
        display_columns=["Player","Team","Best qualifying","Kalshi listing","Games","3PA / game"]+threshold_columns
        st.dataframe(probability_table[display_columns],hide_index=True,use_container_width=True)
        st.caption("Best qualifying is the highest modeled 1+ to 4+ threshold meeting your confidence floor. These are uncalibrated model probabilities, not market prices. Unverified thresholds may not be listed on Kalshi.")
    else:
        st.info("No YES picks currently meet the selected confidence cutoff and minimum-games requirement.")

    # Explain why every player on today's scheduled teams did or did not qualify.
    with st.expander("Player eligibility audit · all matchups", expanded=False):
        st.caption("Season-data audit for every scheduled team. Best qualifying is the highest 1+ to 4+ threshold meeting the selected confidence floor. This is not a confirmed active roster or injury report.")
        audit_rows=[]
        today_games=schedule[["away","home"]].drop_duplicates() if not schedule.empty else pd.DataFrame()
        if not today_games.empty:
            # Include players who lack enough games to enter player_forecasts.
            source_logs=logs.copy()
            source_logs["PLAYER_ID"]=source_logs["PLAYER_ID"].astype(str)
            for _,matchup in today_games.iterrows():
                matchup_label=f'{matchup["away"]} @ {matchup["home"]}'
                for team_code in (matchup["away"],matchup["home"]):
                    team_logs=source_logs[source_logs["TEAM_ABBREVIATION"]==team_code]
                    if team_logs.empty:
                        audit_rows.append({"Game":matchup_label,"Team":team_code,"Player":"No player logs","Games":0,"Proj 3PA":None,"Best YES %":None,"Best qualifying":"—","Status":"MISSING TEAM DATA"})
                        continue
                    for player_id,history in team_logs.groupby("PLAYER_ID"):
                        history=history.sort_values("GAME_DATE",ascending=False)
                        latest=history.iloc[0]
                        name=str(latest["PLAYER_NAME"])
                        n=len(history)
                        season_pa=float(history["FG3A"].mean())
                        recent_pa=float(history.head(5)["FG3A"].mean())
                        projected=.65*season_pa+.35*recent_pa
                        player_fc=forecasts[forecasts["player_id"].astype(str)==player_id]
                        eligible_fc=player_fc[player_fc["threshold"].between(1,4)] if not player_fc.empty else player_fc
                        qualified_fc=eligible_fc[eligible_fc["fair_yes"]>=minimum_probability]
                        best_qualified=qualified_fc.sort_values("threshold",ascending=False).head(1)
                        best=eligible_fc.sort_values("fair_yes",ascending=False).head(1)
                        best_pct=round(float(best.iloc[0]["fair_yes"])*100,1) if not best.empty else None
                        best_threshold=(f'{int(best_qualified.iloc[0]["threshold"])}+ ({float(best_qualified.iloc[0]["fair_yes"])*100:.1f}%)' if not best_qualified.empty else "—")
                        if n<min_games:
                            status=f'INSUFFICIENT GAMES ({n}/{min_games})'
                        elif projected<1.5:
                            status="LOW 3PA VOLUME"
                        elif best_pct is None:
                            status="NO MODEL FORECAST"
                        elif best_pct>=confidence_floor:
                            status=f"QUALIFIES {confidence_floor}%+"
                        else:
                            status=f"BELOW {confidence_floor}%"
                        audit_rows.append({"Game":matchup_label,"Team":team_code,"Player":name,
                                           "Games":n,"Proj 3PA":round(projected,1),
                                           "Best YES %":best_pct,"Best qualifying":best_threshold,
                                           "Status":status})
            audit=pd.DataFrame(audit_rows)
            game_options=list(dict.fromkeys(audit["Game"].tolist()))
            game_choice=st.selectbox("Matchup",["All matchups"]+game_options,key="eligibility_game")
            audit_view=audit if game_choice=="All matchups" else audit[audit["Game"]==game_choice]
            st.dataframe(audit_view.sort_values(["Game","Team","Best YES %"],ascending=[True,True,False],na_position="last"),
                         hide_index=True,use_container_width=True)
            st.caption("Historical team assignment is taken from each player's latest stored game for that team; traded or inactive players may appear. Availability must be checked separately.")
            st.download_button("Download player eligibility audit",audit.to_csv(index=False),
                               "milkball_player_eligibility_audit.csv",key="eligibility_download")
        else:
            st.info("No scheduled games available for an eligibility audit.")

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
    st.markdown("#### Results by made-threes threshold")
    threshold_choice=st.selectbox("Past performance threshold",
        ["All thresholds","1+ threes","2+ threes","3+ threes","4+ threes"],
        key="tracking_threshold")
    # Show every threshold, including thresholds without any settled picks.
    if not settled.empty:
        threshold_summary=settled.copy()
        threshold_summary["threshold"]=pd.to_numeric(threshold_summary["threshold"],errors="coerce")
        threshold_summary["probability"]=pd.to_numeric(threshold_summary["probability"],errors="coerce")
        threshold_summary["correct_flag"]=threshold_summary["correct"].astype(str).str.lower().eq("true")
        summary_rows=[]
        for t in (1,2,3,4):
            g=threshold_summary[threshold_summary["threshold"]==t]
            n=len(g)
            summary_rows.append({"Threshold":f"{t}+","Correct":int(g["correct_flag"].sum()),
                "Total":n,"Accuracy":round(g["correct_flag"].mean()*100,1) if n else None,
                "Avg model %":round(g["probability"].mean()*100,1) if n else None})
        st.dataframe(pd.DataFrame(summary_rows),hide_index=True,use_container_width=True)
    if threshold_choice!="All thresholds":
        selected_threshold=int(threshold_choice[0])
        settled=settled[pd.to_numeric(settled["threshold"],errors="coerce")==selected_threshold].copy()
    if not settled.empty:
        correct=settled["correct"].astype(str).str.lower().eq("true")
        a,b,d=st.columns(3)
        a.metric("Accuracy",f"{correct.mean()*100:.1f}%")
        b.metric("Correct",f"{int(correct.sum())} / {len(correct)}")
        d.metric("Pending",int(tracked["correct"].isna().sum()))
        # Mutually exclusive confidence bands; the 100% upper bound belongs to 90%+.
        st.markdown("#### Accuracy by model confidence")
        st.caption("Each saved prediction belongs to one tier. Only settled, pregame predictions are counted; small samples can be misleading.")
        tiered=settled.copy()
        tiered["probability"]=pd.to_numeric(tiered["probability"],errors="coerce")
        tiered=tiered[tiered["probability"].between(0,1)].copy()
        tiered["correct_flag"]=tiered["correct"].astype(str).str.lower().eq("true")
        tier_edges=[0,.50,.60,.70,.80,.90,1.0000001]
        tier_labels=["Below 50%","50–59%","60–69%","70–79%","80–89%","90%+"]
        tiered["Confidence tier"]=pd.cut(tiered["probability"],bins=tier_edges,
            labels=tier_labels,right=False,include_lowest=True)
        tier_summary=tiered.groupby("Confidence tier",observed=False).agg(
            Correct=("correct_flag","sum"),Total=("correct_flag","size"),
            Predicted=("probability","mean")).reset_index()
        tier_summary=tier_summary[tier_summary["Total"]>0].copy()
        if not tier_summary.empty:
            tier_summary["Accuracy"]=(tier_summary["Correct"]/tier_summary["Total"]*100).round(1)
            tier_summary["Avg model %"]=(tier_summary["Predicted"]*100).round(1)
            tier_summary["Difference (pp)"]=(tier_summary["Accuracy"]-tier_summary["Avg model %"]).round(1)
            tier_summary["Confidence tier"]=tier_summary["Confidence tier"].astype(str)
            tier_summary=tier_summary.iloc[::-1]
            st.dataframe(tier_summary[["Confidence tier","Correct","Total","Accuracy",
                "Avg model %","Difference (pp)"]],hide_index=True,use_container_width=True)
            st.caption("Difference (pp) = actual accuracy minus average predicted probability. Positive means outcomes exceeded forecasts.")
        else:
            st.info("No settled predictions with valid probabilities in this period.")
        st.caption("Older snapshots saved only 90%+ predictions. Lower tiers populate from newer snapshots; no retrospective picks are invented.")
        if len(settled)<100:
            st.warning(f"Calibration sample is small ({len(settled)} settled predictions). Do not change model probabilities based on these results alone.")
        daily=summarize(settled)
        st.dataframe(daily.sort_values("Date",ascending=False),hide_index=True,use_container_width=True)
        st.line_chart(daily.set_index("Date")["Accuracy"])
        with st.expander("Individual prediction results"):
            view=settled[["prediction_date","player","side","threshold","probability","result","correct"]].copy()
            view["probability"]=(view["probability"]*100).round(1)
            st.dataframe(view.sort_values("prediction_date",ascending=False),hide_index=True,use_container_width=True)
        st.download_button("Download tracked results",settled.to_csv(index=False),"scanner_milk_model_results.csv",key="track_download")
    else:
        st.info("No settled pregame predictions for this threshold and period yet.")
        if not tracked.empty:
            st.caption(f"{int(tracked['correct'].isna().sum())} predictions are awaiting completed player boxscores.")
    st.caption("Tracking starts with saved prospective predictions; it does not backfill hypothetical historical picks. Missing player boxscores remain ungraded.")

st.subheader("Milkball NO Scanner — Research Preview")
st.caption("NO-only predictions. Phase 1 uses real saved three-point logs; points, rebounds, assists, steals, and blocks will be added only after their historical data and backtests are available.")
no_col1,no_col2,no_col3=st.columns(3)
with no_col1:
    no_floor=st.slider("Minimum NO confidence",50,99,80,5,key="no_floor")
with no_col2:
    no_threshold=st.selectbox("Minimum 3PT threshold",[1,2,3,4,5,6],index=1,key="no_threshold")
with no_col3:
    no_games=st.slider("Minimum player games",5,25,8,key="no_games")
no_candidates=build_no_candidates(forecasts,min_probability=no_floor/100,
    min_games=no_games,min_threshold=no_threshold)
if no_candidates.empty:
    st.info("No NO candidates meet these filters. Lower the confidence floor or minimum threshold to explore.")
else:
    st.metric("NO candidates (unpriced)",len(no_candidates))
    st.dataframe(no_candidates,hide_index=True,use_container_width=True)
    st.download_button("Download NO research candidates",no_candidates.to_csv(index=False),
        "milkball_no_research.csv",key="no_download")
st.caption("No Kalshi contract prices are attached to this table. High NO probability alone does not establish positive expected value; NO signals are not approved trades.")
with st.expander("Upcoming NO prop categories"):
    st.write("Points · Rebounds · Assists · Steals · Blocks — awaiting reliable player-game logs and separate chronological validation. No probabilities are fabricated for missing categories.")

st.markdown("#### NO Scanner — Points, Rebounds, Assists, Steals & Blocks")
st.caption("Experimental count-stat distributions. No Kalshi prices or verified positive edges; do not treat these as approved trades.")
prop_data_path=Path("data/nba_2026_27_player_props.csv")
if not prop_data_path.exists():
    st.info("Player prop history is not collected yet. The scheduled GitHub Actions workflow will backfill completed box scores; you can also run it manually.")
else:
    try:
        prop_logs=pd.read_csv(prop_data_path,dtype={"PLAYER_ID":str,"GAME_ID":str})
        required_props={"PLAYER_ID","GAME_DATE","GAME_ID","PTS","REB","AST","STL","BLK"}
        if not required_props.issubset(prop_logs.columns):
            raise ValueError("Player prop dataset is missing required statistics")
        prop_logs=prop_logs[prop_logs["GAME_DATE"].astype(str)<dt.datetime.now(
            __import__("zoneinfo").ZoneInfo("America/New_York")).date().isoformat()]
        st.caption(f"Saved player-game rows: {len(prop_logs):,}. Data source: completed ESPN box scores.")
        prop_predictions=forecast_props(prop_logs,min_games=no_games)
        if prop_predictions.empty:
            st.info("Not enough complete player-game history to forecast these props.")
        else:
            pcol1,pcol2=st.columns(2)
            with pcol1:
                selected_props=st.multiselect("NO stat categories",
                    ["PTS","REB","AST","STL","BLK"],
                    default=["PTS","REB","AST"],key="no_stat_categories")
            with pcol2:
                no_min=st.slider("NO confidence floor (%)",50,99,80,5,key="no_prop_floor")
            filtered=prop_predictions[
                prop_predictions["prop"].isin(selected_props)&
                (prop_predictions["model_no_pct"]>=no_min)].copy()
            if filtered.empty:
                st.info("No NO candidates match these settings.")
            else:
                st.metric("Multi-stat NO research candidates",len(filtered))
                st.dataframe(filtered.sort_values("model_no_pct",ascending=False),
                    hide_index=True,use_container_width=True)
                st.download_button("Download multi-stat NO research",
                    filtered.to_csv(index=False),"milkball_multi_stat_no.csv",key="multi_no_download")
            with st.expander("Historical walk-forward calibration (research)"):
                st.caption("Uses only games preceding each target game. No market-price or fee data; Brier scores measure probability quality, not profit.")
                if st.button("Run historical NO backtest",key="run_no_backtest"):
                    with st.spinner("Evaluating historical NO probabilities..."):
                        st.session_state["no_backtest_results"]=backtest_no_props(prop_logs,min_games=8)
                report=st.session_state.get("no_backtest_results")
                if report is not None and not report.empty:
                    shown=report.copy()
                    for field in ("Actual_NO","Predicted_NO"):
                        shown[field]=(shown[field]*100).round(1)
                    shown["Brier"]=shown["Brier"].round(4)
                    st.dataframe(shown,hide_index=True,use_container_width=True)
                    st.caption("Calibration is preliminary. Low-frequency events and small sample sizes can produce misleading confidence.")
    except Exception as exc:
        st.warning(f"Multi-stat NO data unavailable: {exc}")

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
