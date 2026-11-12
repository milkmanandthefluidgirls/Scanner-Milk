import time
import requests
import pandas as pd
BASE='https://external-api.kalshi.com/trade-api/v2'

def nba_schedule():
    from nba_api.live.nba.endpoints import scoreboard
    games=scoreboard.ScoreBoard().games.get_dict()
    return pd.DataFrame([{'game_id':g['gameId'],'away':g['awayTeam']['teamTricode'],
        'home':g['homeTeam']['teamTricode'],'status':g.get('gameStatusText','')} for g in games])

def nba_player_logs(season='2026-27'):
    from nba_api.stats.endpoints import leaguegamelog
    data=leaguegamelog.LeagueGameLog(season=season,player_or_team_abbreviation='P',
        season_type_all_star='Regular Season',timeout=40)
    df=data.get_data_frames()[0]
    required={'PLAYER_ID','PLAYER_NAME','TEAM_ABBREVIATION','GAME_DATE','FG3M','FG3A'}
    if not required.issubset(df.columns):
        raise ValueError('NBA stats columns missing: '+str(required-set(df.columns)))
    return df

def kalshi_markets(max_pages=20,status='open'):
    s=requests.Session(); cursor=None; rows=[]
    for _ in range(max_pages):
        params={'limit':1000,'status':status}
        if cursor: params['cursor']=cursor
        r=s.get(BASE+'/markets',params=params,timeout=25);r.raise_for_status()
        data=r.json(); rows.extend(data.get('markets',[]))
        next_cursor=data.get('cursor')
        if not next_cursor or next_cursor==cursor: break
        cursor=next_cursor; time.sleep(.3)
    return pd.DataFrame(rows)

def kalshi_orderbook(ticker):
    r=requests.get(BASE+'/markets/'+ticker+'/orderbook',timeout=20)
    r.raise_for_status();return r.json()

def extract_best_asks(orderbook):
    book=orderbook.get('orderbook_fp',{})
    def best(levels):
        vals=[]
        for level in levels:
            try:
                if float(level[1])>0: vals.append(float(level[0]))
            except (TypeError,ValueError,IndexError): pass
        return max(vals) if vals else None
    y=best(book.get('yes_dollars',[]));n=best(book.get('no_dollars',[]))
    return {'yes_ask':round(1-n,4) if n is not None else None,
            'no_ask':round(1-y,4) if y is not None else None}
