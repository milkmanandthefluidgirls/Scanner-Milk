"""Regression checks for independent opportunity accounting. Run: python -m unittest test_strategy_lab.py"""
import unittest
import pandas as pd
from strategy_lab import prepare,representative,independent,clusters,simulate

class LabTests(unittest.TestCase):
    def setUp(self):
        self.raw=pd.DataFrame([
          dict(prediction_date="2026-11-19",player_id="1",player="A",prop="3PT made",
               threshold=t,probability=p,correct=True,game_id="g1",
               created_at_utc="2026-11-19T12:00:00Z")
          for t,p in [(5,.93),(8,.9999),(12,1.0)]]
          +[dict(prediction_date="2026-11-19",player_id="2",player="B",prop="PTS",
               threshold=20,probability=.91,correct=False,game_id="g1",
               created_at_utc="2026-11-19T12:00:00Z")])
    def test_overlap(self):
        d=prepare(self.raw)
        self.assertEqual(len(d),4)
        self.assertEqual(len(independent(d)),2)
        self.assertEqual(len(representative(d)),2)
        self.assertEqual(int(representative(d).query("player_id == '1'").iloc[0].threshold),5)
        self.assertEqual(len(clusters(d)),2)
    def test_no_fabricated_profit(self):
        trades,message=simulate(prepare(self.raw),None)
        self.assertTrue(trades.empty)
        self.assertIn("UNAVAILABLE",message)
    def test_no_future_quote(self):
        d=prepare(self.raw)
        q=pd.DataFrame([dict(prediction_date="2026-11-19",player_id="1",
            prop="3PT made",threshold=5,no_ask_cents=70,
            quoted_at_utc="2026-11-19T20:00:00Z",
            game_start_utc="2026-11-19T19:00:00Z")])
        trades,message=simulate(d,q,fee_dollars=.01)
        self.assertTrue(trades.empty)
if __name__=="__main__":unittest.main()
