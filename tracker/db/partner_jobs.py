"""Durable partner requests; queue runs only under the existing worker lock."""
from .base import connect


def recover_interrupted():
    with connect() as c:
        c.execute("""UPDATE partner_search_jobs SET status='failed', finished_at=now(),
                     error='采集服务重启，任务中断；请重新提交' WHERE status='running'""")


def claim():
    with connect() as c:
        return c.execute("""UPDATE partner_search_jobs SET status='running',started_at=now()
          WHERE id=(SELECT id FROM partner_search_jobs WHERE status='queued'
                    ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1)
          RETURNING id,title,country""").fetchone()


def finish(job_id, run_id, progress):
    failed = run_id is None or (progress.errors > 0 and progress.total == 0)
    status = 'failed' if failed else ('completed' if progress.total else 'no_results')
    # Do not expose raw network exceptions, URLs, cookies or headers to partners.
    error = '外部采集失败，请稍后重试' if failed else None
    with connect() as c:
        c.execute("""UPDATE partner_search_jobs SET status=%s,finished_at=now(),run_id=%s,
          partial=%s,error=%s WHERE id=%s AND status='running'""",
          (status,run_id, bool(progress.partial_pages or progress.errors),error,job_id))
