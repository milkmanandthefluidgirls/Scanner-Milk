"""Regression tests for multi-prop prospective snapshot and settlement."""
import unittest
import pandas as pd
from tracking import COLUMNS,append_prop_rows,settle_predictions

class MultiPropTrackingTests(unittest.TestCase):
    def test_each_stat_uses_its_own_boxscore_and_idempotent(self):
        rows=[]
        for prop,threshold in [("3PT made",3),("PTS",20),("REB",6),
                               ("AST",4),("STL",2),("BLK",1)]:
            rows.append(dict(prediction_date="2026-11-22",
                created_at_utc="2026-11-22T12:00:00+00:00",
                player_id="123",player="Example",team="BOS",prop=prop,
                threshold=threshold,side="NO",probability=.9,
                game_id=pd.NA,result=pd.NA,correct=pd.NA,settled_at_utc=pd.NA))
        base=pd.DataFrame(columns=COLUMNS)
        saved=append_prop_rows(base,pd.DataFrame(rows))
        self.assertEqual(len(saved),6)
        self.assertEqual(len(append_prop_rows(saved,pd.DataFrame(rows))),6)
        threes=pd.DataFrame([dict(PLAYER_ID="123",GAME_DATE="2026-11-22",
            GAME_ID="g",FG3M=2)])
        first=settle_predictions(saved,threes)
        self.assertEqual(int(first["correct"].notna().sum()),1)
        props=pd.DataFrame([dict(PLAYER_ID="123",GAME_DATE="2026-11-22",
            GAME_ID="g",FG3M=2,PTS=18,REB=8,AST=1,STL=0,BLK=0)])
        final=settle_predictions(first,props)
        self.assertEqual(int(final["correct"].notna().sum()),6)
        outcomes=dict(zip(final["prop"],final["correct"]))
        self.assertTrue(outcomes["PTS"])
        self.assertFalse(outcomes["REB"])
        self.assertTrue(outcomes["AST"])
    def test_missing_boxscore_never_becomes_loss(self):
        row=pd.DataFrame([dict(prediction_date="2026-11-22",
            created_at_utc="2026-11-22T12:00:00+00:00",
            player_id="123",player="Example",team="BOS",prop="PTS",
            threshold=20,side="NO",probability=.9,game_id=pd.NA,
            result=pd.NA,correct=pd.NA,settled_at_utc=pd.NA)])
        logs=pd.DataFrame([dict(PLAYER_ID="999",GAME_DATE="2026-11-22",
            GAME_ID="g",PTS=10)])
        self.assertTrue(pd.isna(settle_predictions(row,logs).iloc[0]["correct"]))

if __name__=="__main__":unittest.main()
