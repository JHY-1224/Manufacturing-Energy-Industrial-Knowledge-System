import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from scripts import manage_workbench as operations

class WorkbenchOperationsTests(unittest.TestCase):
    def setUp(self):self.root=Path(tempfile.mkdtemp())
    def test_reused_pid_outside_project_is_never_signalled(self):
        folder=self.root/'data/runtime/service-logs';folder.mkdir(parents=True);(folder/'stack-fixture.json').write_text(json.dumps({'pid':34567}))
        outputs=['python /other/project/scripts/run_local_stack.py','p34567\nfcwd\nn/other/project\n']
        with patch.object(operations,'ROOT',self.root),patch.object(operations.sys,'platform','darwin'),patch.object(operations.subprocess,'check_output',side_effect=outputs),patch.object(operations.os,'kill') as kill:
            result=operations.stop()
        kill.assert_not_called();self.assertEqual(result['stopped_pids'],[]);self.assertFalse(result['forced_kill'])
    def test_active_work_prevents_normal_stop(self):
        from app.storage.knowledge_store import KnowledgeStore
        from app.storage.task_store import TaskStore
        store=KnowledgeStore(self.root/'data/runtime');TaskStore(store).create('fixture',{},[])
        with patch.object(operations,'ROOT',self.root),patch.object(operations.os,'kill') as kill:
            with self.assertRaisesRegex(RuntimeError,'Pause active tasks'):operations.stop()
        kill.assert_not_called()
    def test_verified_manifest_receives_only_normal_interrupt(self):
        folder=self.root/'data/runtime/service-logs';folder.mkdir(parents=True);command=['python','scripts/run_local_stack.py'];(folder/'stack-fixture.json').write_text(json.dumps({'pid':34567,'command':command}));identity={'pid':34567,'command':' '.join(command),'project':str(self.root)}
        with patch.object(operations,'ROOT',self.root),patch.object(operations,'process_identity',side_effect=[identity,identity,None]),patch.object(operations.os,'kill') as kill:
            result=operations.stop()
        kill.assert_called_once_with(34567,operations.signal.SIGINT);self.assertEqual(result['stopped_pids'],[34567]);self.assertFalse(result['forced_kill'])
    def test_unknown_listener_and_missing_model_profile_do_not_launch_or_kill(self):
        ready={'ready':True,'local_model_profile_prepared':False,'vision_profile_prepared':False}
        with patch.object(operations,'environment_check',return_value=ready),patch.object(operations,'status',return_value={'backend_ready':False,'frontend_ready':False}),patch.object(operations,'occupied',return_value=True),patch.object(operations,'spawn') as spawn:
            with self.assertRaisesRegex(RuntimeError,'unrecognized'):operations.start('offline')
            with self.assertRaisesRegex(RuntimeError,'not prepared'):operations.start('local')
        spawn.assert_not_called()
    def test_offline_backend_does_not_inherit_configured_model_credentials(self):
        ready={'ready':True,'local_model_profile_prepared':False,'vision_profile_prepared':False}
        with patch.object(operations,'environment_check',return_value=ready),patch.object(operations,'status',side_effect=[{'backend_ready':False,'frontend_ready':True},{'backend_ready':True,'frontend_ready':True}]),patch.object(operations,'occupied',return_value=False),patch.object(operations,'probe',return_value=True),patch.object(operations,'spawn') as spawn,patch.dict(operations.os.environ,{'RAG_CHAT_URL':'https://example.invalid','RAG_CHAT_API_KEY':'synthetic-fixture-only'}):
            result=operations.start('offline')
        self.assertEqual(result['mode'],'offline');environment=spawn.call_args.args[2];self.assertEqual(environment['RAG_CHAT_URL'],'');self.assertEqual(environment['RAG_CHAT_API_KEY'],'')

if __name__=='__main__':unittest.main()
