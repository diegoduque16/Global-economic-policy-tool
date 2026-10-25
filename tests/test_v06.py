import sys,unittest,pathlib
import numpy as np,pandas as pd
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from causal_engine import local_projection, future_shock_placebo
from policy_events import load_events,validate_event
from evidence_gate import classify
class Tests(unittest.TestCase):
 def setUp(self):
  rng=np.random.default_rng(42);n=150;x=rng.normal(size=n);y=np.zeros(n)
  for i in range(1,n):y[i]=y[i-1]+.8*x[i]+rng.normal(scale=.2)
  self.d=pd.DataFrame({'period':range(n),'outcome':y,'shock':x})
 def test_estimation(self):
  r=local_projection(self.d);self.assertAlmostEqual(r['estimate'],.8,delta=.12);self.assertFalse(r['causal']);self.assertEqual(len(r['ci95']),2)
 def test_placebo(self):self.assertIn('estimate',future_shock_placebo(self.d))
 def test_duplicate_rejected(self):
  with self.assertRaises(ValueError):local_projection(pd.concat([self.d,self.d.iloc[[0]]]))
 def test_insufficient_rejected(self):
  with self.assertRaises(ValueError):local_projection(self.d.iloc[:10])
 def test_policy_sources(self):self.assertEqual(len(load_events(pathlib.Path(__file__).resolve().parents[1]/'data/policy_event_sources.csv')),4)
 def test_evidence_gate(self):self.assertFalse(classify({'estimate':1},replicated=True)['publish_as_causal'])
if __name__=='__main__':unittest.main()
