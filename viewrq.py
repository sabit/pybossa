from pybossa.core import create_app, sentinel
from rq import Worker

app = create_app(run_as_server=False)
with app.app_context():
    for worker in Worker.all(connection=sentinel.master):
        print({
            "name": worker.name,
            "state": worker.get_state(),
            "queues": [queue.name for queue in worker.queues],
            "current_job_id": worker.get_current_job_id(),
        })
