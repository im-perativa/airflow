# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

from connexion import NoContent
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload, subqueryload

from airflow.api_connexion import security
from airflow.api_connexion.exceptions import Conflict, NotFound
from airflow.api_connexion.parameters import apply_sorting, check_limit, format_parameters
from airflow.api_connexion.schemas.dataset_schema import (
    DatasetCollection,
    DatasetEventCollection,
    dataset_collection_schema,
    dataset_event_collection_schema,
    dataset_schema,
)
from airflow.models.dataset import DatasetEvent, DatasetModel
from airflow.security import permissions
from airflow.utils.db import get_query_count
from airflow.utils.log.action_logger import action_event_from_permission
from airflow.utils.session import NEW_SESSION, provide_session
from airflow.www.decorators import action_logging

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from airflow.api_connexion.types import APIResponse

RESOURCE_EVENT_PREFIX = "dataset"


@security.requires_access_dataset("DELETE")
@provide_session
@action_logging(
    event=action_event_from_permission(
        prefix=RESOURCE_EVENT_PREFIX,
        permission=permissions.ACTION_CAN_DELETE,
    ),
)
def delete_dataset(*, uri: str, session: Session = NEW_SESSION) -> APIResponse:
    """Delete a Dataset."""
    dataset = session.scalar(select(DatasetModel).where(DatasetModel.uri == uri))
    if dataset is None:
        raise NotFound(
            "Dataset not found",
            detail=f"The Dataset with uri: `{uri}` was not found",
        )
    if dataset.consuming_dags or dataset.producing_tasks:
        raise Conflict(detail="Dataset is still referenced by other DAG")

    session.delete(dataset)
    return NoContent, HTTPStatus.NO_CONTENT


@security.requires_access_dataset("GET")
@provide_session
def get_dataset(*, uri: str, session: Session = NEW_SESSION) -> APIResponse:
    """Get a Dataset."""
    dataset = session.scalar(
        select(DatasetModel)
        .where(DatasetModel.uri == uri)
        .options(joinedload(DatasetModel.consuming_dags), joinedload(DatasetModel.producing_tasks))
    )
    if not dataset:
        raise NotFound(
            "Dataset not found",
            detail=f"The Dataset with uri: `{uri}` was not found",
        )
    return dataset_schema.dump(dataset)


@security.requires_access_dataset("GET")
@format_parameters({"limit": check_limit})
@provide_session
def get_datasets(
    *,
    limit: int,
    offset: int = 0,
    uri_pattern: str | None = None,
    order_by: str = "id",
    session: Session = NEW_SESSION,
) -> APIResponse:
    """Get datasets."""
    allowed_attrs = ["id", "uri", "created_at", "updated_at"]

    total_entries = session.scalars(select(func.count(DatasetModel.id))).one()
    query = select(DatasetModel)
    if uri_pattern:
        query = query.where(DatasetModel.uri.ilike(f"%{uri_pattern}%"))
    query = apply_sorting(query, order_by, {}, allowed_attrs)
    datasets = session.scalars(
        query.options(subqueryload(DatasetModel.consuming_dags), subqueryload(DatasetModel.producing_tasks))
        .offset(offset)
        .limit(limit)
    ).all()
    return dataset_collection_schema.dump(DatasetCollection(datasets=datasets, total_entries=total_entries))


@security.requires_access_dataset("GET")
@provide_session
@format_parameters({"limit": check_limit})
def get_dataset_events(
    *,
    limit: int,
    offset: int = 0,
    order_by: str = "timestamp",
    dataset_id: int | None = None,
    source_dag_id: str | None = None,
    source_task_id: str | None = None,
    source_run_id: str | None = None,
    source_map_index: int | None = None,
    session: Session = NEW_SESSION,
) -> APIResponse:
    """Get dataset events."""
    allowed_attrs = ["source_dag_id", "source_task_id", "source_run_id", "source_map_index", "timestamp"]

    query = select(DatasetEvent)

    if dataset_id:
        query = query.where(DatasetEvent.dataset_id == dataset_id)
    if source_dag_id:
        query = query.where(DatasetEvent.source_dag_id == source_dag_id)
    if source_task_id:
        query = query.where(DatasetEvent.source_task_id == source_task_id)
    if source_run_id:
        query = query.where(DatasetEvent.source_run_id == source_run_id)
    if source_map_index:
        query = query.where(DatasetEvent.source_map_index == source_map_index)

    query = query.options(subqueryload(DatasetEvent.created_dagruns))

    total_entries = get_query_count(query, session=session)
    query = apply_sorting(query, order_by, {}, allowed_attrs)
    events = session.scalars(query.offset(offset).limit(limit)).all()
    return dataset_event_collection_schema.dump(
        DatasetEventCollection(dataset_events=events, total_entries=total_entries)
    )
