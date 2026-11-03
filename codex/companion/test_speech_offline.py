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


class WheelImport(unittest.TestCase):
    def test_pinned_wheel_cache_survives_sd_removal_and_rejects_changed_files(self):
        import offline_resources as offline
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);card=root/'card';source=card/'Snowball-Setup/offline/wheels/windows-x64-py313';source.mkdir(parents=True)
            data=b'fixture wheel';(source/'fixture.whl').write_bytes(data)
            manifest={'platforms':{'windows-x64-py313':{'files':{'fixture.whl':{'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}}}}}
            with patch.object(offline,'target_key',return_value='windows-x64-py313'),patch('offline_resources.json.loads',return_value=manifest):
                target=offline.prepare_wheels('fixture','speech',[card],root/'cache')
                self.assertEqual((target/'fixture.whl').read_bytes(),data)
                (source/'fixture.whl').write_bytes(b'corrupt')
                self.assertEqual(offline.prepare_wheels('fixture','speech',[],root/'cache'),target)
                (target/'fixture.whl').write_bytes(b'corrupt')
                self.assertIsNone(offline.prepare_wheels('fixture','speech',[card],root/'cache'))
    def test_platform_selection_keeps_macos_arm_and_intel_separate(self):
        import offline_resources as offline
        for data,expected in [(['darwin','arm64',3,13,'14.0'],'macos-arm64-py313'),(['darwin','x86_64',3,12,'15.0'],'macos-x64-py312'),(['win32','arm64',3,13,''],None),(['win32','amd64',3,14,''],None)]:
            with patch('offline_resources.subprocess.run',return_value=type('Reply',(),{'stdout':json.dumps(data)})()):
                self.assertEqual(offline.target_key('fixture'),expected)

if __name__=='__main__':unittest.main()
