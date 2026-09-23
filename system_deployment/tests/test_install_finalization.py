"""Exercise generated finalization for every configured target without host changes."""
import importlib.util
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('builder_finalization', ROOT / 'one_stop/build_one_stop_package.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class InstallFinalizationTest(unittest.TestCase):
    def test_all_delivery_targets_finalize_for_both_models(self):
        for prefix in ('', 'special-wa-t-jk2-v1-'):
            config = builder.load_delivery(ROOT / f'one_stop/{prefix}package-urls.json',
                                           ROOT / f'one_stop/{prefix}supervisor.json')
            for target_id, target in config['targets'].items():
                for model in ('WA-T', 'JK2-V1'):
                    with self.subTest(delivery=prefix, target=target_id, model=model), tempfile.TemporaryDirectory() as tmp:
                        stage = Path(tmp)
                        checksums = []
                        builder.stage_config_files(stage, target_id, target, checksums, False)
                        startup, registrations, post, paths = builder.stage_supervisor_modules(stage, target_id, target, checksums, False)
                        agent = builder.stage_supervisor_agent(stage, target_id, paths, checksums, False)
                        services = builder.supervisor_systemd_services(target_id, target)
                        services.extend(target.get('managed_services', []))
                        services.extend(item[0] for item in startup)
                        services.extend(shlex.split(command)[2] for command in post
                                        if command.startswith('systemctl restart '))
                        filters = {item[0]: item[5] for item in startup}
                        script = builder.target_install(
                            target_id, 'config', '', None, [], [], services,
                            supervisor_startup=startup, registrations=registrations,
                            post_install=post, agent_service=paths, agent_payload=agent,
                            service_robot_types=filters, release_tracking=True,
                        )
                        install = stage / f'targets/{target_id}/install.sh'
                        install.write_text(script)
                        builder.validate_staged_shell_scripts(stage)
                        tail = script[script.index("install_stage='installing configuration files'"):]
                        # Every mutating command is a shell stub, also exported to config children.
                        prelude = '''set -Eeuo pipefail
install() { :; }
cp() { :; }
chown() { :; }
rm() { :; }
python3() { :; }
systemctl() { printf 'SYSTEMCTL %s\\n' "$*"; }
navi_release() { :; }
export -f install cp chown rm python3 systemctl
'''
                        prelude += f'root={shlex.quote(tmp)}\nrobot_type={shlex.quote(model)}\n'
                        prelude += 'managed_services=(' + ' '.join(shlex.quote(x) for x in services) + ')\n'
                        # Keep registration directory lookups within the temporary workspace.
                        tail = tail.replace('/etc/', tmp + '/etc/')
                        result = subprocess.run(['bash', '-c', prelude + tail], capture_output=True, text=True)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        for service, allowed in filters.items():
                            self.assertEqual('SYSTEMCTL restart ' + service in result.stdout,
                                             not allowed or model in allowed, service)
                        if agent:
                            self.assertIn('SYSTEMCTL restart ' + agent['service'], result.stdout)

    def test_build_guard_rejects_invalid_service_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp)
            script = stage / 'targets/test/install.sh'
            script.parent.mkdir(parents=True)
            script.write_text('case "$unit" in x) if true; then continue ;; fi ;; esac\n')
            with self.assertRaisesRegex(builder.BuildError, 'invalid shell script'):
                builder.validate_staged_shell_scripts(stage)
