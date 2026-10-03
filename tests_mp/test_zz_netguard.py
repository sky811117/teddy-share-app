# -*- coding: utf-8 -*-
"""最後跑：整個 tests_mp 期間連外嘗試必須是 0 次。"""
try:
    from . import _sguard
except ImportError:
    import _sguard

import socket
import unittest


class NetguardSummaryTest(unittest.TestCase):

    def test_guard_installed(self):
        with self.assertRaises(_sguard.NetBlocked):
            socket.create_connection(('example.com', 443), timeout=1)
        _sguard._state()['attempts'].pop()                  # 這一次是刻意的

    def test_zz_no_outbound_attempts(self):
        self.assertEqual(_sguard.attempts(), [])


if __name__ == '__main__':
    unittest.main()
