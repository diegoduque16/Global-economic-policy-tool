import unittest,sys,pathlib
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from research import profile,descriptive,WDI
class ResearchTests(unittest.TestCase):
 def test_empty_data(self):
  self.assertEqual(descriptive([]),[])
  self.assertTrue(all(x['observations']==0 for x in profile([])))
 def test_correlations_not_causal(self):
  rows=[]
  for y in range(2000,2020):
   rows.extend([{'country':'USA','year':y,'indicator':WDI['employment_ratio'],'value':y/100}, {'country':'USA','year':y,'indicator':WDI['gdp_growth'],'value':y-1990}])
  out=descriptive(rows)
  self.assertEqual(len(out),1)
  self.assertIn('DESCRIPTIVE ONLY',out[0]['interpretation'])
 def test_indicator_codes_unique(self):self.assertEqual(len(WDI),len(set(WDI.values())))
if __name__=='__main__':unittest.main()
