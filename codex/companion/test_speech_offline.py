import hashlib,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import speech_offline

class OfflineImport(unittest.TestCase):
    def test_new_model_resolver_falls_back_to_sd_then_reuses_pc_cache(self):
        from urllib.error import URLError
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();card=root/'card';source=card/'Snowball-Setup/speech-models/base';source.mkdir(parents=True)
            data=b'fixture model';(source/'model.bin').write_bytes(data)
            manifest={'repo':'fixture/model','revision':'fixture','files':{'model.bin':{'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}}}
            with patch('speech_offline.json.loads',return_value=manifest),patch('resource_fetch.SMALL_BYTES',1),patch('resource_fetch.open_https',side_effect=URLError('offline')) as network:
                target=speech_offline.prepare_model('base',[card],root/'cache')
                self.assertEqual((target/'model.bin').read_bytes(),data)
                (source/'model.bin').unlink()
                self.assertEqual(speech_offline.prepare_model('base',[],root/'cache'),target)
                self.assertEqual(network.call_count,1)

    def test_hf_blob_link_is_reused_but_escape_link_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Preserve native Windows short-path input to reproduce its alias
            # comparison; canonicalize macOS's normal /var temporary parent.
            root=Path(tmp) if os.name=='nt' else Path(tmp).resolve()
            repository=root/'models--fixture';snapshot=repository/'snapshots/revision';snapshot.mkdir(parents=True)
            blobs=repository/'blobs';blobs.mkdir();data=b'cached model';blob=blobs/'fixed';blob.write_bytes(data)
            entry={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
            try:(snapshot/'model.bin').symlink_to(blob)
            except OSError:self.skipTest('This host cannot create symlinks')
            self.assertEqual(speech_offline.hf_cached_file(snapshot,'model.bin',entry),blob.resolve())
            (snapshot/'model.bin').unlink();outside=root/'outside';outside.write_bytes(data)
            (snapshot/'model.bin').symlink_to(outside)
            self.assertIsNone(speech_offline.hf_cached_file(snapshot,'model.bin',entry))

    def test_verified_model_is_copied_and_survives_card_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();card=root/'card';source=card/'Snowball-Setup/speech-models/base';source.mkdir(parents=True)
            data=b'fixture model';(source/'model.bin').write_bytes(data)
            manifest={'revision':'fixture','files':{'model.bin':{'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}}}
            with patch('speech_offline.json.loads',return_value=manifest):
                target=speech_offline.import_available('base',[card],root/'cache')
                self.assertEqual((target/'model.bin').read_bytes(),data)
                (source/'model.bin').unlink()
                self.assertEqual(speech_offline.import_available('base',[],root/'cache'),target)
    def test_wrong_hash_is_never_loaded_or_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();source=root/'Snowball-Setup/speech-models/base';source.mkdir(parents=True)
            (source/'model.bin').write_bytes(b'bad')
            manifest={'revision':'fixture','files':{'model.bin':{'bytes':3,'sha256':'0'*64}}}
            with patch('speech_offline.json.loads',return_value=manifest):
                self.assertIsNone(speech_offline.import_available('base',[root],root/'cache'))
                self.assertEqual(list((root/'cache').iterdir()),[])


class WheelImport(unittest.TestCase):
    def test_connection_group_does_not_import_speech_wheels_and_reuses_cache(self):
        import offline_resources as offline
        from resource_fetch import ResourceFetcher
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();card=root/'card';key='windows-x64-py313'
            source=card/'Snowball-Setup/offline/wheels'/key;source.mkdir(parents=True)
            content={'connection.whl':b'connection wheel','speech.whl':b'speech wheel','connection.txt':b'connection fixture\n'}
            files={}
            for name,data in content.items():
                (source/name).write_bytes(data)
                files[name]={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
            manifest={'platforms':{key:{'files':files,'groups':{'connection':{'files':['connection.whl'],'requirements':content['connection.txt'].decode()}}}}}
            with patch.object(offline,'target_key',return_value=key),patch('offline_resources.json.loads',return_value=manifest),patch('resource_fetch.open_https') as network:
                target=offline.prepare_wheels('fixture','connection',[card],root/'cache')
                self.assertEqual({p.name for p in target.iterdir()},{'connection.txt','connection.whl'})
                (source/'connection.whl').unlink()
                self.assertEqual(offline.prepare_wheels('fixture','connection',[],root/'cache'),target)
            network.assert_not_called()

    def test_pinned_wheel_cache_survives_sd_removal_and_rejects_changed_files(self):
        import offline_resources as offline
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();card=root/'card';source=card/'Snowball-Setup/offline/wheels/windows-x64-py313';source.mkdir(parents=True)
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
