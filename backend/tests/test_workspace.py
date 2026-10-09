"""D2 detail ownership and complete, sorted conversation navigation."""
import unittest
from datetime import datetime, timezone, timedelta
import test_dashboard
from app.models.knowledge import Workspace, SessionWorkspace
from app.models.chat_sessions import ChatSession


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        test_dashboard.DashboardTests.setUp(self)
        with self.factory() as db:
            db.add_all([Workspace(id="owned", name="Real workspace", user_id=self.users[0][0]),
                        Workspace(id="other", name="Private", user_id=self.users[1][0])])
            db.commit()

    def test_workspace_detail_is_owned_and_read_only(self):
        self.assertEqual(self.client.get("/api/workspaces/owned").status_code, 401)
        headers = self.users[0][1]
        response = self.client.get("/api/workspaces/owned", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "Real workspace")
        for workspace in ["other", "missing"]:
            self.assertEqual(self.client.get(f"/api/workspaces/{workspace}", headers=headers).status_code, 404)
        with self.factory() as db:
            self.assertEqual(db.query(Workspace).count(), 2)

    def test_paging_sorting_and_old_session_lookup_preserve_ownership(self):
        now = datetime.now(timezone.utc)
        with self.factory() as db:
            for i in range(105):
                db.add(ChatSession(id=f"c{i:03}", title=f"Title {i:03}", user_id=self.users[0][0], updated_at=now+timedelta(seconds=i)))
            db.add(ChatSession(id="private", title="Private", user_id=self.users[1][0]))
            db.flush()
            db.add_all([SessionWorkspace(session_id=f"c{i:03}", workspace_id="owned") for i in range(105)])
            db.add(SessionWorkspace(session_id="private", workspace_id="other"))
            db.commit()
        headers=self.users[0][1]
        url="/api/chat/sessions?workspace_id=owned"
        default=self.client.get(url,headers=headers).json()
        self.assertEqual(len(default),100)
        self.assertEqual(default[0]["id"],"c104")
        pages=[self.client.get(url+f"&limit=30&offset={offset}",headers=headers).json() for offset in [0,30,60,90]]
        ids=[row["id"] for page in pages for row in page]
        self.assertEqual(len(ids),105)
        self.assertEqual(len(set(ids)),105)
        for sort in ["oldest","title"]:
            first=self.client.get(url+f"&sort={sort}&limit=1",headers=headers).json()
            self.assertEqual(first[0]["id"],"c000")
        old=self.client.get(url+"&session_id=c000",headers=headers).json()
        self.assertEqual(old[0]["workspace_id"],"owned")
        self.assertIsNone(old[0]["session_type"])
        self.assertEqual(self.client.get(url+"&session_id=private",headers=headers).json(),[])
        self.assertEqual(self.client.get("/api/chat/sessions?workspace_id=other",headers=headers).status_code,404)
        for query in ["&offset=-1","&limit=101","&sort=unknown"]:
            self.assertEqual(self.client.get(url+query,headers=headers).status_code,422)
