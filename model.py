import math
import pandas as pd

def poisson_tail(lam, threshold):
    return max(0.0, min(1.0, 1-sum(math.exp(-lam)*lam**k/math.factorial(k) for k in range(threshold))))

def player_forecasts(logs, min_games=5, prior_attempts=70, prior_pct=.36):
    out=[]
    if logs.empty: return pd.DataFrame()
    for pid,g in logs.groupby('PLAYER_ID'):
        g=g.sort_values('GAME_DATE',ascending=False)
        if len(g)<min_games: continue
        recent=g.head(5)
        attempts=.65*g.FG3A.mean()+.35*recent.FG3A.mean()
        makes=g.FG3M.sum(); total=g.FG3A.sum()
        pct=(makes+prior_attempts*prior_pct)/(total+prior_attempts) if total+prior_attempts else prior_pct
        lam=attempts*pct
        for threshold in (1,2,3,4):
            fair=poisson_tail(lam,threshold)
            out.append(dict(player=str(g.PLAYER_NAME.iloc[0]),team=str(g.TEAM_ABBREVIATION.iloc[0]),
                player_id=int(pid),threshold=threshold,games=len(g),
                three_pa=round(float(g.FG3A.mean()),2),last5_pa=round(float(recent.FG3A.mean()),2),
                projected_pa=round(float(attempts),2),shrunk_pct=round(float(pct),3),
                hit_rate=round(float((g.FG3M>=threshold).mean()),3),
                fair_yes=round(float(fair),4),fair_no=round(float(1-fair),4),
                model='Uncalibrated Poisson baseline'))
    return pd.DataFrame(out)

def fee_estimate(price):
    return .07*price*(1-price) # Approximate screening only, NOT exchange fee quote

def score(forecasts,quotes,min_edge=.05):
    if forecasts.empty: return forecasts
    out=forecasts.copy()
    if quotes is not None and not quotes.empty:
        out=out.merge(quotes,on=['player','threshold'],how='left')
    else:
        out['yes_ask']=float('nan'); out['no_ask']=float('nan')
    rows=[]
    for _,r in out.iterrows():
        for side in ('YES','NO'):
            ask=r.get(side.lower()+'_ask'); fair=r['fair_'+side.lower()]
            valid=pd.notna(ask) and 0<float(ask)<1
            edge=float(fair)-float(ask)-fee_estimate(float(ask)) if valid else float('nan')
            d=r.to_dict()
            d.update(side=side,price=float(ask) if valid else float('nan'),
                     fair=float(fair),net_edge=edge,
                     decision=('REVIEW' if edge>=min_edge else 'PASS') if valid else 'NO QUOTE')
            rows.append(d)
    return pd.DataFrame(rows).sort_values('net_edge',ascending=False,na_position='last')
