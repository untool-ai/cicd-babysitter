import http.client
import threading
import unittest
from src.webhook_server import make_server


class WebhookTests(unittest.TestCase):
    def request(self, service, headers=None, body=b'{}'):
        server = make_server(service, "secret", port=0, max_body=128)
        thread = threading.Thread(target=server.handle_request)
        thread.start()
        connection = http.client.HTTPConnection(*server.server_address, timeout=3)
        try:
            connection.request("POST", "/webhook", body, headers or {})
            response = connection.getresponse()
            result = response.status, response.read()
        finally:
            connection.close()
            thread.join(3)
            server.server_close()
        self.assertFalse(thread.is_alive())
        return result

    def headers(self):
        return {"X-Hub-Signature-256":"sig", "X-GitHub-Delivery":"delivery", "X-GitHub-Event":"workflow_run"}

    def test_passes_exact_bytes_and_acks_after_return(self):
        calls = []
        class Service:
            def ingest(self, *args):
                calls.append(args)
        self.assertEqual(self.request(Service(), self.headers(), b'{ "a": 1 }')[0], 202)
        self.assertEqual(calls[0], (b'{ "a": 1 }', "sig", "delivery", "workflow_run", b"secret"))

    def test_failed_durability_never_acknowledged(self):
        class Service:
            def ingest(self, *args):
                raise RuntimeError("sensitive secret")
        status, body = self.request(Service(), self.headers())
        self.assertEqual(status, 503)
        self.assertNotIn(b"secret", body)

    def test_auth_denied(self):
        class Service:
            def ingest(self, *args):
                raise PermissionError()
        self.assertEqual(self.request(Service(), self.headers())[0], 401)

    def test_oversized_body(self):
        self.assertEqual(self.request(None, self.headers(), b'x' * 129)[0], 413)

    def test_missing_delivery(self):
        self.assertEqual(self.request(None)[0], 400)


    def test_actual_babysitter_persists_signed_request(self):
        import hashlib
        import hmac
        import json
        import tempfile
        from pathlib import Path
        from src.babysitter_agent import Babysitter
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / 'ledger.sqlite')
            class Service:
                def ingest(self, *args):
                    service = Babysitter(database, {'allowed_repositories':['untool-ai/test']})
                    try:
                        return service.ingest(*args)
                    finally:
                        service.close()
            raw = json.dumps({'repository':{'full_name':'untool-ai/test'}, 'workflow_run':{
                'id':1,'run_attempt':1,'workflow_id':2,'head_sha':'a'*40,
                'head_branch':'main','status':'completed','conclusion':'success'}}).encode()
            headers = self.headers()
            headers['X-Hub-Signature-256'] = 'sha256=' + hmac.new(b'secret',raw,hashlib.sha256).hexdigest()
            # Integration payload exceeds tiny unit-test bound.
            server = make_server(Service(), 'secret', port=0)
            thread = threading.Thread(target=server.handle_request)
            thread.start()
            connection = http.client.HTTPConnection(*server.server_address, timeout=3)
            try:
                connection.request('POST','/webhook',raw,headers)
                response = connection.getresponse()
                self.assertEqual(response.status,202)
                response.read()
            finally:
                connection.close()
                thread.join(3)
                server.server_close()
            service = Babysitter(database, {'allowed_repositories':['untool-ai/test']})
            try:
                self.assertEqual(service.store.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],1)
            finally:
                service.close()

    def test_body_total_deadline_rejects_before_ingestion(self):
        from unittest.mock import patch
        with patch('src.webhook_server.time.monotonic', side_effect=[0, 11]):
            self.assertEqual(self.request(None, self.headers())[0], 503)

    def test_default_rejects_over_service_limit_before_body_read(self):
        server = make_server(None, 'secret', port=0)
        thread = threading.Thread(target=server.handle_request)
        thread.start()
        connection = http.client.HTTPConnection(*server.server_address, timeout=3)
        try:
            # Declare one byte over the service limit but send no body: rejection
            # must happen from headers alone, before a read or ingest call.
            connection.request('POST', '/webhook', headers={'Content-Length':'1000001'})
            response = connection.getresponse()
            self.assertEqual(response.status, 413)
            response.read()
        finally:
            connection.close()
            thread.join(3)
            server.server_close()
        self.assertFalse(thread.is_alive())

    def test_config_cannot_exceed_service_limit(self):
        with self.assertRaises(ValueError):
            make_server(None, 'secret', port=0, max_body=1_000_001)
