"""Safety tests always run with DB dependencies; integration is opt-in and disposable."""
import copy
import csv
import io
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

try:
    import psycopg
    from assigment_test_emoralesv.project.database import importer
    from assigment_test_emoralesv.project.database.safety import DatabaseConfig, SafetyError, validate, from_environment
    DB_DEPS = True
except ModuleNotFoundError:
    DB_DEPS = False

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(DB_DEPS, 'Install requirements.txt for database tests')
class DatabaseSafetyTests(unittest.TestCase):
    def setUp(self):
        self.config=DatabaseConfig('test','127.0.0.1',15432,'sales_assignment_test','test_user','test-secret-not-a-real-credential')

    def test_reject_production_like_environments(self):
        for env in ['production','prod','staging','shared','LOCAL','',None]:
            with self.subTest(env=env), self.assertRaises(SafetyError):validate(replace(self.config,environment=env),True)

    def test_confirmation_is_mandatory(self):
        with self.assertRaises(SafetyError):validate(self.config,False)

    def test_reject_system_default_and_unresolved_names(self):
        for name in ['', 'postgres','template0','template1','default','*','${DATABASE_NAME}','sales_assignment_local','another_project','sales_assignment_test; DROP DATABASE postgres']:
            with self.subTest(name=name), self.assertRaises(SafetyError):validate(replace(self.config,name=name),True)

    def test_reject_remote_hosts_and_invalid_ports(self):
        for host in ['db.example.com','10.0.0.2','postgres','', '/var/run/postgresql']:
            with self.assertRaises(SafetyError):validate(replace(self.config,host=host),True)
        for port in [0,65536,-1,True]:
            with self.assertRaises(SafetyError):validate(replace(self.config,port=port),True)

    def test_safe_explicit_target_and_secret_repr(self):
        self.assertEqual(validate(self.config,True),self.config)
        self.assertNotIn(self.config.password,repr(self.config))
        self.assertNotIn('password',self.config.target())

    def test_configuration_missing_and_no_interpolation(self):
        with self.assertRaises(SafetyError):from_environment(environ={})
        values={'APP_ENV':'test','DATABASE_HOST':'127.0.0.1','DATABASE_PORT':'15432','DATABASE_NAME':'sales_assignment_test','DATABASE_USER':'test_user','DATABASE_PASSWORD':'${SECRET}'}
        with self.assertRaises(SafetyError):validate(from_environment(environ=values),True)
        values['DATABASE_PASSWORD']='nonempty';values['DATABASE_URL']='postgresql://unidentified'
        with self.assertRaises(SafetyError):from_environment(environ=values)

    def test_rejected_safety_never_connects(self):
        with patch.object(importer,'wait_for_database') as wait:
            with self.assertRaises(SafetyError):importer.reset(replace(self.config,environment='prod'),True)
            wait.assert_not_called()

    def test_missing_artifacts_never_connect(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(importer,'wait_for_database') as wait:
            with self.assertRaisesRegex(ValueError,'Missing normalized inputs'):importer.reset(self.config,True,Path(temp))
            wait.assert_not_called()
            self.assertEqual(list(Path(temp).iterdir()),[])

    def test_duplicate_primary_ids_rejected_before_connection(self):
        bundle=importer.read_bundle(ROOT/'normalized')
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)
            for name,item in bundle['files'].items():
                target=path/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(item['text'])
            users=path/'usuarios.csv';lines=users.read_text().splitlines();users.write_text('\n'.join(lines+[lines[1]])+'\n')
            with self.assertRaisesRegex(ValueError,'duplicate primary IDs'):importer.read_bundle(path)


@unittest.skipUnless(DB_DEPS and os.environ.get('RUN_DATABASE_INTEGRATION_TESTS')=='1', 'Use scripts/run_database_tests.py for isolated PostgreSQL tests')
class DatabaseIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config=from_environment(environ=os.environ)
        validate(cls.config,True)
        if cls.config.environment!='test':raise RuntimeError('Integration tests require APP_ENV=test')
        cls.conn=importer.wait_for_database(cls.config,60)
        cls.bundle=importer.read_bundle(ROOT/'normalized')
        cls.conn.execute('CREATE TABLE IF NOT EXISTS public.test_sentinel (id integer PRIMARY KEY)')
        cls.conn.execute('INSERT INTO public.test_sentinel VALUES (1) ON CONFLICT DO NOTHING')

    @classmethod
    def tearDownClass(cls):cls.conn.close()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        for name,item in self.bundle['files'].items():
            dest=self.path/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(item['text'])

    def tearDown(self):self.temp.cleanup()

    def run_reset(self):
        with patch('builtins.print'):
            return importer.reset(self.config,True,self.path)

    def assert_committed(self,result):
        self.assertEqual(result['transaction_status'],'committed',result['blocking_errors'])

    def operational_snapshot(self):
        result={}
        for table in importer.TABLES:
            if table=='normalization_runs':continue
            values=self.conn.execute(f"SELECT to_jsonb(t) - 'run_id' - 'created_at' - 'updated_at' FROM sales_assignment.{table} t").fetchall()
            result[table]=sorted(json.dumps(row[0],sort_keys=True) for row in values)
        return result

    def test_01_empty_database_creation_counts_and_foreign_keys(self):
        result=self.run_reset();self.assert_committed(result)
        self.assertEqual({t:result['rows_loaded_by_table'][t] for t in importer.EXPECTED},importer.EXPECTED)
        self.assertTrue(all(c['passed'] for c in result['business_invariant_results'].values()))
        self.assertTrue(all(c['unresolved']==0 for c in result['foreign_key_results'].values()))
        for table in importer.EMPTY_TABLES:self.assertEqual(result['rows_loaded_by_table'][table],0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM public.test_sentinel').fetchone()[0],1)

    def test_02_replacement_and_idempotency(self):
        first=self.run_reset();self.assert_committed(first);snapshot=self.operational_snapshot()
        second=self.run_reset();self.assert_committed(second)
        self.assertNotEqual(first['normalization_run_id'],second['normalization_run_id'])
        self.assertEqual(first['rows_loaded_by_table'],second['rows_loaded_by_table'])
        self.assertEqual(snapshot,self.operational_snapshot())
        self.assertEqual(len(list((self.path/'database_import_runs').glob('*.json'))),2)

    def test_03_circular_refs_invalid_team_and_nulls(self):
        self.assert_committed(self.run_reset())
        leaders=self.conn.execute('SELECT t.id,u.team_id FROM sales_assignment.teams t JOIN sales_assignment.users u ON u.id=t.leader_id').fetchall()
        self.assertEqual(len(leaders),4);self.assertTrue(all(team==userteam for team,userteam in leaders))
        user=self.conn.execute("SELECT team_id,source_team_id,source_payload->>'equipo_id_original' FROM sales_assignment.users WHERE id='17'").fetchone()
        self.assertEqual(user,(None,'99','99'))
        self.assertEqual(self.conn.execute("SELECT maximum_capacity FROM sales_assignment.users WHERE id='6'").fetchone()[0],None)
        self.assertEqual(self.conn.execute("SELECT maximum_capacity FROM sales_assignment.users WHERE id='7'").fetchone()[0],0)
        self.assertEqual(self.conn.execute("SELECT ends_on FROM sales_assignment.absences WHERE id='6'").fetchone()[0],None)
        self.assertGreater(self.conn.execute("SELECT count(*) FROM sales_assignment.records WHERE original_nit IS DISTINCT FROM nit").fetchone()[0],0)

    def test_04_business_mismatch_rolls_back_reset_and_load(self):
        good=self.run_reset();self.assert_committed(good);snapshot=self.operational_snapshot()
        path=self.path/'registros.csv';rows=list(csv.DictReader(io.StringIO(path.read_text())))
        row=next(r for r in rows if r['estado_normalizado']=='nuevo');row['estado_normalizado']='descartado'
        with path.open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=rows[0]);writer.writeheader();writer.writerows(rows)
        failed=self.run_reset();self.assertEqual(failed['transaction_status'],'rolled_back')
        self.assertFalse(failed['business_invariant_results']['status_nuevo']['passed'])
        self.assertEqual(snapshot,self.operational_snapshot())
        self.assertEqual(str(self.conn.execute('SELECT id FROM sales_assignment.normalization_runs').fetchone()[0]),good['normalization_run_id'])
        self.assertTrue(all(n==0 for n in failed['rows_loaded_by_table'].values()))

    def test_05_foreign_key_failure_rolls_back(self):
        good=self.run_reset();self.assert_committed(good)
        path=self.path/'actividad.csv';rows=list(csv.DictReader(io.StringIO(path.read_text())));rows[0]['usuario_id_normalizado']='missing-user'
        with path.open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=rows[0]);writer.writeheader();writer.writerows(rows)
        failed=self.run_reset();self.assertEqual(failed['transaction_status'],'rolled_back')
        self.assertEqual(failed['rows_rejected_by_table']['activities'],1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM sales_assignment.activities').fetchone()[0],264)

    def test_06_unowned_schema_refused(self):
        self.assert_committed(self.run_reset())
        self.conn.execute("COMMENT ON SCHEMA sales_assignment IS 'not-owned-by-this-project'")
        try:
            failed=self.run_reset();self.assertEqual(failed['transaction_status'],'rolled_back')
            self.assertIn('ownership',failed['blocking_errors'][0]['message'])
            self.assertEqual(self.conn.execute('SELECT count(*) FROM sales_assignment.records').fetchone()[0],167)
        finally:self.conn.execute("COMMENT ON SCHEMA sales_assignment IS 'sales-assignment-owned-schema:v1'")

    def test_07_external_dependencies_are_not_cascaded(self):
        self.assert_committed(self.run_reset())
        self.conn.execute('CREATE TABLE public.external_reference (record_id text REFERENCES sales_assignment.records(id))')
        try:
            self.conn.execute("INSERT INTO public.external_reference VALUES ('1')")
            failed=self.run_reset();self.assertEqual(failed['transaction_status'],'rolled_back')
            self.assertEqual(self.conn.execute('SELECT count(*) FROM public.external_reference').fetchone()[0],1)
            self.assertEqual(self.conn.execute('SELECT count(*) FROM sales_assignment.records').fetchone()[0],167)
        finally:self.conn.execute('DROP TABLE public.external_reference')

    def test_08_reports_contain_no_password(self):
        report=self.run_reset();self.assert_committed(report)
        text=(self.path/'database_import_report.json').read_text()
        self.assertNotIn(self.config.password,text)
        self.assertEqual(json.loads(text)['database_target'],self.config.target())

if __name__=='__main__':unittest.main()
