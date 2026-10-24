"""Offline checks for the benchmark input contract; no performance execution."""
import importlib.util
from pathlib import Path
import re
import unittest

spec = importlib.util.spec_from_file_location('d1', Path(__file__).parent / 'experiments/rag_c1_dataset.py')
d1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d1)
analysis_spec = importlib.util.spec_from_file_location('analysis', Path(__file__).parent / 'experiments/rag_c1_analysis.py')
analysis = importlib.util.module_from_spec(analysis_spec)
analysis_spec.loader.exec_module(analysis)


class DatasetTests(unittest.TestCase):
    def test_database_model_identity_requires_backend_namespace(self):
        d1.verify_index_identity([('fastembed:model-x', 'ready', 10)], 'fastembed', 'model-x')
        for bad in [('model-x', 'ready', 10), ('test:model-x', 'ready', 10),
                    ('fastembed:model-x', 'failed', 10), ('fastembed:model-x', 'ready', 9)]:
            with self.assertRaises(AssertionError):
                d1.verify_index_identity([bad], 'fastembed', 'model-x')

    def test_percentile_thresholds(self):
        self.assertNotIn('p95', analysis.distribution(list(range(199))))
        self.assertIn('p95', analysis.distribution(list(range(200))))
        self.assertNotIn('p99', analysis.distribution(list(range(999))))

    def test_rejection_timeout_and_drain_are_not_successful_goodput(self):
        rows = [dict(status=200, end=2, elapsed_ms=5, database_ok=True, oracle_ok=True),
                dict(status=200, end=11, elapsed_ms=5, database_ok=True, oracle_ok=True),
                dict(status=429, end=3, elapsed_ms=1),
                dict(error='timeout', end=90, elapsed_ms=90000, database_ok=True)]
        result = analysis.summarize(rows, 0, 10)
        self.assertEqual(result['goodput'], .1)
        self.assertEqual(result['successful'], 2)
        self.assertEqual(result['sent'], 4)
        self.assertEqual(result['rejected'], 1)
        self.assertEqual(result['timeouts'], 1)
        self.assertEqual(result['latency_all_http_ms']['n'], 3)

    def test_deterministic(self):
        self.assertEqual(d1.dataset(), d1.dataset())

    def test_size_and_oracle(self):
        data = d1.dataset()
        self.assertEqual(len(data['documents']), 20)
        self.assertEqual(len(data['queries']), 60)
        self.assertEqual(len({q['question'] for q in data['queries']}), 60)
        for doc in data['documents']:
            self.assertEqual(len(doc['content']), 8000)
            self.assertEqual(re.sub(r'\s+', ' ', doc['content']).strip(), doc['content'])
            self.assertEqual(len(doc['chunk_hashes']), 10)
        for q in data['queries']:
            doc = next(d for d in data['documents'] if d['name'] == q['document'])
            self.assertTrue(q['allowed_ordinals'])
            for n in q['allowed_ordinals']:
                self.assertIn(q['fact'], doc['content'][800*n:800*n+1000])


if __name__ == '__main__':
    unittest.main()
