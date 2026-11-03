import contextlib,io,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from scripts import run_local_stack
class VisionLauncherTests(unittest.TestCase):
 def test_background_preserves_vision_flag_and_local_paths(self):
  root=Path(tempfile.mkdtemp());process=Mock(pid=12345)
  args=['run_local_stack','--background','--with-vision','--vision-model','/tmp/fixture-vision','--vision-embedding','/tmp/fixture-embedding']
  with patch.object(run_local_stack,'ROOT',root),patch.object(run_local_stack,'require_free'),patch.object(run_local_stack.subprocess,'Popen',return_value=process) as popen,patch('sys.argv',args),contextlib.redirect_stdout(io.StringIO()):
   self.assertEqual(run_local_stack.main(),0)
  command=popen.call_args.args[0];self.assertIn('--with-vision',command);self.assertEqual(command[command.index('--vision-model')+1],'/tmp/fixture-vision');self.assertEqual(command[command.index('--vision-embedding')+1],'/tmp/fixture-embedding')

 def test_optional_vision_crash_does_not_stop_text_backend(self):
  root=Path(tempfile.mkdtemp())
  for name in ['.venv-mlx','.venv-retrieval','.venv-vision']:
   path=root/name/'bin/python';path.parent.mkdir(parents=True);path.write_text('fixture interpreter')
  chat=Mock(pid=10);chat.poll.return_value=None
  retrieval=Mock(pid=11);retrieval.poll.return_value=None
  vision=Mock(pid=12);vision.poll.return_value=17
  backend=Mock(pid=13,returncode=0);backend.poll.side_effect=[None,0,0]
  status={}
  def healthy(kind,port):
   status[kind]=status.get(kind,0)+1;return status[kind]>1
  output=io.StringIO()
  with patch.object(run_local_stack,'ROOT',root),patch.object(run_local_stack,'require_free'),patch.object(run_local_stack,'healthy',side_effect=healthy),patch.object(run_local_stack.time,'sleep'),patch.object(run_local_stack.subprocess,'Popen',side_effect=[chat,retrieval,vision,backend]) as popen,patch('sys.argv',['run_local_stack','--with-vision']),contextlib.redirect_stdout(output):
   self.assertEqual(run_local_stack.main(),0)
  self.assertIn('Optional vision worker stopped',output.getvalue());self.assertIn('scripts.serve_local_vision_limited',popen.call_args_list[2].args[0])
