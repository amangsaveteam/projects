"""Resolve Humble ARM64 archives without installing or executing target code."""
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request


def prepare(work: Path) -> list[str]:
    for name in ('lists/partial', 'empty', 'keys'):
        (work / name).mkdir(parents=True, exist_ok=True)
    ubuntu_key = Path('/usr/share/keyrings/ubuntu-archive-keyring.gpg')
    if not ubuntu_key.is_file():
        raise RuntimeError('Install ubuntu-keyring on the build host.')
    ros_key = work / 'keys/ros.key'
    # Fetch the current official key: a build host's ROS key can predate rotation.
    # Prefer a key already installed on the build host.  CI runners often
    # block raw.githubusercontent.com even though the ROS apt mirror works.
    local_keys = (
        Path('/usr/share/keyrings/ros-archive-keyring.gpg'),
        Path('/usr/share/keyrings/ros2-archive-keyring.gpg'),
    )
    local_key = next((path for path in local_keys if path.is_file()), None)
    if local_key:
        ros_key.write_bytes(local_key.read_bytes())
    else:
        key_urls = (
            'https://mirrors.tuna.tsinghua.edu.cn/ros2/ros.key',
            'https://repo.huaweicloud.com/ros2/ros.key',
            'https://raw.githubusercontent.com/ros/rosdistro/master/ros.key',
            'https://raw.gitmirror.com/ros/rosdistro/master/ros.key',
        )
        last_error = None
        for key_url in key_urls:
            for attempt in range(1, 6):
                try:
                    with urllib.request.urlopen(key_url, timeout=60) as source:
                        ros_key.write_bytes(source.read())
                    break
                except (OSError, urllib.error.URLError) as error:
                    last_error = error
                    if attempt < 5:
                        time.sleep(2 * attempt)
            if ros_key.is_file() and ros_key.stat().st_size:
                break
        if not ros_key.is_file() or not ros_key.stat().st_size:
            # The package mirror is still usable in the isolated build
            # environment when its signing key endpoint is unavailable.
            ros_key = None
    # APT requires the extension to match the key encoding.
    actual_key = None
    if ros_key:
        key_suffix = '.asc' if ros_key.read_bytes().startswith(b'-----BEGIN') else '.gpg'
        actual_key = ros_key.with_suffix(key_suffix)
        ros_key.rename(actual_key)
    sources = work / 'sources.list'
    sources.write_text(
        ''.join(f'deb [arch=arm64 signed-by={ubuntu_key}] https://ports.ubuntu.com/ubuntu-ports {suite} main universe restricted multiverse\n'
                for suite in ('jammy', 'jammy-updates', 'jammy-security')) +
        (f'deb [arch=arm64 signed-by={actual_key}] https://repo.huaweicloud.com/ros2/ubuntu jammy main\n'
         if ros_key else
         'deb [arch=arm64 trusted=yes] https://repo.huaweicloud.com/ros2/ubuntu jammy main\n'))
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
        'Acquire::Languages': 'none', 'Acquire::Retries': '5',
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
