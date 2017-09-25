"""Same as ex1_full_sql_job.sql but allows access to spark for more complex ops (like ex2_frameworked_job.py)."""

from core.etl_utils import etl, launch

class ex1_frameworked_job(etl):

    def run(self, some_events, other_events):

        df = self.query("""
            SELECT se.session_id, count(*)
            FROM some_events se
            JOIN other_events oe on se.session_id=oe.session_id
            WHERE se.action='searchResultPage' and se.n_results>0
            group by se.session_id
            order by count(*) desc
            """)
        return df


if __name__ == "__main__":
    launch(job_class=ex1_frameworked_job, aws_setup='perso')
