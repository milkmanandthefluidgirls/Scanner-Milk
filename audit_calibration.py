"""Audit prospective Milkball probability calibration without fitting on tiny samples.

Run: python audit_calibration.py
Only recorded pregame predictions with settled outcomes are used.
Outputs per-threshold accuracy, average forecast, Brier score, and
non-overlapping confidence bands. No retrospective forecasts or model changes.
"""
from pathlib import Path
import pandas as pd

DATA=Path(__file__).parent/"data/model_predictions.csv"

def audit():
    df=pd.read_csv(DATA)
    df=df[df.correct.notna()].copy()
    if df.empty:
        print("No settled forecasts yet")
        return
    df["y"]=df.correct.astype(str).str.lower().eq("true").astype(int)
    df["probability"]=pd.to_numeric(df.probability,errors="coerce")
    df=df[df.probability.between(0,1)]
    df["brier"]=(df.probability-df.y)**2
    print(f"Settled forecasts: {len(df)}")
    print(f"Observed: {df.y.mean():.3%}; forecast: {df.probability.mean():.3%}; Brier: {df.brier.mean():.5f}")
    print("\nBy threshold:")
    for threshold in (1,2,3,4):
        g=df[df.threshold==threshold]
        if g.empty:
            print(f" {threshold}+: no settled predictions")
        else:
            print(f" {threshold}+: {g.y.sum()}/{len(g)} ({g.y.mean():.1%}), "
                  f"mean forecast {g.probability.mean():.1%}, Brier {g.brier.mean():.5f}")
    df["tier"]=pd.cut(df.probability,bins=[0,.5,.6,.7,.8,.9,1.000001],
        labels=["<50%","50-59%","60-69%","70-79%","80-89%","90%+"],
        right=False,include_lowest=True)
    print("\nBy confidence tier:")
    for tier,g in df.groupby("tier",observed=True):
        print(f" {tier}: {g.y.sum()}/{len(g)} ({g.y.mean():.1%}); "
              f"mean forecast {g.probability.mean():.1%}")
    if len(df)<100:
        print("\nINSUFFICIENT SAMPLE: do not fit a calibration transform yet.")
    return df

if __name__=="__main__":
    audit()
