import importlib.util,unittest,tempfile,shutil,json,os,hashlib
from unittest.mock import patch
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]/'plugins/xueqiu-status'
spec=importlib.util.spec_from_file_location('pet_setup',ROOT/'scripts/pet_setup.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class IsolatedPetTest(unittest.TestCase):
 """Tests cannot resolve the real user's HOME/state/token or contact a Hub."""
 def setUp(self):
  self.private=tempfile.TemporaryDirectory();self.addCleanup(self.private.cleanup)
  self.home=Path(self.private.name)/'home';self.home.mkdir()
  env=patch.dict(os.environ,HOME=str(self.home),CODEX_HOME=str(self.home/'.codex'),XDG_CONFIG_HOME=str(self.home/'.config'),XDG_STATE_HOME=str(self.home/'.local/state'))
  env.start();self.addCleanup(env.stop)
  home=patch.object(Path,'home',return_value=self.home);home.start();self.addCleanup(home.stop)
  network=patch.object(m.http.client,'HTTPConnection',side_effect=AssertionError('Real HTTP is disabled in Pet unit tests'))
  network.start();self.addCleanup(network.stop)
  self.fake_token=self.home/'.config/xueqiu/hub-token';self.fake_token.parent.mkdir(parents=True)
  self.fake_token.write_text('TEST_ONLY_FAKE_TOKEN_'+'x'*32)
 def fixture_catalog(self,folder,character,status):
  target=folder/'assets/pet';target.mkdir(parents=True)
  catalog=m.catalog()
  entry=next(item for item in catalog['characters'] if item['id']==character)
  entry['status']=status
  (target/'catalog.json').write_text(json.dumps(catalog))
  return folder

class PetSetupTests(IsolatedPetTest):
 def test_bundled_art_checks_out(self):
  a=m.asset();self.assertEqual((a['width'],a['height']),(1536,2288));self.assertEqual(a['name'],'雪球')
 def test_tampered_art_fails_before_install(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);shutil.copytree(ROOT/'assets',p/'assets');f=p/'assets/pet/snowball.png';f.write_bytes(f.read_bytes()+b'bad')
   with self.assertRaises(ValueError):m.asset(p)
 def test_record_is_private_idempotent_and_separate_from_assets(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'state/pet-install.json';a=m.asset();r=m.record('pet_test-owned',a,p);m.record('pet_test-owned',a,p)
   self.assertEqual(m.read_state(p),r);self.assertEqual(p.stat().st_mode&0o777,0o600);self.assertEqual(p.parent.stat().st_mode&0o777,0o700)
 def test_builtins_and_invalid_ids_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   for val in ['codex','../../other','',None]:
    with self.assertRaises((ValueError,TypeError)):m.record(val,m.asset(),Path(d)/'state.json')
 def test_symlink_state_refused(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);other=p/'other';other.mkdir();link=p/'link';link.symlink_to(other,target_is_directory=True)
   with self.assertRaises(ValueError):m.record('pet_test',m.asset(),link/'state.json')
   self.assertEqual(list(other.iterdir()),[])

class CharacterLibraryTests(IsolatedPetTest):
 def test_catalog_has_fourteen_and_old_joseph(self):
  c=m.catalog();self.assertEqual(len(c['characters']),14)
  joseph=m.character_info('joseph-joestar');self.assertIn('星尘斗士',joseph['description']);self.assertIn('老年',joseph['name'])
 def test_pending_does_not_substitute_snowball(self):
  with tempfile.TemporaryDirectory() as d:
   root=self.fixture_catalog(Path(d)/'fixture','border-collie','pending')
   state=Path(d)/'state.json';r=m.inspect('border-collie',root=root,path=state)
   self.assertIsNone(r['asset']);self.assertFalse(state.exists())
   with self.assertRaises(ValueError):m.asset(root,character='border-collie')
 def test_legacy_migration_preserves_snowball_when_recording_another(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'state.json';old={'pet_id':'pet_original','asset_key':'old','sha256':'old'};p.write_text(json.dumps(old))
   migrated=m.read_state(p);self.assertEqual(migrated['characters']['snowball'],old)
   self.assertEqual(json.loads(p.read_text()),old)
   meta=dict(m.asset(),character='border-collie',asset_key='new')
   result=m.record('pet_collie',meta,p,selected=True)
   self.assertEqual(result['characters']['snowball'],old);self.assertEqual(result['characters']['border-collie']['pet_id'],'pet_collie');self.assertEqual(result['selected_character'],'border-collie')
 def test_receipt_does_not_claim_activation(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'state.json';meta=m.asset();result=m.record('pet_owned',meta,p)
   self.assertIsNone(result['selected_character'])
   m.record('pet_owned',meta,p,selected=True)
   result=m.record('pet_other',dict(meta,character='ragdoll'),p)
   self.assertEqual(result['selected_character'],'snowball')
 def test_unsupported_state_is_not_destroyed(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'state.json';original='{"schema_version":999,"characters":{}}';p.write_text(original)
   with self.assertRaises(ValueError):m.record('pet_test',m.asset(),p)
   self.assertEqual(p.read_text(),original)
 def test_manifest_traversal_and_incomplete_frames_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);shutil.copytree(ROOT/'assets',p/'assets');mp=p/'assets/pet/manifest.json';meta=json.loads(mp.read_text());meta['frames_per_row']=[1];mp.write_text(json.dumps(meta))
   with self.assertRaises(ValueError):m.asset(p)
   meta['frames_per_row']=m.FRAMES;meta['file']='../snowball.png';mp.write_text(json.dumps(meta))
   with self.assertRaises((ValueError,FileNotFoundError)):m.asset(p)
 def test_catalog_ready_requires_actual_art(self):
  with tempfile.TemporaryDirectory() as d:
   p=self.fixture_catalog(Path(d),'border-collie','ready')
   # No manifest or PNG exists in this private fixture, regardless of release availability.
   with self.assertRaises(FileNotFoundError):m.asset(p,'border-collie')

class CompanionSelectionTests(IsolatedPetTest):
 def test_selection_revision_changes_only_when_selection_changes(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'state.json';snow=m.asset();collie=dict(snow,character='border-collie')
   self.assertEqual(m.record('pet_snow',snow,p,selected=True)['selected_revision'],1)
   self.assertEqual(m.record('pet_snow',snow,p,selected=True)['selected_revision'],1)
   self.assertEqual(m.record('pet_collie',collie,p,selected=True)['selected_revision'],2)
   self.assertEqual(m.record('pet_snow',snow,p,selected=False)['selected_revision'],2)
 def test_hub_sync_uses_only_loopback_and_validates_confirmation(self):
  from unittest.mock import patch,MagicMock
  with tempfile.TemporaryDirectory() as d:
   token=Path(d)/'token';token.write_text('a'*32)
   connection=MagicMock();response=connection.getresponse.return_value
   response.status=200;response.read.return_value=b'{"character_id":"snowball","revision":3}'
   with patch.object(m.http.client,'HTTPConnection',return_value=connection) as constructor:
    result=m.sync('snowball',token_path=token)
    constructor.assert_called_once_with('127.0.0.1',4190,timeout=2)
    request=connection.request.call_args.args
    self.assertEqual(request[:2],('POST','/v1/pet'));self.assertEqual(json.loads(request[2]),{'character_id':'snowball'})
    self.assertFalse(result['device_verified']);self.assertEqual(result['hub_selection']['revision'],3)
    response.read.return_value=b'{"character_id":"wrong","revision":3}'
    with self.assertRaises(ValueError):m.sync('snowball',token_path=token)
    response.status=404
    with self.assertRaises(ValueError):m.sync('snowball',token_path=token)
 def test_pending_character_never_notifies_hub(self):
  from unittest.mock import patch
  root=self.fixture_catalog(Path(self.private.name)/'pending','border-collie','pending')
  with patch.object(m.http.client,'HTTPConnection') as constructor:
   with self.assertRaises(ValueError):m.sync('border-collie',root=root,token_path=self.fake_token)
   self.assertEqual(constructor.call_count,0) # Never dump request call arguments.

class ReleaseAssetTests(IsolatedPetTest):
 def test_every_published_ready_asset_matches_its_hash_and_dimensions(self):
  ready=[entry for entry in m.catalog()['characters'] if entry['status']=='ready']
  self.assertTrue(ready)
  for entry in ready:
   with self.subTest(character=entry['id']):
    meta=m.asset(ROOT,entry['id']);data=Path(meta['file']).read_bytes()
    self.assertEqual(hashlib.sha256(data).hexdigest(),meta['sha256'])
    self.assertEqual((meta['width'],meta['height']),(1536,2288))
    self.assertEqual(meta['frames_per_row'],m.FRAMES)
 def test_all_character_receipts_survive_other_character_selections(self):
  state=self.home/'.codex/state/plugins/xueqiu-status/pet-install.json'
  expected={}
  for entry in m.catalog()['characters']:
   character=entry['id']
   # Receipt migration is independent of artwork readiness. Pending fixtures use
   # clearly test-only identities, never author account IDs or real private state.
   meta=m.asset(ROOT,character) if entry['status']=='ready' else dict(character=character,asset_key='fixture-'+character,sha256='0'*64)
   identifier='pet_test_'+character
   result=m.record(identifier,meta,state,selected=True)
   expected[character]=identifier
   self.assertEqual({key:value['pet_id'] for key,value in result['characters'].items()},expected)
  self.assertEqual(len(m.read_state(state)['characters']),14)
 def test_default_paths_are_confined_to_fake_home(self):
  self.assertTrue(m.state_path().is_relative_to(self.home))
  # Even an accidental default-token sync sees only a fake token; HTTP is blocked.
  with self.assertRaisesRegex(AssertionError,'Real HTTP is disabled'):
   m.sync('snowball')
  self.assertFalse(m.state_path().exists())

class BundledAudioTests(IsolatedPetTest):
 def test_audio_package_all_characters_and_original_history(self):
  import wave,struct,math
  folder=ROOT/'assets/audio';manifest=json.loads((folder/'manifest.json').read_text())
  self.assertEqual(set(manifest['characters']),{entry['id'] for entry in m.catalog()['characters']})
  hashes=set()
  for character,record in manifest['characters'].items():
   self.assertEqual(set(record['clips']),{'complete','waiting','error'})
   for cue,meta in record['clips'].items():
    f=folder/character/(cue+'.wav');self.assertEqual(hashlib.sha256(f.read_bytes()).hexdigest(),meta['sha256']);hashes.add(meta['sha256'])
    with wave.open(str(f),'rb') as wav:
     self.assertEqual((wav.getframerate(),wav.getnchannels(),wav.getsampwidth()),(16000,1,2))
     n=wav.getnframes();samples=struct.unpack('<'+'h'*n,wav.readframes(n))
    self.assertLess(n/16000,1.5);self.assertLessEqual(20*math.log10(max(map(abs,samples))/32768),-6)
    self.assertLess(abs(sum(samples)/n/32768),.001)
  self.assertEqual(len(hashes),42)
  self.assertEqual(manifest['characters']['snowball']['revision'],2)
  legacy=json.loads((folder/'source-history/snowball-v1/manifest.json').read_text())
  for cue,sha in legacy['sha256'].items():
   self.assertEqual(hashlib.sha256((folder/'source-history/snowball-v1'/(cue+'.wav')).read_bytes()).hexdigest(),sha)
   self.assertNotEqual(manifest['characters']['snowball']['clips'][cue]['sha256'],sha)
  self.assertIn('does not automatically play',manifest['host_playback'])

if __name__=='__main__':unittest.main()
