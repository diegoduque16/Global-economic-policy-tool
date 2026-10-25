import unittest,tempfile,csv,pathlib,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from analysis_engine import audit,within_country_association,build_report
from research_governance import preregister,assess_claim
from agent_workflow import run
class TestV04(unittest.TestCase):
 def test_duplicates(self):
  a={'provider':'world_bank','series':'x','geo':'USA','period':'2020','value':1,'unit':''}
  r=audit([a,{**a,'value':2}]);self.assertEqual(len(r['duplicate_keys']),1);self.assertTrue(r['duplicate_keys'][0]['conflicting_values'])
 def test_insufficient_data(self):self.assertEqual(within_country_association([],'world_bank','x','y')['status'],'insufficient_data')
 def test_no_unjustified_causality(self):self.assertFalse(assess_claim({'causal':True})['publishable'])
 def test_immutable_preregistration(self):
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d)/'spec.json';s={k:'test' for k in ['hypothesis','treatment','outcome','population','period','identification','confounders','placebo_tests','sensitivity_tests','data_sources']};preregister(s,p)
   with self.assertRaises(FileExistsError):preregister(s,p)
 def test_empty_workflow(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);r=run(root,root/'out');self.assertFalse(r['autonomous_agents_running']);self.assertEqual(r['observations'],0)
if __name__=='__main__':unittest.main()
