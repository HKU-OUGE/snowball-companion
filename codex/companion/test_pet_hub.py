import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from status_hub import StatusHub, HubHandler, Invalid

class PetHubTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.hub = StatusHub(self.root)
    def tearDown(self): self.tmp.cleanup()
    def test_initial_snowball_selection_is_explicit_and_idempotent(self):
        self.assertEqual(self.hub.snapshot()['pet']['revision'], 0)
        result = self.hub.set_pet({'character_id': 'snowball'})
        self.assertEqual(result['revision'], 1)
        self.assertFalse(result['device_applied'])
        self.assertEqual(self.hub.set_pet({'character_id': 'snowball'})['revision'], 1)
        self.assertEqual(StatusHub(self.root).snapshot()['pet']['revision'], 1)

    def request_pet(self, token='TEST_ONLY_FAKE_TOKEN_0123456789abcdef', origin=None):
        import io, json
        from email.message import Message
        from types import SimpleNamespace
        body = json.dumps({'character_id': 'ragdoll'}).encode()
        handler = object.__new__(HubHandler)
        handler.path = '/v1/pet'
        handler.server = SimpleNamespace(hub=self.hub,
            token='TEST_ONLY_FAKE_TOKEN_0123456789abcdef', server_port=4190)
        handler.headers = Message()
        for key, value in {'Authorization': 'Bearer ' + token,
                'Host': '127.0.0.1:4190', 'Content-Type': 'application/json',
                'Content-Length': str(len(body))}.items():
            handler.headers[key] = value
        if origin:
            handler.headers['Origin'] = origin
        handler.rfile = io.BytesIO(body)
        replies = []
        handler.reply = lambda status, data: replies.append((status, data))
        handler.do_POST()
        return replies[0]

    def test_authenticated_route_reaches_pet_selection_without_network(self):
        status, result = self.request_pet()
        self.assertEqual(status, 200)
        self.assertEqual((result['character_id'], result['revision']), ('ragdoll', 1))
        self.assertFalse(result['device_applied'])

    def test_pet_route_rejects_bad_token_and_browser_origin(self):
        self.assertEqual(self.request_pet(token='invalid')[0], 401)
        self.assertEqual(self.request_pet(origin='http://localhost:5173')[0], 403)
        self.assertEqual(self.hub.pet['revision'], 0)

    def test_selection_persists_without_session_side_effects(self):
        before = self.hub.snapshot()
        reply = self.hub.set_pet({'character_id':'joseph-joestar'})
        self.assertEqual(reply['revision'], 1)
        self.assertFalse(reply['device_applied'])
        self.assertEqual(StatusHub(self.root).snapshot()['pet'], {'character_id':'joseph-joestar','revision':1})
        after = self.hub.snapshot()
        for key in ['sessions','priority_order','focused_session','focus_pinned']:
            self.assertEqual(before[key], after[key])
    def test_duplicate_is_idempotent_and_all_new_ids_supported(self):
        for number, ident in enumerate(['heisenberg','stellalou','frank-underwood'], 1):
            self.assertEqual(self.hub.set_pet({'character_id':ident})['revision'], number)
            self.assertEqual(self.hub.set_pet({'character_id':ident})['revision'], number)
    def test_rejects_unknown_traversal_extra_and_nonstring(self):
        for data in [None, [], {}, {'character_id':'../snowball'}, {'character_id':[]}, {'character_id':'snowball','extra':1}, {'character_id':'young-joseph'}]:
            with self.assertRaises(Invalid): self.hub.set_pet(data)
        self.assertEqual(self.hub.pet['revision'], 0)
    def test_failed_disk_write_does_not_change_live_selection(self):
        with patch('status_hub.atomic_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.hub.set_pet({'character_id':'ragdoll'})
        self.assertEqual(self.hub.pet, {'character_id':'snowball','revision':0})

if __name__ == '__main__': unittest.main()
