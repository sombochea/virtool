from logging import getLogger
from typing import Union, List

from aiohttp.web_exceptions import HTTPBadRequest, HTTPConflict, HTTPNoContent
from aiohttp_pydantic import PydanticView
from aiohttp_pydantic.oas.typing import r200, r204, r400, r403, r404, r409

from virtool.api.response import NotFound, json_response
from virtool.data.errors import (
    ResourceConflictError,
    ResourceNotFoundError,
)
from virtool.data.utils import get_data_from_req
from virtool.http.policy import policy, PermissionsRoutePolicy
from virtool.http.routes import Routes
from virtool.http.schema import schema
from virtool.users.utils import Permission
from virtool.jobs.oas import GetJobResponse, JobResponse

logger = getLogger(__name__)

routes = Routes()


@routes.view("/jobs")
class JobsView(PydanticView):
    async def get(self) -> Union[r200[List[GetJobResponse]], r400]:
        """
        Find jobs.

        Finds jobs on the instance.

        Jobs can be filtered by their current ``state`` by providing desired states as
        query parameters.

        **Archived jobs are not currently returned from the API**.

        Status Codes:
            200: Successful operation
            400: Invalid query
        """
        return json_response(
            await get_data_from_req(self.request).jobs.find(self.request.query)
        )

    @policy(PermissionsRoutePolicy(Permission.remove_job))
    async def delete(self) -> r200:
        """
        Clear jobs.

        Clears completed, failed or all finished jobs.

        Status Codes:
            200: Successful Operation
        """
        job_filter = self.request.query.get("filter")

        # Remove jobs that completed successfully.
        complete = job_filter in [None, "finished", "complete"]

        # Remove jobs that errored or were cancelled.
        failed = job_filter in [None, "failed", "finished" "terminated"]

        removed_job_ids = await get_data_from_req(self.request).jobs.clear(
            complete=complete, failed=failed
        )

        return json_response({"removed": removed_job_ids})


@routes.view("/jobs/{job_id}")
class JobView(PydanticView):
    async def get(self) -> Union[r200[JobResponse], r404]:
        """
        Get a job.

        Retrieves the details for a job.

        Status Codes:
            200: Successful operation
            404: Not found
        """
        try:
            document = await get_data_from_req(self.request).jobs.get(
                self.request.match_info["job_id"]
            )
        except ResourceNotFoundError:
            raise NotFound()

        return json_response(document)

    @policy(PermissionsRoutePolicy(Permission.remove_job))
    async def delete(self) -> Union[r204, r403, r404, r409]:
        """
        Delete a job.

        Deletes a job.

        Jobs that are in an active state (waiting, pending, preparing
        running) cannot be deleted. A `409` will be returned if this operation is
        attempted.

        **We recommend archiving jobs instead of deleting them**. In the future, job
        deletion will not be supported.

        Status Codes:
            204: Successful operation
            403: Not permitted
            404: Not found
            409: Job is running or waiting and cannot be removed
        """
        try:
            await get_data_from_req(self.request).jobs.delete(
                self.request.match_info["job_id"]
            )
        except ResourceConflictError:
            raise HTTPConflict(text="Job is running or waiting and cannot be removed")
        except ResourceNotFoundError:
            raise NotFound()

        raise HTTPNoContent


@routes.jobs_api.get("/jobs/{job_id}")
async def get(req):
    """
    Get a job.

    """
    try:
        document = await get_data_from_req(req).jobs.get(req.match_info["job_id"])
    except ResourceNotFoundError:
        raise NotFound()

    return json_response(document)


@routes.jobs_api.patch("/jobs/{job_id}")
@schema({"acquired": {"type": "boolean", "allowed": [True], "required": True}})
async def acquire(req):
    """
    Sets the acquired field on the job document.

    This is used to let the server know that a job process has accepted the ID and needs
    to have the secure token returned to it. Pushes a status record indicating the job
    is in the 'Preparing' state. This sets an arbitrary progress value of 3 to give
    visual feedback in the UI that the job has started.
    """
    try:
        document = await get_data_from_req(req).jobs.acquire(req.match_info["job_id"])
    except ResourceNotFoundError:
        raise NotFound()
    except ResourceConflictError:
        raise HTTPBadRequest(text="Job already acquired")

    return json_response(document)


@routes.patch("/jobs/{job_id}/archive")
@routes.jobs_api.patch("/jobs/{job_id}/archive")
async def archive(req):
    """
    Sets the archived field on the job document.
    """
    try:
        document = await get_data_from_req(req).jobs.archive(req.match_info["job_id"])
    except ResourceNotFoundError:
        raise NotFound()
    except ResourceConflictError:
        raise HTTPBadRequest(text="Job already archived")

    return json_response(document)


@routes.view("/jobs/{job_id}/cancel")
class CancelJobView(PydanticView):
    @policy(PermissionsRoutePolicy(Permission.cancel_job))
    async def put(self) -> Union[r200[JobResponse], r403, r404, r409]:
        """
        Cancel a job.

        Status Codes:
            200: Successful operation
            403: Not permitted
            404: Not found
            409: Not cancellable
        """
        try:
            document = await get_data_from_req(self.request).jobs.cancel(
                self.request.match_info["job_id"]
            )
        except ResourceNotFoundError:
            raise NotFound
        except ResourceConflictError:
            raise HTTPConflict(text="Job cannot be cancelled in its current state")

        return json_response(document)


@routes.post("/jobs/{job_id}/status")
@routes.jobs_api.post("/jobs/{job_id}/status")
@schema(
    {
        "error": {
            "type": "dict",
            "default": None,
            "nullable": True,
            "schema": {
                "type": {"type": "string", "required": True},
                "traceback": {
                    "type": "list",
                    "schema": {"type": "string"},
                    "required": True,
                },
                "details": {
                    "type": "list",
                    "schema": {"type": "string"},
                    "required": True,
                },
            },
        },
        "progress": {"type": "integer", "required": True, "min": 0, "max": 100},
        "stage": {
            "type": "string",
            "required": True,
        },
        "step_name": {"type": "string", "default": None, "nullable": True},
        "step_description": {"type": "string", "default": None, "nullable": True},
        "state": {
            "type": "string",
            "allowed": [
                "waiting",
                "running",
                "complete",
                "cancelled",
                "error",
                "terminated",
            ],
            "required": True,
        },
    }
)
async def push_status(req):
    """Push a status update to a job."""
    data = req["data"]

    if data["state"] == "error" and not data["error"]:
        raise HTTPBadRequest(text="Missing error information")

    try:
        document = await get_data_from_req(req).jobs.push_status(
            req.match_info["job_id"],
            data["state"],
            data["stage"],
            data["step_name"],
            data["step_description"],
            data["error"],
            data["progress"],
        )
    except ResourceNotFoundError:
        raise NotFound
    except ResourceConflictError:
        raise HTTPConflict(text="Job is finished")

    return json_response(document["status"][-1], status=201)
