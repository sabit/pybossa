"""add task import mappings

Revision ID: 5b7d0f9c1a2e
Revises: 31c3c2ff9fab
"""
revision = '5b7d0f9c1a2e'
down_revision = '31c3c2ff9fab'

from alembic import op
import sqlalchemy as sa

def upgrade():
    op.create_table('task_import_mapping',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('job_id', sa.Text, nullable=False),
        sa.Column('project_id', sa.Integer, sa.ForeignKey('project.id', ondelete='CASCADE'), nullable=False),
        sa.Column('occurrence_id', sa.Text, nullable=False),
        sa.Column('task_id', sa.Integer, sa.ForeignKey('task.id', ondelete='CASCADE'), nullable=False),
        sa.UniqueConstraint('job_id', 'occurrence_id', name='uq_import_occurrence'))
    op.create_index('ix_import_mapping_job_id_id', 'task_import_mapping', ['job_id', 'id'])

def downgrade():
    op.drop_index('ix_import_mapping_job_id_id', table_name='task_import_mapping')
    op.drop_table('task_import_mapping')
