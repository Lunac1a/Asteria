"""Deterministic engineering regressions; semantic quality is measured separately."""
import math
import unittest

from app.services.retrieval_scoring import lexical_terms, rank_candidates


def candidate(key, score, content):
    return {'id':key,'content':content,'vector':[score, math.sqrt(1-score*score)]}


class RetrievalScoringTests(unittest.TestCase):
    def test_near_threshold_requires_real_informative_overlap(self):
        query='According to Example.pdf page 2, what happens when h(n) is zero everywhere? Keep the answer brief and cite the document.'
        rows=[candidate('supported',.32,'If h(n) is zero everywhere, f(n) equals g(n).'),
              candidate('weak',.33,'A search example with h(n).'),
              candidate('far',.20,'h(n) is zero everywhere.'),
              candidate('dense',.6,'A semantic candidate.')]
        result=rank_candidates([1,0],rows,query,['Example.pdf'],.35,lexical_rescue=True)
        self.assertEqual([r['id'] for r in result if r['accepted']],['dense','supported'])
        self.assertEqual(next(r['reason'] for r in result if r['id']=='supported'),'lexical_rescue')
        baseline=rank_candidates([1,0],rows,query,['Example.pdf'],.35)
        self.assertEqual([r['id'] for r in baseline if r['accepted']],['dense'])

    def test_filename_page_and_generic_instructions_do_not_rescue(self):
        query='According to Example.pdf page 2, please give a brief answer and cite the document.'
        rows=[candidate('metadata',.34,query)]
        self.assertFalse(rank_candidates([1,0],rows,query,['Example.pdf'],.35,lexical_rescue=True)[0]['accepted'])
        self.assertNotIn('example',lexical_terms(query,['Example.pdf']))

    def test_coverage_prevents_small_overlap_in_a_long_query(self):
        query='chlorophyll glucose mitochondria wavelength temperature rainfall altitude nitrogen enzyme'
        rows=[candidate('partial',.34,'chlorophyll glucose mitochondria')]
        self.assertFalse(rank_candidates([1,0],rows,query,[],.35,lexical_rescue=True)[0]['accepted'])

    def test_chinese_and_formula_terms_without_answer_synonyms(self):
        terms=lexical_terms('请根据 Example.pdf 第2页，绿色色素吸收阳光 h(n)。',['Example.pdf'])
        self.assertIn('h(n)',terms);self.assertIn('绿色',terms);self.assertNotIn('example',terms)
        self.assertNotIn('uniform',lexical_terms('A-star with zero estimate'))

    def test_dense_order_ties_dimension_and_nonfinite_guards(self):
        rows=[candidate('b',.5,'one'),candidate('a',.5,'two'),{'id':'bad','content':'','vector':[1]},
              {'id':'nan','content':'','vector':[float('nan'),0]}]
        result=rank_candidates([1,0],rows,'unrelated',[],.35,lexical_rescue=True)
        self.assertEqual([r['id'] for r in result],['a','b'])
        self.assertTrue(all(r['reason']=='dense' for r in result))

    def test_stricter_config_does_not_allow_fixed_floor_bypass(self):
        rows=[candidate('low',.32,'green pigment absorbs sunlight')]
        self.assertFalse(rank_candidates([1,0],rows,'green pigment absorbs sunlight',[],.6,lexical_rescue=True)[0]['accepted'])
