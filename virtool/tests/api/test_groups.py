import json

from virtool.web import create_app


class TestFind:

    async def test_no_params(self, test_db, do_get, all_permissions, no_permissions):
        """
        Test that a ``GET /api/groups`` return a complete list of groups.
         
        """
        test_db.groups.insert({
            "_id": "test",
            "permissions": all_permissions
        })

        test_db.groups.insert({
            "_id": "limited",
            "permissions": no_permissions
        })

        resp = await do_get("/api/groups")

        assert resp.status == 200

        assert await resp.json() == [
            {
                "group_id": "test",
                "permissions": all_permissions
            },
            {
                "group_id": "limited",
                "permissions": no_permissions
            }
        ]


class TestCreate:

    async def test_valid(self, do_post, test_db, no_permissions):
        """
        Test that a group can be added to the database at ``POST /api/groups/:group_id``.
         
        """
        resp = await do_post("/api/groups", data={
            "group_id": "test"
        })

        assert resp.status == 200

        assert await resp.json() == {
            "group_id": "test",
            "permissions": no_permissions
        }

        document = test_db.groups.find_one()

        assert document == {
            "_id": "test",
            "permissions": no_permissions
        }

    async def test_exists(self, do_post, test_db, all_permissions):
        """
        Test that a 404 is returned when attempting to create a new group at ``POST /api/groups/:group_id`` when the
        ``group_id`` already exists.
         
        """
        test_db.groups.insert_one({
            "_id": "test",
            "permissions": all_permissions
        })

        resp = await do_post("/api/groups", data={
            "group_id": "test"
        })

        assert resp.status == 400

        assert await resp.json() == {
            "message": "Group already exists"
        }

    async def test_missing(self, do_post):
        resp = await do_post("/api/groups", data={
            "test": "test"
        })

        assert resp.status == 400

        assert await resp.json() == {
            "message": "Missing group_id"
        }

    async def test_wrong_type(self, do_post):
        resp = await do_post("/api/groups", data={
            "group_id": 1
        })

        assert resp.status == 400

        assert await resp.json() == {
            "message": "Wrong type for group_id"
        }


class TestGet:

    async def test_valid(self, do_get, test_db, all_permissions):
        """
        Test that a ``GET /api/groups/:group_id`` return the correct group.
    
        """
        test_db.groups.insert({
            "_id": "test",
            "permissions": all_permissions
        })

        resp = await do_get("/api/groups/test")

        assert await resp.json() == {
            "group_id": "test",
            "permissions": all_permissions
        }

    async def test_not_found(self, do_get):
        """
        Test that a ``GET /api/groups/:group_id`` returns 404 for a non-existent ``group_id``.
         
        """
        resp = await do_get("/api/groups/foo")

        assert resp.status == 404

        assert await resp.json() == {
            "message": "Not found"
        }


class TestUpdatePermissions:

    async def test_valid(self, do_put, test_db, no_permissions):
        test_db.groups.insert({
            "_id": "test",
            "permissions": no_permissions
        })

        resp = await do_put("/api/groups/test", data={
            "modify_virus": True
        })

        assert resp.status == 200

        no_permissions["modify_virus"] = True

        assert await resp.json() == {
            "group_id": "test",
            "permissions": no_permissions
        }

        document = test_db.groups.find_one({"_id": "test"})

        assert document == {
            "_id": "test",
            "permissions": no_permissions
        }

    async def test_invalid_key(self, do_put, test_db, no_permissions):
        test_db.groups.insert({
            "_id": "test",
            "permissions": no_permissions
        })

        resp = await do_put("/api/groups/test", data={
            "foo_bar": True
        })

        assert resp.status == 400

        assert await resp.json() == {
            "message": "Invalid key"
        }

    async def test_not_found(self, do_put):
        resp = await do_put("/api/groups/test", data={
            "modify_virus": True
        })

        assert resp.status == 404

        assert await resp.json() == {
            "message": "Not found"
        }


class TestRemove:

    async def test_valid(self, test_db, do_delete, no_permissions):
        """
        Test that an existing document can be removed at ``DELETE /api/groups/:group_id``.
         
        """
        test_db.groups.insert({
            "_id": "test",
            "permissions": no_permissions
        })

        resp = await do_delete("/api/groups/test")

        assert await resp.json() == {
            "removed": "test"
        }

        assert resp.status == 200

        assert test_db.groups.count({"_id": "test"}) == 0

    async def test_not_found(self, do_delete):
        """
        Test that 404 is returned for non-existent group.
         
        """
        resp = await do_delete("/api/groups/test")

        assert await resp.json() == {
            "message": "Not found"
        }

        assert resp.status == 404

    async def test_administrator(self, do_delete):
        """
        Test that the administrator group cannot be removed (400).
         
        """
        resp = await do_delete("/api/groups/administrator")

        assert await resp.json() == {
            "message": "Cannot remove administrator group"
        }

        assert resp.status == 400
