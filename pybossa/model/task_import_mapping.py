# -*- coding: utf8 -*-
from sqlalchemy import Integer, Text, UniqueConstraint, Index
from sqlalchemy.schema import Column, ForeignKey

from pybossa.core import db


class TaskImportMapping(db.Model):
    __tablename__ = 'task_import_mapping'
    __table_args__ = (
        UniqueConstraint('job_id', 'occurrence_id', name='uq_import_occurrence'),
        Index('ix_import_mapping_job_id_id', 'job_id', 'id'),
    )

    id = Column(Integer, primary_key=True)
    job_id = Column(Text, nullable=False, index=True)
    project_id = Column(Integer, ForeignKey('project.id', ondelete='CASCADE'),
                        nullable=False)
    occurrence_id = Column(Text, nullable=False)
    task_id = Column(Integer, ForeignKey('task.id', ondelete='CASCADE'),
                     nullable=False)
