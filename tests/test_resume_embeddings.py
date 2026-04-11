import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pyarrow as pa
from graphrag_llm.tokenizer.tiktoken_tokenizer import TiktokenTokenizer

from scripts.resume_embeddings import preflight, validate_vectors


class ResumeEmbeddingsTests(unittest.TestCase):
    def test_vector_validation_checks_ids_shape_and_values(self):
        cases = [
            ('duplicate IDs', ['a', 'a'], [[1, 0], [0, 1]], 2),
            ('missing ID', ['a'], [[1, 0]], 2),
            ('wrong dimension', ['a', 'b'], [[1, 0, 0], [0, 1, 0]], 3),
            ('NaN', ['a', 'b'], [[float('nan'), 0], [0, 1]], 2),
            ('infinity', ['a', 'b'], [[float('inf'), 0], [0, 1]], 2),
            ('zero vector', ['a', 'b'], [[0, 0], [0, 1]], 2),
            ('null vector', ['a', 'b'], [None, [0, 1]], 2),
            ('null component', ['a', 'b'], [[1, None], [0, 1]], 2),
        ]
        for label, ids, vectors, width in cases:
            with self.subTest(label=label):
                table = pa.table({'id': ids, 'vector': pa.array(vectors, type=pa.list_(pa.float32(), width))})
                with self.assertRaises(ValueError):
                    validate_vectors(table, ['a', 'b'], 2)

        table = pa.table({'id': ['b', 'a'], 'vector': pa.array([[1, 0], [0, 1]], type=pa.list_(pa.float32(), 2))})
        self.assertEqual(validate_vectors(table, ['a', 'b'], 2), 2)
        duplicated = table.set_column(0, 'id', pa.array(['a', 'a']))
        with self.assertRaises(ValueError):
            validate_vectors(duplicated, ['a', 'a'], 2)

    def test_preflight_splits_long_reports_without_changing_source(self):
        long_text = 'Revenue was $123.45 million, an increase of 12% versus the prior year. ' * 300
        tables = {
            'community_reports': pd.DataFrame({'full_content': [long_text]}),
            'text_units': pd.DataFrame({'text': ['Revenue increased.']}),
        }
        originals = {name: table.copy(deep=True) for name, table in tables.items()}
        config = SimpleNamespace(
            embedding_models={'embedding': object()},
            embed_text=SimpleNamespace(embedding_model_id='embedding', batch_size=8, batch_max_tokens=8000),
            concurrent_requests=2,
        )
        tokenizer = TiktokenTokenizer(encoding_name='cl100k_base')
        self.assertGreater(len(long_text), 8192)
        self.assertLess(tokenizer.num_tokens(long_text), 8000)
        with patch('graphrag_llm.embedding.create_embedding', return_value=SimpleNamespace(tokenizer=tokenizer)):
            with self.assertRaisesRegex(ValueError, 'gateway character limit'):
                preflight(config, tables)
            config.embed_text.batch_max_tokens = 1200
            result = preflight(config, tables)

        self.assertGreater(result['community_reports']['snippets'], 1)
        for stats in result.values():
            self.assertLessEqual(stats['max_input_chars'], 8192)
            self.assertLessEqual(stats['max_batch_chars'], 8192)
        for name, table in tables.items():
            pd.testing.assert_frame_equal(table, originals[name])


if __name__ == '__main__':
    unittest.main()
