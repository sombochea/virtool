from copy import deepcopy

import dictdiffer

import virtool.db.kinds
import virtool.errors
import virtool.kinds
import virtool.utils
import virtool.history
from virtool.api.utils import paginate

MOST_RECENT_PROJECTION = [
    "_id",
    "description",
    "method_name",
    "user",
    "kind",
    "created_at"
]

LIST_PROJECTION = [
    "_id",
    "description",
    "method_name",
    "created_at",
    "index",
    "kind",
    "ref",
    "user"
]

PROJECTION = LIST_PROJECTION + [
    "diff"
]


async def add(db, method_name, old, new, description, user_id):
    """
    Add a change document to the history collection.

    :param db: the application database client
    :type db: :class:`~motor.motor_asyncio.AsyncIOMotorClient`

    :param method_name: the name of the handler method that executed the change
    :type method_name: str

    :param old: the kind document prior to the change
    :type new: Union[dict, None]

    :param new: the kind document after the change
    :type new: Union[dict, None]

    :param description: a human readable description of the change
    :type description: str

    :param user_id: the id of the requesting user
    :type user_id: str

    :return: the change document
    :rtype: Coroutine[dict]

    """
    try:
        kind_id = old["_id"]
    except TypeError:
        kind_id = new["_id"]

    try:
        kind_name = old["name"]
    except TypeError:
        kind_name = new["name"]

    try:
        kind_version = int(new["version"])
    except (TypeError, KeyError):
        kind_version = "removed"

    try:
        ref_id = old["ref"]["id"]
    except (TypeError, KeyError):
        ref_id = new["ref"]["id"]

    document = {
        "_id": ".".join([str(kind_id), str(kind_version)]),
        "method_name": method_name,
        "description": description,
        "created_at": virtool.utils.timestamp(),
        "kind": {
            "id": kind_id,
            "name": kind_name,
            "version": kind_version
        },
        "ref": {
            "id": ref_id
        },
        "index": {
            "id": "unbuilt",
            "version": "unbuilt"
        },
        "user": {
            "id": user_id
        }
    }

    if method_name == "create":
        document["diff"] = new

    elif method_name == "remove":
        document["diff"] = old

    else:
        document["diff"] = virtool.history.calculate_diff(old, new)

    await db.history.insert_one(document)

    return document


async def find(db, req_query, base_query=None):
    data = await paginate(
        db.history,
        {},
        req_query,
        base_query=base_query,
        sort="created_at",
        projection=LIST_PROJECTION,
        reverse=True
    )

    return data


async def get_contributors(db, query):
    """
    Return an list of contributors and their contribution count for a specific set of history.

    :param db: the application database client
    :type db: :class:`~motor.motor_asyncio.AsyncIOMotorClient`

    :param query: a query to filter scanned history by
    :type query: dict

    :return: a list of contributors to the scanned history changes
    :rtype: List[dict]

    """
    contributors = await db.history.aggregate([
        {"$match": query},
        {"$group": {
            "_id": "$user.id",
            "count": {"$sum": 1}
        }}
    ]).to_list(None)

    return [{"id": c["_id"], "count": c["count"]} for c in contributors]


async def get_most_recent_change(db, kind_id):
    """
    Get the most recent change for the kind identified by the passed ``kind_id``.

    :param db: the application database client
    :type db: :class:`~motor.motor_asyncio.AsyncIOMotorClient`

    :param kind_id: the target kind_id
    :type kind_id: str

    :return: the most recent change document
    :rtype: Coroutine[dict]

    """
    return await db.history.find_one({
        "kind.id": kind_id,
        "index.id": "unbuilt"
    }, MOST_RECENT_PROJECTION, sort=[("created_at", -1)])


async def patch_to_version(db, kind_id, version):
    """
    Take a joined kind back in time to the passed ``version``. Uses the diffs in the change documents associated with
    the kind.

    :param db: the application database client
    :type db: :class:`~motor.motor_asyncio.AsyncIOMotorClient`

    :param kind_id: the id of the kind to patch
    :type kind_id: str

    :param version: the version to patch to
    :type version: str or int

    :return: the current joined kind, patched kind, and the ids of changes reverted in the process
    :rtype: Coroutine[tuple]

    """
    # A list of history_ids reverted to produce the patched entry.
    reverted_history_ids = list()

    current = await virtool.db.kinds.join(db, kind_id) or dict()

    if "version" in current and current["version"] == version:
        return current, deepcopy(current), reverted_history_ids

    patched = deepcopy(current)

    # Sort the changes by descending timestamp.
    async for change in db.history.find({"kind.id": kind_id}, sort=[("created_at", -1)]):
        if change["kind"]["version"] == "removed" or change["kind"]["version"] > version:
            reverted_history_ids.append(change["_id"])

            if change["method_name"] == "remove":
                patched = change["diff"]

            elif change["method_name"] == "create":
                patched = None

            else:
                diff = dictdiffer.swap(change["diff"])
                patched = dictdiffer.patch(diff, patched)
        else:
            break

    if current == {}:
        current = None

    return current, patched, reverted_history_ids


async def revert(db, change_id):
    """
    Revert a history change given by the passed ``change_id``.

    :param db: the application database client
    :type db: :class:`~motor.motor_asyncio.AsyncIOMotorClient`

    :param change_id: a unique id for the cah
    :return:
    """
    change = await db.history.find_one({"_id": change_id}, ["index"])

    if not change:
        raise virtool.errors.DatabaseError("Change does not exist")

    if change["index"]["id"] != "unbuilt" or change["index"]["version"] != "unbuilt":
        raise virtool.errors.DatabaseError("Change is included in a build an not revertible")

    kind_id, kind_version = change_id.split(".")

    if kind_version != "removed":
        kind_version = int(kind_version)

    _, patched, history_to_delete = await patch_to_version(
        db,
        kind_id,
        kind_version - 1
    )

    # Remove the old sequences from the collection.
    await db.sequences.delete_many({"kind_id": kind_id})

    if patched is not None:
        patched_kind, sequences = virtool.kinds.split(patched)

        # Add the reverted sequences to the collection.
        if len(sequences):
            await db.sequences.insert_many(sequences)

        # Replace the existing kind with the patched one. If it doesn't exist, insert it.
        await db.kinds.replace_one({"_id": kind_id}, patched_kind, upsert=True)

    else:
        await db.kinds.delete_one({"_id": kind_id})

    await db.history.delete_many({"_id": {"$in": history_to_delete}})

    return patched
