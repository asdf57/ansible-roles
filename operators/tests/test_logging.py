import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import run_ansible, run_command


class LoggingTests(unittest.TestCase):

    def test_generic_command_uses_literal_argv_working_directory_and_supplied_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            diagnostic = directory / 'command.log'
            output = io.StringIO()
            literal = 'spaces $NO_EXPANSION; $(not-a-command)'
            script = "import os,sys; print(os.getcwd()); print(sys.argv[1]); print(os.environ['FIXTURE_VALUE'])"
            with contextlib.redirect_stdout(output):
                result = run_command([sys.executable, '-c', script, literal], diagnostic, 5,
                                     cwd=directory, environment={'FIXTURE_VALUE': 'runner-context'})
            self.assertEqual(result.returncode, 0)
            self.assertEqual(output.getvalue(), f'{directory}\n{literal}\nrunner-context\n')
            self.assertEqual(diagnostic.stat().st_mode & 0o777, 0o600)

    def test_standard_output_is_streamed_and_private_diagnostic_is_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            diagnostic = Path(tmp) / 'private' / 'stage.log'
            output = io.StringIO()
            script = "import os; print('TASK [visible progress]', flush=True); print(os.environ['ANSIBLE_STDOUT_CALLBACK']); print(os.environ['ANSIBLE_DISPLAY_ARGS_TO_STDOUT']); print(os.environ['ANSIBLE_DISPLAY_SKIPPED_HOSTS']); print(os.environ['ANSIBLE_SSH_USETTY']); print(os.environ['ANSIBLE_INJECT_FACT_VARS']); print(os.environ['ANSIBLE_CALLBACK_RESULT_FORMAT'])"
            with contextlib.redirect_stdout(output):
                result = run_ansible([sys.executable, '-u', '-c', script], diagnostic, 5)
            self.assertEqual(result.returncode, 0)
            self.assertIn('TASK [visible progress]', output.getvalue())
            self.assertEqual(output.getvalue(), diagnostic.read_text())
            self.assertIn('default\nfalse\nfalse\nfalse\nfalse\nyaml\n', output.getvalue())
            self.assertEqual(os.stat(diagnostic).st_mode & 0o777, 0o600)

    def test_real_warnings_and_failure_exit_are_not_filtered(self):
        with tempfile.TemporaryDirectory() as tmp:
            diagnostic = Path(tmp) / 'warning.log'
            output = io.StringIO()
            script = "import sys; print('[WARNING]: useful diagnostic', flush=True); print('fatal: safety failure', flush=True); sys.exit(2)"
            with contextlib.redirect_stdout(output):
                result = run_ansible([sys.executable, '-u', '-c', script], diagnostic, 5)
            self.assertEqual(result.returncode, 2)
            self.assertIn('[WARNING]: useful diagnostic', output.getvalue())
            self.assertIn('fatal: safety failure', output.getvalue())
            self.assertEqual(output.getvalue(), diagnostic.read_text())
