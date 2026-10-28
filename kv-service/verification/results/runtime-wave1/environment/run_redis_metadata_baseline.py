#!/usr/bin/env python3
"""Exercise the two Redis-gated tests with a private task-owned Redis process."""
import datetime
import json
import os
import pathlib
import socket
import subprocess
import time

root = pathlib.Path('/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029')
evidence = root / 'evidence'
redis_dir = root / 'redis-baseline'
redis_dir.mkdir(exist_ok=True)
label = 'configured_redis_metadata'
command = ['cargo', 'test', '--locked', '--offline', '-j', '4', '-p',
           'contextstore-server', '--test', 'redis_metadata_e2e', '--', '--nocapture']
with socket.socket() as reservation:
    reservation.bind(('127.0.0.1', 0))
    port = reservation.getsockname()[1]

with (redis_dir / 'redis.log').open('wb') as redis_log:
    process = subprocess.Popen([
        'redis-server', '--bind', '127.0.0.1', '--port', str(port),
        '--save', '', '--appendonly', 'no', '--dir', str(redis_dir),
    ], stdout=redis_log, stderr=subprocess.STDOUT)
    try:
        for attempt in range(100):
            if process.poll() is not None:
                raise RuntimeError('Task-owned Redis exited during startup')
            try:
                with socket.create_connection(('127.0.0.1', port), 0.2) as connection:
                    connection.sendall(b'*1\r\n$4\r\nPING\r\n')
                    if connection.recv(32).startswith(b'+PONG'):
                        break
            except OSError:
                time.sleep(0.05)
        else:
            raise RuntimeError('Task-owned Redis readiness timeout')
        env = os.environ.copy()
        env['CS_REDIS_URL'] = f'redis://127.0.0.1:{port}/'
        (evidence / (label + '.environment.json')).write_text(json.dumps({
            'redis_pid': process.pid, 'redis_url': env['CS_REDIS_URL'],
            'redis_dir': str(redis_dir), 'command': command,
        }, indent=2) + '\n')
        (evidence / (label + '.command')).write_text(' '.join(command) + '\n')
        (evidence / (label + '.started')).write_text(datetime.datetime.now(datetime.timezone.utc).isoformat() + '\n')
        with (evidence / (label + '.log')).open('wb') as output:
            result = subprocess.run(command, cwd=root / 'src-baseline', env=env,
                                    stdout=output, stderr=subprocess.STDOUT)
        (evidence / (label + '.exit_code')).write_text(str(result.returncode) + '\n')
        (evidence / (label + '.finished')).write_text(datetime.datetime.now(datetime.timezone.utc).isoformat() + '\n')
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        (evidence / (label + '.redis_stopped')).write_text(str(process.returncode) + '\n')
