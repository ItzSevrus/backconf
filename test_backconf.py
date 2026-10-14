import json, os, tempfile, unittest
from backconf_core import parse_duration, format_duration, atomic_json, read_json, BackconfError, validate_config, default_config

class CoreTests(unittest.TestCase):
    def test_duration(self):
        self.assertEqual(parse_duration('30m'),1800)
        self.assertEqual(parse_duration('2h'),7200)
        self.assertEqual(parse_duration('1d'),86400)
        self.assertEqual(format_duration(2700),'45m')
    def test_invalid_duration(self):
        for v in ('0m','5s','foo','2w','999999d'):
            with self.subTest(v=v), self.assertRaises(BackconfError): parse_duration(v)
    def test_atomic_json(self):
        with tempfile.TemporaryDirectory() as d:
            p=os.path.join(d,'state.json'); atomic_json(p,{'ok':True}); self.assertEqual(read_json(p),{'ok':True})
            self.assertEqual(os.stat(p).st_mode & 0o777,0o640)
    def test_config_validation(self):
        self.assertEqual(validate_config(default_config())['schema'],1)
        with self.assertRaises(BackconfError): validate_config({'interval_seconds':30})
    def test_bad_target(self):
        cfg=default_config(); cfg['targets']=[{'id':'x','path':'relative'}]
        with self.assertRaises(BackconfError): validate_config(cfg)

if __name__=='__main__': unittest.main()
