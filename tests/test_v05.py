import unittest,tempfile,pathlib,csv
from panel_integrity import validate_file,compatible
from causal_design import did_diagnostic
class TestV05(unittest.TestCase):
 def test_invalid_frequency_and_missing_unit(self):
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d)/'a.csv';p.write_text('provider,series,geo,period,value,unit,frequency\nx,y,USA,2020-03,2,,A\n')
   self.assertEqual(validate_file(p)['valid_rows'],0)
 def test_incompatible_units(self):
  a=dict(concept_id='employment',unit='percent',frequency='A',price_basis='NA',seasonal_adjustment='NSA')
  self.assertFalse(compatible(a,{**a,'unit':'persons'}))
 def test_did_detects_pretrend_and_does_not_claim_causality(self):
  rows=[]
  for g in ('USA','CAN'):
   for y in range(2010,2018):rows.append(dict(geo=g,year=y,outcome='jobs',value=(y-2010)*(2 if g=='USA' else 1)+(5 if y>=2015 and g=='USA' else 0)))
  r=did_diagnostic(rows,['USA'],2015,'jobs',4,3)
  self.assertFalse(r['causal']);self.assertNotEqual(r['pretrend_slope_gap'],0)
 def test_no_controls(self):
  self.assertEqual(did_diagnostic([],['USA'],2020,'jobs')['status'],'insufficient_balanced_groups')
if __name__=='__main__':unittest.main()
