import importlib.util
import tempfile
import unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('batch',Path(__file__).resolve().parents[1]/'tools/campaign_batch.py')
batch=importlib.util.module_from_spec(spec); spec.loader.exec_module(batch)
class CampaignTests(unittest.TestCase):
    def test_preserves_destination_parameters_and_labels_prepared_only(self):
        with tempfile.TemporaryDirectory() as out:
            result=batch.package('https://tools.test.org/cost?currency=AUD',out,video=False)
            self.assertEqual(len(result['assets']),4)
            self.assertTrue(all(p['status']=='prepared' and p['conversions'] is None for p in result['posts']))
            self.assertIn('currency=AUD',result['posts'][0]['destination'])
            self.assertIn('utm_source=pinterest',result['posts'][0]['destination'])
    def test_unapproved_affiliate_and_placeholder_rejected(self):
        with tempfile.TemporaryDirectory() as out:
            with self.assertRaises(ValueError): batch.package('https://example.com',out,video=False)
            with self.assertRaises(ValueError): batch.package('https://merchant.org/ref',out,affiliate=True,video=False)
    def test_signed_affiliate_link_unchanged_and_disclosed(self):
        with tempfile.TemporaryDirectory() as out:
            url='https://merchant.org/ref?signature=opaque'
            result=batch.package(url,out,affiliate=True,approved=True,video=False)
            self.assertTrue(all(p['destination']==url and p['caption'].startswith('Ad / affiliate') for p in result['posts']))
