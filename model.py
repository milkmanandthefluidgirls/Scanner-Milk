"""Research-only 3PT forecasts: shot-volume uncertainty + beta-binomial accuracy."""
import math
import pandas as pd

MODEL_NAME="Uncalibrated volume-mixture beta-binomial v2"

def _poisson_pmf(k,mu):
    if mu<=0: return 1.0 if k==0 else 0.0
    return math.exp(-mu+k*math.log(mu)-math.lgamma(k+1))

def _volume_pmf(k,mu,variance):
    if mu<=0: return 1.0 if k==0 else 0.0
    if variance<=mu*1.01: return _poisson_pmf(k,mu)
    size=max(.5,mu*mu/(variance-mu))
    p=size/(size+mu)
    return math.exp(math.lgamma(k+size)-math.lgamma(size)-math.lgamma(k+1)
                    +size*math.log(p)+k*math.log1p(-p))

def _beta_binomial_pmf(m,n,alpha,beta):
    if m<0 or m>n:return 0.
    return math.exp(math.lgamma(n+1)-math.lgamma(m+1)-math.lgamma(n-m+1)
                    +math.lgamma(m+alpha)+math.lgamma(n-m+beta)
                    -math.lgamma(n+alpha+beta)+math.lgamma(alpha+beta)
                    -math.lgamma(alpha)-math.lgamma(beta))

def forecast_probabilities(attempt_mean,attempt_variance,alpha,beta,max_attempts=75):
    """Integrate a dispersed shot-attempt distribution over uncertain shooting accuracy."""
    mass=[0.]*5
    normalizer=0.
    for n in range(max_attempts+1):
        weight=_volume_pmf(n,attempt_mean,attempt_variance)
        normalizer+=weight
        for made in range(min(3,n)+1):
            mass[made]+=weight*_beta_binomial_pmf(made,n,alpha,beta)
    if normalizer<=0: return {t:float('nan') for t in (1,2,3,4)}
    return {t:max(0.,min(1.,1.-sum(mass[:t])/normalizer)) for t in (1,2,3,4)}

def player_forecasts(logs,min_games=5,prior_attempts=70,prior_pct=.36):
    if logs is None or logs.empty:return pd.DataFrame()
    out=[]
    for pid,g in logs.groupby("PLAYER_ID"):
        g=g.sort_values(["GAME_DATE","GAME_ID"],ascending=False)
        if len(g)<min_games:continue
        recent=g.head(5)
        # Weighted attempt volume reacts to recent role changes but preserves season baseline.
        season_mean=float(g.FG3A.mean())
        recent_mean=float(recent.FG3A.mean())
        projected=max(0.,.65*season_mean+.35*recent_mean)
        season_var=float(g.FG3A.var(ddof=1)) if len(g)>1 else season_mean
        recent_var=float(recent.FG3A.var(ddof=1)) if len(recent)>1 else recent_mean
        # Blend within-sample variation and recent-vs-season regime shift.
        variance=max(projected,.65*season_var+.35*recent_var
                     +.65*.35*(season_mean-recent_mean)**2)
        makes=float(g.FG3M.sum())
        attempts=float(g.FG3A.sum())
        alpha=max(.01,prior_attempts*prior_pct+makes)
        beta=max(.01,prior_attempts*(1-prior_pct)+attempts-makes)
        accuracy=alpha/(alpha+beta)
        probs=forecast_probabilities(projected,variance,alpha,beta)
        latest=g.iloc[0]
        for threshold,fair in probs.items():
            out.append(dict(player=str(latest.PLAYER_NAME),
                team=str(latest.TEAM_ABBREVIATION),player_id=str(pid),
                threshold=threshold,games=len(g),
                three_pa=round(season_mean,2),last5_pa=round(recent_mean,2),
                projected_pa=round(projected,2),
                projected_pa_variance=round(variance,2),
                shrunk_pct=round(accuracy,3),
                hit_rate=round(float((g.FG3M>=threshold).mean()),3),
                last5_hit_rate=round(float((recent.FG3M>=threshold).mean()),3),
                last5_games=len(recent),
                fair_yes=round(fair,4),fair_no=round(1-fair,4),
                model=MODEL_NAME))
    return pd.DataFrame(out)

def fee_estimate(price):
    # Placeholder for screening; actual Kalshi fees depend on market and fill.
    return .07*price*(1-price)

def score(forecasts,quotes,min_edge=.05):
    if forecasts.empty:return forecasts
    out=forecasts.copy()
    if quotes is not None and not quotes.empty:
        out=out.merge(quotes,on=["player","threshold"],how="left")
    else:
        out["yes_ask"]=float("nan")
        out["no_ask"]=float("nan")
    rows=[]
    for _,r in out.iterrows():
        for side in ("YES","NO"):
            ask=r.get(side.lower()+"_ask")
            fair=float(r["fair_"+side.lower()])
            valid=pd.notna(ask) and 0<float(ask)<1
            edge=fair-float(ask)-fee_estimate(float(ask)) if valid else float("nan")
            d=r.to_dict()
            d.update(side=side,price=float(ask) if valid else float("nan"),
                     fair=fair,net_edge=edge,
                     decision=("REVIEW" if edge>=min_edge else "PASS") if valid else "NO QUOTE")
            rows.append(d)
    return pd.DataFrame(rows).sort_values("net_edge",ascending=False,na_position="last")
