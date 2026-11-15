from _setup import widget, ok, done
import unittest.mock as mock

worker = widget.PingWorker()
results = []
worker.ping_result.connect(lambda lat, online: results.append((lat, online)))

# Test 1: Single failure does not emit offline (it retries)
with mock.patch("subprocess.run") as mock_ping, mock.patch("socket.create_connection") as mock_tcp:
    # First ping fails
    mock_ping.return_value = mock.Mock(returncode=2, stdout="")
    # TCP fallback fails
    mock_tcp.side_effect = Exception("timeout")

    # Stop after 1 iteration
    worker.running = True
    def stop_after_one(*a, **k):
        worker.running = False
        raise Exception("timeout")
    mock_tcp.side_effect = stop_after_one

    worker.run()
    ok("single drop does not emit offline", len(results) == 0 and worker.consecutive_drops == 1)

# Test 2: Second consecutive failure emits (None, False)
worker.consecutive_drops = 1
with mock.patch("subprocess.run") as mock_ping, mock.patch("socket.create_connection") as mock_tcp:
    mock_ping.return_value = mock.Mock(returncode=2, stdout="")
    mock_tcp.side_effect = Exception("timeout")
    
    # Run one step
    worker.running = True
    def stop_worker(*a, **k):
        worker.running = False
    worker.sleep = stop_worker
    
    # We mock time.sleep to stop worker so it doesn't loop
    def fake_sleep(s):
        worker.running = False
    with mock.patch("time.sleep", side_effect=fake_sleep):
        worker.run()

    ok("second consecutive drop emits offline", (None, False) in results and worker.consecutive_drops >= 2)

# Test 3: Success resets consecutive_drops and emits online
results.clear()
with mock.patch("subprocess.run") as mock_ping:
    mock_ping.return_value = mock.Mock(returncode=0, stdout="64 bytes from 8.8.8.8: icmp_seq=0 ttl=113 time=45.2 ms\n")
    worker.running = True
    def fake_sleep(s):
        worker.running = False
    with mock.patch("time.sleep", side_effect=fake_sleep):
        worker.run()

    ok("success resets drops and emits latency", results == [(45.2, True)] and worker.consecutive_drops == 0)

done()
