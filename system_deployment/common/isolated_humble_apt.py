"""Resolve Humble ARM64 archives without installing or executing target code."""
import os
from pathlib import Path
import subprocess
import urllib.request


def prepare(work: Path) -> list[str]:
    for name in ('lists/partial', 'empty', 'keys'):
        (work / name).mkdir(parents=True, exist_ok=True)
    ubuntu_key = Path('/usr/share/keyrings/ubuntu-archive-keyring.gpg')
    if not ubuntu_key.is_file():
        raise RuntimeError('Install ubuntu-keyring on the build host.')
    ros_key = work / 'keys/ros.key'
    # Fetch the current official key: a build host's ROS key can predate rotation.
    with urllib.request.urlopen('https://raw.githubusercontent.com/ros/rosdistro/master/ros.key', timeout=60) as source:
        ros_key.write_bytes(source.read())
    # APT requires the extension to match the key encoding.
    key_suffix = '.asc' if ros_key.read_bytes().startswith(b'-----BEGIN') else '.gpg'
    actual_key = ros_key.with_suffix(key_suffix)
    ros_key.rename(actual_key)
    sources = work / 'sources.list'
    sources.write_text(
        ''.join(f'deb [arch=arm64 signed-by={ubuntu_key}] https://ports.ubuntu.com/ubuntu-ports {suite} main universe restricted multiverse\n'
                for suite in ('jammy', 'jammy-updates', 'jammy-security')) +
        f'deb [arch=arm64 signed-by={actual_key}] https://repo.huaweicloud.com/ros2/ubuntu jammy main\n')
    config = work / 'apt.conf'
    # Do not read host source lists, architecture lists, hooks or dpkg state.
    config.write_text('\n'.join([
        '#clear APT::Architectures;', 'APT::Architectures { "arm64"; };',
        '#clear APT::Update::Post-Invoke;', '#clear APT::Update::Post-Invoke-Success;',
        '#clear DPkg::Post-Invoke;', '#clear DPkg::Pre-Install-Pkgs;',
    ]) + '\n')
    options = ['-c', str(config)]
    values = {
        'APT::Architecture': 'arm64', 'Dir::Etc::sourcelist': str(sources),
        'Dir::Etc::sourceparts': str(work / 'empty'),
        'Dir::Etc::preferences': str(work / 'empty/preferences'),
        'Dir::Etc::preferencesparts': str(work / 'empty'),
        'Dir::State::lists': str(work / 'lists'),
        'Dir::State::extended_states': str(work / 'extended_states'),
        'Dir::Cache::pkgcache': '', 'Dir::Cache::srcpkgcache': '',
        'Acquire::Languages': 'none', 'Acquire::Retries': '3',
        'APT::Update::Error-Mode': 'any',
        'Acquire::IndexTargets::deb::DEP-11::DefaultEnabled': 'false',
        'Acquire::IndexTargets::deb::DEP-11-icons-small::DefaultEnabled': 'false',
        'Acquire::IndexTargets::deb::DEP-11-icons::DefaultEnabled': 'false',
        'Acquire::IndexTargets::deb::DEP-11-icons-hidpi::DefaultEnabled': 'false',
        'Acquire::IndexTargets::deb::CNF::DefaultEnabled': 'false',
        'APT::Sandbox::User': __import__('pwd').getpwuid(os.getuid()).pw_name,
    }
    for protocol in ('http', 'https'):
        proxy = os.environ.get(protocol + '_proxy') or os.environ.get(protocol.upper() + '_PROXY')
        if proxy:
            # Stored in private work directory; never printed as command arguments.
            if any(c in proxy for c in ('"', '\n', '\r', '\\')):
                raise RuntimeError('Unsupported proxy URL characters')
            with config.open('a') as output:
                output.write(f'Acquire::{protocol}::Proxy "{proxy}";\n')
    for key, value in values.items():
        options.extend(['-o', f'{key}={value}'])
    subprocess.run(['apt-get', *options, 'update'], check=True)
    return options
