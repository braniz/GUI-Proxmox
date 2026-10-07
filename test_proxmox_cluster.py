import unittest
from unittest.mock import patch

from proxmox_cluster import ProxmoxClient


class SummaryTest(unittest.TestCase):
    def test_summary_and_auth_header(self):
        c = ProxmoxClient("h", "u@pve!t", "secret")
        self.assertEqual(c._headers["Authorization"], "PVEAPIToken=u@pve!t=secret")
        data = {
            "/cluster/status": [{"type": "cluster", "name": "c1", "quorate": 1}],
            "/nodes": [{"node": "pve1", "status": "online", "cpu": 0.1}],
        }
        with patch.object(ProxmoxClient, "get", side_effect=lambda p: data[p]):
            s = c.summary()
        self.assertEqual(s["cluster_name"], "c1")
        self.assertTrue(s["quorate"])
        self.assertEqual(s["nodes"][0]["name"], "pve1")


if __name__ == "__main__":
    unittest.main()
