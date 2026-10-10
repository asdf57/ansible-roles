"""Real callback checks: concise successes, visible assertion failures, no SSH."""
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import run_ansible, write_private


def main():
    """Run only local assertions and facts; never connect to managed nodes."""
    with tempfile.TemporaryDirectory(prefix='ansible-output-') as temporary:
        directory = Path(temporary)
        os.environ['ANSIBLE_LOCAL_TEMP'] = str(directory / 'local')
        os.environ['ANSIBLE_REMOTE_TEMP'] = str(directory / 'remote')
        play = directory / 'logging.json'
        tasks = [{
            'name': 'Visible successful safety check',
            'ansible.builtin.assert': {
                'that': ['ansible_facts.distribution | length > 0'],
                'quiet': True
            }
        }, {
            'name': 'Unused stage must not clutter output',
            'ansible.builtin.fail': {
                'msg': 'Must never run'
            },
            'when': False
        }]
        for failing in (False, True):
            selected = list(tasks)
            if failing:
                selected.append({
                    'name': 'Visible failed safety check',
                    'ansible.builtin.assert': {
                        'that': [False],
                        'quiet': True,
                        'fail_msg': 'Explicit disk safety failure'
                    }
                })
            write_private(
                play,
                json.dumps([{
                    'name': 'Logging contract',
                    'hosts': 'all',
                    'gather_facts': True,
                    'vars': {
                        'ansible_python_interpreter': sys.executable
                    },
                    'tasks': selected
                }]))
            result = run_ansible(['ansible-playbook', '-i', 'localhost,', '-c', 'local',
                                  str(play)], directory / 'output.log', 60)
            assert result.returncode == (2 if failing else 0), result.stdout
            assert 'Visible successful safety check' in result.stdout
            assert 'PLAY RECAP' in result.stdout
            assert 'All assertions passed' not in result.stdout
            assert 'Unused stage must not clutter output' not in result.stdout
            assert '[DEPRECATION WARNING]' not in result.stdout
            if failing:
                assert 'Explicit disk safety failure' in result.stdout
                assert 'failed=1' in result.stdout
        print('PASS: concise successes and skipped stages; failures remain visible')


if __name__ == '__main__':
    main()
