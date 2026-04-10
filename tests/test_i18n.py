import ast
import unittest
from pathlib import Path
from string import Formatter

from ir_graphrag.i18n import ZH, normalize_language, translate


class TranslationTests(unittest.TestCase):
    def test_default_fallback_and_placeholder_parity(self):
        self.assertEqual(normalize_language(None), 'en')
        self.assertEqual(normalize_language('unknown'), 'en')
        self.assertEqual(translate('Hello {name}', 'zh', name='世界'), 'Hello 世界')
        for english, chinese in ZH.items():
            fields = lambda text: {key for _, key, _, _ in Formatter().parse(text) if key is not None}
            with self.subTest(message=english):
                self.assertEqual(fields(english), fields(chinese))
                values = {key: '{source text}' for key in fields(english)}
                self.assertEqual(translate(english, **values), english.format(**values))
                self.assertEqual(translate(english, 'zh', **values), chinese.format(**values))

    def test_ui_messages_have_chinese_translations(self):
        app = Path(__file__).resolve().parents[1] / 'app.py'
        tree = ast.parse(app.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 't':
                # Conditional source messages are also literal catalog entries.
                argument = node.args[0]
                literals = (argument.body, argument.orelse) if isinstance(argument, ast.IfExp) else (argument,)
                for literal in literals:
                    if isinstance(literal, ast.Constant) and isinstance(literal.value, str):
                        self.assertIn(literal.value, ZH)


if __name__ == '__main__':
    unittest.main()
