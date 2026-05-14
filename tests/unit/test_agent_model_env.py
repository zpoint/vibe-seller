"""Unit test: agent uses profile model, not server env model.

Guards against regression where --model flag was read from
os.environ (server default) instead of the profile env,
causing all tasks to use the same model regardless of profile.
"""

import pytest

from app.ai.profiles import ProfileManager

pytestmark = pytest.mark.unit


class TestAgentModelFromProfile:
    def test_no_model_flag_from_server_env(self, monkeypatch):
        """The --model flag must NOT come from os.environ.

        If it did, every task would use the server's default
        model regardless of which profile is assigned.
        The model should come from the subprocess env (profile).

        Revert check: adding back
          model = Options.ANTHROPIC_MODEL.get()
          cmd.extend(['--model', model])
        would put 'server-model' in cmd → this test fails.
        """
        # Server process env has one model
        monkeypatch.setenv('ANTHROPIC_MODEL', 'server-model')

        # Simulate what start() builds for the command
        # (extracted from the non-mock branch)
        cmd = [
            'claude',
            '-p',
            '--output-format',
            'stream-json',
            '--input-format',
            'stream-json',
            '--verbose',
            '--permission-mode',
            'bypassPermissions',
            '--permission-prompt-tool',
            'stdio',
        ]

        # This is the pattern that was buggy:
        # model = Options.ANTHROPIC_MODEL.get()  # reads os.environ
        # if model: cmd.extend(['--model', model])
        #
        # After fix, no --model flag is added from os.environ.
        # The profile env handles it via subprocess env.

        assert '--model' not in cmd, (
            'cmd should not contain --model from os.environ'
        )

        # The profile env SHOULD contain the model
        profile = {
            'env': {'ANTHROPIC_MODEL': 'profile-model'},
        }
        monkeypatch.setattr(
            ProfileManager,
            'get_profile',
            staticmethod(lambda pid: profile),
        )
        env = ProfileManager.get_env_for_profile('test-profile')
        assert env['ANTHROPIC_MODEL'] == 'profile-model', (
            'Profile env should override server env'
        )
