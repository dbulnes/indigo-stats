import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from backend import backups,db,manage


class ManageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.data=patch.object(db,'DATA',Path(self.temp.name)); self.data.start()
    def tearDown(self): self.data.stop(); self.temp.cleanup()

    def run_cli(self,args,payload=''):
        output=io.StringIO()
        with redirect_stdout(output): manage.main(args,io.StringIO(payload))
        return output.getvalue()

    def test_backup_check_and_private_configure(self):
        message=self.run_cli(['configure'],'{"timezone":"UTC"}')
        self.assertIn('Values omitted',message); self.assertNotIn('UTC',message)
        self.assertIn('created',self.run_cli(['backup']))
        self.assertIn('integrity: ok',self.run_cli(['check']))
        with self.assertRaises(SystemExit) as error: self.run_cli(['configure'],'{"credential":"PRIVATE"}')
        self.assertNotIn('PRIVATE',str(error.exception))

    def test_remote_list_accepts_stdin_config_without_secrets(self):
        item=backups.RemoteSnapshot('20260921T120000Z.sqlite','a'*64,123,1,'PRIVATE REFERENCE')
        with patch.object(backups,'remote_list',return_value=[item]) as remote:
            output=self.run_cli(['remote-list','--config-stdin'],'{"provider":"filesystem"}')
        remote.assert_called_once_with({'provider':'filesystem'})
        self.assertIn(item.filename,output); self.assertIn(item.checksum,output); self.assertNotIn('PRIVATE REFERENCE',output)

    def test_remote_fetch_reports_recovery_path_and_requires_name(self):
        with self.assertRaises(SystemExit): self.run_cli(['remote-fetch'])
        target=Path(self.temp.name)/'recovery'/'20260921T120000Z.sqlite'
        with patch.object(backups,'fetch',return_value=target) as fetch:
            output=self.run_cli(['remote-fetch',target.name])
        fetch.assert_called_once_with(target.name,None); self.assertIn('live database was not changed',output)


if __name__=='__main__': unittest.main()
