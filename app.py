import datetime as dt
import pandas as pd
import streamlit as st
from model import player_forecasts, score
from sources import nba_schedule, nba_player_logs, kalshi_markets, kalshi_orderbook, extract_best_asks

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

@st.cache_data(ttl=900)
def get_logs(s): return nba_player_logs(s)
@st.cache_data(ttl=300)
def get_schedule(): return nba_schedule()
@st.cache_data(ttl=300)
def get_markets(): return kalshi_markets()

schedule=pd.DataFrame()
try:
    schedule=get_schedule()
    st.subheader("Today's NBA games")
    st.dataframe(schedule,hide_index=True,use_container_width=True)
except Exception as e:
    st.warning(f"Schedule feed unavailable: {e}")

logs=None
try: logs=get_logs(season)
except Exception as e:
    st.warning(f"NBA stats feed unavailable: {e}")
    st.caption("NBA.com may block some cloud hosts. Upload game logs as CSV instead.")
upload=st.file_uploader("Optional player logs CSV: PLAYER_ID, PLAYER_NAME, TEAM_ABBREVIATION, GAME_DATE, FG3M, FG3A",type="csv")
if upload is not None:
    try: logs=pd.read_csv(upload)
    except Exception as e: st.error(str(e))
if logs is None or logs.empty:
    st.info("Waiting for NBA logs or CSV upload.")
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
st.caption("65% season + 35% last-five attempts; shooting accuracy shrunk toward 36%; Poisson makes. No injury, minutes or opponent adjustment yet.")
st.dataframe(forecasts,hide_index=True,use_container_width=True)

st.subheader("Public Kalshi market catalog")
st.warning("Automatic player-to-contract matching is not enabled. Exact contract verification required.")
if st.button("Fetch Kalshi markets"):
    try: st.session_state["markets"]=get_markets()
    except Exception as e: st.error(f"Kalshi API: {e}")
markets=st.session_state.get("markets",pd.DataFrame())
if not markets.empty:
    st.metric("Markets retrieved",len(markets))
    columns=[c for c in ("ticker","title","subtitle","status") if c in markets.columns]
    st.dataframe(markets[columns].head(1000),hide_index=True,use_container_width=True)
    st.download_button("Download market catalog",markets.to_csv(index=False),"kalshi_markets.csv")

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
