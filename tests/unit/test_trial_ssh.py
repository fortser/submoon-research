"""SSH/SFTP эмулятор: только локальные байты, без подключения и нагрузки CPU."""
import hashlib
import io
import json

import pytest

from submoon_research.compute.hardware_trial import TrialError
from submoon_research.compute.trial_transport import MAX_HARDWARE, SSHProbe


def hardware():
    return dict(complete=True, checks=dict(cpu_work=True, memory_sample=True, disk_sample=True),
                limitations=['synthetic_only'], affinity=list(range(64)), topology_known=True,
                physical_groups=[{}] * 32, cgroup={'cpu.max': '6400000 100000', 'memory.max': '257061000000'},
                meminfo='MemTotal: 251100000 kB\n',
                lscpu=dict(returncode=0, stdout=json.dumps(dict(lscpu=[dict(
                    field='Model name:', data='AMD Ryzen Threadripper PRO 9975WX 32-Cores')]))))


class Channel:
    def __init__(self, code=0):
        self.code = code

    def settimeout(self, value):
        assert value > 0

    def recv_exit_status(self):
        return self.code


class Output(io.BytesIO):
    def __init__(self, content=b'', code=0):
        super().__init__(content)
        self.channel = Channel(code)


class Writer(io.BytesIO):
    def __init__(self, files, path):
        super().__init__()
        self.files, self.path = files, path

    def close(self):
        if not self.closed:
            self.files[self.path] = self.getvalue()
        super().close()


class SFTP:
    def __init__(self, owner):
        self.owner = owner
        self.closed = False

    def get_channel(self):
        return Channel()

    def open(self, path, mode):
        if mode == 'wx':
            assert path not in self.owner.files
            return Writer(self.owner.files, path)
        if self.owner.download_fails:
            raise OSError('TOP_SECRET')
        return io.BytesIO(self.owner.files[path])

    def close(self):
        self.closed = True


class Client:
    def __init__(self):
        self.files = {}
        self.calls = []
        self.sftp = SFTP(self)
        self.closed = False
        self.fail_connect = False
        self.probe_fails = False
        self.download_fails = False
        self.corrupt_upload = False
        self.corrupt_download = False
        self.oversize = False
        self.collision = False

    def set_missing_host_key_policy(self, policy):
        pass

    def connect(self, *args, **kwargs):
        self.calls.append(('connect', kwargs))
        if self.fail_connect:
            raise TimeoutError('TOP_SECRET')

    def save_host_keys(self, path):
        from pathlib import Path
        Path(path).write_text('synthetic known_hosts')

    def exec_command(self, command, timeout):
        self.calls.append(('command', command))
        if command.startswith('test ! -e'):
            return None, Output(code=1 if self.collision else 0), Output()
        if command.startswith('sha256sum'):
            path = command.split()[-1]
            value = hashlib.sha256(self.files[path]).hexdigest()
            if (path.endswith('probe.py') and self.corrupt_upload) or (
                    path.endswith('hardware.json') and self.corrupt_download):
                value = '0' * 64
            return None, Output((value + '  file\n').encode()), Output()
        if command.startswith('timeout '):
            root = command.split('python3 ')[1].split('/probe.py')[0]
            self.files[root + '/hardware.json'] = b'x' * (MAX_HARDWARE + 1) if self.oversize else json.dumps(hardware()).encode()
            self.files[root + '/stderr.txt'] = b'TOP_SECRET shell stderr must not be saved'
            return None, Output(code=1 if self.probe_fails else 0), Output()
        return None, Output(b'SUBMOON_SSH_OK'), Output()

    def open_sftp(self):
        return self.sftp

    def close(self):
        self.closed = True


@pytest.fixture
def setup(tmp_path, monkeypatch):
    paramiko = pytest.importorskip('paramiko')
    client = Client()
    monkeypatch.setattr(paramiko, 'SSHClient', lambda: client)
    source = tmp_path / 'probe_source.py'
    source.write_text('"synthetic probe bytes, not executed"\n')
    folder = tmp_path / 'result'
    folder.mkdir()
    probe = SSHProbe(source, tmp_path / 'synthetic_private_key')
    info = dict(ssh_host='synthetic.invalid', ssh_port=22)
    return probe, client, folder, info


def execute(setup):
    probe, _, folder, info = setup
    return probe._run(info, folder, 'submoon-hw-' + 'a' * 24, 60)


def test_complete_sftp_upload_download_sha_and_no_stderr_secret(setup):
    _, client, folder, _ = setup
    result = execute(setup)
    assert result['completed'] and result['measured']['quota_vcpus'] == 64
    assert result['download_bytes'] < 100000 and result['upload_bytes'] < 100000
    assert result['hardware_sha256'] == hashlib.sha256((folder / 'hardware.json').read_bytes()).hexdigest()
    assert client.closed and client.sftp.closed
    assert not (folder / 'stderr.txt').exists()
    assert all('TOP_SECRET' not in p.read_text() for p in folder.iterdir())


@pytest.mark.parametrize('flag,code', [('fail_connect', 'ssh_or_transfer_failed'),
                                     ('probe_fails', 'remote_probe_failed'),
                                     ('download_fails', 'incomplete_probe'),
                                     ('corrupt_upload', 'upload_sha'),
                                     ('corrupt_download', 'download_sha'),
                                     ('oversize', 'download_limit'),
                                     ('collision', 'remote_command_failed')])
def test_transport_failures_bounded_no_overwrite_and_closed(setup, flag, code):
    _, client, folder, _ = setup
    setattr(client, flag, True)
    with pytest.raises(TrialError, match=code):
        execute(setup)
    assert client.closed
    assert all('TOP_SECRET' not in p.read_text() for p in folder.iterdir())
    if flag == 'collision':
        assert not client.files


def test_missing_endpoint_never_connects(setup):
    probe, client, folder, _ = setup
    with pytest.raises(TrialError, match='ssh_endpoint_missing'):
        probe._run({}, folder, 'submoon-hw-' + 'a' * 24, 1)
    assert not client.calls
