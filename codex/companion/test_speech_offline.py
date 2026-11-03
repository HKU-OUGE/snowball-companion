import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import speech_offline

class OfflineImport(unittest.TestCase):
    def test_verified_model_is_copied_and_survives_card_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);card=root/'card';source=card/'Snowball-Setup/speech-models/base';source.mkdir(parents=True)
            data=b'fixture model';(source/'model.bin').write_bytes(data)
            manifest={'revision':'fixture','files':{'model.bin':{'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}}}
            with patch('speech_offline.json.loads',return_value=manifest):
                target=speech_offline.import_available('base',[card],root/'cache')
                self.assertEqual((target/'model.bin').read_bytes(),data)
                (source/'model.bin').unlink()
                self.assertEqual(speech_offline.import_available('base',[],root/'cache'),target)
    def test_wrong_hash_is_never_loaded_or_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'Snowball-Setup/speech-models/base';source.mkdir(parents=True)
            (source/'model.bin').write_bytes(b'bad')
            manifest={'revision':'fixture','files':{'model.bin':{'bytes':3,'sha256':'0'*64}}}
            with patch('speech_offline.json.loads',return_value=manifest):
                self.assertIsNone(speech_offline.import_available('base',[root],root/'cache'))
                self.assertEqual(list((root/'cache').iterdir()),[])

if __name__=='__main__':unittest.main()
