import os

import aiojobs.aiohttp

import virtool.db.history
import virtool.db.indexes
import virtool.db.otus
import virtool.db.processes
import virtool.db.references
import virtool.db.utils
import virtool.http.routes
import virtool.otus
import virtool.utils
from virtool.api.utils import compose_regex_query, json_response, not_found, paginate

routes = virtool.http.routes.Routes()


@routes.get("/api/refs")
async def find(req):
    db = req.app["db"]

    term = req.query.get("find", None)

    db_query = dict()

    if term:
        db_query.update(compose_regex_query(term, ["name", "data_type"]))

    data = await paginate(db.references, db_query, req.query, sort="name", projection=virtool.db.references.PROJECTION)

    for d in data["documents"]:
        d["latest_build"] = await virtool.db.references.get_latest_build(db, d["id"])

    return json_response(data)


@routes.get("/api/refs/{ref_id}")
async def get(req):
    """
    Get the complete representation of a specfic reference.

    """
    db = req.app["db"]

    ref_id = req.match_info["ref_id"]

    document = await db.references.find_one(ref_id)

    if not document:
        return not_found()

    try:
        internal_control_id = document["internal_control"]["id"]
    except (KeyError, TypeError):
        internal_control_id = None

    document.update(await virtool.db.references.get_computed(db, ref_id, internal_control_id))

    return json_response(virtool.utils.base_processor(document))


@routes.get("/api/refs/{ref_id}/otus")
async def find_otus(req):
    db = req.app["db"]

    ref_id = req.match_info["ref_id"]

    term = req.query.get("find", None)
    verified = req.query.get("verified", None)
    names = req.query.get("names", False)

    data = await virtool.db.otus.find(
        db,
        names,
        term,
        req.query,
        verified,
        ref_id
    )

    return json_response(data)


@routes.get("/api/refs/{ref_id}/history")
async def find_history(req):
    db = req.app["db"]

    ref_id = req.match_info["ref_id"]

    if not await db.references.count({"_id": ref_id}):
        return not_found()

    base_query = {
        "reference.id": ref_id
    }

    data = await virtool.db.history.find(
        db,
        req.query,
        base_query
    )

    return json_response(data)


@routes.get("/api/refs/{ref_id}/indexes")
async def find_indexes(req):
    db = req.app["db"]

    ref_id = req.match_info["ref_id"]

    if not await db.references.count({"_id": ref_id}):
        return not_found()

    data = await virtool.db.indexes.find(
        db,
        req.query,
        ref_id=ref_id
    )

    return json_response(data)


@routes.post("/api/refs", permission="create_ref", schema={
    "name": {
        "type": "string",
        "required": True
    },
    "description": {
        "type": "string",
        "default": ""
    },
    "data_type": {
        "type": "string",
        "allowed": ["genome", "barcode"],
        "default": "genome"
    },
    "clone_from": {
        "type": "string",
        "excludes": [
            "import_from",
            "remote_from"
        ]
    },
    "import_from": {
        "type": "string",
        "excludes": [
            "clone_from",
            "remote_from"
        ]
    },
    "remote_from": {
        "type": "string",
        "excludes": [
            "clone_from",
            "import_from"
        ]
    },
    "organism": {
        "type": "string",
        "default": ""
    },
    "public": {
        "type": "boolean",
        "default": False
    }

})
async def create(req):
    db = req.app["db"]
    data = req["data"]
    settings = req.app["settings"]

    user_id = req["client"].user_id

    clone_from = data.get("clone_from", None)
    import_from = data.get("import_from", None)

    if clone_from:
        document = await virtool.db.references.clone(
            db,
            settings,
            data["name"],
            clone_from,
            data["description"],
            data["public"],
            user_id
        )

    elif import_from:
        if not await db.files.count({"_id": import_from}):
            return not_found("File not found")

        path = os.path.join(req.app["settings"]["data_path"], "files", import_from)

        document = await virtool.db.references.create_for_import(
            db,
            settings,
            data["name"],
            data["description"],
            data["public"],
            import_from,
            user_id
        )

        process = await virtool.db.processes.register(db, "import_reference")

        document["process"] = {
            "id": process["id"]
        }

        await aiojobs.aiohttp.spawn(req, virtool.db.references.import_file(
            req.app,
            path,
            document["_id"],
            document["created_at"],
            process["id"],
            user_id
        ))

    else:
        document = await virtool.db.references.create_document(
            db,
            settings,
            data["name"],
            data["organism"],
            data["description"],
            data["data_type"],
            data["public"],
            user_id=req["client"].user_id
        )

    await db.references.insert_one(document)

    headers = {
        "Location": "/api/refs/" + document["_id"]
    }

    document.update(await virtool.db.references.get_computed(db, document["_id"], None))

    return json_response(virtool.utils.base_processor(document), headers=headers, status=201)


@routes.patch("/api/refs/{ref_id}", schema={
    "name": {
        "type": "string"
    },
    "description": {
        "type": "string"
    },
    "data_type": {
        "type": "string",
        "allowed": ["genome", "barcode"]
    },
    "organism": {
        "type": "string"
    },
    "public": {
        "type": "boolean"
    },
    "internal_control": {
        "type": "string"
    },
    "source_types": {
        "type": "list",
        "schema": {
            "type": "string"
        }
    }
})
async def edit(req):
    db = req.app["db"]
    data = req["data"]

    ref_id = req.match_info["ref_id"]

    if not await db.references.count({"_id": ref_id}):
        return not_found()

    update = data

    internal_control_id = data.get("internal_control", None)

    if internal_control_id:
        if not await db.otus.find_one({"_id": internal_control_id, "reference.id": ref_id}):
            return not_found("Internal control not found")

        update["internal_control"] = {
            "id": update["internal_control"]
        }

    document = await db.references.find_one_and_update({"_id": ref_id}, {
        "$set": update
    }, projection=virtool.db.references.PROJECTION)

    document["internal_control"] = await virtool.db.references.get_internal_control(db, internal_control_id)

    document = virtool.utils.base_processor(document)

    return json_response(document)


@routes.get("/api/refs/{ref_id}/unbuilt")
async def get_unbuilt_changes(req):
    """
    Get a JSON document describing the unbuilt changes that could be used to create a new index.

    """
    db = req.app["db"]

    ref_id = req.match_info["ref_id"]

    history = await db.history.find({
        "reference.id": ref_id,
        "index.id": "unbuilt"
    }, virtool.db.history.LIST_PROJECTION).to_list(None)

    return json_response({
        "history": [virtool.utils.base_processor(c) for c in history]
    })


@routes.delete("/api/refs/{ref_id}")
async def remove(req):
    """
    Remove a reference and its otus, history, and indexes.

    """
    db = req.app["db"]

    ref_id = req.match_info["ref_id"]

    user_id = req["client"].user_id

    delete_result = await db.references.delete_one({
        "_id": ref_id
    })

    if not delete_result.deleted_count:
        return not_found()

    process = await virtool.db.processes.register(db, "delete_reference")

    await aiojobs.aiohttp.spawn(req, virtool.db.references.cleanup_removed(
        db,
        process["id"],
        ref_id,
        user_id
    ))

    headers = {
        "Content-Location": "/api/processes/" + process["id"]
    }

    return json_response(process, 202, headers)


