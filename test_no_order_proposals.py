import unittest
import pandas as pd
from no_order_proposals import propose_no_orders

class ProposalTests(unittest.TestCase):
    def setUp(self):
        self.quotes=pd.DataFrame([
            {"player":"A","prop":"PTS","threshold":20,"kalshi_ticker":"A",
             "verification":"MATCHED — QUOTED","model_no_pct":96,
             "kalshi_no_ask_cents":80,"estimated_edge_pp":16},
            {"player":"B","prop":"REB","threshold":6,"kalshi_ticker":"B",
             "verification":"MATCHED — QUOTED","model_no_pct":94,
             "kalshi_no_ask_cents":85,"estimated_edge_pp":9},
            {"player":"C","prop":"AST","threshold":4,"kalshi_ticker":"C",
             "verification":"MATCHED — QUOTED","model_no_pct":93,
             "kalshi_no_ask_cents":88,"estimated_edge_pp":5},
        ])
    def test_unlimited_picks_and_budget(self):
        p=propose_no_orders(self.quotes,min_probability=.90,
             min_gross_edge_pp=5,max_price_cents=90,budget_dollars=10)
        self.assertEqual(len(p),3)
        self.assertLessEqual(p.max_cost_dollars.sum(),10)
    def test_budget_limits_selection(self):
        p=propose_no_orders(self.quotes,min_probability=.90,
             min_gross_edge_pp=5,max_price_cents=90,budget_dollars=1)
        self.assertEqual(len(p),1)
    def test_remove_and_replenish(self):
        p=propose_no_orders(self.quotes[self.quotes.kalshi_ticker!="A"],
             min_probability=.90,min_gross_edge_pp=5,max_price_cents=90,budget_dollars=10)
        self.assertEqual(set(p.kalshi_ticker),{"B","C"})
    def test_empty(self):
        p=propose_no_orders(self.quotes,min_probability=.99)
        self.assertTrue(p.empty)

if __name__=="__main__":unittest.main()
