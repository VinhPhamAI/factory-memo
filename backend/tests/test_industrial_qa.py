import unittest
import json
import tempfile
from pathlib import Path

from backend.knowledge_bases.industrial_qa import SPEC
from backend.knowledge_bases.industrial_qa.pilot import build_parser, gold_questions, select
from backend.knowledge_bases.industrial_qa.schema import IndustrialQaExtraction


class IndustrialQaSpecTests(unittest.TestCase):
    def test_spec_owns_its_extraction_contract(self):
        self.assertEqual(SPEC.database, "kb_industrial_qa")
        self.assertIs(SPEC.extraction_output_model, IndustrialQaExtraction)
        self.assertEqual(SPEC.extraction_version, "industrial-qa-case-lesson-v1")
        self.assertIn("{RAW_CONVERSATION}", SPEC.extract_prompt)
        self.assertIn("industrial maintenance knowledge assistant", SPEC.agent_prompt)

    def test_spec_uses_only_the_portable_failure_mode_ontology(self):
        self.assertIsNotNone(SPEC.ontology)
        self.assertEqual(list(SPEC.ontology.keys), ["failure_mode"])
        self.assertIn("electrical_fault", SPEC.ontology.keys["failure_mode"].values)


class IndustrialQaPilotTests(unittest.TestCase):
    def test_selects_questions_and_distractors_only_from_train(self):
        with tempfile.TemporaryDirectory() as directory:
            args = build_parser().parse_args([
                "select", "--run-dir", directory, "--sample", "10", "--distractors", "5",
            ])
            run = select(args)
            train = gold_questions("train")
            self.assertEqual(run["split"], "train")
            self.assertEqual(len(run["question_ids"]), 10)
            self.assertEqual(len(run["distractor_conversation_ids"]), 5)
            self.assertTrue(set(run["question_ids"]) <= {q["question_id"] for q in train})
            self.assertTrue(set(run["conversation_ids"]) <= {q["conversation_id"] for q in train})
            self.assertEqual(select(args), run)

    def test_rejects_test_split_and_existing_test_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            parser = build_parser()
            with self.assertRaises(SystemExit):
                parser.parse_args(["select", "--run-dir", directory, "--split", "test"])
            args = parser.parse_args(["select", "--run-dir", directory])
            checkpoint = Path(directory) / "run.json"
            original = json.dumps({"split": "test"})
            checkpoint.write_text(original)
            with self.assertRaisesRegex(SystemExit, "train split"):
                select(args)
            self.assertEqual(checkpoint.read_text(), original)

    def test_rejects_test_questions_in_train_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            args = build_parser().parse_args(["select", "--run-dir", directory])
            question = gold_questions("test")[0]
            (Path(directory) / "run.json").write_text(json.dumps({
                "split": "train", "question_ids": [question["question_id"]],
                "conversation_ids": [question["conversation_id"]],
            }))
            with self.assertRaisesRegex(SystemExit, "outside the train split"):
                select(args)


if __name__ == "__main__":
    unittest.main()
