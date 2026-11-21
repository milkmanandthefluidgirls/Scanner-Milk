import datetime as dt
import json
from pathlib import Path
import pandas as pd
import streamlit as st
from model import player_forecasts, score
from no_scanner import build_no_candidates
from no_prop_model import forecast_props, backtest as backtest_no_props
from kalshi_no_verify import verify_candidates
# Streamlit reruns app.py but can retain imported helper modules in sys.modules.
# Reload the allocator so a deployed helper update actually takes effect.
import importlib
import no_order_proposals
importlib.reload(no_order_proposals)
propose_no_orders = no_order_proposals.propose_no_orders
import kalshi_trader
importlib.reload(kalshi_trader)
KalshiTrader = kalshi_trader.KalshiTrader
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

st.set_page_config(page_title="Milkball | NBA NO Scanner",page_icon="🥛",layout="wide",initial_sidebar_state="collapsed")
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
    st.caption("NBA NO SCANNER • Player prop research • Market verification")

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


# Render NO model picks at the top once their data is calculated.
no_picks_slot=st.container()

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

        if manifest_path.exists():
            manifest=json.loads(manifest_path.read_text())

    except Exception as exc:
        st.session_state["saved_stats_error"]=str(exc)
if logs is None or logs.empty:
    st.error("Current-season game logs are unavailable. Open Research & data diagnostics below.")
    st.stop()
manifest_data=json.loads(manifest_path.read_text()) if manifest_path.exists() else None
logs,quality=validate_logs(logs,manifest_data)
if quality.get("errors"):
    st.stop()
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
st.subheader("NO model results tracker")
st.caption("NO predictions only, beginning yesterday. Older YES-model results are excluded, not deleted.")
today=dt.datetime.now(__import__("zoneinfo").ZoneInfo("America/New_York")).date()
no_tracking_start=dt.date(2026,11,17)
tracked=load_predictions()
tracked=tracked[(tracked["side"].astype(str).str.upper()=="NO") &
                (tracked["prediction_date"].astype(str)>=no_tracking_start.isoformat())].copy()
settled=tracked[tracked["correct"].notna()].copy()
period=st.selectbox("Results period",["Last 7 days","Last 30 days","This season","Custom dates"],index=1,key="tracking_period")
if period=="Custom dates":
    d1,d2=st.columns(2)
    start_date=d1.date_input("From",value=no_tracking_start,min_value=no_tracking_start,key="track_from")
    end_date=d2.date_input("To",value=today,key="track_to")
elif period=="Last 7 days":
    start_date,end_date=max(no_tracking_start,today-dt.timedelta(days=6)),today
elif period=="Last 30 days":
    start_date,end_date=max(no_tracking_start,today-dt.timedelta(days=29)),today
else:
    start_date,end_date=no_tracking_start,today
if not settled.empty:
    settled=settled[settled["prediction_date"].between(str(start_date),str(end_date))]
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
    st.caption("NO predictions are tracked only from the new cutoff; prior YES-model results remain archived.")
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
    st.info("No settled NO predictions since yesterday yet. Older YES results are intentionally hidden.")
    if not tracked.empty:
        st.caption(f"{int(tracked['correct'].isna().sum())} predictions are awaiting completed player boxscores.")
st.caption("Tracking starts with saved prospective predictions; it does not backfill hypothetical historical picks. Missing player boxscores remain ungraded.")

with no_picks_slot:
    st.subheader("Milkball NO Model Picks")
    st.caption("NBA player props · NO only · Current-season forecasts · Uncalibrated research probabilities")
    no_a,no_b,no_c=st.columns(3)
    with no_a:
        no_floor=st.slider("Minimum model NO confidence (%)",50,99,80,5,key="no_floor")
    with no_b:
        no_games=st.slider("Minimum player games",5,25,8,key="no_games")
    with no_c:
        selected_props=st.multiselect("Player props",["3PT made","PTS","REB","AST","STL","BLK"],
            default=["3PT made","PTS","REB","AST"],key="no_stat_categories")
    today_et=dt.datetime.now(__import__("zoneinfo").ZoneInfo("America/New_York")).date().isoformat()
    all_no=[]
    three_no=build_no_candidates(forecasts,min_probability=no_floor/100,
        min_games=no_games,min_threshold=1)
    if not three_no.empty:
        three_no=three_no.rename(columns={"projected_3pa":"projected_mean"})
        all_no.append(three_no)
    prop_data_path=Path("data/nba_2026_27_player_props.csv")
    prop_logs=None
    if prop_data_path.exists():
        try:
            prop_logs=pd.read_csv(prop_data_path,dtype={"PLAYER_ID":str,"GAME_ID":str})
            required_props={"PLAYER_ID","PLAYER_NAME","TEAM_ABBREVIATION","GAME_DATE",
                            "GAME_ID","PTS","REB","AST","STL","BLK"}
            if not required_props.issubset(prop_logs.columns):
                raise ValueError("Player-prop dataset is missing required columns")
            prop_logs=prop_logs[prop_logs["GAME_DATE"].astype(str)<today_et].copy()
            if not prop_logs.empty:
                multi_no=forecast_props(prop_logs,min_games=no_games)
                multi_no=multi_no[multi_no["model_no_pct"]>=no_floor].copy()
                if not schedule.empty:
                    scheduled_teams=set(schedule["away"])|set(schedule["home"])
                    multi_no=multi_no[multi_no["team"].isin(scheduled_teams)]
                if not multi_no.empty:all_no.append(multi_no)
        except Exception as exc:
            st.warning(f"Multi-stat NO dataset unavailable: {exc}")
    else:
        st.caption("Additional stat categories will populate after the player-prop history collector completes.")
    candidates=pd.concat(all_no,ignore_index=True) if all_no else pd.DataFrame()
    if not candidates.empty:
        candidates=candidates[candidates["prop"].isin(selected_props)].copy()
        candidates=candidates.sort_values(["model_no_pct","games"],ascending=[False,False]).reset_index(drop=True)
    if candidates.empty:
        st.info("No NO forecasts match the selected filters.")
    else:
        st.caption(f"{len(candidates):,} model candidates before Kalshi verification. Tap the button to display ONLY exact, currently quoted Kalshi contracts.")
        if st.button("Search Kalshi · Verify available NO props & prices",key="verify_all_no",type="primary"):
            with st.spinner("Checking player, stat, threshold, game date and current NO orderbooks..."):
                try:
                    # Search the entire filtered set, not merely the first 100 picks.
                    verified,diagnostics=verify_candidates(candidates,max_quotes=250)
                    st.session_state["verified_no"]=verified
                    st.session_state["verified_no_diagnostics"]=diagnostics
                    st.session_state["verified_no_filter_key"]=(today_et,tuple(selected_props),no_floor,no_games)
                    st.session_state["verified_no_checked_at"]=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                except Exception as exc:
                    st.error(f"Kalshi verification failed: {exc}")
        current_key=(today_et,tuple(selected_props),no_floor,no_games)
        verified=st.session_state.get("verified_no")
        if verified is None or st.session_state.get("verified_no_filter_key")!=current_key:
            st.info("Kalshi availability and prices have not been checked for these filters. No unverified picks are displayed.")
        else:
            available=verified[verified["verification"].eq("MATCHED — QUOTED")].copy()
            st.caption(f"Verified {st.session_state.get('verified_no_checked_at','')}. Prices are snapshots and may change.")
            m1,m2,m3=st.columns(3)
            m1.metric("Kalshi quoted NO picks",len(available))
            m2.metric("Filtered / unavailable",len(verified)-len(available))
            m3.metric("Model candidates checked",len(verified))
            if available.empty:
                st.warning("No exact, currently quoted Kalshi NO contracts were verified. The market scan may be incomplete; see diagnostics.")
            else:
                available=available.sort_values("estimated_edge_pp",ascending=False)
                available["NO price (¢)"]=pd.to_numeric(available["kalshi_no_ask_cents"],errors="coerce").round(1)
                available["Model NO %"]=pd.to_numeric(available["model_no_pct"],errors="coerce").round(1)
                available["Gross edge (pp)"]=pd.to_numeric(available["estimated_edge_pp"],errors="coerce").round(1)
                cols=["player","team","prop","threshold","Model NO %","NO price (¢)",
                      "Gross edge (pp)","season_no_pct","last5_no_pct","games","kalshi_ticker"]
                st.dataframe(available[[c for c in cols if c in available.columns]],
                    hide_index=True,use_container_width=True)
                st.download_button("Download available Kalshi NO picks",
                    available.to_csv(index=False),"milkball_verified_no_picks.csv",key="verified_no_download")
            st.markdown("#### Automatic NO picks — review before ordering")
            st.caption("Milkball proposes qualifying contracts automatically. Review and remove picks before approving any demo or live orders.")
            p1,p2,p3=st.columns(3)
            with p1:
                min_order_prob=st.slider("Minimum NO probability (%)",70,99,90,1,key="order_min_prob")
                max_order_price=st.slider("Maximum NO price (¢)",30,99,80,1,key="order_max_price")
            with p2:
                min_order_edge=st.slider("Minimum gross edge (percentage points)",1,30,10,1,key="order_min_edge")

            with p3:
                order_budget=st.number_input("Maximum total spend ($)",min_value=1.,max_value=500.,value=10.,step=1.,key="order_budget")
                contracts_each=st.number_input("Minimum contracts per pick",1,100,1,1,key="contracts_each")
            # Show why an automatic order list may be empty rather than silently
            # hiding the available contracts behind strict probability/price rules.
            eligible=available.copy()
            for col in ("model_no_pct","kalshi_no_ask_cents","estimated_edge_pp"):
                eligible[col]=pd.to_numeric(eligible[col],errors="coerce")
            eligible=eligible[(eligible["model_no_pct"]>=min_order_prob)&
                (eligible["kalshi_no_ask_cents"]>0)&
                (eligible["kalshi_no_ask_cents"]<=max_order_price)&
                (eligible["estimated_edge_pp"]>=min_order_edge)].copy()
            eligible=eligible.sort_values(["estimated_edge_pp","model_no_pct"],
                ascending=False)
            qualifying_rows=len(eligible)
            unique_eligible=eligible.drop_duplicates(subset=["kalshi_ticker"],keep="first").copy()
            duplicates=qualifying_rows-len(unique_eligible)
            st.caption(f"Verified model rows: {len(available)} · Qualifying rows: {qualifying_rows} · **Distinct Kalshi contracts: {len(unique_eligible)}**")
            if duplicates:
                st.warning(f"{duplicates} qualifying model rows refer to Kalshi tickers already counted. Each ticker can only be submitted once in this order proposal. Increasing the budget cannot create additional distinct markets.")
            eligible=unique_eligible
            if eligible.empty and not available.empty:
                st.warning("No verified picks satisfy all three limits. The controls above are filters, not guarantees. Adjust them or verify new quotes.")
                with st.expander("See why picks were excluded"):
                    st.write(f"Below model probability: {int((pd.to_numeric(available['model_no_pct'],errors='coerce')<min_order_prob).sum())}")
                    st.write(f"Above maximum NO price: {int((pd.to_numeric(available['kalshi_no_ask_cents'],errors='coerce')>max_order_price).sum())}")
                    st.write(f"Below minimum gross edge: {int((pd.to_numeric(available['estimated_edge_pp'],errors='coerce')<min_order_edge).sum())}")
            # Keep manual removals across reruns, and fill the remaining budget
            # with the next eligible picks after exclusions.
            proposal_source=available.copy()
            try:
                proposal=propose_no_orders(proposal_source,min_probability=min_order_prob/100,
                    min_gross_edge_pp=min_order_edge,max_price_cents=max_order_price,
                    contracts_each=int(contracts_each),budget_dollars=float(order_budget))
            except Exception as exc:
                st.error(f"Order proposal calculation failed: {type(exc).__name__}: {exc}")
                proposal=pd.DataFrame()
            # Keep manual removals across Streamlit reruns, but reset when the
            # underlying verified quote snapshot or model filters change.
            removal_scope=(st.session_state.get("verified_no_checked_at"),current_key)
            if st.session_state.get("no_removal_scope")!=removal_scope:
                st.session_state["no_removal_scope"]=removal_scope
                st.session_state["no_removed_tickers"]=[]
            removed=set(st.session_state.get("no_removed_tickers",[]))
            if removed:
                proposal_source=proposal_source[~proposal_source["kalshi_ticker"].astype(str).isin(removed)].copy()
                try:
                    proposal=propose_no_orders(proposal_source,min_probability=min_order_prob/100,
                        min_gross_edge_pp=min_order_edge,max_price_cents=max_order_price,
                        contracts_each=int(contracts_each),budget_dollars=float(order_budget))
                except Exception as exc:
                    st.error(f"Order proposal refresh failed: {type(exc).__name__}: {exc}")
                    proposal=pd.DataFrame()
            if removed:
                if st.button("Restore removed picks",key="restore_no_picks"):
                    st.session_state["no_removed_tickers"]=[]
                    st.rerun()
            # Display EVERY qualifying market, even if it cannot fit in the
            # current budget. The execution list is separately budget-limited.
            visible_eligible=eligible[
                ~eligible["kalshi_ticker"].astype(str).isin(removed)].copy()
            proposed_tickers=set(proposal["kalshi_ticker"].astype(str)) if not proposal.empty else set()
            st.markdown(f"##### All qualifying picks ({len(visible_eligible)})")
            st.caption("Every contract meeting your filters appears here. 'In order' means it is included in the budget-limited execution list. Remove any pick to exclude it.")
            if visible_eligible.empty:
                st.info("No qualifying picks remain. Use Restore removed picks or adjust filters.")
            else:
                for _,pick in visible_eligible.iterrows():
                    ticker=str(pick["kalshi_ticker"])
                    in_order=ticker in proposed_tickers
                    label="IN ORDER" if in_order else "NOT IN ORDER (budget or quantity limit)"
                    with st.container(border=True):
                        st.write(f"**{pick['player']}** · {pick['prop']} {pick['threshold']}+ · NO")
                        st.caption(f"{pick['kalshi_no_ask_cents']:.1f}¢ · {pick['model_no_pct']:.1f}% model · {pick['estimated_edge_pp']:.1f} pp gross edge · {label}")
                        if st.button("✕ Remove",key=f"remove_no_{ticker}",use_container_width=True):
                            st.session_state["no_removed_tickers"].append(ticker)
                            st.session_state.pop("paper_no_orders",None)
                            st.session_state.pop("kalshi_execution_results",None)
                            st.rerun()
            # Verify the proposal respects the current budget and actually
            # allocates it. Fail closed rather than showing a stale $2.31 order.
            if not proposal.empty:
                selected_contracts=int(pd.to_numeric(proposal["contracts"],errors="coerce").sum())
                if float(order_budget)>10 and selected_contracts==len(proposal) and len(proposal)>0:
                    st.error("Order allocator is still returning one contract per pick despite the larger budget. Live submission is blocked until this is resolved.")
                    st.session_state["no_allocator_error"]=True
                else:
                    st.session_state["no_allocator_error"]=False
            else:
                st.session_state["no_allocator_error"]=False
            st.caption("Order allocator: v3 · live quantity allocation")
            selected_cost=float(proposal["max_cost_dollars"].sum()) if not proposal.empty else 0.0
            st.markdown(f"##### Orders selected within budget ({len(proposal)})")
            st.caption(f"Budget: ${float(order_budget):.2f} · Minimum per pick: {int(contracts_each)} · Selected cost: ${selected_cost:.2f} · Remaining: ${max(0.0,float(order_budget)-selected_cost):.2f}")
            if len(proposal)==len(eligible) and len(proposal)>0:
                st.info("All distinct qualifying contracts are already selected. Increasing the budget now increases contract quantities across these markets, up to 100 per market.")
            order_signature=(float(order_budget),int(contracts_each),tuple(
                (str(p["kalshi_ticker"]),int(p["contracts"]),float(p["kalshi_no_ask_cents"]))
                for _,p in proposal.iterrows()))
            if st.session_state.get("no_order_signature")!=order_signature:
                st.session_state["no_order_signature"]=order_signature
                st.session_state.pop("kalshi_trade_ack",None)
                st.session_state.pop("kalshi_live_phrase",None)
                st.session_state.pop("paper_approval",None)
            if proposal.empty:
                st.info("No picks are currently selected for ordering within the spending limit.")
            else:
                st.dataframe(proposal,hide_index=True,use_container_width=True)
                st.metric("Maximum proposed purchase cost",f"${proposal['max_cost_dollars'].sum():.2f}")
                st.caption("Only these orders can be submitted. Larger budgets automatically increase quantities and financial exposure; prices are snapshots and fees are excluded.")
                approval=st.checkbox("I have reviewed the proposed picks and understand this is a simulation.",key="paper_approval")
                if st.button("Approve paper-trade picks",disabled=not approval,key="approve_paper"):
                    st.session_state["paper_no_orders"]=proposal.copy()
                    st.success("Paper picks recorded in this browser session. No Kalshi orders were placed.")
            st.markdown("##### Kalshi account connection & order execution")
            st.caption("Production Kalshi account only. Credentials stay in Streamlit Secrets; never paste a private key into the app or GitHub.")
            trade_mode="production"
            secrets_group="kalshi_production"
            try:
                credentials=st.secrets[secrets_group]
                trader=KalshiTrader(credentials["key_id"],credentials["private_key_pem"],trade_mode)
                st.success(f"{trade_mode.title()} credentials configured")
            except Exception:
                trader=None
                st.info(f"Configure [{secrets_group}] key_id and private_key_pem in Streamlit Secrets to enable trading.")
            if trader is not None and st.button("Check Kalshi account balance",key="check_kalshi_balance"):
                try:
                    balance=trader.balance()
                    st.write("Available balance (cents):",balance.get("balance","Unavailable"))
                except Exception as exc:
                    st.error(f"Account check failed: {exc}")
            if not proposal.empty:
                st.warning("Orders are immediate-or-cancel limits; fills may be partial. Prices must be refreshed immediately before approval. Fee estimates are NOT included.")
                acknowledge=st.checkbox(f"I approve up to ${proposal['max_cost_dollars'].sum():.2f} in {trade_mode.upper()} NO orders for the exact listed contracts.",key="kalshi_trade_ack")
                phrase=st.text_input("Type PLACE LIVE ORDERS to authorize real-money trading",key="kalshi_live_phrase")
                enabled=trader is not None and acknowledge and phrase=="PLACE LIVE ORDERS" and not st.session_state.get("no_allocator_error",False)
                if st.button(f"Submit {trade_mode.upper()} NO limit orders",disabled=not enabled,key="submit_kalshi_orders"):
                    # Re-verify all candidate markets and reject stale/unavailable quotes.
                    try:
                        fresh,_=verify_candidates(proposal,max_quotes=len(proposal))
                        fresh=fresh[fresh["verification"]=="MATCHED — QUOTED"]
                        results=[]
                        total_limit_cents=0.
                        for _,pick in proposal.iterrows():
                            matches=fresh[fresh["kalshi_ticker"]==pick["kalshi_ticker"]]
                            if len(matches)!=1:
                                results.append({"ticker":pick["kalshi_ticker"],"status":"SKIPPED: market unavailable"})
                                continue
                            current_price=float(matches.iloc[0]["kalshi_no_ask_cents"])
                            limit=float(pick["kalshi_no_ask_cents"])
                            if current_price>limit:
                                results.append({"ticker":pick["kalshi_ticker"],"status":"SKIPPED: price increased"})
                                continue
                            count=int(pick["contracts"])
                            total_limit_cents+=count*limit
                            if total_limit_cents>float(order_budget)*100+0.001:
                                results.append({"ticker":pick["kalshi_ticker"],"status":"SKIPPED: budget cap"})
                                continue
                            try:
                                response=trader.buy_no_ioc(str(pick["kalshi_ticker"]),count,limit)
                                order=response.get("order",response)
                                results.append({"ticker":pick["kalshi_ticker"],
                                    "status":"SUBMITTED — inspect fill status",
                                    "order_id":order.get("order_id",""),
                                    "fill_count":order.get("fill_count",""),
                                    "remaining_count":order.get("remaining_count","")})
                            except Exception as exc:
                                results.append({"ticker":pick["kalshi_ticker"],"status":f"ERROR: {type(exc).__name__}: {exc}"})
                                # Stop on unknown execution outcome to avoid duplicates.
                                break
                        st.session_state["kalshi_execution_results"]=pd.DataFrame(results)
                    except Exception as exc:
                        st.error(f"Re-verification failed; no orders submitted in this attempt: {exc}")
                st.caption("Kalshi order client: v2 diagnostics · API rejection details shown in the execution results")
                if "kalshi_execution_results" in st.session_state:
                    st.dataframe(st.session_state["kalshi_execution_results"],hide_index=True,use_container_width=True)
            with st.expander("Verification diagnostics / excluded contracts"):
                st.dataframe(verified["verification"].value_counts().rename_axis("Reason").reset_index(name="Count"),
                    hide_index=True,use_container_width=True)
                diagnostics=st.session_state.get("verified_no_diagnostics")
                if diagnostics is not None and not diagnostics.empty:
                    st.dataframe(diagnostics,hide_index=True,use_container_width=True)
                st.dataframe(verified[verified["verification"].ne("MATCHED — QUOTED")],
                    hide_index=True,use_container_width=True)
            st.caption("Only exact matches with a NO orderbook ask appear. Gross edge excludes fees, slippage and model error. Orders are submitted only when explicitly confirmed in the execution section.")
    if prop_logs is not None and not prop_logs.empty:
        with st.expander("Historical NO model calibration (research)"):
            st.caption("Walk-forward test: prior games only; Brier scores evaluate probability accuracy, not trading profit.")
            if st.button("Run historical NO backtest",key="run_no_backtest"):
                with st.spinner("Backtesting historical NO forecasts..."):
                    st.session_state["no_backtest_results"]=backtest_no_props(prop_logs,min_games=8)
            report=st.session_state.get("no_backtest_results")
            if report is not None and not report.empty:
                shown=report.copy()
                for field in ("Actual_NO","Predicted_NO"):
                    shown[field]=(shown[field]*100).round(1)
                shown["Brier"]=shown["Brier"].round(4)
                st.dataframe(shown,hide_index=True,use_container_width=True)

# Secondary research is intentionally collapsed beneath the three primary workflows.
with st.expander("Research & data diagnostics — season data, health, historical NO rates",expanded=False):
    st.subheader("Season data")
    st.caption("Current season ONLY. Saved game logs are updated by GitHub Actions.")
    if saved_path.exists() and logs is not None:
        st.success(f"Saved current-season data: {len(logs):,} validated player-game records.")
    if "saved_stats_error" in st.session_state:
        st.error(st.session_state["saved_stats_error"])
    if manifest_path.exists():
        st.caption(f"Last collection: {manifest.get('last_checked','unknown')}")
        if not manifest.get("complete",False):
            st.warning("Historical collection incomplete; forecasts may be biased.")
            with st.expander("Collection diagnostics"):
                st.json({k:v for k,v in manifest.items() if k!="completed_game_ids"})
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

    st.subheader("Data health")
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

    st.subheader("Historical NO hit rates — all player props")
    st.caption("Actual regular-season box scores, not model forecasts. NO hit rate means the player finished BELOW the selected threshold.")
    history_prop_path=Path("data/nba_2026_27_player_props.csv")
    history_prop_logs=None
    if history_prop_path.exists():
        try:
            history_prop_logs=pd.read_csv(history_prop_path,dtype={"PLAYER_ID":str,"GAME_ID":str})
            needed={"PLAYER_ID","PLAYER_NAME","TEAM_ABBREVIATION","GAME_DATE","PTS","REB","AST","STL","BLK","FG3M"}
            if not needed.issubset(history_prop_logs.columns):
                raise ValueError("Saved prop logs lack required columns")
        except Exception as exc:
            st.warning(f"Additional player-prop history unavailable: {exc}")
            history_prop_logs=None
    stat_labels={"Three-pointers":"FG3M","Points":"PTS","Rebounds":"REB",
                 "Assists":"AST","Steals":"STL","Blocks":"BLK"}
    stat_thresholds={"FG3M":[1,2,3,4,5,6],"PTS":[10,15,20,25,30,35],
                     "REB":[4,6,8,10,12,14],"AST":[2,4,6,8,10,12],
                     "STL":[1,2,3,4],"BLK":[1,2,3,4]}
    available_stats=["Three-pointers"]+([k for k in stat_labels if k!="Three-pointers"] if history_prop_logs is not None else [])
    if history_prop_logs is None:
        st.info("Points, rebounds, assists, steals and blocks appear after the player-prop history backfill completes.")
    hcol1,hcol2,hcol3=st.columns(3)
    with hcol1:
        history_stat=st.selectbox("Historical stat",available_stats,key="historical_no_stat")
    history_key=stat_labels[history_stat]
    with hcol2:
        history_threshold=st.selectbox("NO on threshold",stat_thresholds[history_key],
                                      index=min(1,len(stat_thresholds[history_key])-1),
                                      format_func=lambda v:f"NO {v}+",key="historical_no_threshold")
    with hcol3:
        history_team_scope=st.checkbox("Tonight's teams only",value=True,key="history_tonight_only")
    history_source=history_prop_logs if history_prop_logs is not None else logs
    history_rows=[]
    if history_key in history_source.columns:
        source=history_source.copy()
        source["GAME_DATE"]=pd.to_datetime(source["GAME_DATE"],errors="coerce")
        source=source[source["GAME_DATE"].notna()]
        source=source[source["GAME_DATE"].dt.date<dt.datetime.now(
            __import__("zoneinfo").ZoneInfo("America/New_York")).date()]
        for player_id,g in source.groupby("PLAYER_ID"):
            g=g.sort_values("GAME_DATE")
            values=pd.to_numeric(g[history_key],errors="coerce").dropna()
            if len(values)<min_games:continue
            latest=g.iloc[-1]
            team=str(latest["TEAM_ABBREVIATION"])
            if history_team_scope and not schedule.empty and team not in (set(schedule["away"])|set(schedule["home"])):
                continue
            last5=values.tail(5)
            last10=values.tail(10)
            history_rows.append({"Player":str(latest["PLAYER_NAME"]),"Team":team,
                "Games":len(values),"NO threshold":f"{history_threshold}+",
                "Season NO %":round(float((values<history_threshold).mean())*100,1),
                "Last 5 NO %":round(float((last5<history_threshold).mean())*100,1),
                "Last 10 NO %":round(float((last10<history_threshold).mean())*100,1),
                "Season avg":round(float(values.mean()),2)})
    history_table=pd.DataFrame(history_rows)
    if history_table.empty:
        st.info("No player history matches the selected stat, threshold and minimum games.")
    else:
        history_table=history_table.sort_values(["Season NO %","Games"],ascending=[False,False])
        st.dataframe(history_table,hide_index=True,use_container_width=True)
        st.download_button("Download historical NO hit rates",history_table.to_csv(index=False),
            "milkball_historical_no_hit_rates.csv",key="download_historical_no")

