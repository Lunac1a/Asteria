"""Number correction never substitutes for semantic evidence support."""

import copy
import json
import unittest
from unittest.mock import patch

from app.services.citation_service import check_mapping, reconcile_numbers
import test_citation_faithfulness as contracts

generated = contracts.generated
verdict = contracts.verdict


def case():
    text = "In the same transaction A reads 100; after COMMIT A reads 150. [1]"
    quote = "A reads 100. COMMIT. A reads 150."
    return dict(
        question="What does the experiment say?",
        answer=text,
        citations=[1],
        claims=[dict(text=text, citations=[1], quotes=[dict(citation=1, text=quote)])],
        sources=[
            dict(number=1, document_id="manual", content="Lost update experiment."),
            dict(number=2, document_id="manual", content=quote),
        ],
    )


class NumberingUnitTests(unittest.TestCase):
    def test_unique_quote_rebinds_without_mutating_evidence_or_facts(self):
        sample = case()
        snapshot = copy.deepcopy(sample)
        self.assertEqual(
            check_mapping(
                sample["answer"],
                sample["citations"],
                sample["claims"],
                sample["sources"],
            ),
            "quote_not_in_source",
        )
        result = reconcile_numbers(
            sample["answer"], sample["citations"], sample["claims"], sample["sources"]
        )
        self.assertEqual(result[0], sample["answer"].replace("[1]", "[2]"))
        self.assertEqual(result[1], [2])
        self.assertIsNone(check_mapping(*result, sample["sources"]))
        self.assertEqual(sample, snapshot)

    def test_ambiguous_cross_document_missing_identity_and_fake_quote_fail_closed(self):
        for variant in (
            "ambiguous",
            "cross_document",
            "no_identity",
            "invented",
            "unmapped",
            "invalid_shape",
        ):
            sample = case()
            if variant == "ambiguous":
                sample["sources"].append({**sample["sources"][1], "number": 3})
            elif variant == "cross_document":
                sample["sources"][1]["document_id"] = "other"
            elif variant == "no_identity":
                del sample["sources"][0]["document_id"]
            elif variant == "invented":
                sample["claims"][0]["quotes"][0]["text"] = "Invented passage"
            elif variant == "unmapped":
                sample["answer"] += " Extra unsupported assertion."
            else:
                sample["claims"].append(
                    dict(text="Invalid", citations=[1], quotes=None)
                )
            original = (sample["answer"], sample["citations"], sample["claims"])
            with self.subTest(variant=variant):
                self.assertEqual(
                    reconcile_numbers(*original, sample["sources"]), original
                )

    def test_swapped_numbers_use_one_pass(self):
        sample = case()
        sample["sources"][0]["content"] = "B waits for A."
        sample["answer"] += " B waits for A. [2]"
        claim = sample["claims"][0]
        claim["text"] = sample["answer"]
        claim["citations"].append(2)
        claim["quotes"].append(dict(citation=2, text="B waits for A."))
        sample["citations"] = [1, 2]
        result = reconcile_numbers(
            sample["answer"], sample["citations"], sample["claims"], sample["sources"]
        )
        self.assertEqual(
            result[0],
            sample["answer"]
            .replace("[1]", "[TEMP]")
            .replace("[2]", "[1]")
            .replace("[TEMP]", "[2]"),
        )
        self.assertEqual(result[1], [2, 1])
        self.assertIsNone(check_mapping(*result, sample["sources"]))

    def test_multiple_quotes_can_require_multiple_chunks(self):
        sample = case()
        sample["sources"][1]["content"] = "A reads 100."
        sample["sources"].append(
            dict(number=3, document_id="manual", content="A reads 150.")
        )
        sample["claims"][0]["quotes"] = [
            dict(citation=1, text=t) for t in ("A reads 100.", "A reads 150.")
        ]
        result = reconcile_numbers(
            sample["answer"], sample["citations"], sample["claims"], sample["sources"]
        )
        self.assertEqual(result[1], [2, 3])
        self.assertTrue(result[0].endswith("[2][3]"))
        self.assertIsNone(check_mapping(*result, sample["sources"]))


class NumberingPipelineTests(unittest.TestCase):
    setUp = contracts.CitationTests.setUp
    call = contracts.CitationTests.call

    def test_number_fix_still_requires_semantic_audit_of_corrected_source(self):
        for support in ("full", "partial", "none"):
            sample = case()
            corrected = {**sample, "citations": [2]}
            diagnostics = {}
            with (
                self.subTest(support=support),
                patch(
                    "app.services.rag_service.provider_completion",
                    side_effect=[generated(sample), verdict(corrected, support)],
                ) as provider,
            ):
                answer, sources, basis = self.call(
                    sample, citation_diagnostics=diagnostics
                )
                audit = json.loads(provider.call_args.args[3])
                self.assertEqual(
                    [s["number"] for s in audit["claims"][0]["sources"]], [2]
                )
                self.assertEqual(diagnostics["binding_outcome"], "repaired")
                if support == "full":
                    self.assertEqual(basis, "grounded")
                    self.assertEqual(sources, [sample["sources"][1]])
                    self.assertIn("[2]", answer)
                else:
                    self.assertEqual((sources, basis), ([], "insufficient"))

    def test_wrong_page_attribution_is_still_present_for_verifier_to_reject(self):
        sample = case()
        sample["sources"][0]["page"] = 1
        sample["sources"][1]["page"] = 2
        sample["answer"] = "Page 1 says A reads 100 and then 150. [1]"
        sample["claims"][0]["text"] = sample["answer"]
        with patch(
            "app.services.rag_service.provider_completion",
            side_effect=[
                generated(sample),
                verdict({**sample, "citations": [2]}, "none"),
            ],
        ) as provider:
            self.assertEqual(self.call(sample)[2], "insufficient")
        audited = json.loads(provider.call_args.args[3])["claims"][0]
        self.assertIn("Page 1", audited["text"])
        self.assertEqual(audited["sources"][0]["page"], 2)
