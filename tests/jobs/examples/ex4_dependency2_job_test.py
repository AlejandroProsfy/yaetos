import pytest
from jobs.examples.ex4_dependency2_job import Job


class Test_Job(object):
    def test_transform(self, sc, sc_sql, ss):
        some_events = ss.read.json(sc.parallelize([
            {'session_id': 1, 'session_length': 1},
            {'session_id': 12, 'session_length': 2},
            {'session_id': 123, 'session_length': 3},
            {'session_id': 1234, 'session_length': 4},
            ]))

        expected = [
            {'session_id': 1,    'session_length': 1, 'doubled_length': 2},
            {'session_id': 12,   'session_length': 2, 'doubled_length': 4},
            {'session_id': 123,  'session_length': 3, 'doubled_length': 6},
            {'session_id': 1234, 'session_length': 4, 'doubled_length': 8},
            ]

        actual = Job(args={'mode_no_io':True}).etl_no_io(sc, sc_sql, loaded_inputs={'some_events': some_events}).toPandas().to_dict(orient='records')
        assert actual == expected
