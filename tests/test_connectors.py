import sys, pathlib, unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import connectors
class ConnectorsTest(unittest.TestCase):
 def test_registry(self):
  self.assertGreaterEqual(len(connectors.REGISTRY),15)
 def test_jsonstat(self):
  o={'id':['geo','time'],'size':[2,2],'dimension':{'geo':{'category':{'index':{'US':0,'DE':1}}},'time':{'category':{'index':{'2023':0,'2024':1}}}},'value':{'0':1,'3':4}}
  rows=connectors.jsonstat_rows(o,'test')
  self.assertEqual([(r['geo'],r['period'],r['value']) for r in rows],[('US','2023',1),('DE','2024',4)])
 def test_record_provenance(self):
  x=connectors.record('test','series','USA',2024,3,metadata={'original_series':'ABC'})
  self.assertIn('ABC',x['metadata'])
if __name__=='__main__':unittest.main()
