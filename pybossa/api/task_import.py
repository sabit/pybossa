# -*- coding: utf8 -*-
"""Bulk task import API endpoints."""

import json
import os
import uuid
from itsdangerous import URLSafeSerializer, BadSignature
from flask import current_app

from flask import Response, request, send_file, url_for
from flask.views import MethodView
from rq import Queue
from rq.exceptions import NoSuchJobError
from rq.job import Job
from werkzeug.exceptions import BadRequest, NotFound

from pybossa.auth import ensure_authorized_to
from pybossa.core import sentinel, uploader
from pybossa.default_settings import TIMEOUT
from pybossa.error import ErrorStatus
from pybossa.jobs import import_tasks
from pybossa.model.task import Task
from pybossa.model.task_import_mapping import TaskImportMapping
from pybossa.uploader.local import LocalUploader


error = ErrorStatus()
importer_queue = Queue('medium', connection=sentinel.master,
                       default_timeout=TIMEOUT)


class TaskImportAPI(MethodView):

    """Create tasks from an uploaded CSV file.

    The uploaded file is persisted before the job is queued, so the worker can
    read it after this request has returned.  The worker removes the temporary
    file after importing it.
    """

    def post(self):
        csv_filename = None
        try:
            project_id = request.form.get('project_id')
            if project_id is None:
                raise BadRequest("project_id is required")
            try:
                project_id = int(project_id)
            except (TypeError, ValueError):
                raise BadRequest("project_id must be an integer")

            csv_file = request.files.get('file')
            if csv_file is None or not csv_file.filename:
                raise BadRequest("a CSV file is required in the file field")
            if not csv_file.filename.lower().endswith('.csv'):
                raise BadRequest("the uploaded file must have a .csv extension")

            # Authorize before saving the upload to disk.
            ensure_authorized_to('create', Task(project_id=project_id))

            job_id = str(uuid.uuid4())
            if not isinstance(uploader, LocalUploader):
                raise BadRequest('CSV imports require the local upload backend')
            upload_dir = os.path.join(uploader.upload_folder, 'task-imports')
            if not os.path.isdir(upload_dir):
                os.makedirs(upload_dir)
            csv_filename = os.path.join(upload_dir, '%s.csv' % job_id)
            mappings_filename = os.path.join(
                upload_dir, '%s-mappings.csv' % job_id)
            csv_file.save(csv_filename)
            job = importer_queue.enqueue(
                import_tasks, project_id, type='localCSV',
                csv_filename=csv_filename, delete_csv_after_import=True,
                require_occurrence_id=True, mapping_job_id=job_id,
                mappings_csv_filename=mappings_filename,
                job_id=job_id)
            # Ownership of the temporary file passes to the RQ worker.
            csv_filename = None
            response = dict(status='queued', job_id=job.id,
                            project_id=project_id)
            return Response(json.dumps(response), status=202,
                            mimetype='application/json')
        except Exception as exc:
            return error.format_exception(exc, target='task',
                                          action='POST')
        finally:
            if csv_filename is not None and os.path.exists(csv_filename):
                os.unlink(csv_filename)

    def get(self, job_id):
        """Return the RQ state of a CSV import owned by the caller."""
        try:
            try:
                job = Job.fetch(job_id, connection=sentinel.master)
            except NoSuchJobError:
                raise NotFound()

            if not self._is_csv_import(job):
                # Do not expose other RQ jobs through this public API.
                raise NotFound()
            project_id = job.args[0]
            ensure_authorized_to('create', Task(project_id=project_id))

            status = job.get_status()
            response = dict(job_id=job.id, project_id=project_id,
                            status=status)
            if status == 'finished':
                response['message'] = job.result
                if os.path.isfile(job.kwargs.get('mappings_csv_filename', '')):
                    response['mappings_csv_url'] = url_for(
                        'api.api_task_import_download', job_id=job.id)
            elif status == 'failed':
                response['message'] = 'Import failed; inspect worker logs.'
            return Response(json.dumps(response), mimetype='application/json')
        except Exception as exc:
            return error.format_exception(exc, target='task', action='GET')

    @staticmethod
    def _is_csv_import(job):
        """Ensure a job was created by this endpoint before disclosing it."""
        expected_func = '%s.%s' % (import_tasks.__module__,
                                   import_tasks.__name__)
        return (getattr(job, 'func_name', None) == expected_func and
                len(job.args) >= 1 and
                job.kwargs.get('type') == 'localCSV' and
                job.kwargs.get('delete_csv_after_import') is True and
                job.kwargs.get('mapping_job_id') == job.id)

    def get_tasks(self, job_id):
        try:
            job, project_id = self._authorized_finished_import(job_id)
            limit = min(10000, max(1, int(request.args.get('limit', 1000))))
            after = self._decode_cursor(request.args.get('cursor'), job_id)
            query = TaskImportMapping.query.filter_by(job_id=job_id)
            if after:
                query = query.filter(TaskImportMapping.id > after)
            rows = query.order_by(TaskImportMapping.id).limit(limit + 1).all()
            page, extra = rows[:limit], rows[limit:]
            next_cursor = self._encode_cursor(job_id, page[-1].id) if extra else None
            return Response(json.dumps(dict(job_id=job_id, project_id=project_id,
                status='finished', total=TaskImportMapping.query.filter_by(job_id=job_id).count(),
                next_cursor=next_cursor, tasks=[dict(occurrence_id=row.occurrence_id,
                task_id=row.task_id) for row in page])), mimetype='application/json')
        except Exception as exc:
            return error.format_exception(exc, target='task', action='GET')

    def get_csv(self, job_id):
        try:
            job, _ = self._authorized_finished_import(job_id)
            filename = job.kwargs.get('mappings_csv_filename')
            if not filename or not os.path.isfile(filename):
                raise NotFound()
            return send_file(filename, mimetype='text/csv', as_attachment=True,
                             attachment_filename='%s-tasks.csv' % job_id)
        except Exception as exc:
            return error.format_exception(exc, target='task', action='GET')

    def _authorized_finished_import(self, job_id):
        try:
            job = Job.fetch(job_id, connection=sentinel.master)
        except NoSuchJobError:
            raise NotFound()
        if not self._is_csv_import(job) or job.get_status() != 'finished':
            raise NotFound()
        project_id = job.args[0]
        ensure_authorized_to('create', Task(project_id=project_id))
        return job, project_id

    @staticmethod
    def _serializer():
        return URLSafeSerializer(current_app.config['SECRET_KEY'], salt='task-import-cursor')

    def _encode_cursor(self, job_id, row_id):
        return self._serializer().dumps([job_id, row_id])

    def _decode_cursor(self, cursor, job_id):
        if not cursor:
            return None
        try:
            cursor_job_id, row_id = self._serializer().loads(cursor)
            if cursor_job_id != job_id:
                raise BadSignature()
            return int(row_id)
        except (BadSignature, ValueError, TypeError):
            raise BadRequest('Invalid cursor')
