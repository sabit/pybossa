# -*- coding: utf8 -*-
# This file is part of PYBOSSA.
#
# Copyright (C) 2015 Scifabric LTD.
#
# PYBOSSA is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# PYBOSSA is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with PYBOSSA.  If not, see <http://www.gnu.org/licenses/>.

import json

from flask_babel import gettext
from sqlalchemy.exc import SQLAlchemyError
from .csv import BulkTaskCSVImport, BulkTaskGDImport, BulkTaskLocalCSVImport
from .dropbox import BulkTaskDropboxImport
from .flickr import BulkTaskFlickrImport
from .twitterapi import BulkTaskTwitterImport
from .youtubeapi import BulkTaskYoutubeImport
from .epicollect import BulkTaskEpiCollectPlusImport
from .iiif import BulkTaskIIIFImporter
from .s3 import BulkTaskS3Import
import six

class Importer(object):

    """Class to import data."""

    def __init__(self):
        """Init method."""
        self._importers = dict(csv=BulkTaskCSVImport,
                               gdocs=BulkTaskGDImport,
                               epicollect=BulkTaskEpiCollectPlusImport,
                               s3=BulkTaskS3Import,
                               localCSV=BulkTaskLocalCSVImport,
                               iiif=BulkTaskIIIFImporter)
        self._importer_constructor_params = dict()

    def register_flickr_importer(self, flickr_params):
        """Register Flickr importer."""
        self._importers['flickr'] = BulkTaskFlickrImport
        self._importer_constructor_params['flickr'] = flickr_params

    def register_dropbox_importer(self):
        """Register Dropbox importer."""
        self._importers['dropbox'] = BulkTaskDropboxImport

    def register_twitter_importer(self, twitter_params):
        self._importers['twitter'] = BulkTaskTwitterImport
        self._importer_constructor_params['twitter'] = twitter_params

    def register_youtube_importer(self, youtube_params):
        self._importers['youtube'] = BulkTaskYoutubeImport
        self._importer_constructor_params['youtube'] = youtube_params

    def create_tasks(self, task_repo, project_id, **form_data):
        """Create tasks in database batches, avoiding per-row commits."""
        from pybossa.model.task import Task
        mapping_job_id = form_data.pop('mapping_job_id', None)
        from pybossa.core import db
        from pybossa.cache import projects as cached_projects
        from pybossa.model.task_import_mapping import TaskImportMapping

        if mapping_job_id:
            # The CSV upload API validates this column.  Keep the guard here
            # as well because mappings without a join key are unusable.
            require_occurrence_id = True
        else:
            require_occurrence_id = False

        importer = self._create_importer_for(**form_data)
        batch = []
        created = 0
        for task_data in importer.tasks():
            occurrence_id = task_data.pop('occurrence_id', None)
            if require_occurrence_id and not occurrence_id:
                raise ValueError('occurrence_id is required for import mappings')
            batch.append((task_data, occurrence_id))
            if len(batch) == 1000:
                created += self._save_batch(
                    db, Task, TaskImportMapping, project_id, batch,
                    mapping_job_id)
                batch = []
        if batch:
            created += self._save_batch(
                db, Task, TaskImportMapping, project_id, batch,
                mapping_job_id)

        if created:
            cached_projects.clean_project(project_id)
        if created == 0:
            msg = gettext('It looks like there were no new records to import')
            return ImportReport(message=msg, metadata=None, total=created)
        metadata = importer.import_metadata()
        msg = str(created) + " " + gettext('new tasks were imported successfully')
        if created == 1:
            msg = str(created) + " " + gettext('new task was imported successfully')
        report = ImportReport(message=msg, metadata=metadata, total=created)
        return report

    @staticmethod
    def _task_key(info):
        """Canonical JSON key matching the existing project/info dedup rule."""
        return json.dumps(info, sort_keys=True, separators=(',', ':'),
                          default=str)

    def _save_batch(self, db, Task, TaskImportMapping, project_id, batch,
                    mapping_job_id):
        """Resolve, insert, and map up to 1,000 CSV rows in one transaction."""
        from pybossa.model.counter import Counter

        rows_by_key = {}
        for task_data, occurrence_id in batch:
            info = task_data.get('info') or {}
            key = self._task_key(info)
            if key not in rows_by_key:
                values = dict(task_data)
                values['project_id'] = project_id
                values['info'] = info
                rows_by_key[key] = dict(values=values, occurrences=[])
            rows_by_key[key]['occurrences'].append(occurrence_id)

        try:
            infos = [row['values']['info'] for row in rows_by_key.values()]
            existing = db.session.query(Task.id, Task.info).filter(
                Task.project_id == project_id, Task.info.in_(infos)).all()
            task_ids = {self._task_key(info): task_id
                        for task_id, info in existing}
            missing = [row['values'] for key, row in rows_by_key.items()
                       if key not in task_ids]
            if missing:
                inserted = db.session.execute(
                    Task.__table__.insert().values(missing).returning(
                        Task.id, Task.info)).fetchall()
                task_ids.update({self._task_key(info): task_id
                                 for task_id, info in inserted})
                # Core bulk inserts bypass Task's after_insert listener, which
                # normally creates this row.  Keep the counter write bulk and
                # in the same transaction as its task rows.
                db.session.execute(Counter.__table__.insert(), [
                    dict(project_id=project_id, task_id=task_id,
                         n_task_runs=0)
                    for task_id, _ in inserted
                ])
            if mapping_job_id:
                mappings = []
                for key, row in rows_by_key.items():
                    mappings.extend(dict(job_id=mapping_job_id,
                        project_id=project_id, occurrence_id=str(occurrence),
                        task_id=task_ids[key]) for occurrence in row['occurrences'])
                db.session.execute(TaskImportMapping.__table__.insert(), mappings)
            db.session.commit()
            return len(missing)
        except SQLAlchemyError:
            db.session.rollback()
            raise

    def count_tasks_to_import(self, **form_data):
        """Count tasks to import."""
        return self._create_importer_for(**form_data).count_tasks()

    def _create_importer_for(self, **form_data):
        """Create importer."""
        importer_id = form_data.get('type')
        params = self._importer_constructor_params.get(importer_id) or {}
        params.update(form_data)
        del params['type']
        return self._importers[importer_id](**params)

    def get_all_importer_names(self):
        """Get all importer names."""
        return self._importers.keys()

    def get_autoimporter_names(self):
        """Get autoimporter names."""
        no_autoimporters = ('dropbox', 's3')
        return [name for name in self._importers.keys() if name not in no_autoimporters]


class ImportReport(object):

    def __init__(self, message, metadata, total):
        self._message = message
        self._metadata = metadata
        self._total = total

    @property
    def message(self):
        return self._message

    @property
    def metadata(self):
        return self._metadata

    @property
    def total(self):
        return self._total
