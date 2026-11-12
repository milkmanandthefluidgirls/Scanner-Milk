import datetime as dt
import pandas as pd
import streamlit as st
from model import player_forecasts, score
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

st.set_page_config(page_title="NBA 3PT Edge Scanner",page_icon="🏀",layout="wide")
st.title("🏀 NBA 3PT Edge Scanner")
st.caption("Read-only research | Independent baseline probabilities | No orders placed")
with st.sidebar:
    season=st.text_input("NBA season",value="2026-27")
    min_games=st.slider("Minimum games",3,25,5)
    min_edge=st.slider("Minimum estimated edge (%)",0,20,5)/100
    st.warning("Uncalibrated model. REVIEW does not mean approved trade.")
    if st.button("Clear cache"):
        st.cache_data.clear()
        st.rerun()

@st.cache_data(ttl=21600,show_spinner=False)
def get_espn_logs(days,as_of):
    return fetch_espn_logs(days=days,today=as_of)

@st.cache_data(ttl=900)
def get_logs(s): return nba_player_logs(s)
@st.cache_data(ttl=300)
def get_schedule(): return nba_schedule()
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
    schedule=get_schedule()
    st.subheader("Today's NBA games")
    st.dataframe(schedule,hide_index=True,use_container_width=True)
except Exception as e:
    st.warning(f"Schedule feed unavailable: {e}")

st.subheader("NBA player statistics — ESPN")
st.caption("Free, unofficial ESPN NBA box scores. Recent completed regular-season games only; NOT a full-season history. Refreshes at most every six hours unless cache is cleared.")
days=st.select_slider("Recent days of completed games",options=[7,14,21,28],value=14,key="espn_days")
st.caption("Longer windows request more game box scores and can take several minutes on Streamlit Cloud.")
if st.button("Load / refresh ESPN three-point logs",key="load_espn"):
    st.session_state["espn_requested"]=True
    st.session_state.pop("espn_result",None)
logs=None
if st.session_state.get("espn_requested"):
    try:
        with st.spinner("Collecting ESPN game box scores; please keep this page open..."):
            result=get_espn_logs(days,dt.datetime.now(dt.timezone(dt.timedelta(hours=-5))).date().isoformat())
            st.session_state["espn_result"]=result
        logs,diagnostics=st.session_state["espn_result"]
        st.success(f"ESPN: {diagnostics['rows']} player-game rows from {diagnostics['games_parsed']} completed games.")
        st.dataframe(pd.DataFrame([{k:v for k,v in diagnostics.items() if k!="errors"}]),hide_index=True,use_container_width=True)
        if not diagnostics["complete"]:
            st.warning("PARTIAL DATA: some days/games failed or the game cap was reached. Hit rates and forecasts can be biased.")
            with st.expander("ESPN request errors"):
                st.write(diagnostics["errors"])
        if not logs.empty:
            preview=logs[["PLAYER_NAME","TEAM_ABBREVIATION","GAME_DATE","FG3M","FG3A","MIN"]].sort_values("GAME_DATE",ascending=False)
            st.dataframe(preview.head(80),hide_index=True,use_container_width=True)
            st.download_button("Download ESPN player logs CSV",logs.to_csv(index=False),"espn_nba_3pt_logs.csv",key="download_espn")
    except (ESPNFeedError,Exception) as exc:
        st.error(f"ESPN feed unavailable: {type(exc).__name__}: {exc}")
        st.session_state["espn_requested"]=False
st.caption("Fallback: upload a verified NBA.com-style CSV if ESPN blocks cloud access.")
upload=st.file_uploader("Player logs CSV: PLAYER_ID, PLAYER_NAME, TEAM_ABBREVIATION, GAME_DATE, FG3M, FG3A",type="csv")
if upload is not None:
    try:
        uploaded_logs=pd.read_csv(upload)
        required={"PLAYER_ID","PLAYER_NAME","TEAM_ABBREVIATION","GAME_DATE","FG3M","FG3A"}
        if not required.issubset(uploaded_logs.columns):
            raise ValueError("Missing columns: "+", ".join(sorted(required-set(uploaded_logs.columns))))
        logs=uploaded_logs
        st.info("Using uploaded CSV in place of ESPN logs.")
    except Exception as exc:
        st.error(f"CSV error: {exc}")
if logs is None or logs.empty:
    st.info("Player forecasts need game logs. Tap Load / refresh ESPN three-point logs or upload a CSV.")
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
logs=logs.copy()
logs["GAME_DATE"]=pd.to_datetime(logs["GAME_DATE"],errors="coerce")
logs=logs[logs["GAME_DATE"].dt.date < dt.datetime.now(dt.timezone(dt.timedelta(hours=-5))).date()]
if not schedule.empty:
    teams=set(schedule["away"])|set(schedule["home"])
    logs=logs[logs["TEAM_ABBREVIATION"].isin(teams)]
forecasts=player_forecasts(logs,min_games)
if forecasts.empty:
    st.warning("No qualifying player logs.")
    st.stop()
st.subheader("Independent player forecasts")
st.caption("65% available sample + 35% last-five attempts; shooting accuracy shrunk toward 36%; Poisson makes. ESPN history is limited to the selected recent-day window, NOT the full season. No injury, minutes or opponent adjustment yet.")
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

st.subheader("Verified ticker mapping and orderbooks")
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
